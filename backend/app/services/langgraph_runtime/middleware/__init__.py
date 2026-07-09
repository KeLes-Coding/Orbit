"""Middleware view for Orbit Agent plugins."""

from app.services.langgraph_runtime.middleware.agent_middleware import AgentMiddleware

__all__ = ["AgentMiddleware"]
