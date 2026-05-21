"""Orbit Agent registry。"""

from __future__ import annotations

from app.services.langgraph_runtime.agent_contract import BaseOrbitAgentAdapter


class AgentRegistry:
    """按 agent_type 注册和解析 adapter。"""

    def __init__(self) -> None:
        self._agents: dict[str, BaseOrbitAgentAdapter] = {}

    def register(self, agent: BaseOrbitAgentAdapter) -> None:
        # registry 的目标是让 chat runtime 只依赖稳定协议，
        # 而不是知道具体是 web agent、file agent 还是其他实现。
        self._agents[agent.agent_type] = agent

    def resolve(self, agent_type: str) -> BaseOrbitAgentAdapter:
        try:
            return self._agents[agent_type]
        except KeyError as exc:
            raise LookupError(f"未注册的 agent 类型：{agent_type}") from exc

    def has(self, agent_type: str) -> bool:
        return agent_type in self._agents
