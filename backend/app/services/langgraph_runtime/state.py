"""ChatState —— LangGraph Chat 执行状态定义。

Phase 2 在 Phase 1 基础上扩展了 agent 路由和 thought 事件字段。
"""

from typing import Any, TypedDict


class ChatState(TypedDict):
    """LangGraph Chat 执行状态。

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

    # --- 执行路由 (Phase 2 新增) ---
    chat_mode: str
    """当前消息原始 mode：chat / agent"""

    execution_mode: str
    """graph 内部执行分支标识：normal_chat / agentic_chat"""

    # --- 输出（由 normal_chat / agentic_chat 节点填充）---
    response_text: str
    """模型返回的完整正文内容"""

    reasoning_text: str
    """模型返回的推理内容（如 Claude thinking / DeepSeek reasoning）"""

    token_usage: dict[str, Any]
    """归一化后的 token 用量"""

    response_metadata: dict[str, Any]
    """归一化后的响应元信息（provider, model, finish_reason 等）"""

    # --- Agent 中间结果 (Phase 2 新增) ---
    thought_events: list[dict[str, Any]]
    """聚合后的 thought 事件列表，供前端渲染 thought block"""

    workspace_files: list[dict[str, Any]]
    """workspace 中文件索引：{path, size}"""

    # --- 错误状态 ---
    error: str | None
    """执行过程中的错误信息，非空时 finalize_message 走失败收口"""
