"""Skill 生成器契约测试。

验证生成器只产出 { descriptor, workflow }，生成的 skill 经同一个 Harness 执行，
即可获得生命周期事件、执行元信息收口、thought_events 边界等全部 runtime 行为。
"""

from __future__ import annotations

import asyncio
from dataclasses import replace
from typing import Any

import pytest
from langchain_core.messages import HumanMessage

from app.services.langgraph_runtime.core.agent_registry import AgentRegistry
from app.services.langgraph_runtime.core.agent_runner import AgentRunner
from app.services.langgraph_runtime.core.agent_services import AgentRuntimeServices
from app.services.langgraph_runtime.core.agent_types import AgentExecutionResult
from app.services.langgraph_runtime.core.runtime_context import (
    OrbitRuntimeContext,
    OrbitRuntimeRequest,
)
from app.services.langgraph_runtime.middleware import AgentMiddleware
from app.services.langgraph_runtime.skill_generator import (
    SkillContractError,
    SkillGenerator,
    SkillSpec,
)
from app.services.tools import OrbitToolRuntime


def run(coro):
    return asyncio.run(coro)


async def _echo_run(
    *,
    user_query: str,
    history_messages: list[Any],
    runtime_context: OrbitRuntimeContext,
    services: AgentRuntimeServices,
) -> AgentExecutionResult:
    services.events.emit_thought(
        event_type="thought.planning",
        phase="planning",
        text="echo plan",
    )
    services.events.emit_content_delta(user_query)
    return AgentExecutionResult(
        final_content=services.events.content_text,
        thought_events=services.events.events,
        token_usage=services.events.token_usage,
        response_metadata={},
    )


def _echo_spec() -> SkillSpec:
    return SkillSpec(
        agent_type="echo_skill",
        display_name="Echo Skill",
        description="Echoes the user query back.",
        capabilities=frozenset({"echo"}),
        run=_echo_run,
        skill_type="echo",
        execution_backend="async_pipeline",
    )


def _middleware() -> AgentMiddleware:
    return AgentMiddleware(llm_invoke=None, tool_runtime=OrbitToolRuntime())


def _runtime_context() -> OrbitRuntimeContext:
    return OrbitRuntimeContext(
        request=OrbitRuntimeRequest(
            conversation_id="conv-1",
            assistant_message_id="msg-1",
            stream_id="stream-1",
            thread_id="thread-1",
            chat_mode="agent",
            agent_type="echo_skill",
            input_messages=[HumanMessage(content="hi")],
            llm_config=None,
            model="test-model",
        ),
        tool_runtime=OrbitToolRuntime(),
        stream_writer=None,
    )


def test_generated_skill_runs_through_harness():
    adapter = SkillGenerator().generate(_echo_spec(), middleware=_middleware())

    registry = AgentRegistry()
    registry.register(adapter)
    runner = AgentRunner(registry=registry)

    events: list[dict[str, Any]] = []
    result = run(
        runner.run(
            agent_type="echo_skill",
            user_query="hi there",
            history_messages=[HumanMessage(content="hi there")],
            runtime_context=_runtime_context(),
            on_event=events.append,
        )
    )

    assert result.error is None
    assert result.final_content == "hi there"
    # 执行元信息由 Harness 收口，生成器不产出。
    assert result.response_metadata["skill_type"] == "echo"
    assert result.response_metadata["execution_backend"] == "async_pipeline"
    assert result.response_metadata["execution_kind"] == "workflow"
    # 生命周期事件由 Harness 发，进 SSE 不进 thought_events。
    lifecycle = {event.get("type") for event in events if event.get("phase") == "lifecycle"}
    assert {"agent.run.started", "agent.run.completed"} <= lifecycle
    assert all(event.get("phase") != "lifecycle" for event in result.thought_events)


def test_generated_skill_resolves_by_skill_type_alias():
    adapter = SkillGenerator().generate(_echo_spec(), middleware=_middleware())
    registry = AgentRegistry()
    registry.register(adapter)

    assert registry.has("echo")
    assert registry.resolve("echo") is adapter


def test_generator_rejects_empty_agent_type():
    generator = SkillGenerator()
    with pytest.raises(SkillContractError):
        generator.generate(replace(_echo_spec(), agent_type=""), middleware=_middleware())


def test_generator_rejects_invalid_execution_mode():
    generator = SkillGenerator()
    with pytest.raises(SkillContractError):
        generator.generate(replace(_echo_spec(), execution_mode="bogus"), middleware=_middleware())


def test_generator_rejects_non_coroutine_run():
    def not_async(**_kwargs):
        return None

    generator = SkillGenerator()
    with pytest.raises(SkillContractError):
        generator.generate(replace(_echo_spec(), run=not_async), middleware=_middleware())


def test_generator_rejects_run_with_wrong_signature():
    async def wrong_signature(*, user_query, services):  # 缺少 history_messages / runtime_context
        return AgentExecutionResult(final_content=user_query)

    generator = SkillGenerator()
    with pytest.raises(SkillContractError):
        generator.generate(replace(_echo_spec(), run=wrong_signature), middleware=_middleware())
