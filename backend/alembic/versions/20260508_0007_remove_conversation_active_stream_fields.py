"""remove conversation active stream fields

Revision ID: 20260508_0007
Revises: 20260507_0006
Create Date: 2026-05-08
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "20260508_0007"
down_revision: Union[str, None] = "20260507_0006"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 恢复机制改为 message 级：conversation_id + assistant_message_id -> runtime stream。
    op.drop_index("idx_conversations_active_stream", table_name="conversations")
    op.drop_constraint("fk_conversations_active_stream_message", "conversations", type_="foreignkey")
    op.drop_column("conversations", "active_stream_message_id")
    op.drop_column("conversations", "active_stream_id")


def downgrade() -> None:
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
