from uuid import UUID

from fastapi import HTTPException, status

from app.schemas.conversation import (
    BranchSwitchRead,
    ConversationForkCreate,
    ConversationForkRead,
    ConversationRead,
    MessageRead,
)
from app.services.conversations.base import ConversationBaseService


class ConversationBranchService(ConversationBaseService):
    # 消息树读取、分支切换和 fork 逻辑集中在这里，和模型运行态保持分离。
    async def list_messages(self, *, user_id: UUID, conversation_id: UUID) -> list[MessageRead]:
        # 先校验会话归属，再读取消息，避免用户枚举 conversation_id 读取他人历史。
        conversation = await self._get_owned_conversation(
            user_id=user_id, conversation_id=conversation_id
        )
        messages = await self.messages.list_visible_path(conversation)
        return await self._message_reads(messages)

    async def switch_branch(
        self,
        *,
        user_id: UUID,
        conversation_id: UUID,
        message_id: UUID,
    ) -> BranchSwitchRead:
        # 切换 branch 只改局部分叉点的 active_child，再沿 active_child 链恢复子路径。
        conversation = await self._get_owned_conversation(
            user_id=user_id,
            conversation_id=conversation_id,
            for_update=True,
        )
        target = await self.messages.get_by_id(
            conversation_id=conversation_id, message_id=message_id
        )
        if target is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="消息不存在")
        if target.parent_message_id is not None:
            parent = await self.messages.get_by_id(
                conversation_id=conversation_id,
                message_id=target.parent_message_id,
            )
            if parent is None:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST, detail="无法找到分支父节点"
                )
            # message_id 是目标 sibling；其 parent 需要把 active_child 指向它。
            parent.active_child_message_id = target.id

        # 不找最深 leaf，而是沿目标 sibling 自己的 active_child 选择向下恢复。
        leaf = await self.messages.resolve_active_leaf_from(
            conversation_id=conversation_id,
            message=target,
        )
        await self.messages.set_conversation_active_leaf(conversation=conversation, message=leaf)
        await self.conversations.touch(conversation_id)
        await self.session.commit()
        messages = await self.messages.list_visible_path(conversation)
        return BranchSwitchRead(
            active_leaf_message_id=conversation.active_leaf_message_id,
            messages=await self._message_reads(messages),
        )

    async def fork_conversation(
        self,
        *,
        user_id: UUID,
        conversation_id: UUID,
        message_id: UUID,
        payload: ConversationForkCreate,
    ) -> ConversationForkRead:
        # Fork v1 只允许从当前 visible path 上的节点复制，避免复制未选中的 sibling 子树。
        conversation = await self._get_owned_conversation(
            user_id=user_id,
            conversation_id=conversation_id,
        )
        visible_path = await self.messages.list_visible_path(conversation)
        target_index = next(
            (index for index, message in enumerate(visible_path) if message.id == message_id), None
        )
        if target_index is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST, detail="只能从当前可见路径 fork"
            )

        # 新会话独立拥有 thread、summary 和 active path，只保留来源字段用于追溯。
        new_conversation = await self.conversations.create(
            user_id=user_id,
            title=payload.title or conversation.title,
            llm_config_id=conversation.llm_config_id,
            chat_mode=conversation.chat_mode,
            metadata=dict(conversation.metadata_ or {}),
            forked_from_conversation_id=conversation.id,
            forked_from_message_id=message_id,
        )
        copied_messages = []
        parent_copy = None
        for source_message in visible_path[: target_index + 1]:
            # 按 root -> target 顺序复制，逐条重建 parent 和 active_child。
            copied = await self.messages.clone_message_to_conversation(
                source=source_message,
                target_conversation_id=new_conversation.id,
                parent_message=parent_copy,
            )
            copied_messages.append(copied)
            parent_copy = copied

        await self.messages.set_conversation_active_leaf(
            conversation=new_conversation, message=parent_copy
        )
        await self.conversations.touch(new_conversation.id)
        await self.session.commit()
        await self.session.refresh(new_conversation)
        return ConversationForkRead(
            conversation=ConversationRead.model_validate(new_conversation),
            messages=await self._message_reads(copied_messages),
        )
