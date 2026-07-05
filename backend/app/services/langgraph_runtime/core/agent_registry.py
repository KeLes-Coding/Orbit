"""Orbit Agent registry。"""

from __future__ import annotations

from app.services.langgraph_runtime.core.agent_contract import BaseOrbitAgentAdapter
from app.services.langgraph_runtime.core.agent_types import AgentDescriptor


class AgentRegistry:
    """按 agent_type 注册和解析 adapter，并支持 skill_type 别名。"""

    def __init__(self) -> None:
        self._agents: dict[str, BaseOrbitAgentAdapter] = {}
        self._aliases: dict[str, str] = {}

    def register(self, agent: BaseOrbitAgentAdapter) -> None:
        # registry 的目标是让 chat runtime 只依赖稳定协议，
        # 而不是知道具体是 web agent、file agent 还是其他实现。
        agent_type = agent.descriptor.agent_type
        self._agents[agent_type] = agent
        skill_type = getattr(agent.descriptor, "skill_type", "")
        if skill_type and skill_type != agent_type:
            self._aliases[skill_type] = agent_type

    def resolve(self, agent_type: str) -> BaseOrbitAgentAdapter:
        key = self._aliases.get(agent_type, agent_type)
        try:
            return self._agents[key]
        except KeyError as exc:
            raise LookupError(f"未注册的 agent 类型：{agent_type}") from exc

    def has(self, agent_type: str) -> bool:
        return agent_type in self._agents or agent_type in self._aliases

    def descriptors(self) -> list[AgentDescriptor]:
        return [agent.descriptor for agent in self._agents.values()]
