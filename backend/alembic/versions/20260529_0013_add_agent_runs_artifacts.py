"""add agent run and artifact tables

Revision ID: 20260529_0013
Revises: 20260527_0012
Create Date: 2026-05-29
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "20260529_0013"
down_revision: Union[str, None] = "20260527_0012"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "agent_runs",
        sa.Column("id", postgresql.UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("conversation_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("message_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("agent_type", sa.String(length=80), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("sandbox_id", sa.Text(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["conversation_id"], ["conversations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["message_id"], ["messages.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("message_id", name="uq_agent_runs_message"),
    )
    op.create_index("idx_agent_runs_conversation_created", "agent_runs", ["conversation_id", "created_at"])
    op.create_index("idx_agent_runs_message", "agent_runs", ["message_id"])

    op.create_table(
        "agent_artifacts",
        sa.Column("id", postgresql.UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("run_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("type", sa.String(length=40), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("path", sa.Text(), nullable=False),
        sa.Column("preview_path", sa.Text(), nullable=True),
        sa.Column("metadata", postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column("preview", postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["run_id"], ["agent_runs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("run_id", "name", name="uq_agent_artifacts_run_name"),
    )
    op.create_index("idx_agent_artifacts_run", "agent_artifacts", ["run_id"])
    op.create_index("idx_agent_artifacts_type", "agent_artifacts", ["type"])


def downgrade() -> None:
    op.drop_index("idx_agent_artifacts_type", table_name="agent_artifacts")
    op.drop_index("idx_agent_artifacts_run", table_name="agent_artifacts")
    op.drop_table("agent_artifacts")
    op.drop_index("idx_agent_runs_message", table_name="agent_runs")
    op.drop_index("idx_agent_runs_conversation_created", table_name="agent_runs")
    op.drop_table("agent_runs")
