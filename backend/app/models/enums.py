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


class ExtractionStatus(str, Enum):
    pending = "pending"
    processing = "processing"
    success = "success"
    failed = "failed"
    skipped = "skipped"


class BindStatus(str, Enum):
    pending = "pending"
    bound = "bound"
    deleted = "deleted"


class RunStatus(str, Enum):
    """统一 run 执行状态，所有 runtime 共用。"""
    streaming = "streaming"
    completed = "completed"
    failed = "failed"
    cancelled = "cancelled"
    interrupted = "interrupted"  # HITL：等待用户审批后恢复


class RuntimeKind(str, Enum):
    """区分 run 由哪种运行时执行，用于监控和后续收敛评估。"""
    classic_chat = "classic_chat"
    langgraph_agent = "langgraph_agent"
