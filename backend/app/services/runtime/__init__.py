"""运行时包：统一会话执行抽象层。

外部代码通过此包使用运行时相关类型，不直接依赖内部实现。
"""
from app.services.runtime.base import BaseRuntime
from app.services.runtime.dispatcher import RuntimeDispatcher
from app.services.runtime.types import RunContext, RuntimeResult, UnifiedStreamEvent

__all__ = [
    "BaseRuntime",
    "RunContext",
    "RuntimeDispatcher",
    "RuntimeResult",
    "UnifiedStreamEvent",
]
