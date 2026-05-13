"""运行时抽象基类。

所有运行时（ClassicChatRuntime / LangGraphAgentRuntime）都必须实现 execute 方法，
确保上层 ConversationService 不需要关心底层执行细节。
"""
from abc import ABC, abstractmethod
from collections.abc import AsyncIterator

from app.services.runtime.types import RunContext, UnifiedStreamEvent


class BaseRuntime(ABC):
    """统一运行时契约：接收 RunContext，产出 UnifiedStreamEvent 序列。"""

    @abstractmethod
    async def execute(self, ctx: RunContext) -> AsyncIterator[UnifiedStreamEvent]:
        """执行生成并逐事件产出。

        每个事件对应一条 SSE 消息，由调用方写入 ConversationStreamStore。
        """
        ...
