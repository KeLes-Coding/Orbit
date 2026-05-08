"""provider multi models

Revision ID: 20260507_0006
Revises: 20260506_0005
Create Date: 2026-05-07
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "20260507_0006"
down_revision: Union[str, None] = "20260506_0005"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 旧 model 单值迁移到 models JSONB 数组
    op.add_column(
        "llm_configs",
        sa.Column("models", postgresql.JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
    )
    op.execute(
        "UPDATE llm_configs SET models = jsonb_build_array(model) WHERE model IS NOT NULL"
    )
    # 移除旧列和旧约束
    op.drop_constraint("ck_llm_configs_model_nonempty", "llm_configs")
    op.drop_column("llm_configs", "model")


def downgrade() -> None:
    op.add_column(
        "llm_configs",
        sa.Column("model", sa.String(length=120), nullable=True),
    )
    op.execute(
        "UPDATE llm_configs SET model = models->>0 WHERE jsonb_array_length(models) > 0"
    )
    op.execute("DELETE FROM llm_configs WHERE model IS NULL")
    op.alter_column("llm_configs", "model", nullable=False)
    op.create_check_constraint(
        "ck_llm_configs_model_nonempty", "llm_configs", "length(trim(model)) > 0"
    )
    op.drop_column("llm_configs", "models")
