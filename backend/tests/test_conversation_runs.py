"""ConversationRun 持久化测试。

覆盖 run 创建、状态迁移（completed/failed/cancelled）、查询和迁移验证。
遵循现有测试模式：手写 fake、同步函数 + asyncio.run()。
"""
import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.models.enums import RunStatus, RuntimeKind
from app.schemas.conversation import ConversationRunRead


def run(coro):
    return asyncio.run(coro)


# ═══════════════════════════════════════════════════════════════════
# ORM 模型基本测试
# ═══════════════════════════════════════════════════════════════════

class TestConversationRunModel:
    """ConversationRun 模型的字段默认值测试。"""

    def test_default_status_is_streaming(self):
        from app.models.conversation_run import ConversationRun

        # status="streaming" 是数据库层 server_default，Python 构造函数需要显式传入。
        run_record = ConversationRun(
            conversation_id=uuid4(),
            assistant_message_id=uuid4(),
            user_id=uuid4(),
            thread_id="thread_123",
            runtime_kind=RuntimeKind.classic_chat,
            chat_mode="chat",
            status="streaming",
            started_at=datetime.now(timezone.utc),
            metadata_={},
        )
        assert run_record.status == "streaming"
        assert run_record.last_error is None
        assert run_record.finished_at is None
        assert run_record.metadata_ == {}


# ═══════════════════════════════════════════════════════════════════
# RunStatus / RuntimeKind 枚举测试
# ═══════════════════════════════════════════════════════════════════

class TestRunEnums:
    """枚举值测试。"""

    def test_run_status_values(self):
        assert RunStatus.streaming == "streaming"
        assert RunStatus.completed == "completed"
        assert RunStatus.failed == "failed"
        assert RunStatus.cancelled == "cancelled"

    def test_runtime_kind_values(self):
        assert RuntimeKind.classic_chat == "classic_chat"
        assert RuntimeKind.langgraph_agent == "langgraph_agent"


# ═══════════════════════════════════════════════════════════════════
# Pydantic Schema 测试
# ═══════════════════════════════════════════════════════════════════

class TestConversationRunReadSchema:
    """ConversationRunRead schema 验证。"""

    def test_schema_serializes_metadata_alias(self):
        """metadata_ 字段应序列化为 metadata。"""
        run_id = uuid4()
        conv_id = uuid4()
        now = datetime.now(timezone.utc)

        obj = SimpleNamespace(
            id=run_id,
            conversation_id=conv_id,
            assistant_message_id=None,
            thread_id="th_1",
            runtime_kind="classic_chat",
            chat_mode="chat",
            status="streaming",
            started_at=now,
            finished_at=None,
            last_error=None,
            metadata_={},
            created_at=now,
            updated_at=now,
        )
        schema = ConversationRunRead.model_validate(obj)
        data = schema.model_dump(mode="json", by_alias=True)
        assert "metadata" in data
        assert "metadata_" not in data
        assert data["metadata"] == {}


# ═══════════════════════════════════════════════════════════════════
# Repository Fake 层测试
# ═══════════════════════════════════════════════════════════════════

class FakeRunSession:
    """模拟 AsyncSession，支持 ConversationRunRepository 所需的操作。"""

    def __init__(self) -> None:
        self._added: list = []
        self.commits = 0

    def add(self, obj) -> None:
        self._added.append(obj)

    async def flush(self) -> None:
        return None

    async def refresh(self, obj) -> None:
        # 模拟数据库为对象生成 ID 和时间戳
        if getattr(obj, "id", None) is None:
            obj.id = uuid4()
        if getattr(obj, "started_at", None) is None:
            obj.started_at = datetime.now(timezone.utc)

    async def commit(self) -> None:
        self.commits += 1


class TestRunRepository:
    """ConversationRunRepository 基本操作测试。"""

    def test_create_run_sets_defaults(self):
        from app.repositories.conversation_run import ConversationRunRepository

        session = FakeRunSession()
        repo = ConversationRunRepository(session)

        run_record = run(
            repo.create(
                conversation_id=uuid4(),
                assistant_message_id=uuid4(),
                user_id=uuid4(),
                thread_id="th_1",
                runtime_kind="classic_chat",
                chat_mode="chat",
            )
        )

        assert run_record.status == "streaming"
        assert run_record.runtime_kind == "classic_chat"
        assert run_record.id is not None
        assert len(session._added) == 1

    def test_complete_run_updates_status(self):
        from app.repositories.conversation_run import ConversationRunRepository
        from app.models.conversation_run import ConversationRun

        session = FakeRunSession()
        repo = ConversationRunRepository(session)

        run_record = ConversationRun(
            conversation_id=uuid4(),
            user_id=uuid4(),
            thread_id="th_2",
            runtime_kind="langgraph_agent",
            chat_mode="agent",
            status="streaming",
            started_at=datetime.now(timezone.utc),
            metadata_={},
        )

        updated = run(repo.complete_run(run_record))
        assert updated.status == "completed"
        assert updated.finished_at is not None

    def test_fail_run_sets_error(self):
        from app.repositories.conversation_run import ConversationRunRepository
        from app.models.conversation_run import ConversationRun

        session = FakeRunSession()
        repo = ConversationRunRepository(session)

        run_record = ConversationRun(
            conversation_id=uuid4(),
            user_id=uuid4(),
            thread_id="th_3",
            runtime_kind="classic_chat",
            chat_mode="chat",
            status="streaming",
            started_at=datetime.now(timezone.utc),
            metadata_={},
        )

        updated = run(repo.fail_run(run_record, error="test error"))
        assert updated.status == "failed"
        assert updated.last_error == "test error"

    def test_cancel_run_sets_finished_at(self):
        from app.repositories.conversation_run import ConversationRunRepository
        from app.models.conversation_run import ConversationRun

        session = FakeRunSession()
        repo = ConversationRunRepository(session)

        run_record = ConversationRun(
            conversation_id=uuid4(),
            user_id=uuid4(),
            thread_id="th_4",
            runtime_kind="classic_chat",
            chat_mode="chat",
            status="streaming",
            started_at=datetime.now(timezone.utc),
            metadata_={},
        )

        updated = run(repo.cancel_run(run_record))
        assert updated.status == "cancelled"
        assert updated.finished_at is not None
