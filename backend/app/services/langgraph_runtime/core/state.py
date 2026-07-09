"""ChatState —— LangGraph Chat 执行状态定义。

只保留 graph 内部真正需要流转、checkpoint 与收口的字段。
宿主请求信息统一经由 runtime context 传递，不再回填到 state。
"""

from typing import Any, TypedDict


class ChatState(TypedDict, total=False):
    """LangGraph Chat 执行状态。

    各节点通过返回 dict 来更新 state，未返回的字段保持不变。
    """

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
