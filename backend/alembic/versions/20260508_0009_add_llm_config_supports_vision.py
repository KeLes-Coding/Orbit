"""add llm_configs.supports_vision

Revision ID: 20260508_0009
Revises: 20260508_0008
Create Date: 2026-05-08
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260508_0009"
down_revision: Union[str, None] = "20260508_0008"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 默认 false，用户需在模型配置中手动勾选后才启用多模态图片输入。
    op.add_column(
        "llm_configs",
        sa.Column(
            "supports_vision",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )


def downgrade() -> None:
    op.drop_column("llm_configs", "supports_vision")
