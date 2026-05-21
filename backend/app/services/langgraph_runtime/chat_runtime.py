"""LangGraph Chat Runtime —— ChatGraph 构建与执行。"""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from typing import Any, cast

from langchain_core.messages import HumanMessage
from langgraph.checkpoint.memory import MemorySaver
from langgraph.config import get_stream_writer
from langgraph.graph import END, START, StateGraph

from app.services.langgraph_runtime.agent_contract import LlmInvoker
from app.services.langgraph_runtime.agent_registry import AgentRegistry
from app.services.langgraph_runtime.agent_runner import AgentRunner
from app.services.langgraph_runtime.runtime_context import OrbitRuntimeContext, OrbitRuntimeRequest
from app.services.langgraph_runtime.state import ChatState
from app.services.langgraph_runtime.stream_adapter import StreamAdapter
from app.services.langgraph_runtime.web_agent.adapter import WebAgentAdapter
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
        runtime_context: OrbitRuntimeContext | None = None,
        agent_registry: AgentRegistry | None = None,
    ) -> None:
        self._stream_factory = stream_factory
        self._llm_invoke = llm_invoke
        self._tool_runtime = tool_runtime or OrbitToolRuntime()
        self._runtime_context = runtime_context
        self._agent_registry = agent_registry or AgentRegistry()
        if llm_invoke is not None and not self._agent_registry.has("web_agent"):
            self._agent_registry.register(
                WebAgentAdapter(
                    llm_invoke=llm_invoke,
                    tool_runtime=self._tool_runtime,
                )
            )
        self._agent_runner = AgentRunner(registry=self._agent_registry)
        self._checkpointer = MemorySaver()
        self._graph = self._build_graph()

    def _state_runtime_request(self, state: ChatState) -> OrbitRuntimeRequest:
        # 这是旧 state -> 新 runtime request 的兜底桥。
        # 长期目标是尽量由 stream_run 直接提供完整 runtime_context，
        # 这里仅为了兼容当前测试和渐进式迁移保留。
        return OrbitRuntimeRequest(
            conversation_id=state.get("conversation_id", ""),
            assistant_message_id=state.get("assistant_message_id", ""),
            stream_id=state.get("stream_id", ""),
            thread_id=state.get("thread_id", "orbit-thread"),
            chat_mode=state.get("chat_mode", "chat"),
            agent_type="web_agent" if state.get("chat_mode") == "agent" else None,
            input_messages=state.get("input_messages", []),
            llm_config=None,
            model=state.get("model"),
        )

    def _resolve_runtime_context(
        self,
        *,
        state: ChatState,
        stream_writer: Callable[[dict], None] | None = None,
    ) -> OrbitRuntimeContext:
        if self._runtime_context is not None:
            # 若上层已显式传入 runtime_context，则以其为准；
            # 但 stream_writer 是 graph 执行期才知道的，所以需要在这里补进去。
            if stream_writer is None:
                return self._runtime_context
            return OrbitRuntimeContext(
                request=self._runtime_context.request,
                tool_runtime=self._runtime_context.tool_runtime,
                stream_writer=stream_writer,
            )
        return OrbitRuntimeContext(
            request=self._state_runtime_request(state),
            tool_runtime=self._tool_runtime,
            stream_writer=stream_writer,
        )

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
        try:
            writer = get_stream_writer()
        except RuntimeError:
            # 单测或非 LangGraph runnable 上下文下，退回到 no-op writer。
            writer = lambda _event: None
        runtime_context = self._resolve_runtime_context(state=state, stream_writer=writer)
        stream_id = runtime_context.request.stream_id

        accumulated_content: list[str] = []
        accumulated_reasoning: list[str] = []
        token_usage: dict = {}
        response_metadata: dict = dict(state.get("response_metadata") or {})

        try:
            async for chunk in self._stream_factory():
                # cancel 判定仍由 Orbit 自己的 stream store 掌控，
                # 不让 LangGraph/agent 执行器直接决定消息生命周期。
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

        try:
            writer = get_stream_writer()
        except RuntimeError:
            writer = lambda _event: None
        runtime_context = self._resolve_runtime_context(state=state, stream_writer=writer)

        def on_event(event: dict[str, Any]) -> None:
            writer(dict(event))

        # 提取用户查询（最后一条 HumanMessage）
        user_query = ""
        for msg in reversed(state.get("input_messages", [])):
            if isinstance(msg, HumanMessage):
                user_query = str(msg.content)
                break

        if not user_query:
            return {"error": "无法从上下文中提取用户问题"}

        agent_type = (
            getattr(getattr(runtime_context, "request", None), "agent_type", None)
            or "web_agent"
        )

        # agentic_chat 不直接依赖具体实现，统一走 registry/runner 分发。
        result = await self._agent_runner.run(
            agent_type=agent_type,
            user_query=user_query,
            history_messages=state.get("input_messages", []),
            runtime_context=runtime_context,
            on_event=on_event,
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

        runtime_context = self._resolve_runtime_context(state=state)
        config: RunnableConfig = {
            "configurable": {
                "thread_id": runtime_context.request.thread_id or state.get("thread_id", "orbit-thread")
            }
        }

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
