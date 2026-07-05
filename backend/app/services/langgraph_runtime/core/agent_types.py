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
class ArtifactPolicy:
    """Skill 的产物策略：是否产出 artifact、类型白名单、是否生成预览。"""

    produces_artifacts: bool = False
    allowed_types: frozenset[str] = frozenset()
    preview: bool = True

    def to_public_dict(self) -> dict[str, Any]:
        return {
            "produces_artifacts": self.produces_artifacts,
            "allowed_types": sorted(self.allowed_types),
            "preview": self.preview,
        }


@dataclass(frozen=True)
class SkillDescriptor:
    """Skill 对宿主和 UI 暴露的静态能力声明。

    SkillDescriptor 是旧 AgentDescriptor 的超集：旧字段全部保留，新增执行形态、
    工具策略、产物策略、权限范围。第一版这些新字段大多是元数据，Harness 逐步接管其强制。
    """

    agent_type: str
    display_name: str
    description: str
    capabilities: frozenset[str]
    default_budget: AgentBudget

    skill_type: str = ""
    """稳定的 skill 标识；留空时回落为 agent_type，旧 API 继续兼容。"""

    execution_mode: str = "workflow"
    """执行形态：inline | workflow | forked。"""

    required_tools: frozenset[str] = frozenset()
    """缺失即无法运行的工具。"""

    allowed_tools: frozenset[str] = frozenset()
    """激活时预批准的工具。"""

    disallowed_tools: frozenset[str] = frozenset()
    """激活时需要移除的工具。"""

    artifact_policy: ArtifactPolicy = field(default_factory=ArtifactPolicy)
    """产物策略。"""

    permission_scope: frozenset[str] = frozenset()
    """粗粒度权限范围：read | write | execute | network。"""

    def __post_init__(self) -> None:
        if not self.skill_type:
            object.__setattr__(self, "skill_type", self.agent_type)

    def to_public_dict(self) -> dict[str, Any]:
        """转换为 API 可直接序列化的结构。旧字段保留，新字段增量添加。"""
        return {
            "agent_type": self.agent_type,
            "skill_type": self.skill_type,
            "display_name": self.display_name,
            "description": self.description,
            "capabilities": sorted(self.capabilities),
            "execution_mode": self.execution_mode,
            "default_budget": {
                "max_rounds": self.default_budget.max_rounds,
                "max_tool_calls": self.default_budget.max_tool_calls,
                "max_search_calls_per_round": self.default_budget.max_search_calls_per_round,
                "timeout_seconds": self.default_budget.timeout_seconds,
            },
            "required_tools": sorted(self.required_tools),
            "allowed_tools": sorted(self.allowed_tools),
            "disallowed_tools": sorted(self.disallowed_tools),
            "artifact_policy": self.artifact_policy.to_public_dict(),
            "permission_scope": sorted(self.permission_scope),
        }


# 过渡兼容：旧代码仍以 AgentDescriptor 引用同一类型。
AgentDescriptor = SkillDescriptor


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
