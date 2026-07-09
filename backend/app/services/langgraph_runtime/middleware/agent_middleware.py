"""Unified dependency view exposed to Orbit Agent adapters."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from app.services.langgraph_runtime.artifacts import ArtifactCollector
from app.services.langgraph_runtime.core.agent_contract import LlmInvoker
from app.services.langgraph_runtime.core.agent_types import AgentBudget
from app.services.langgraph_runtime.sandbox import SandboxManager
from app.services.tools import OrbitToolRuntime


@dataclass(frozen=True)
class AgentMiddleware:
    """Host-provided dependencies for agent plugins."""

    llm_invoke: LlmInvoker | None
    tool_runtime: OrbitToolRuntime
    sandbox_manager: SandboxManager | None = None
    artifact_collector_factory: Callable[[SandboxManager], ArtifactCollector] | None = None
    permissions: object | None = None
    budget: AgentBudget | None = None
