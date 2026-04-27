from enum import Enum


class ChatMode(str, Enum):
    # MVP 先使用 chat，其他模式作为后续 RAG / Agent / Tool 的扩展点。
    chat = "chat"
    rag = "rag"
    agent = "agent"
    tool = "tool"


class MessageRole(str, Enum):
    system = "system"
    user = "user"
    assistant = "assistant"
    tool = "tool"


class MessageStatus(str, Enum):
    # 前端根据状态展示生成中、完成、失败、部分结果或用户取消。
    streaming = "streaming"
    completed = "completed"
    cancelled = "cancelled"
    failed = "failed"
    partial = "partial"
