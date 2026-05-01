"""add message tree branching

Revision ID: 20260502_0003
Revises: 20260501_0002
Create Date: 2026-05-02
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "20260502_0003"
down_revision: Union[str, None] = "20260501_0002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("conversations", sa.Column("active_leaf_message_id", postgresql.UUID(as_uuid=True), nullable=True))
    op.add_column(
        "conversations",
        sa.Column("forked_from_conversation_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.add_column("conversations", sa.Column("forked_from_message_id", postgresql.UUID(as_uuid=True), nullable=True))
    op.add_column("conversations", sa.Column("summary_leaf_message_id", postgresql.UUID(as_uuid=True), nullable=True))

    op.add_column("messages", sa.Column("parent_message_id", postgresql.UUID(as_uuid=True), nullable=True))
    op.add_column("messages", sa.Column("active_child_message_id", postgresql.UUID(as_uuid=True), nullable=True))
    op.add_column("messages", sa.Column("depth", sa.Integer(), nullable=False, server_default=sa.text("0")))
    op.add_column("messages", sa.Column("source_message_id", postgresql.UUID(as_uuid=True), nullable=True))
    op.add_column("messages", sa.Column("revision_type", sa.String(length=30), nullable=True))

    op.create_foreign_key(
        "fk_conversations_active_leaf_message",
        "conversations",
        "messages",
        ["active_leaf_message_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_foreign_key(
        "fk_conversations_forked_from_conversation",
        "conversations",
        "conversations",
        ["forked_from_conversation_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_foreign_key(
        "fk_conversations_forked_from_message",
        "conversations",
        "messages",
        ["forked_from_message_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_foreign_key(
        "fk_conversations_summary_leaf_message",
        "conversations",
        "messages",
        ["summary_leaf_message_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_foreign_key(
        "fk_messages_parent_message",
        "messages",
        "messages",
        ["parent_message_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_foreign_key(
        "fk_messages_active_child_message",
        "messages",
        "messages",
        ["active_child_message_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_foreign_key(
        "fk_messages_source_message",
        "messages",
        "messages",
        ["source_message_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_check_constraint(
        "ck_messages_revision_type",
        "messages",
        "revision_type is null or revision_type in ('normal', 'edit', 'regenerate', 'fork_copy')",
    )

    # Existing linear conversations become single-path trees.
    op.execute(
        """
        with ordered as (
          select
            id,
            conversation_id,
            lag(id) over (partition by conversation_id order by sequence_no asc) as parent_id,
            lead(id) over (partition by conversation_id order by sequence_no asc) as child_id,
            row_number() over (partition by conversation_id order by sequence_no asc) - 1 as new_depth
          from messages
        )
        update messages m
        set
          parent_message_id = ordered.parent_id,
          active_child_message_id = ordered.child_id,
          depth = ordered.new_depth,
          revision_type = coalesce(m.revision_type, 'normal')
        from ordered
        where m.id = ordered.id
        """
    )
    op.execute(
        """
        with leaves as (
          select distinct on (conversation_id)
            conversation_id,
            id
          from messages
          order by conversation_id, sequence_no desc
        )
        update conversations c
        set active_leaf_message_id = leaves.id
        from leaves
        where c.id = leaves.conversation_id
        """
    )

    op.create_index("idx_messages_conversation_parent", "messages", ["conversation_id", "parent_message_id"])
    op.create_index(
        "idx_messages_conversation_active_child",
        "messages",
        ["conversation_id", "active_child_message_id"],
    )
    op.create_index("idx_messages_conversation_depth", "messages", ["conversation_id", "depth"])
    op.create_index(
        "idx_messages_source_message",
        "messages",
        ["source_message_id"],
        postgresql_where=sa.text("source_message_id is not null"),
    )
    op.create_index(
        "idx_conversations_active_leaf",
        "conversations",
        ["active_leaf_message_id"],
        postgresql_where=sa.text("active_leaf_message_id is not null"),
    )


def downgrade() -> None:
    op.drop_index("idx_conversations_active_leaf", table_name="conversations")
    op.drop_index("idx_messages_source_message", table_name="messages")
    op.drop_index("idx_messages_conversation_depth", table_name="messages")
    op.drop_index("idx_messages_conversation_active_child", table_name="messages")
    op.drop_index("idx_messages_conversation_parent", table_name="messages")

    op.drop_constraint("ck_messages_revision_type", "messages", type_="check")
    op.drop_constraint("fk_messages_source_message", "messages", type_="foreignkey")
    op.drop_constraint("fk_messages_active_child_message", "messages", type_="foreignkey")
    op.drop_constraint("fk_messages_parent_message", "messages", type_="foreignkey")
    op.drop_constraint("fk_conversations_summary_leaf_message", "conversations", type_="foreignkey")
    op.drop_constraint("fk_conversations_forked_from_message", "conversations", type_="foreignkey")
    op.drop_constraint("fk_conversations_forked_from_conversation", "conversations", type_="foreignkey")
    op.drop_constraint("fk_conversations_active_leaf_message", "conversations", type_="foreignkey")

    op.drop_column("messages", "revision_type")
    op.drop_column("messages", "source_message_id")
    op.drop_column("messages", "depth")
    op.drop_column("messages", "active_child_message_id")
    op.drop_column("messages", "parent_message_id")

    op.drop_column("conversations", "summary_leaf_message_id")
    op.drop_column("conversations", "forked_from_message_id")
    op.drop_column("conversations", "forked_from_conversation_id")
    op.drop_column("conversations", "active_leaf_message_id")
