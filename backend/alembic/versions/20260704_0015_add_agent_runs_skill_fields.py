"""add skill fields to agent_runs

Revision ID: 20260704_0015
Revises: 20260606_0014
Create Date: 2026-07-04
"""

from typing import Sequence, Union

from alembic import op


revision: str = "20260704_0015"
down_revision: Union[str, None] = "20260606_0014"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("alter table agent_runs add column if not exists execution_kind varchar(20)")
    op.execute("alter table agent_runs add column if not exists skill_type varchar(80)")
    op.execute("alter table agent_runs add column if not exists execution_backend varchar(32)")


def downgrade() -> None:
    op.execute("alter table agent_runs drop column if exists execution_backend")
    op.execute("alter table agent_runs drop column if exists skill_type")
    op.execute("alter table agent_runs drop column if exists execution_kind")
