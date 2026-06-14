"""Shared service bundle for Orbit Agent workflows."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.services.langgraph_runtime.artifacts import ArtifactCollector
from app.services.langgraph_runtime.core.agent_contract import LlmInvoker
from app.services.langgraph_runtime.core.agent_events import AgentEventEmitter
from app.services.langgraph_runtime.core.agent_types import AgentBudget
from app.services.langgraph_runtime.sandbox import SandboxManager
from app.services.tools import OrbitToolRuntime


@dataclass(frozen=True)
class AgentRuntimeServices:
    """Host-provided services available to standard Agent workflows."""

    llm_invoke: LlmInvoker | None
    tool_runtime: OrbitToolRuntime
    events: AgentEventEmitter
    sandbox_manager: SandboxManager | None = None
    artifact_collector: ArtifactCollector | None = None
    permissions: object | None = None
    budget: AgentBudget | None = None
    extras: dict[str, Any] | None = None