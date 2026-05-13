"""LangGraph Agent 运行时实现。

基于 deepagents + LangGraph 的 agent 运行路径：
  1. 将数据库历史消息转为 LangChain 消息格式
  2. 通过工厂构建 deep agent
  3. 通过 astream 执行并适配为统一事件流
"""
from collections.abc import AsyncIterator

from app.services.agents.factory import build_agent
from app.services.agents.stream_adapter import adapt_langgraph_stream
from app.services.llm_client import LLMClient
from app.services.runtime.base import BaseRuntime
from app.services.runtime.types import RunContext, UnifiedStreamEvent


class LangGraphAgentRuntime(BaseRuntime):
    """基于 LangGraph/DeepAgents 的 agent 运行时。

    与 ClassicChatRuntime 的区别：
      - 使用 deep agent 替代单次 LLM 调用
      - 支持工具调用（tool_call / tool_result 事件）
      - 使用 LangGraph checkpoint 管理执行状态
      - 复用 LLMClient 的消息构建逻辑
    """

    async def execute(self, ctx: RunContext) -> AsyncIterator[UnifiedStreamEvent]:
        """使用 deep agent 执行生成，输出统一事件流。"""
        # 1. 复用 LLMClient 的消息构建逻辑，将 DB 消息转为 LangChain 格式
        llm_client = LLMClient()
        langchain_messages = llm_client._build_langchain_messages(
            messages=ctx.history_messages,
            summary=ctx.conversation.summary,
        )

        # 2. 构建 agent
        try:
            agent = build_agent(
                ctx.llm_config,
                model=ctx.assistant_message.model,
            )
        except Exception as exc:
            yield UnifiedStreamEvent(
                event="message.failed",
                data={
                    "message_id": str(ctx.assistant_message.id),
                    "error": f"Agent 初始化失败：{exc}",
                },
            )
            return

        # 3. 执行 agent，适配事件流
        try:
            async for event in adapt_langgraph_stream(
                agent=agent,
                messages=langchain_messages,
                thread_id=ctx.conversation.thread_id,
                assistant_message_id=ctx.assistant_message.id,
            ):
                # 协作式取消检查
                if ctx.cancel_event.is_set():
                    yield UnifiedStreamEvent(
                        event="message.cancelled",
                        data={"message_id": str(ctx.assistant_message.id)},
                    )
                    return
                yield event
        except Exception as exc:
            yield UnifiedStreamEvent(
                event="message.failed",
                data={
                    "message_id": str(ctx.assistant_message.id),
                    "error": str(exc),
                },
            )
