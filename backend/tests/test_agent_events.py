"""AgentEventEmitter contract tests."""

from __future__ import annotations

from typing import Any

from app.services.langgraph_runtime.core.agent_events import AgentEventEmitter
from app.services.langgraph_runtime.core.agent_services import AgentRuntimeServices
from app.services.tools.runtime import OrbitToolRuntime


def test_agent_event_emitter_emits_standard_step_event():
    events: list[dict[str, Any]] = []
    emitter = AgentEventEmitter(on_event=events.append)

    emitter.emit_step(
        event_type="agent.step.started",
        step_id="sandbox.exec.1",
        step_kind="sandbox.exec",
        title="执行脚本",
        phase="execute",
        status="running",
        input={"command": "python work/analysis.py"},
    )

    assert events == [
        {
            "type": "agent.step.started",
            "phase": "execute",
            "text": "执行脚本",
            "meta": {},
            "step_id": "sandbox.exec.1",
            "step_kind": "sandbox.exec",
            "status": "running",
            "input": {"command": "python work/analysis.py"},
        }
    ]
    assert emitter.events == events


def test_agent_event_emitter_separates_deltas_from_thought_events():
    events: list[dict[str, Any]] = []
    emitter = AgentEventEmitter(on_event=events.append)

    emitter.emit_content_delta("答案")
    emitter.emit_reasoning_delta("思考")
    emitter.merge_token_usage({"output_tokens": 2})
    emitter.merge_token_usage({"output_tokens": 3, "model": "test"})

    assert events == [
        {"type": "content_delta", "delta": "答案"},
        {"type": "reasoning_delta", "delta": "思考"},
    ]
    assert emitter.events == []
    assert emitter.content_text == "答案"
    assert emitter.reasoning_text == "思考"
    assert emitter.token_usage == {"output_tokens": 5, "model": "test"}


def test_agent_event_emitter_compacts_adjacent_non_tool_events():
    emitter = AgentEventEmitter(on_event=lambda _event: None)

    emitter.emit_thought(event_type="thought.planning", phase="planning", text="先")
    emitter.emit_thought(event_type="thought.planning", phase="planning", text="规划")
    emitter.emit_thought(
        event_type="thought.tool",
        phase="loop",
        text="调用工具 A",
        step_id="tool.call.a",
    )
    emitter.emit_thought(
        event_type="thought.tool",
        phase="loop",
        text="调用工具 B",
        step_id="tool.call.b",
    )

    compacted = emitter.compact_events()

    assert compacted[0]["text"] == "先规划"
    assert [event["text"] for event in compacted[1:]] == ["调用工具 A", "调用工具 B"]


def test_agent_runtime_services_carries_standard_dependencies():
    emitter = AgentEventEmitter(on_event=lambda _event: None)
    tool_runtime = OrbitToolRuntime()

    services = AgentRuntimeServices(
        llm_invoke=None,
        tool_runtime=tool_runtime,
        events=emitter,
    )

    assert services.llm_invoke is None
    assert services.tool_runtime is tool_runtime
    assert services.events is emitter