from datetime import datetime, timezone
from typing import Any
from uuid import UUID

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.conversation import Conversation
from app.models.message import Message


class ConversationRepository:
    # ConversationRepository 只处理会话元信息，不直接处理模型调用。
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def list_active(self, user_id: UUID) -> list[Conversation]:
        # 会话列表按最近更新时间倒序，符合聊天产品的常见展示方式。
        statement = (
            select(Conversation)
            .where(Conversation.user_id == user_id, Conversation.archived_at.is_(None))
            .order_by(Conversation.updated_at.desc())
        )
        result = await self.session.execute(statement)
        return list(result.scalars().all())

    async def get_active(
        self,
        *,
        user_id: UUID,
        conversation_id: UUID,
        for_update: bool = False,
    ) -> Conversation | None:
        # 会话读取带 user_id，确保用户只能访问自己的会话。
        statement = select(Conversation).where(
            Conversation.id == conversation_id,
            Conversation.user_id == user_id,
            Conversation.archived_at.is_(None),
        )
        if for_update:
            statement = statement.with_for_update()
        result = await self.session.execute(statement)
        return result.scalar_one_or_none()

    async def create(
        self,
        *,
        user_id: UUID,
        title: str | None,
        llm_config_id: UUID | None,
        chat_mode: str,
        metadata: dict,
        forked_from_conversation_id: UUID | None = None,
        forked_from_message_id: UUID | None = None,
    ) -> Conversation:
        # thread_id 由数据库默认生成，供后续 LangGraph checkpointer 使用。
        conversation = Conversation(
            user_id=user_id,
            title=title,
            llm_config_id=llm_config_id,
            chat_mode=chat_mode,
            metadata_=metadata,
            forked_from_conversation_id=forked_from_conversation_id,
            forked_from_message_id=forked_from_message_id,
        )
        self.session.add(conversation)
        await self.session.flush()
        await self.session.refresh(conversation)
        return conversation

    async def touch(self, conversation_id: UUID) -> None:
        # 新增消息或更新摘要后刷新 updated_at，用于会话列表排序。
        await self.session.execute(
            update(Conversation)
            .where(Conversation.id == conversation_id)
            .values(updated_at=func.now())
        )

    async def allocate_message_sequence_no(self, conversation_id: UUID) -> int:
        # 并发写入时统一从 conversations.next_message_sequence_no 原子分配序号。
        result = await self.session.execute(
            update(Conversation)
            .where(Conversation.id == conversation_id)
            .values(next_message_sequence_no=Conversation.next_message_sequence_no + 1)
            .returning(Conversation.next_message_sequence_no)
        )
        next_value = result.scalar_one_or_none()
        if next_value is None:
            raise ValueError(f"conversation not found: {conversation_id}")
        return int(next_value) - 1

    async def recompute_has_active_run(self, conversation_id: UUID) -> bool:
        # has_active_run 是缓存字段，真相仍来自是否存在 streaming assistant message。
        result = await self.session.execute(
            select(
                func.count(Message.id) > 0,
            ).where(
                Message.conversation_id == conversation_id,
                Message.role == "assistant",
                Message.status == "streaming",
            )
        )
        has_active_run = bool(result.scalar_one())
        await self.session.execute(
            update(Conversation)
            .where(Conversation.id == conversation_id)
            .values(has_active_run=has_active_run)
        )
        return has_active_run

    async def archive(self, conversation: Conversation) -> None:
        # 归档会话不删除消息，便于后续恢复、审计或导出。
        conversation.archived_at = datetime.now(timezone.utc)


