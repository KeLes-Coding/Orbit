from __future__ import annotations

import asyncio
from dataclasses import dataclass
from uuid import UUID


@dataclass
class ActiveMessageStream:
    # 单条 assistant 消息当前对应的运行中流。MVP 先使用进程内状态。
    message_id: UUID
    cancel_event: asyncio.Event
    task: asyncio.Task | None = None


class MessageStreamRegistry:
    # 进程内流注册表只覆盖 MVP 单进程场景；多 worker 时需要升级到共享存储。
    def __init__(self) -> None:
        self._streams: dict[UUID, ActiveMessageStream] = {}
        self._lock = asyncio.Lock()

    async def register(self, message_id: UUID) -> ActiveMessageStream:
        # 注册时先绑定当前任务，后续 StreamingResponse 接管后会重新 attach。
        stream = ActiveMessageStream(
            message_id=message_id,
            cancel_event=asyncio.Event(),
            task=asyncio.current_task(),
        )
        async with self._lock:
            self._streams[message_id] = stream
        return stream

    async def cancel(self, message_id: UUID) -> bool:
        async with self._lock:
            stream = self._streams.get(message_id)
            if stream is None:
                return False
            stream.cancel_event.set()
            if stream.task is not None:
                # 直接取消任务可以打断正在等待 provider 返回下一段 chunk 的场景。
                stream.task.cancel()
            return True

    async def attach_current_task(self, message_id: UUID) -> None:
        # StreamingResponse 会在独立任务里消费生成器，取消信号要指向这个任务。
        async with self._lock:
            stream = self._streams.get(message_id)
            if stream is not None:
                stream.task = asyncio.current_task()

    async def unregister(self, message_id: UUID) -> None:
        async with self._lock:
            self._streams.pop(message_id, None)


message_stream_registry = MessageStreamRegistry()
