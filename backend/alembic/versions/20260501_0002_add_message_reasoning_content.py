"""add message reasoning content

Revision ID: 20260501_0002
Revises: 20260427_0001
Create Date: 2026-05-01
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "20260501_0002"
down_revision: Union[str, None] = "20260427_0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # reasoning_content 单独保存模型推理文本，避免混入 assistant 正文和后续上下文。
    op.add_column(
        "messages",
        sa.Column("reasoning_content", sa.Text(), nullable=False, server_default=sa.text("''")),
    )


def downgrade() -> None:
    op.drop_column("messages", "reasoning_content")
