"""drop unique constraint on conversation_files checksum index

Revision ID: 20260509_0010
Revises: 20260508_0009
Create Date: 2026-05-09
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260509_0010"
down_revision: Union[str, None] = "20260508_0009"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 跨会话上传相同文件时会创建独立记录共享物理存储，UNIQUE 约束阻止了这种用法。
    op.drop_index("uq_conversation_files_user_checksum", table_name="conversation_files")
    op.create_index(
        "idx_conversation_files_user_checksum",
        "conversation_files",
        ["user_id", "checksum_sha256"],
        postgresql_where=sa.text("deleted_at is null"),
    )


def downgrade() -> None:
    op.drop_index("idx_conversation_files_user_checksum", table_name="conversation_files")
    op.create_index(
        "uq_conversation_files_user_checksum",
        "conversation_files",
        ["user_id", "checksum_sha256"],
        unique=True,
        postgresql_where=sa.text("deleted_at is null"),
    )
