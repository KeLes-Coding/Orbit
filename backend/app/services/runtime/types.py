"""运行时共享类型定义。

所有 runtime 都使用同一套 RunContext 入参和 UnifiedStreamEvent 产出，
保证 chat/agent 在上层看来是同一种执行模型。
"""
from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.conversation import Conversation
from app.models.llm_config import LLMConfig
from app.models.message import Message


@dataclass(frozen=True)
class RunContext:
    """所有 runtime 的入参都走同一个上下文对象，方便后续扩展字段。"""
    session: AsyncSession
    conversation: Conversation
    assistant_message: Message
    llm_config: LLMConfig
    history_messages: list[Message]
    stream_id: str
    # cancel_event 是 asyncio.Event，供 runtime 在执行循环内协作式检查是否被取消。
    cancel_event: Any  # asyncio.Event


@dataclass(frozen=True)
class UnifiedStreamEvent:
    """runtime 产出的统一事件，对应 SSE 事件流中的一条。

    通用事件（所有 runtime 共用）：
      run.started / message.created / message.delta / message.reasoning_delta
      message.completed / message.failed / message.cancelled / run.completed / run.failed

    agent 扩展事件（仅 agent runtime 产出）：
      message.agent_delta — 承载 tool_call / tool_result / todo 等子类型。
    """
    event: str
    data: dict[str, Any]


@dataclass(frozen=True)
class RuntimeResult:
    """runtime 执行完毕后的汇总结果，由调用方落成最终消息状态。"""
    content: str = ""
    reasoning_content: str = ""
    token_usage: dict[str, Any] = field(default_factory=dict)
    response_metadata: dict[str, Any] = field(default_factory=dict)
    finish_reason: str | None = None
