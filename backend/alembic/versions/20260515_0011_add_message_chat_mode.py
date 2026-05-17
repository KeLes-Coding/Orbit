"""add chat_mode column to messages table

Revision ID: 20260515_0011
Revises: 20260509_0010
Create Date: 2026-05-15
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260515_0011"
down_revision: Union[str, None] = "20260509_0010"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "messages",
        sa.Column("chat_mode", sa.String(32), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("messages", "chat_mode")
