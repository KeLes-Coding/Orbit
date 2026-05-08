"""add conversation_files table

Revision ID: 20260508_0008
Revises: 20260508_0007
Create Date: 2026-05-08
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260508_0008"
down_revision: Union[str, None] = "20260508_0007"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "conversation_files",
        sa.Column("id", sa.Uuid(), nullable=False, server_default=sa.text("gen_random_uuid()")),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("conversation_id", sa.Uuid(), nullable=True),
        sa.Column("original_name", sa.String(length=255), nullable=False),
        sa.Column("file_type", sa.String(length=100), nullable=False),
        sa.Column("file_size", sa.BigInteger(), nullable=False),
        sa.Column("file_extension", sa.String(length=20), nullable=True),
        sa.Column("checksum_sha256", sa.String(length=64), nullable=False),
        sa.Column("storage_type", sa.String(length=20), nullable=False, server_default=sa.text("'local'")),
        sa.Column("storage_path", sa.Text(), nullable=False),
        sa.Column("storage_bucket", sa.Text(), nullable=True),
        sa.Column("extracted_text", sa.Text(), nullable=True),
        sa.Column("extracted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "extraction_status",
            sa.String(length=20),
            nullable=False,
            server_default=sa.text("'pending'"),
        ),
        sa.Column("extraction_error", sa.Text(), nullable=True),
        sa.Column(
            "bind_status", sa.String(length=20), nullable=False, server_default=sa.text("'pending'")
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["conversation_id"], ["conversations.id"], ondelete="SET NULL"
        ),
    )
    op.create_index(
        "idx_conversation_files_user_created",
        "conversation_files",
        ["user_id", sa.text("created_at desc")],
    )
    op.create_index(
        "idx_conversation_files_conversation",
        "conversation_files",
        ["conversation_id"],
    )
    op.create_index(
        "idx_conversation_files_extraction_status",
        "conversation_files",
        ["extraction_status"],
    )
    op.create_index(
        "idx_conversation_files_pending_expires",
        "conversation_files",
        ["expires_at"],
        postgresql_where=sa.text("bind_status = 'pending'"),
    )
    op.create_index(
        "uq_conversation_files_user_checksum",
        "conversation_files",
        ["user_id", "checksum_sha256"],
        unique=True,
        postgresql_where=sa.text("deleted_at is null"),
    )


def downgrade() -> None:
    op.drop_index("uq_conversation_files_user_checksum", table_name="conversation_files")
    op.drop_index("idx_conversation_files_pending_expires", table_name="conversation_files")
    op.drop_index("idx_conversation_files_extraction_status", table_name="conversation_files")
    op.drop_index("idx_conversation_files_conversation", table_name="conversation_files")
    op.drop_index("idx_conversation_files_user_created", table_name="conversation_files")
    op.drop_table("conversation_files")