class MessageRepository:
    # MessageRepository 负责消息事实源的读写，顺序由 sequence_no 保证。
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def list_by_conversation(self, conversation_id: UUID) -> list[Message]:
        # 管理类查询仍可读取整棵树；聊天 UI 默认使用 list_visible_path。
        statement = (
            select(Message)
            .where(Message.conversation_id == conversation_id)
            .order_by(Message.sequence_no.asc())
        )
        result = await self.session.execute(statement)
        return list(result.scalars().all())

    async def list_visible_path(self, conversation: Conversation) -> list[Message]:
        # active_leaf 是缓存；读取 visible path 时从 leaf 回溯到 root。
        if conversation.active_leaf_message_id is None:
            return []

        messages_by_id: dict[UUID, Message] = {}
        current_id: UUID | None = conversation.active_leaf_message_id
        visited: set[UUID] = set()
        while current_id is not None:
            if current_id in visited:
                break
            visited.add(current_id)
            message = await self.get_by_id(conversation_id=conversation.id, message_id=current_id)
            if message is None:
                break
            messages_by_id[message.id] = message
            current_id = message.parent_message_id

        path = list(messages_by_id.values())
        path.reverse()
        return path

    async def list_path_to_message(self, *, conversation_id: UUID, message_id: UUID) -> list[Message]:
        # 生成上下文时常常需要 root -> 指定消息，而不一定是当前 active_leaf。
        messages_by_id: dict[UUID, Message] = {}
        current_id: UUID | None = message_id
        visited: set[UUID] = set()
        while current_id is not None:
            if current_id in visited:
                break
            visited.add(current_id)
            message = await self.get_by_id(conversation_id=conversation_id, message_id=current_id)
            if message is None:
                break
            messages_by_id[message.id] = message
            current_id = message.parent_message_id

        path = list(messages_by_id.values())
        path.reverse()
        return path

    async def get_by_id(self, *, conversation_id: UUID, message_id: UUID) -> Message | None:
        statement = select(Message).where(
            Message.conversation_id == conversation_id,
            Message.id == message_id,
        )
        result = await self.session.execute(statement)
        return result.scalar_one_or_none()

    async def get_by_id_for_update(self, *, conversation_id: UUID, message_id: UUID) -> Message | None:
        statement = (
            select(Message)
            .where(
                Message.conversation_id == conversation_id,
                Message.id == message_id,
            )
            .with_for_update()
        )
        result = await self.session.execute(statement)
        return result.scalar_one_or_none()

    async def find_user_message_by_idempotency(
        self,
        *,
        conversation_id: UUID,
        parent_message_id: UUID | None,
        idempotency_key: str,
    ) -> Message | None:
        statement = (
            select(Message)
            .where(
                Message.conversation_id == conversation_id,
                Message.parent_message_id.is_(None)
                if parent_message_id is None
                else Message.parent_message_id == parent_message_id,
                Message.idempotency_key == idempotency_key,
                Message.role == "user",
            )
            .order_by(Message.sequence_no.asc())
        )
        result = await self.session.execute(statement)
        return result.scalars().first()

    async def get_first_assistant_child(self, *, conversation_id: UUID, parent_message_id: UUID) -> Message | None:
        statement = (
            select(Message)
            .where(
                Message.conversation_id == conversation_id,
                Message.parent_message_id == parent_message_id,
                Message.role == "assistant",
            )
            .order_by(Message.sequence_no.asc())
        )
        result = await self.session.execute(statement)
        return result.scalars().first()

    async def find_assistant_message_by_idempotency(
        self,
        *,
        conversation_id: UUID,
        parent_message_id: UUID,
        idempotency_key: str,
        source_message_id: UUID | None = None,
        revision_type: str | None = None,
    ) -> Message | None:
        # assistant 的幂等复用比普通 user message 更严格：
        # 既要看 parent，也要看 source_message/revision_type，避免不同 regenerate 串到一起。
        statement = select(Message).where(
            Message.conversation_id == conversation_id,
            Message.parent_message_id == parent_message_id,
            Message.idempotency_key == idempotency_key,
            Message.role == "assistant",
        )
        if source_message_id is not None:
            statement = statement.where(Message.source_message_id == source_message_id)
        if revision_type is not None:
            statement = statement.where(Message.revision_type == revision_type)
        statement = statement.order_by(Message.sequence_no.asc())
        result = await self.session.execute(statement)
        return result.scalars().first()

    async def create_user_message(
        self,
        *,
        conversation_id: UUID,
        content: str,
        parent_message: Message | None = None,
        source_message_id: UUID | None = None,
        revision_type: str = "normal",
        idempotency_key: str | None = None,
        content_parts: list = [],
    ) -> Message:
        sequence_no = await ConversationRepository(self.session).allocate_message_sequence_no(conversation_id)
        message = Message(
            conversation_id=conversation_id,
            sequence_no=sequence_no,
            parent_message_id=parent_message.id if parent_message else None,
            depth=(parent_message.depth + 1) if parent_message else 0,
            source_message_id=source_message_id,
            revision_type=revision_type,
            idempotency_key=idempotency_key,
            role="user",
            content=content,
            content_parts=content_parts,
            status="completed",
        )
        self.session.add(message)
        await self.session.flush()
        if parent_message is not None:
            # active_child 是分支选择源，创建新 child 时同步让父节点选中新分支。
            parent_message.active_child_message_id = message.id
            await self.session.flush()
        await self.session.refresh(message)
        return message

    async def create_assistant_placeholder(
        self,
        *,
        conversation_id: UUID,
        llm_config_id: UUID,
        provider: str,
        model: str,
        parent_message: Message | None = None,
        source_message_id: UUID | None = None,
        revision_type: str = "normal",
        idempotency_key: str | None = None,
        chat_mode: str | None = None,
    ) -> Message:
        # 先写入 streaming 占位；它也是树上的普通 child，可被取消、重发或切换。
        sequence_no = await ConversationRepository(self.session).allocate_message_sequence_no(conversation_id)
        message = Message(
            conversation_id=conversation_id,
            sequence_no=sequence_no,
            parent_message_id=parent_message.id if parent_message else None,
            depth=(parent_message.depth + 1) if parent_message else 0,
            source_message_id=source_message_id,
            revision_type=revision_type,
            idempotency_key=idempotency_key,
            role="assistant",
            content="",
            status="streaming",
            llm_config_id=llm_config_id,
            provider=provider,
            model=model,
            chat_mode=chat_mode,
        )
        self.session.add(message)
        await self.session.flush()
        if parent_message is not None:
            # assistant 生成后成为当前 user 节点选中的 child。
            parent_message.active_child_message_id = message.id
            await self.session.flush()
        await self.session.refresh(message)
        return message

    async def set_conversation_active_leaf(self, *, conversation: Conversation, message: Message | None) -> None:
        # active_leaf 只是路径终点缓存，便于快速加载和继续发送。
        conversation.active_leaf_message_id = message.id if message else None
        await self.session.flush()

    async def resolve_active_leaf_from(self, *, conversation_id: UUID, message: Message) -> Message:
        # active_child 是 branch 选择真实状态；沿这条链恢复该子树的当前 leaf。
        current = message
        visited: set[UUID] = set()
        while current.active_child_message_id is not None:
            if current.id in visited:
                break
            visited.add(current.id)
            child = await self.get_by_id(
                conversation_id=conversation_id,
                message_id=current.active_child_message_id,
            )
            if child is None or child.parent_message_id != current.id:
                break
            current = child
        return current

    async def list_siblings(self, message: Message) -> list[Message]:
        # sibling 是同一个 parent 下的 child，用 sequence_no 保持版本顺序稳定。
        statement = (
            select(Message)
            .where(
                Message.conversation_id == message.conversation_id,
                Message.parent_message_id.is_(None)
                if message.parent_message_id is None
                else Message.parent_message_id == message.parent_message_id,
            )
            .order_by(Message.sequence_no.asc())
        )
        result = await self.session.execute(statement)
        return list(result.scalars().all())

    async def get_message_read_state(self, message: Message) -> dict[str, Any]:
        # 给前端补充 1/n 和左右切换所需的 sibling 信息。
        response_metadata = dict(message.response_metadata or {})
        thought_events = response_metadata.get("thought_events")
        if not isinstance(thought_events, list):
            thought_events = []
        siblings = await self.list_siblings(message)
        sibling_ids = [sibling.id for sibling in siblings]
        try:
            index = sibling_ids.index(message.id)
        except ValueError:
            return {
                "thought_events": thought_events,
                "sibling_index": 1,
                "sibling_count": 1,
                "previous_sibling_id": None,
                "next_sibling_id": None,
            }
        return {
            "thought_events": thought_events,
            "sibling_index": index + 1,
            "sibling_count": len(siblings),
            "previous_sibling_id": sibling_ids[index - 1] if index > 0 else None,
            "next_sibling_id": sibling_ids[index + 1] if index + 1 < len(sibling_ids) else None,
        }

    async def clone_message_to_conversation(
        self,
        *,
        source: Message,
        target_conversation_id: UUID,
        parent_message: Message | None,
    ) -> Message:
        # Fork v1 只复制 visible path，复制出的消息重新建立 parent/active_child 链。
        sequence_no = await ConversationRepository(self.session).allocate_message_sequence_no(
            target_conversation_id
        )
        clone = Message(
            conversation_id=target_conversation_id,
            sequence_no=sequence_no,
            parent_message_id=parent_message.id if parent_message else None,
            depth=(parent_message.depth + 1) if parent_message else 0,
            source_message_id=source.id,
            revision_type="fork_copy",
            langgraph_message_id=source.langgraph_message_id,
            role=source.role,
            content=source.content,
            reasoning_content=source.reasoning_content,
            content_parts=source.content_parts,
            status=source.status,
            llm_config_id=source.llm_config_id,
            provider=source.provider,
            model=source.model,
            token_usage=source.token_usage,
            response_metadata=source.response_metadata,
        )
        self.session.add(clone)
        await self.session.flush()
        if parent_message is not None:
            parent_message.active_child_message_id = clone.id
            await self.session.flush()
        await self.session.refresh(clone)
        return clone

    async def complete_assistant_message(
        self,
        *,
        message: Message,
        content: str,
        token_usage: dict[str, Any],
        response_metadata: dict[str, Any],
        reasoning_content: str = "",
    ) -> Message:
        # 模型调用成功后，把占位消息推进到 completed，并保存用量和供应商元信息。
        message.content = content
        message.reasoning_content = reasoning_content
        message.status = "completed"
        message.token_usage = token_usage
        message.response_metadata = response_metadata
        await self.session.flush()
        await self.session.refresh(message)
        return message

    async def fail_assistant_message(self, *, message: Message, error: str) -> Message:
        # 调用失败也保留 assistant 消息，前端可以根据 failed 状态展示重试入口。
        message.status = "failed"
        message.response_metadata = {"error": error}
        await self.session.flush()
        await self.session.refresh(message)
        return message

    async def partial_assistant_message(
        self,
        *,
        message: Message,
        content: str,
        error: str,
        token_usage: dict[str, Any] | None = None,
        response_metadata: dict[str, Any] | None = None,
        reasoning_content: str = "",
    ) -> Message:
        # 流式生成中途失败但已有内容时，保留部分回复并标记为 partial。
        message.content = content
        message.reasoning_content = reasoning_content
        message.status = "partial"
        message.token_usage = token_usage or {}
        message.response_metadata = {**(response_metadata or {}), "error": error}
        await self.session.flush()
        await self.session.refresh(message)
        return message

    async def cancel_assistant_message(
        self,
        *,
        message: Message,
        content: str,
        reasoning_content: str = "",
        token_usage: dict[str, Any] | None = None,
        response_metadata: dict[str, Any] | None = None,
    ) -> Message:
        # 用户主动停止或连接断开时，保存已生成内容并标记为 cancelled。
        message.content = content
        message.reasoning_content = reasoning_content
        message.status = "cancelled"
        message.token_usage = token_usage or {}
        message.response_metadata = {**(response_metadata or {}), "error": "cancelled_by_user"}
        await self.session.flush()
        await self.session.refresh(message)
        return message
