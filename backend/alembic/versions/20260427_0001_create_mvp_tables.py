"""create mvp tables

Revision ID: 20260427_0001
Revises:
Create Date: 2026-04-27
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "20260427_0001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # gen_random_uuid() 来自 pgcrypto，用于生成四张核心表的 UUID 主键。
    op.execute("create extension if not exists pgcrypto")

    # users 是顶层租户表，MVP 采用邮箱密码登录，因此包含 password_hash。
    op.create_table(
        "users",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("email", sa.String(length=255), nullable=False),
        sa.Column("password_hash", sa.Text(), nullable=False),
        sa.Column("display_name", sa.String(length=100), nullable=True),
        sa.Column("avatar_url", sa.Text(), nullable=True),
        sa.Column("metadata", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("is_enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint("length(trim(email)) > 0", name="ck_users_email_nonempty"),
    )
    op.create_index("uq_users_email_active", "users", [sa.text("lower(email)")], unique=True, postgresql_where=sa.text("archived_at is null"))
    op.create_index("idx_users_enabled", "users", ["is_enabled"], postgresql_where=sa.text("archived_at is null"))

    # llm_configs 保存用户接入不同模型供应商所需的连接配置。
    op.create_table(
        "llm_configs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("provider", sa.String(length=50), nullable=False),
        sa.Column("model", sa.String(length=120), nullable=False),
        sa.Column("base_url", sa.Text(), nullable=True),
        sa.Column("api_key_ciphertext", sa.Text(), nullable=True),
        sa.Column("provider_options", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("is_default", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("is_enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint("length(trim(provider)) > 0", name="ck_llm_configs_provider_nonempty"),
        sa.CheckConstraint("length(trim(model)) > 0", name="ck_llm_configs_model_nonempty"),
    )
    op.create_index("uq_llm_configs_user_name_active", "llm_configs", ["user_id", "name"], unique=True, postgresql_where=sa.text("archived_at is null"))
    # 每个用户只能有一个未归档的默认模型配置。
    op.create_index("uq_llm_configs_user_default_active", "llm_configs", ["user_id"], unique=True, postgresql_where=sa.text("is_default = true and archived_at is null"))
    op.create_index("idx_llm_configs_user_enabled", "llm_configs", ["user_id", "is_enabled"], postgresql_where=sa.text("archived_at is null"))

    # conversations 保存产品侧会话元信息，thread_id 预留给 LangGraph checkpointer 使用。
    op.create_table(
        "conversations",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("thread_id", sa.Text(), nullable=False, server_default=sa.text("gen_random_uuid()::text")),
        sa.Column("title", sa.String(length=200), nullable=True),
        sa.Column("llm_config_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("llm_configs.id", ondelete="SET NULL"), nullable=True),
        sa.Column("chat_mode", sa.String(length=32), nullable=False, server_default=sa.text("'chat'")),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column("summary_updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("summary_message_count", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("metadata", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint("chat_mode in ('chat', 'rag', 'agent', 'tool')", name="ck_conversations_chat_mode"),
    )
    op.create_index("uq_conversations_thread_id", "conversations", ["thread_id"], unique=True)
    op.create_index("idx_conversations_user_updated", "conversations", ["user_id", sa.text("updated_at desc")], postgresql_where=sa.text("archived_at is null"))

    # messages 是聊天历史事实源，sequence_no 保证同一会话内顺序稳定。
    op.create_table(
        "messages",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("conversation_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("conversations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("sequence_no", sa.Integer(), nullable=False),
        sa.Column("langgraph_message_id", sa.Text(), nullable=True),
        sa.Column("role", sa.String(length=32), nullable=False),
        sa.Column("content", sa.Text(), nullable=False, server_default=sa.text("''")),
        sa.Column("content_parts", postgresql.JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("status", sa.String(length=32), nullable=False, server_default=sa.text("'completed'")),
        sa.Column("llm_config_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("llm_configs.id", ondelete="SET NULL"), nullable=True),
        sa.Column("provider", sa.String(length=50), nullable=True),
        sa.Column("model", sa.String(length=120), nullable=True),
        sa.Column("token_usage", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("response_metadata", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint("role in ('system', 'user', 'assistant', 'tool')", name="ck_messages_role"),
        sa.CheckConstraint("status in ('streaming', 'completed', 'cancelled', 'failed', 'partial')", name="ck_messages_status"),
        sa.UniqueConstraint("conversation_id", "sequence_no", name="uq_messages_conversation_sequence"),
    )
    op.create_index("idx_messages_conversation_created", "messages", ["conversation_id", "created_at"])
    op.create_index("idx_messages_conversation_sequence", "messages", ["conversation_id", "sequence_no"])
    op.create_index("idx_messages_langgraph_message_id", "messages", ["langgraph_message_id"], postgresql_where=sa.text("langgraph_message_id is not null"))


def downgrade() -> None:
    op.drop_index("idx_messages_langgraph_message_id", table_name="messages")
    op.drop_index("idx_messages_conversation_sequence", table_name="messages")
    op.drop_index("idx_messages_conversation_created", table_name="messages")
    op.drop_table("messages")

    op.drop_index("idx_conversations_user_updated", table_name="conversations")
    op.drop_index("uq_conversations_thread_id", table_name="conversations")
    op.drop_table("conversations")

    op.drop_index("idx_llm_configs_user_enabled", table_name="llm_configs")
    op.drop_index("uq_llm_configs_user_default_active", table_name="llm_configs")
    op.drop_index("uq_llm_configs_user_name_active", table_name="llm_configs")
    op.drop_table("llm_configs")

    op.drop_index("idx_users_enabled", table_name="users")
    op.drop_index("uq_users_email_active", table_name="users")
    op.drop_table("users")
