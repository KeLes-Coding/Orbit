"""Agent 专用类型定义。"""
from dataclasses import dataclass, field
from typing import Any, Callable


@dataclass(frozen=True)
class AgentToolDef:
    """Agent 工具描述：函数 + 名称 + 描述，用于注册到 deep agent。"""
    func: Callable
    name: str
    description: str


@dataclass
class AgentStreamEvent:
    """Agent 流式事件的内部表示，在 stream_adapter 中使用。"""
    event_type: str  # "llm_message" / "tool_call" / "tool_result" / "todo"
    content: str = ""
    tool_name: str | None = None
    tool_input: dict[str, Any] = field(default_factory=dict)
    tool_output: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)
