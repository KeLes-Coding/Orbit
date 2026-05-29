"""restore missing revision placeholder

Revision ID: 20260527_0012
Revises: 20260515_0011
Create Date: 2026-05-27
"""

from typing import Sequence, Union


revision: str = "20260527_0012"
down_revision: Union[str, None] = "20260515_0011"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # This revision restores a missing link in the Alembic chain.
    # Keep it as a no-op so databases already stamped/applied to this
    # revision can start normally without unintended schema changes.
    pass


def downgrade() -> None:
    pass
