"""add conversation_runs table

Revision ID: 20260512_0011
Revises: 20260509_0010
Create Date: 2026-05-12

conversation_runs 是统一执行抽象的核心表:
  - 每次生成请求（chat/agent）产生一条 run 记录
  - runtime_kind 区分 classic_chat / langgraph_agent
  - 用于 run 历史查询、恢复入口和监控审计
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID


revision: str = "20260512_0011"
down_revision: Union[str, None] = "20260509_0010"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "conversation_runs",
        sa.Column("id", UUID(), nullable=False, server_default=sa.text("gen_random_uuid()")),
        sa.Column("conversation_id", UUID(), nullable=False),
        sa.Column("assistant_message_id", UUID(), nullable=True),
        sa.Column("user_id", UUID(), nullable=False),
        sa.Column("thread_id", sa.Text(), nullable=False),
        sa.Column("runtime_kind", sa.String(32), nullable=False),
        sa.Column("chat_mode", sa.String(32), nullable=False),
        sa.Column(
            "status", sa.String(32), nullable=False, server_default=sa.text("'streaming'")
        ),
        sa.Column(
            "started_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column(
            "metadata",
            JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(
            ["conversation_id"], ["conversations.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["assistant_message_id"], ["messages.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
    )
    op.create_index(
        "idx_conversation_runs_conversation_started",
        "conversation_runs",
        ["conversation_id", sa.text("started_at DESC")],
    )
    op.create_index(
        "idx_conversation_runs_conversation_status",
        "conversation_runs",
        ["conversation_id", "status"],
    )
    op.create_index(
        "idx_conversation_runs_thread",
        "conversation_runs",
        ["thread_id"],
    )


def downgrade() -> None:
    op.drop_index("idx_conversation_runs_thread", table_name="conversation_runs")
    op.drop_index("idx_conversation_runs_conversation_status", table_name="conversation_runs")
    op.drop_index("idx_conversation_runs_conversation_started", table_name="conversation_runs")
    op.drop_table("conversation_runs")
