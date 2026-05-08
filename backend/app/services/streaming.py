from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import UUID, uuid4


@dataclass(frozen=True)
class StreamEventRecord:
    # 运行时事件日志中的一条事件，seq 在单条 stream 内单调递增。
    stream_id: str
    seq: int
    event: str
    data: dict[str, Any]

    @property
    def event_id(self) -> str:
        return f"{self.stream_id}:{self.seq}"


@dataclass
class StreamSubscriber:
    # 每个在线订阅者都有一个独立队列，避免慢客户端阻塞其他客户端。
    subscriber_id: str
    queue: asyncio.Queue[StreamEventRecord | None]


@dataclass
class ActiveConversationStream:
    # 单条会话流的运行时状态，当前先保存在单进程内存中。
    stream_id: str
    conversation_id: UUID
    message_id: UUID
    user_id: UUID
    next_seq: int = 1
    is_active: bool = True
    retire_at: datetime | None = None
    event_log: list[StreamEventRecord] = field(default_factory=list)
    subscribers: dict[str, StreamSubscriber] = field(default_factory=dict)
    cancel_event: asyncio.Event = field(default_factory=asyncio.Event)
    producer_task: asyncio.Task | None = None


class ConversationStreamStore:
    # 当前实现是进程内 store，接口保持稳定，后续可切换到 Redis Streams。
    def __init__(self) -> None:
        self._streams: dict[str, ActiveConversationStream] = {}
        self._message_to_stream: dict[UUID, str] = {}
        self._lock = asyncio.Lock()

    async def create_stream(
        self,
        *,
        stream_id: str,
        conversation_id: UUID,
        message_id: UUID,
        user_id: UUID,
    ) -> ActiveConversationStream:
        # 每次创建新流前顺手清理过期 replay 窗口，避免进程内事件日志无限增长。
        await self._cleanup_expired_streams()
        stream = ActiveConversationStream(
            stream_id=stream_id,
            conversation_id=conversation_id,
            message_id=message_id,
            user_id=user_id,
        )
        async with self._lock:
            self._streams[stream_id] = stream
            self._message_to_stream[message_id] = stream_id
        return stream

    async def get_stream(self, stream_id: str) -> ActiveConversationStream | None:
        await self._cleanup_expired_streams()
        async with self._lock:
            return self._streams.get(stream_id)

    async def get_stream_by_message_id(self, message_id: UUID) -> ActiveConversationStream | None:
        await self._cleanup_expired_streams()
        async with self._lock:
            stream_id = self._message_to_stream.get(message_id)
            if stream_id is None:
                return None
            return self._streams.get(stream_id)

    async def attach_producer_task(self, stream_id: str, task: asyncio.Task | None = None) -> None:
        async with self._lock:
            stream = self._streams.get(stream_id)
            if stream is not None:
                stream.producer_task = task or asyncio.current_task()

    async def append_event(self, stream_id: str, *, event: str, data: dict[str, Any]) -> StreamEventRecord:
        async with self._lock:
            stream = self._streams.get(stream_id)
            if stream is None:
                raise KeyError(stream_id)

            # 事件先写日志再广播，这样新老订阅者都共享同一个顺序事实源。
            record = StreamEventRecord(
                stream_id=stream.stream_id,
                seq=stream.next_seq,
                event=event,
                data=data,
            )
            stream.next_seq += 1
            stream.event_log.append(record)
            subscribers = [subscriber.queue for subscriber in stream.subscribers.values()]

        for queue in subscribers:
            queue.put_nowait(record)
        return record

    async def subscribe(self, stream_id: str) -> AsyncIterator[StreamEventRecord]:
        stream = await self.get_stream(stream_id)
        if stream is None:
            return

        subscriber_id = str(uuid4())
        queue: asyncio.Queue[StreamEventRecord | None] = asyncio.Queue()

        async with self._lock:
            current = self._streams.get(stream_id)
            if current is None:
                return
            # 新订阅者先回放当前内存窗口内的完整事件日志，再无缝切到 live 队列。
            replay_records = list(current.event_log)
            should_wait_live = current.is_active
            if should_wait_live:
                current.subscribers[subscriber_id] = StreamSubscriber(
                    subscriber_id=subscriber_id,
                    queue=queue,
                )

        try:
            for record in replay_records:
                yield record

            if not should_wait_live:
                return

            while True:
                record = await queue.get()
                if record is None:
                    break
                yield record
        finally:
            async with self._lock:
                current = self._streams.get(stream_id)
                if current is not None:
                    current.subscribers.pop(subscriber_id, None)

    async def cancel(self, *, message_id: UUID) -> bool:
        async with self._lock:
            stream_id = self._message_to_stream.get(message_id)
            if stream_id is None:
                return False
            stream = self._streams.get(stream_id)
            if stream is None:
                return False
            stream.cancel_event.set()
            producer_task = stream.producer_task

        if producer_task is not None:
            # 主动取消任务，可以打断 provider 正在等待下一个 chunk 的场景。
            producer_task.cancel()
        return True

    async def is_cancelled(self, stream_id: str) -> bool:
        async with self._lock:
            stream = self._streams.get(stream_id)
            return bool(stream and stream.cancel_event.is_set())

    async def complete_stream(self, stream_id: str, *, retention_seconds: int) -> None:
        async with self._lock:
            stream = self._streams.get(stream_id)
            if stream is None:
                return
            # 流完成后不立刻销毁，而是进入一个短暂 replay 保留窗口。
            stream.is_active = False
            stream.retire_at = datetime.now(timezone.utc) + timedelta(seconds=retention_seconds)
            stream.producer_task = None
            subscribers = [subscriber.queue for subscriber in stream.subscribers.values()]

        for queue in subscribers:
            queue.put_nowait(None)

    async def _cleanup_expired_streams(self) -> None:
        now = datetime.now(timezone.utc)
        async with self._lock:
            expired_stream_ids = [
                stream_id
                for stream_id, stream in self._streams.items()
                if stream.retire_at is not None and stream.retire_at <= now
            ]
            for stream_id in expired_stream_ids:
                stream = self._streams.pop(stream_id, None)
                if stream is None:
                    continue
                self._message_to_stream.pop(stream.message_id, None)


conversation_stream_store = ConversationStreamStore()
