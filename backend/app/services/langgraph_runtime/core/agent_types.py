"""Agent Runtime 共享类型定义。"""

from dataclasses import dataclass, field
from typing import Any

from typing_extensions import TypedDict


@dataclass(frozen=True)
class AgentBudget:
    """Agent 执行的预算控制，硬限制防止失控。

    所有限制在第一版由后端固定配置，不做前端可配。
    """

    max_rounds: int = 5
    """最大 tool calling 轮数"""

    max_tool_calls: int = 25
    """最大工具调用总次数"""

    max_search_calls_per_round: int = 5
    """单轮最多允许的 websearch 次数"""

    timeout_seconds: float = 60.0
    """单次 run() 超时"""


@dataclass(frozen=True)
class AgentDescriptor:
    """Agent 插件对宿主和 UI 暴露的静态能力声明。"""

    agent_type: str
    display_name: str
    description: str
    capabilities: frozenset[str]
    default_budget: AgentBudget

    def to_public_dict(self) -> dict[str, Any]:
        """转换为 API 可直接序列化的结构。"""
        return {
            "agent_type": self.agent_type,
            "display_name": self.display_name,
            "description": self.description,
            "capabilities": sorted(self.capabilities),
            "default_budget": {
                "max_rounds": self.default_budget.max_rounds,
                "max_tool_calls": self.default_budget.max_tool_calls,
                "max_search_calls_per_round": self.default_budget.max_search_calls_per_round,
                "timeout_seconds": self.default_budget.timeout_seconds,
            },
        }


class AgentEvent(TypedDict, total=False):
    """单条 thought 事件，通过 on_event 回调发射。

    前端按 phase 分组渲染 thought block：
      planning -> loop(tool + summary) -> reason -> content
    """

    type: str
    """事件子类型：thought.planning | thought.tool | thought.summary | thought.reason"""

    phase: str
    """展示阶段：planning | loop | reason"""

    text: str
    """展示文本"""

    meta: dict[str, Any]
    """附加元数据（工具名、计数、域名列表等）"""

    step_id: str
    """timeline step 稳定标识，用于前端分组渲染"""

    step_kind: str
    """timeline step 类型，如 llm.codegen / sandbox.exec / tool.call"""

    status: str
    """running / completed / failed 等 step 状态"""

    input: Any
    """step 输入摘要"""

    output: Any
    """step 输出摘要"""

    error: str
    """step 错误摘要"""


@dataclass
class AgentExecutionResult:
    """统一的 Agent 执行结果。"""

    planning_text: str = ""
    """planning 阶段生成的文本"""

    loop_summaries: list[dict[str, Any]] = field(default_factory=list)
    """每轮 loop 摘要列表，结构：{step, tool, summary, meta}"""

    reasoning_text: str = ""
    """模型原生 reasoning/thinking 块"""

    final_content: str = ""
    """最终回答正文"""

    thought_events: list[dict[str, Any]] = field(default_factory=list)
    """聚合后的 thought 事件列表，供前端渲染 thought block"""

    workspace_files: list[dict[str, Any]] = field(default_factory=list)
    """workspace 中文件索引：{path, size}"""

    token_usage: dict[str, Any] = field(default_factory=dict)
    """归一化后的 token 用量（合并所有 LLM 调用）"""

    response_metadata: dict[str, Any] = field(default_factory=dict)
    """执行元信息，如执行后端、agent 类型等"""

    error: str | None = None
    """执行错误信息，非空时外层走失败收口"""

    @property
    def is_success(self) -> bool:
        return self.error is None and bool(self.final_content)


# 过渡兼容：旧代码仍可能引用 AgentResult。
AgentResult = AgentExecutionResult
