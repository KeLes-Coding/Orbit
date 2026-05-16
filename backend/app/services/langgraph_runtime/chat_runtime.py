"""LangGraph Chat Runtime —— 最小 ChatGraph 构建与执行。

Phase 1 只包含 3 个线性节点：
  prepare_context → call_model → finalize_message

这一版刻意复用现有 `LLMClient.stream()` 的标准化输出，而不是重新直连 provider。
这样 LangGraph 路径和旧聊天链路仍共享同一套 chunk 归一化逻辑，避免 Phase 1
就出现 provider 行为分叉。
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from typing import cast

from langchain_core.runnables.config import RunnableConfig
from langgraph.checkpoint.memory import MemorySaver
from langgraph.config import get_stream_writer
from langgraph.graph import END, START, StateGraph

from app.services.langgraph_runtime.state import ChatState
from app.services.langgraph_runtime.stream_adapter import StreamAdapter
from app.services.llm_client import LLMClientError, LLMStreamChunk
from app.services.streaming import conversation_stream_store


class LangGraphChatRuntime:
    """LangGraph Chat 运行时。

    负责：
    - 构建并编译最小 ChatGraph
    - 提供 run_stream() 作为统一的流式执行入口
    - 通过注入的 stream_factory 复用现有 LLMClient 流式归一化逻辑

    注意：
    - Phase 1 仍然只做 Chat，不做 tool loop / agent routing
    - 为了避免把密钥等敏感信息写入 checkpoint，模型调用参数不放入 ChatState，
      而是通过运行时注入的 stream_factory 闭包传入
    """

    def __init__(
        self,
        *,
        stream_factory: Callable[[], AsyncIterator[LLMStreamChunk]],
    ) -> None:
        self._stream_factory = stream_factory
        # Phase 1 先使用内存级 checkpointer，为后续 resume 预留 thread_id 语义。
        self._checkpointer = MemorySaver()
        self._graph = self._build_graph()

    def _build_graph(self):
        """构建最小 ChatGraph：prepare_context → call_model → finalize_message。"""
        builder = StateGraph(ChatState)

        builder.add_node("prepare_context", self._prepare_context)
        builder.add_node("call_model", self._call_model)
        builder.add_node("finalize_message", self._finalize_message)

        builder.add_edge(START, "prepare_context")
        builder.add_edge("prepare_context", "call_model")
        builder.add_edge("call_model", "finalize_message")
        builder.add_edge("finalize_message", END)

        return builder.compile(checkpointer=self._checkpointer)

    def _prepare_context(self, state: ChatState) -> dict:
        """准备上下文节点。

        Phase 1 中输入上下文已由外层服务构造完毕，这里保留节点边界，后续可在此接入
        memory compression、summary 注入或 execution routing。
        """
        return {}

    async def _call_model(self, state: ChatState) -> dict:
        """调用模型节点。

        这里不再重新解析 provider chunk，而是直接消费 `LLMClient.stream()` 已经标准化后的
        `LLMStreamChunk`。这样可以保证：

        - content / reasoning 拆分规则一致
        - token_usage / finish_reason 归一化规则一致
        - LangGraph chat 路径与 legacy chat 路径行为尽量一致
        """
        writer = get_stream_writer()
        stream_id = state.get("stream_id", "")

        accumulated_content: list[str] = []
        accumulated_reasoning: list[str] = []
        token_usage: dict = {}
        response_metadata: dict = dict(state.get("response_metadata") or {})

        try:
            async for chunk in self._stream_factory():
                # cancel 仍然沿用现有 stream_store 信号，不改变上层取消契约。
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

    async def _finalize_message(self, state: ChatState) -> dict:
        """收口消息节点。

        Phase 1 中数据库写入和 completed/failed 事件仍由外层 ConversationService 收口。
        这里保留 finalize 节点边界，后续可在此接入 thought 聚合、artifact 汇总等逻辑。
        """
        return {}

    async def run_stream(
        self,
        *,
        state: ChatState,
        stream_adapter: StreamAdapter,
    ) -> ChatState:
        """执行 graph 并通过 stream_adapter 处理自定义流事件。"""
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
