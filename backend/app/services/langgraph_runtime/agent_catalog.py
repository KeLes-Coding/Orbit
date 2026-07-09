"""Built-in Orbit Agent catalog."""

from __future__ import annotations

from collections.abc import Iterable

from app.services.langgraph_runtime.agents.data_workspace_agent.adapter import (
    DATA_WORKSPACE_AGENT_DESCRIPTOR,
    DataWorkspaceAgentAdapter,
)
from app.services.langgraph_runtime.agents.web_agent.adapter import WEB_AGENT_DESCRIPTOR, WebAgentAdapter
from app.services.langgraph_runtime.core.agent_contract import BaseOrbitAgentAdapter, LlmInvoker
from app.services.langgraph_runtime.core.agent_registry import AgentRegistry
from app.services.langgraph_runtime.middleware import AgentMiddleware
from app.services.tools import OrbitToolRuntime


class AgentCatalog:
    """Creates and describes built-in Orbit agents."""

    def __init__(self, agents: Iterable[BaseOrbitAgentAdapter]) -> None:
        self._agents = list(agents)

    @classmethod
    def builtins(
        cls,
        *,
        llm_invoke: LlmInvoker | None = None,
        tool_runtime: OrbitToolRuntime | None = None,
        middleware: AgentMiddleware | None = None,
    ) -> "AgentCatalog":
        agents: list[BaseOrbitAgentAdapter] = []
        base_middleware = middleware or AgentMiddleware(
            llm_invoke=llm_invoke,
            tool_runtime=tool_runtime or OrbitToolRuntime(),
        )
        if base_middleware.llm_invoke is not None:
            agents.append(WebAgentAdapter(middleware=base_middleware))
        agents.append(DataWorkspaceAgentAdapter(middleware=base_middleware))
        return cls(agents)

    def register_into(self, registry: AgentRegistry) -> AgentRegistry:
        for agent in self._agents:
            if not registry.has(agent.agent_type):
                registry.register(agent)
        return registry

    def descriptors(self) -> list[dict]:
        return [agent.descriptor.to_public_dict() for agent in self._agents]

    @staticmethod
    def resolve_execution_chat_mode(
        *,
        chat_mode: str,
        file_refs: list[dict],
    ) -> str:
        if chat_mode == "chat" and AgentCatalog.has_data_workspace_files(file_refs):
            return "agent"
        return chat_mode

    @staticmethod
    def resolve_agent_type(
        *,
        chat_mode: str,
        requested_agent_type: str | None,
        file_refs: list[dict],
    ) -> str | None:
        if chat_mode != "agent":
            return None
        normalized = AgentCatalog.normalize_to_agent_type(requested_agent_type)
        if normalized is not None:
            return normalized
        if AgentCatalog.has_data_workspace_files(file_refs):
            return DATA_WORKSPACE_AGENT_DESCRIPTOR.agent_type
        return WEB_AGENT_DESCRIPTOR.agent_type

    @staticmethod
    def normalize_to_agent_type(identifier: str | None) -> str | None:
        """把 skill_type 或 agent_type 归一到 agent_type，未知标识返回 None。"""
        if not identifier:
            return None
        known_agent_types = {
            WEB_AGENT_DESCRIPTOR.agent_type,
            DATA_WORKSPACE_AGENT_DESCRIPTOR.agent_type,
        }
        if identifier in known_agent_types:
            return identifier
        skill_type_to_agent_type = {
            WEB_AGENT_DESCRIPTOR.skill_type: WEB_AGENT_DESCRIPTOR.agent_type,
            DATA_WORKSPACE_AGENT_DESCRIPTOR.skill_type: DATA_WORKSPACE_AGENT_DESCRIPTOR.agent_type,
        }
        return skill_type_to_agent_type.get(identifier)

    @staticmethod
    def has_data_workspace_files(file_refs: list[dict]) -> bool:
        data_extensions = {".csv", ".tsv", ".json", ".xlsx"}
        data_mime_fragments = {
            "csv",
            "json",
            "spreadsheet",
            "excel",
            "tab-separated-values",
        }
        for ref in file_refs:
            name = str(ref.get("name") or "").lower()
            mime_type = str(ref.get("mime_type") or "").lower()
            if any(name.endswith(ext) for ext in data_extensions):
                return True
            if any(fragment in mime_type for fragment in data_mime_fragments):
                return True
        return False


def list_builtin_agent_descriptors() -> list[dict]:
    """Return statically known descriptors for API consumers."""
    return [
        WEB_AGENT_DESCRIPTOR.to_public_dict(),
        DATA_WORKSPACE_AGENT_DESCRIPTOR.to_public_dict(),
    ]
