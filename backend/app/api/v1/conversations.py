from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user
from app.db.session import get_db_session
from app.models.user import User
from app.schemas.conversation import (
    ConversationCreate,
    ConversationRead,
    ConversationUpdate,
    MessageCreate,
    MessageRead,
)
from app.services.conversation import ConversationService


router = APIRouter(prefix="/conversations", tags=["conversations"])


@router.get("", response_model=list[ConversationRead])
async def list_conversations(
    current_user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> list[ConversationRead]:
    # 返回当前用户未归档会话，用于会话侧边栏列表。
    return await ConversationService(session).list_conversations(current_user.id)


@router.post("", response_model=ConversationRead, status_code=status.HTTP_201_CREATED)
async def create_conversation(
    payload: ConversationCreate,
    current_user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> ConversationRead:
    # 新建会话时可以显式选择模型配置，也可以使用默认配置。
    return await ConversationService(session).create_conversation(
        user_id=current_user.id,
        payload=payload,
    )


@router.get("/{conversation_id}", response_model=ConversationRead)
async def get_conversation(
    conversation_id: UUID,
    current_user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> ConversationRead:
    # 查询会话详情会先校验 user_id 归属。
    return await ConversationService(session).get_conversation(
        user_id=current_user.id,
        conversation_id=conversation_id,
    )


@router.patch("/{conversation_id}", response_model=ConversationRead)
async def update_conversation(
    conversation_id: UUID,
    payload: ConversationUpdate,
    current_user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> ConversationRead:
    # 更新标题、默认模型配置或 metadata，历史消息不受影响。
    return await ConversationService(session).update_conversation(
        user_id=current_user.id,
        conversation_id=conversation_id,
        payload=payload,
    )


@router.delete("/{conversation_id}", status_code=status.HTTP_204_NO_CONTENT)
async def archive_conversation(
    conversation_id: UUID,
    current_user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> Response:
    # 会话归档后不再出现在列表中，但数据库仍保留原始消息。
    await ConversationService(session).archive_conversation(
        user_id=current_user.id,
        conversation_id=conversation_id,
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/{conversation_id}/messages", response_model=list[MessageRead])
async def list_messages(
    conversation_id: UUID,
    current_user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> list[MessageRead]:
    # 消息读取统一按 sequence_no 升序返回，保证聊天顺序稳定。
    return await ConversationService(session).list_messages(
        user_id=current_user.id,
        conversation_id=conversation_id,
    )


@router.post("/{conversation_id}/messages", response_model=MessageRead, status_code=201)
async def create_user_message(
    conversation_id: UUID,
    payload: MessageCreate,
    current_user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> MessageRead:
    # 当前只写入用户消息；assistant 占位和模型调用会在下一步接入。
    return await ConversationService(session).create_user_message(
        user_id=current_user.id,
        conversation_id=conversation_id,
        content=payload.content,
    )
