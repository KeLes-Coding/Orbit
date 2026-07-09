"""Skill 生成器的输入规格。

SkillSpec 是生成一个 skill 需要的全部输入：元数据 + 一段 workflow 逻辑。
它刻意只承载 Descriptor 字段和一个 run 协程，不包含事件管道、SSE 映射、
持久化、权限、生命周期。这些对所有 skill 一致，由 Harness 统一承担。
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

from langchain_core.messages import BaseMessage

from app.services.langgraph_runtime.core.agent_services import AgentRuntimeServices
from app.services.langgraph_runtime.core.agent_types import (
    AgentBudget,
    AgentExecutionResult,
    ArtifactPolicy,
)
from app.services.langgraph_runtime.core.runtime_context import OrbitRuntimeContext


SkillWorkflowRun = Callable[..., Awaitable[AgentExecutionResult]]
"""workflow 逻辑签名：run(*, user_query, history_messages, runtime_context, services)。"""


@dataclass(frozen=True)
class SkillSpec:
    """生成一个 skill 的输入规格。"""

    agent_type: str
    display_name: str
    description: str
    capabilities: frozenset[str]
    run: SkillWorkflowRun

    budget: AgentBudget = field(default_factory=AgentBudget)
    skill_type: str = ""
    execution_mode: str = "workflow"
    execution_backend: str = "async_pipeline"
    required_tools: frozenset[str] = frozenset()
    allowed_tools: frozenset[str] = frozenset()
    disallowed_tools: frozenset[str] = frozenset()
    artifact_policy: ArtifactPolicy = field(default_factory=ArtifactPolicy)
    permission_scope: frozenset[str] = frozenset()


async def _run_signature_reference(
    *,
    user_query: str,
    history_messages: list[BaseMessage],
    runtime_context: OrbitRuntimeContext,
    services: AgentRuntimeServices,
) -> AgentExecutionResult:
    """run 协程的参考签名，供契约校验对照。"""
    raise NotImplementedError


REQUIRED_RUN_PARAMS: frozenset[str] = frozenset(
    {"user_query", "history_messages", "runtime_context", "services"}
)
