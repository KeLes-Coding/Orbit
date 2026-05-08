"""add conversation active stream fields

Revision ID: 20260503_0004
Revises: 20260502_0003
Create Date: 2026-05-03
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "20260503_0004"
down_revision: Union[str, None] = "20260502_0003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # active_stream_* 是会话级运行态指针，具体事件序列仍保存在运行时 stream store。
    op.add_column("conversations", sa.Column("active_stream_id", sa.Text(), nullable=True))
    op.add_column(
        "conversations",
        sa.Column("active_stream_message_id", postgresql.UUID(as_uuid=True), nullable=True),
    )

    op.create_foreign_key(
        "fk_conversations_active_stream_message",
        "conversations",
        "messages",
        ["active_stream_message_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index(
        "idx_conversations_active_stream",
        "conversations",
        ["active_stream_id"],
        postgresql_where=sa.text("active_stream_id is not null"),
    )


def downgrade() -> None:
    op.drop_index("idx_conversations_active_stream", table_name="conversations")
    op.drop_constraint("fk_conversations_active_stream_message", "conversations", type_="foreignkey")
    op.drop_column("conversations", "active_stream_message_id")
    op.drop_column("conversations", "active_stream_id")
