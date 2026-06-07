"""Agent Runtime 本地 thread runtime store。

当前阶段只保留 LangGraph checkpointer 的统一入口，
避免在 runtime 层继续保留未闭环的 thread/file 兼容抽象。
"""

from __future__ import annotations

from langgraph.checkpoint.memory import MemorySaver


class InMemoryThreadRuntimeStore:
    """进程内 thread runtime store。

    当前仅负责提供共享 `MemorySaver`。
    """

    def __init__(self) -> None:
        self._checkpointer = MemorySaver()

    def get_checkpointer(self) -> MemorySaver:
        return self._checkpointer


thread_runtime_store = InMemoryThreadRuntimeStore()
