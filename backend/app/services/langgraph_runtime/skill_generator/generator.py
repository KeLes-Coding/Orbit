"""Skill 生成器：从 SkillSpec 产出 { SkillDescriptor, SkillWorkflow }。

生成器是协议是否统一的验收标准。四个契约冻结、Harness 收口后，生成一个 skill
需要产出的对象收敛为两样：Descriptor 与 Workflow。生成器不产出事件管道、SSE 映射、
持久化、artifact 落库、权限、生命周期，这些对所有 skill 一致，由 Harness 承担。

生成结果是一个 workflow-backed adapter：带 descriptor、build_workflow()、
build_services()，可直接注册进 AgentRegistry，经同一个 Harness 执行。
"""

from __future__ import annotations

import inspect
from collections.abc import Callable

from langchain_core.messages import BaseMessage

from app.services.langgraph_runtime.core.agent_contract import is_workflow_backed
from app.services.langgraph_runtime.core.agent_events import AgentEventEmitter
from app.services.langgraph_runtime.core.agent_services import AgentRuntimeServices
from app.services.langgraph_runtime.core.agent_types import AgentBudget, SkillDescriptor
from app.services.langgraph_runtime.core.runtime_context import OrbitRuntimeContext
from app.services.langgraph_runtime.middleware import AgentMiddleware
from app.services.langgraph_runtime.skill_generator.spec import (
    REQUIRED_RUN_PARAMS,
    SkillSpec,
    SkillWorkflowRun,
)

_VALID_EXECUTION_MODES = frozenset({"inline", "workflow", "forked"})


class SkillContractError(ValueError):
    """生成的 skill 不满足契约时抛出。"""


class GeneratedSkillWorkflow:
    """把 SkillSpec.run 包装成满足 AgentWorkflow 协议的 workflow。"""

    def __init__(self, *, run: SkillWorkflowRun, execution_backend: str) -> None:
        self._run = run
        self.execution_backend = execution_backend

    async def run(
        self,
        *,
        user_query: str,
        history_messages: list[BaseMessage],
        runtime_context: OrbitRuntimeContext,
        services: AgentRuntimeServices,
    ):
        return await self._run(
            user_query=user_query,
            history_messages=history_messages,
            runtime_context=runtime_context,
            services=services,
        )


class GeneratedSkillAdapter:
    """生成的 skill 的 adapter，形状与内置 skill 一致。"""

    def __init__(
        self,
        *,
        descriptor: SkillDescriptor,
        run: SkillWorkflowRun,
        middleware: AgentMiddleware,
        budget: AgentBudget,
        execution_backend: str,
    ) -> None:
        self.descriptor = descriptor
        self._run = run
        self._middleware = middleware
        self._budget = budget
        self._execution_backend = execution_backend

    @property
    def agent_type(self) -> str:
        return self.descriptor.agent_type

    @property
    def skill_type(self) -> str:
        return self.descriptor.skill_type

    def build_workflow(self) -> GeneratedSkillWorkflow:
        return GeneratedSkillWorkflow(
            run=self._run,
            execution_backend=self._execution_backend,
        )

    def build_services(self, events: AgentEventEmitter) -> AgentRuntimeServices:
        return AgentRuntimeServices(
            llm_invoke=self._middleware.llm_invoke,
            tool_runtime=self._middleware.tool_runtime,
            events=events,
            sandbox_manager=self._middleware.sandbox_manager,
            budget=self._budget,
            permissions=self._middleware.permissions,
        )


class SkillGenerator:
    """从 SkillSpec 生成可注册的 skill adapter，并做契约校验。"""

    def generate(self, spec: SkillSpec, *, middleware: AgentMiddleware) -> GeneratedSkillAdapter:
        self._validate_spec(spec)
        descriptor = self._build_descriptor(spec)
        adapter = GeneratedSkillAdapter(
            descriptor=descriptor,
            run=spec.run,
            middleware=middleware,
            budget=spec.budget,
            execution_backend=spec.execution_backend,
        )
        self._validate_adapter(adapter)
        return adapter

    @staticmethod
    def _build_descriptor(spec: SkillSpec) -> SkillDescriptor:
        return SkillDescriptor(
            agent_type=spec.agent_type,
            display_name=spec.display_name,
            description=spec.description,
            capabilities=spec.capabilities,
            default_budget=spec.budget,
            skill_type=spec.skill_type,
            execution_mode=spec.execution_mode,
            required_tools=spec.required_tools,
            allowed_tools=spec.allowed_tools,
            disallowed_tools=spec.disallowed_tools,
            artifact_policy=spec.artifact_policy,
            permission_scope=spec.permission_scope,
        )

    @staticmethod
    def _validate_spec(spec: SkillSpec) -> None:
        for field_name in ("agent_type", "display_name", "description"):
            if not getattr(spec, field_name):
                raise SkillContractError(f"SkillSpec.{field_name} 不能为空")
        if spec.execution_mode not in _VALID_EXECUTION_MODES:
            raise SkillContractError(
                f"execution_mode 非法：{spec.execution_mode}，允许 {sorted(_VALID_EXECUTION_MODES)}"
            )
        _validate_run_callable(spec.run)

    @staticmethod
    def _validate_adapter(adapter: GeneratedSkillAdapter) -> None:
        # 生成结果必须是 workflow-backed（带 build_workflow + build_services），
        # 才能经同一个 Harness 执行，不需要生成任何 runtime 胶水代码。
        if not is_workflow_backed(adapter):
            raise SkillContractError("生成的 skill 必须是 workflow-backed adapter")
        workflow = adapter.build_workflow()
        _validate_run_callable(workflow.run)


def _validate_run_callable(run: Callable) -> None:
    if not inspect.iscoroutinefunction(run):
        raise SkillContractError("workflow run 必须是 async 协程函数")
    params = set(inspect.signature(run).parameters)
    missing = REQUIRED_RUN_PARAMS - params
    if missing:
        raise SkillContractError(f"workflow run 缺少参数：{sorted(missing)}")
