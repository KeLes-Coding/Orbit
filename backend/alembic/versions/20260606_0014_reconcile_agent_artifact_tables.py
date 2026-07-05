"""reconcile legacy agent artifact tables

Revision ID: 20260606_0014
Revises: 20260529_0013
Create Date: 2026-06-06
"""

from typing import Sequence, Union

from alembic import op


revision: str = "20260606_0014"
down_revision: Union[str, None] = "20260529_0013"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Some local/dev databases had an earlier draft of these tables stamped as
    # 20260529_0013. Bring those tables in line with the current ORM shape.
    op.execute("alter table agent_runs add column if not exists created_at timestamp with time zone not null default now()")

    op.execute("alter table agent_artifacts add column if not exists type varchar(40)")
    op.execute(
        """
        do $$
        begin
            if exists (
                select 1
                from information_schema.columns
                where table_schema = 'public'
                  and table_name = 'agent_artifacts'
                  and column_name = 'artifact_type'
            ) then
                update agent_artifacts
                set type = artifact_type
                where type is null and artifact_type is not null;

                alter table agent_artifacts alter column artifact_type drop not null;
            end if;
        end $$;
        """
    )
    op.execute("alter table agent_artifacts alter column type set not null")
    op.execute("alter table agent_artifacts add column if not exists preview_path text")

    op.execute(
        """
        do $$
        begin
            if exists (
                select 1
                from information_schema.columns
                where table_schema = 'public'
                  and table_name = 'agent_artifacts'
                  and column_name = 'storage_path'
            ) then
                alter table agent_artifacts alter column storage_path drop not null;
            end if;
        end $$;
        """
    )

    op.execute(
        "create index if not exists idx_agent_runs_conversation_created "
        "on agent_runs (conversation_id, created_at)"
    )
    op.execute("create index if not exists idx_agent_runs_message on agent_runs (message_id)")
    op.execute("create index if not exists idx_agent_artifacts_run on agent_artifacts (run_id)")
    op.execute("create index if not exists idx_agent_artifacts_type on agent_artifacts (type)")


def downgrade() -> None:
    op.execute("drop index if exists idx_agent_artifacts_type")
    op.execute("drop index if exists idx_agent_artifacts_run")
    op.execute("drop index if exists idx_agent_runs_conversation_created")
    op.execute("alter table agent_artifacts drop column if exists preview_path")
    op.execute("alter table agent_artifacts drop column if exists type")
    op.execute("alter table agent_runs drop column if exists created_at")
