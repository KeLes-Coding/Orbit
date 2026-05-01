from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class ConversationCreate(BaseModel):
    # 新建会话请求；不传 llm_config_id 时服务层会尝试使用默认模型配置。
    title: str | None = Field(default=None, max_length=200)
    llm_config_id: UUID | None = None
    chat_mode: Literal["chat", "rag", "agent", "tool"] = "chat"
    metadata: dict = Field(default_factory=dict)


class ConversationUpdate(BaseModel):
    # 会话更新请求；PATCH 语义下只更新前端显式传入的字段。
    title: str | None = Field(default=None, max_length=200)
    llm_config_id: UUID | None = None
    metadata: dict | None = None


class ConversationRead(BaseModel):
    # 会话响应模型，metadata_ 序列化为 metadata，避开 SQLAlchemy 保留属性名。
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    user_id: UUID
    thread_id: str
    title: str | None
    llm_config_id: UUID | None
    chat_mode: str
    summary: str | None
    summary_updated_at: datetime | None
    summary_message_count: int
    metadata_: dict = Field(serialization_alias="metadata")
    created_at: datetime
    updated_at: datetime


class MessageCreate(BaseModel):
    # 当前阶段只接收纯文本用户消息，多模态内容后续放入 content_parts。
    content: str = Field(min_length=1)


class ConversationMessageCreate(MessageCreate):
    # 首条消息入口：发送第一条用户消息时由后端同步创建会话。
    # 这样前端点击 New Chat 时无需提前落库一个空会话。
    llm_config_id: UUID | None = None
    chat_mode: Literal["chat", "rag", "agent", "tool"] = "chat"
    metadata: dict = Field(default_factory=dict)


class MessageRead(BaseModel):
    # 消息响应模型包含状态和模型快照，方便前端展示与后续审计。
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    conversation_id: UUID
    sequence_no: int
    langgraph_message_id: str | None
    role: str
    content: str
    content_parts: list
    status: str
    llm_config_id: UUID | None
    provider: str | None
    model: str | None
    token_usage: dict
    response_metadata: dict
    created_at: datetime


class MessageExchangeRead(BaseModel):
    # 发送消息接口返回本轮写入的 user 消息和 assistant 生成结果。
    user_message: MessageRead
    assistant_message: MessageRead
