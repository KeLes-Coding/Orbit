"""add parallel run foundations

Revision ID: 20260506_0005
Revises: 20260503_0004
Create Date: 2026-05-06
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "20260506_0005"
down_revision: Union[str, None] = "20260503_0004"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "conversations",
        sa.Column("has_active_run", sa.Boolean(), nullable=False, server_default=sa.text("false")),
    )
    op.add_column(
        "conversations",
        sa.Column("next_message_sequence_no", sa.Integer(), nullable=False, server_default=sa.text("1")),
    )
    op.add_column("messages", sa.Column("idempotency_key", sa.String(length=120), nullable=True))

    # 现有会话回填 next_message_sequence_no，避免后续序号分配从 1 重新开始。
    op.execute(
        """
        update conversations c
        set next_message_sequence_no = coalesce(next_seq.max_sequence_no, 0) + 1
        from (
          select conversation_id, max(sequence_no) as max_sequence_no
          from messages
          group by conversation_id
        ) as next_seq
        where c.id = next_seq.conversation_id
        """
    )

    # has_active_run 只是 UI 缓存，真相来自 streaming assistant 是否存在。
    op.execute(
        """
        update conversations c
        set has_active_run = exists (
          select 1
          from messages m
          where m.conversation_id = c.id
            and m.role = 'assistant'
            and m.status = 'streaming'
        )
        """
    )

    op.create_index(
        "uq_messages_conversation_parent_idempotency",
        "messages",
        ["conversation_id", "parent_message_id", "idempotency_key"],
        unique=True,
        postgresql_where=sa.text("idempotency_key is not null"),
    )
    op.create_index(
        "uq_messages_conversation_root_idempotency",
        "messages",
        ["conversation_id", "idempotency_key"],
        unique=True,
        postgresql_where=sa.text("idempotency_key is not null and parent_message_id is null"),
    )


def downgrade() -> None:
    op.drop_index("uq_messages_conversation_root_idempotency", table_name="messages")
    op.drop_index("uq_messages_conversation_parent_idempotency", table_name="messages")
    op.drop_column("messages", "idempotency_key")
    op.drop_column("conversations", "next_message_sequence_no")
    op.drop_column("conversations", "has_active_run")
