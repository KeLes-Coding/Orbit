"""Agent 包：deep agents 实现层。

内部按职责拆分：
  - tools.py:         只读工具定义
  - factory.py:       根据 LLMConfig 构建 agent
  - stream_adapter.py: LangGraph 事件 → 统一事件
  - runtime.py:       LangGraphAgentRuntime 实现
  - types.py:         agent 专用类型

外部代码通过 runtime.dispatcher 按 chat_mode 路由到本包，不应直接依赖内部模块。
"""
from app.services.agents.runtime import LangGraphAgentRuntime

__all__ = ["LangGraphAgentRuntime"]
