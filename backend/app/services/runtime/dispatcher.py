"""运行时调度器：根据 chat_mode 选择对应的运行时实现。

当前阶段：
  - chat / rag / tool → ClassicChatRuntime
  - agent → LangGraphAgentRuntime

后续当 agent 路径稳定后，可考虑将 tool 模式也路由到 agent runtime。
"""
from app.models.enums import ChatMode
from app.services.runtime.base import BaseRuntime
from app.services.runtime.classic import ClassicChatRuntime


class RuntimeDispatcher:
    """根据会话的 chat_mode 返回对应的 runtime 实例。"""

    def __init__(self) -> None:
        self._classic = ClassicChatRuntime()
        # agent runtime 延迟初始化，避免在 chat 路径中引入不必要的依赖。
        self._agent = None

    def dispatch(self, chat_mode: str) -> BaseRuntime:
        """返回对应运行时的实例。未知 mode 默认退回经典聊天路径。"""
        if chat_mode == ChatMode.agent:
            if self._agent is None:
                from app.services.agents.runtime import LangGraphAgentRuntime
                self._agent = LangGraphAgentRuntime()
            return self._agent
        # chat / rag / tool 当前都走经典路径
        return self._classic
