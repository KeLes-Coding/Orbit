"""LangGraph Chat Runtime —— ChatGraph 构建与执行。

Phase 2 将 Phase 1 的 3 节点线性 graph 扩展为带分支的 5 节点结构：

  prepare_context → route_execution → normal_chat ──┐
                                    → agentic_chat ─┤
                                                  finalize_message → END

normal_chat 沿用 Phase 1 普通聊天逻辑，
agentic_chat 通过 DeepAgent 实现 LLM 驱动的搜索/研究工作流。
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from typing import Any, cast

from langchain_core.messages import BaseMessage, HumanMessage
from langchain_core.tools import StructuredTool
from langgraph.checkpoint.memory import MemorySaver
from langgraph.config import get_stream_writer
from langgraph.graph import END, START, StateGraph

from app.services.langgraph_runtime.agent_types import AgentBudget, AgentEvent
from app.services.langgraph_runtime.agent_workspace import AgentWorkspace
from app.services.langgraph_runtime.deep_agent import DeepAgent, LlmInvoker
from app.services.langgraph_runtime.state import ChatState
from app.services.langgraph_runtime.stream_adapter import StreamAdapter
from app.services.llm_client import LLMClientError, LLMStreamChunk
from app.services.streaming import conversation_stream_store
from app.services.tools import OrbitToolRuntime


class LangGraphChatRuntime:
    """LangGraph Chat 运行时。

    负责：
    - 构建并编译包含 normal_chat / agentic_chat 双分支的 ChatGraph
    - 提供 run_stream() 作为统一的流式执行入口
    - normal_chat: 通过 stream_factory 复用 LLMClient 流式归一化
    - agentic_chat: 通过 llm_invoke 复用 LLMClient 的 tool-calling 能力

    Phase 2 中 agentic_chat 仅在 llm_invoke 非空时可用。
    """

    def __init__(
        self,
        *,
        stream_factory: Callable[[], AsyncIterator[LLMStreamChunk]],
        llm_invoke: LlmInvoker | None = None,
        tool_runtime: OrbitToolRuntime | None = None,
    ) -> None:
        self._stream_factory = stream_factory
        self._llm_invoke = llm_invoke
        self._tool_runtime = tool_runtime or OrbitToolRuntime()
        self._checkpointer = MemorySaver()
        self._graph = self._build_graph()

    # ── Graph 构建 ─────────────────────────────────────────────────

    def _build_graph(self):
        """构建 Phase 2 ChatGraph。

        prepare_context → route_execution
                            ├─ normal_chat ──┐
                            └─ agentic_chat ─┘
                                        finalize_message → END
        """
        builder = StateGraph(ChatState)

        builder.add_node("prepare_context", self._prepare_context)
        builder.add_node("route_execution", self._route_execution)
        builder.add_node("normal_chat", self._normal_chat)
        builder.add_node("agentic_chat", self._agentic_chat)
        builder.add_node("finalize_message", self._finalize_message)

        builder.add_edge(START, "prepare_context")
        builder.add_edge("prepare_context", "route_execution")

        builder.add_conditional_edges(
            "route_execution",
            self._route_decision,
            {
                "normal_chat": "normal_chat",
                "agentic_chat": "agentic_chat",
            },
        )

        builder.add_edge("normal_chat", "finalize_message")
        builder.add_edge("agentic_chat", "finalize_message")
        builder.add_edge("finalize_message", END)

        return builder.compile(checkpointer=self._checkpointer)

    # ── 节点：prepare_context ─────────────────────────────────────

    def _prepare_context(self, state: ChatState) -> dict:
        """准备上下文节点。Phase 2 中保持轻量，后续可接入 memory compression。"""
        return {}

    # ── 节点：route_execution ─────────────────────────────────────

    def _route_execution(self, state: ChatState) -> dict:
        """根据 chat_mode 决定执行分支。

        chat_mode="agent" → agentic_chat，否则 → normal_chat。
        """
        chat_mode = state.get("chat_mode", "chat")
        execution_mode = "agentic_chat" if chat_mode == "agent" else "normal_chat"
        return {"execution_mode": execution_mode}

    @staticmethod
    def _route_decision(state: ChatState) -> str:
        """条件边决策函数。"""
        return state.get("execution_mode", "normal_chat")

    # ── 节点：normal_chat ─────────────────────────────────────────

    async def _normal_chat(self, state: ChatState) -> dict:
        """普通聊天节点（原 call_model）。

        复用 LLMClient.stream() 的标准化输出，与 Phase 1 行为一致。
        """
        writer = get_stream_writer()
        stream_id = state.get("stream_id", "")

        accumulated_content: list[str] = []
        accumulated_reasoning: list[str] = []
        token_usage: dict = {}
        response_metadata: dict = dict(state.get("response_metadata") or {})

        try:
            async for chunk in self._stream_factory():
                if stream_id and await conversation_stream_store.is_cancelled(stream_id):
                    return {
                        "response_text": "".join(accumulated_content),
                        "reasoning_text": "".join(accumulated_reasoning),
                        "token_usage": token_usage,
                        "response_metadata": response_metadata,
                        "error": "cancelled",
                    }

                if chunk.content_delta:
                    accumulated_content.append(chunk.content_delta)
                    writer({"type": "content_delta", "delta": chunk.content_delta})

                if chunk.reasoning_delta:
                    accumulated_reasoning.append(chunk.reasoning_delta)
                    writer({"type": "reasoning_delta", "delta": chunk.reasoning_delta})

                if chunk.token_usage:
                    token_usage = dict(chunk.token_usage)
                    writer({"type": "token_usage", "usage": token_usage})

                if chunk.response_metadata:
                    response_metadata.update(chunk.response_metadata)
                    writer({"type": "response_metadata", "metadata": chunk.response_metadata})

                if chunk.finish_reason:
                    response_metadata["finish_reason"] = chunk.finish_reason
                    writer({"type": "finish_reason", "finish_reason": chunk.finish_reason})
        except LLMClientError as exc:
            return {
                "response_text": "".join(accumulated_content),
                "reasoning_text": "".join(accumulated_reasoning),
                "token_usage": token_usage,
                "response_metadata": response_metadata,
                "error": str(exc),
            }
        except Exception as exc:
            return {
                "response_text": "".join(accumulated_content),
                "reasoning_text": "".join(accumulated_reasoning),
                "token_usage": token_usage,
                "response_metadata": response_metadata,
                "error": f"LangGraph chat runtime 执行失败：{exc}",
            }

        full_content = "".join(accumulated_content)
        if not full_content:
            return {
                "response_text": "",
                "reasoning_text": "".join(accumulated_reasoning),
                "token_usage": token_usage,
                "response_metadata": response_metadata,
                "error": "模型服务没有返回 assistant 内容",
            }

        return {
            "response_text": full_content,
            "reasoning_text": "".join(accumulated_reasoning),
            "token_usage": token_usage,
            "response_metadata": response_metadata,
            "error": None,
        }

    # ── 节点：agentic_chat ────────────────────────────────────────

    async def _agentic_chat(self, state: ChatState) -> dict:
        """Agent 执行节点。

        创建 DeepAgent 并执行 LLM 驱动的 agent loop，
        将 AgentResult 映射回 ChatState。
        """
        if self._llm_invoke is None:
            return {
                "error": "agentic_chat 不可用：缺少 llm_invoke",
            }

        writer = get_stream_writer()
        stream_id = state.get("stream_id", "")

        def on_event(event: dict[str, Any]) -> None:
            writer(dict(event))

        # 获取 agent 专用工具（websearch + webfetch）
        external_tools = self._build_agent_tools()

        # 创建隔离 workspace
        workspace = AgentWorkspace(run_id=state.get("assistant_message_id", "default"))

        # 创建 DeepAgent
        agent = DeepAgent(
            external_tools=external_tools,
            tool_runtime=self._tool_runtime,
            llm_invoke=self._llm_invoke,
            budget=AgentBudget(
                max_rounds=3,
                max_tool_calls=6,
                max_search_calls_per_round=2,
                timeout_seconds=120,
            ),
            on_event=on_event,
            workspace=workspace,
        )

        # 提取用户查询（最后一条 HumanMessage）
        user_query = ""
        for msg in reversed(state.get("input_messages", [])):
            if isinstance(msg, HumanMessage):
                user_query = str(msg.content)
                break

        if not user_query:
            return {"error": "无法从上下文中提取用户问题"}

        # 执行 agent
        result = await agent.run(
            user_query=user_query,
            history_messages=state.get("input_messages", []),
        )

        # 将 AgentResult 映射回 ChatState
        return_updates: dict[str, Any] = {
            "response_text": result.final_content,
            "reasoning_text": result.reasoning_text,
            "thought_events": result.thought_events,
            "workspace_files": result.workspace_files,
        }

        if result.token_usage:
            return_updates["token_usage"] = result.token_usage
        if result.error:
            return_updates["error"] = result.error

        return return_updates

    def _build_agent_tools(self) -> list[StructuredTool]:
        """组装 agentic_chat 的工具集。

        后续扩展工具时只需扩展此方法返回的列表。
        """
        all_tools = self._tool_runtime.get_langchain_tools()
        # 只保留 websearch 和 webfetch（排除 getweather 等不相关工具）
        return [t for t in all_tools if t.name in {"websearch", "webfetch"}]

    # ── 节点：finalize_message ────────────────────────────────────

    async def _finalize_message(self, state: ChatState) -> dict:
        """收口消息节点。Phase 2 中 thought_events 和 workspace_files 已由 agentic_chat 写入 state。"""
        return {}

    # ── 执行入口 ──────────────────────────────────────────────────

    async def run_stream(
        self,
        *,
        state: ChatState,
        stream_adapter: StreamAdapter,
    ) -> ChatState:
        """执行 graph 并通过 stream_adapter 处理自定义流事件。"""
        from langchain_core.runnables.config import RunnableConfig

        config: RunnableConfig = {"configurable": {"thread_id": state["thread_id"]}}

        async for event in self._graph.astream(
            state,
            config,
            stream_mode="custom",
        ):
            if isinstance(event, dict) and event.get("type"):
                await stream_adapter.emit_custom_event(event)

        snapshot = await self._graph.aget_state(config)
        if snapshot and snapshot.values:
            return cast(ChatState, snapshot.values)
        return state
