"""ConversationRun 仓库层。

负责 conversation_runs 表的 CRUD 操作，遵循现有 repository 模式：
  - 所有方法为 async
  - create 方法 flush + refresh 后返回实例
  - update 方法直接修改传入的 ORM 对象后 flush
"""
from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.conversation_run import ConversationRun


class ConversationRunRepository:
    """统一 run 记录的持久化操作。"""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def create(
        self,
        *,
        conversation_id: UUID,
        assistant_message_id: UUID,
        user_id: UUID,
        thread_id: str,
        runtime_kind: str,
        chat_mode: str,
        metadata: dict | None = None,
    ) -> ConversationRun:
        """创建一条 streaming 状态的 run 记录。"""
        run = ConversationRun(
            conversation_id=conversation_id,
            assistant_message_id=assistant_message_id,
            user_id=user_id,
            thread_id=thread_id,
            runtime_kind=runtime_kind,
            chat_mode=chat_mode,
            status="streaming",
            started_at=datetime.now(timezone.utc),
            metadata_=metadata or {},
        )
        self.session.add(run)
        await self.session.flush()
        await self.session.refresh(run)
        return run

    async def get_by_id(self, run_id: UUID) -> ConversationRun | None:
        result = await self.session.execute(
            select(ConversationRun).where(ConversationRun.id == run_id)
        )
        return result.scalar_one_or_none()

    async def list_by_conversation(
        self, conversation_id: UUID
    ) -> list[ConversationRun]:
        """返回会话的所有 run 记录，按开始时间倒序。"""
        result = await self.session.execute(
            select(ConversationRun)
            .where(ConversationRun.conversation_id == conversation_id)
            .order_by(ConversationRun.started_at.desc())
        )
        return list(result.scalars().all())

    async def get_active_run(
        self, conversation_id: UUID
    ) -> ConversationRun | None:
        """获取会话当前正在执行的 run（至多一条）。"""
        result = await self.session.execute(
            select(ConversationRun)
            .where(
                ConversationRun.conversation_id == conversation_id,
                ConversationRun.status == "streaming",
            )
            .order_by(ConversationRun.started_at.desc())
            .limit(1)
        )
        return result.scalar_one_or_none()

    async def complete_run(
        self,
        run: ConversationRun,
        *,
        metadata: dict | None = None,
    ) -> ConversationRun:
        """标记 run 为 completed。"""
        run.status = "completed"
        run.finished_at = datetime.now(timezone.utc)
        if metadata:
            current_meta = dict(run.metadata_)
            current_meta.update(metadata)
            run.metadata_ = current_meta
        await self.session.flush()
        await self.session.refresh(run)
        return run

    async def fail_run(
        self,
        run: ConversationRun,
        *,
        error: str,
    ) -> ConversationRun:
        """标记 run 为 failed。"""
        run.status = "failed"
        run.finished_at = datetime.now(timezone.utc)
        run.last_error = error
        await self.session.flush()
        await self.session.refresh(run)
        return run

    async def cancel_run(
        self,
        run: ConversationRun,
    ) -> ConversationRun:
        """标记 run 为 cancelled。"""
        run.status = "cancelled"
        run.finished_at = datetime.now(timezone.utc)
        await self.session.flush()
        await self.session.refresh(run)
        return run

    async def interrupt_run(
        self,
        run: ConversationRun,
        *,
        metadata: dict | None = None,
    ) -> ConversationRun:
        """标记 run 为 interrupted（HITL 等待用户审批）。

        与 cancelled 的区别：interrupted 状态没有 finished_at，
        表示 run 尚未结束，只是暂停等待外部输入。
        """
        run.status = "interrupted"
        if metadata:
            current_meta = dict(run.metadata_)
            current_meta.update(metadata)
            run.metadata_ = current_meta
        await self.session.flush()
        await self.session.refresh(run)
        return run
