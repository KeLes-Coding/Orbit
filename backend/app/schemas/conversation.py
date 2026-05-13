from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator


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
    has_active_run: bool
    next_message_sequence_no: int
    active_leaf_message_id: UUID | None
    forked_from_conversation_id: UUID | None
    forked_from_message_id: UUID | None
    summary_leaf_message_id: UUID | None
    metadata_: dict = Field(serialization_alias="metadata")
    created_at: datetime
    updated_at: datetime


class MessageCreate(BaseModel):
    # 多模态内容放入 content_parts；content 和 file_ids 至少需要一个。
    content: str = Field(default="", min_length=0)
    llm_config_id: UUID | None = None
    file_ids: list[UUID] = Field(default_factory=list)
    parent_message_id: UUID | None = None
    idempotency_key: str | None = Field(default=None, min_length=1, max_length=120)
    model: str | None = Field(default=None, min_length=1, max_length=120)

    @model_validator(mode="after")
    def check_content_or_files(self) -> "MessageCreate":
        if not self.content.strip() and len(self.file_ids) == 0:
            raise ValueError("content 和 file_ids 至少需要一个")
        return self


class MessageEdit(BaseModel):
    # 编辑历史 user 消息时创建新的 sibling user message。
    content: str = Field(default="", min_length=0)
    llm_config_id: UUID | None = None
    file_ids: list[UUID] = Field(default_factory=list)
    idempotency_key: str | None = Field(default=None, min_length=1, max_length=120)
    model: str | None = Field(default=None, min_length=1, max_length=120)

    @model_validator(mode="after")
    def check_content_or_files(self) -> "MessageEdit":
        if not self.content.strip() and len(self.file_ids) == 0:
            raise ValueError("content 和 file_ids 至少需要一个")
        return self


class MessageRegenerate(BaseModel):
    # 重发 assistant 时允许前端传幂等键，避免重复点击生成多个 sibling。
    llm_config_id: UUID | None = None
    idempotency_key: str | None = Field(default=None, min_length=1, max_length=120)
    model: str | None = Field(default=None, min_length=1, max_length=120)


class ConversationForkCreate(BaseModel):
    # 从当前 visible path 的某个历史节点复制出新会话。
    title: str | None = Field(default=None, max_length=200)


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
    parent_message_id: UUID | None
    active_child_message_id: UUID | None
    depth: int
    source_message_id: UUID | None
    revision_type: str | None
    role: str
    content: str
    reasoning_content: str
    content_parts: list
    status: str
    llm_config_id: UUID | None
    provider: str | None
    model: str | None
    token_usage: dict
    response_metadata: dict
    created_at: datetime
    sibling_index: int = 1
    sibling_count: int = 1
    previous_sibling_id: UUID | None = None
    next_sibling_id: UUID | None = None


class MessageExchangeRead(BaseModel):
    # 发送消息接口返回本轮写入的 user 消息和 assistant 生成结果。
    user_message: MessageRead
    assistant_message: MessageRead


class ActiveStreamRead(BaseModel):
    # branch 级恢复入口：先查当前 message 所关联的活跃流，再按 stream_id 订阅。
    conversation_id: UUID
    message_id: UUID
    assistant_message_id: UUID
    stream_id: str


class BranchSwitchRead(BaseModel):
    # 切换 branch 后返回新的 visible path，前端可直接替换消息数组。
    active_leaf_message_id: UUID | None
    messages: list[MessageRead]


class ConversationForkRead(BaseModel):
    # Fork 后返回新会话和复制后的 visible path。
    conversation: ConversationRead
    messages: list[MessageRead]


class ConversationRunRead(BaseModel):
    """统一 run 记录的响应模型，所有 chat_mode 共用。"""
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    conversation_id: UUID
    assistant_message_id: UUID | None
    thread_id: str
    runtime_kind: str
    chat_mode: str
    status: str
    started_at: datetime
    finished_at: datetime | None
    last_error: str | None
    metadata_: dict = Field(serialization_alias="metadata")
    created_at: datetime
    updated_at: datetime
