"""Phase 1 Chat Graph 最小 State 定义。

只包含普通 Chat 执行所需的字段，不引入 tool state、agent scratchpad 等 Phase 2 内容。
"""

from typing import Any, TypedDict


class ChatState(TypedDict):
    """LangGraph Chat 执行的最小状态。

    各节点通过返回 dict 来更新 state，未返回的字段保持不变。
    """

    # --- 会话标识 ---
    conversation_id: str
    """会话 ID，用于数据库查询和事件关联"""

    assistant_message_id: str
    """当前 assistant 占位消息的 ID，用于流事件关联和后续写入"""

    stream_id: str
    """运行时 stream ID，用于 cancel 检测和流事件关联"""

    thread_id: str
    """LangGraph checkpoint 的 thread namespace，对应 Conversation.thread_id"""

    # --- 模型配置 ---
    llm_config_id: str
    """LLM 配置 ID，用于关联配置快照"""

    provider: str
    """Provider 标识，如 anthropic/openai/google/ollama"""

    model: str
    """实际使用的模型名称"""

    # --- 输入 ---
    input_messages: list
    """构造好的 LangChain BaseMessage 列表，便于 checkpoint 观察输入上下文"""

    # --- 输出（由 call_model 节点填充）---
    response_text: str
    """模型返回的完整正文内容"""

    reasoning_text: str
    """模型返回的推理内容（如 Claude thinking / DeepSeek reasoning）"""

    token_usage: dict[str, Any]
    """归一化后的 token 用量"""

    response_metadata: dict[str, Any]
    """归一化后的响应元信息（provider, model, finish_reason 等）"""

    # --- 错误状态 ---
    error: str | None
    """执行过程中的错误信息，非空时 finalize_message 走失败收口"""
