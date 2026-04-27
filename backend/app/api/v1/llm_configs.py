from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user
from app.db.session import get_db_session
from app.models.user import User
from app.schemas.llm_config import LLMConfigCreate, LLMConfigRead, LLMConfigUpdate
from app.services.llm_config import LLMConfigService


router = APIRouter(prefix="/llm-configs", tags=["llm-configs"])


@router.get("", response_model=list[LLMConfigRead])
async def list_llm_configs(
    current_user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> list[LLMConfigRead]:
    # 返回当前用户自己的模型配置列表，API Key 只返回 has_api_key。
    return await LLMConfigService(session).list_configs(current_user.id)


@router.post("", response_model=LLMConfigRead, status_code=status.HTTP_201_CREATED)
async def create_llm_config(
    payload: LLMConfigCreate,
    current_user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> LLMConfigRead:
    # 创建配置时服务层会负责加密 API Key 和维护默认配置唯一性。
    return await LLMConfigService(session).create_config(user_id=current_user.id, payload=payload)


@router.get("/{config_id}", response_model=LLMConfigRead)
async def get_llm_config(
    config_id: UUID,
    current_user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> LLMConfigRead:
    # 读取单条配置时会校验归属，避免跨用户访问。
    return await LLMConfigService(session).get_config(
        user_id=current_user.id,
        config_id=config_id,
    )


@router.patch("/{config_id}", response_model=LLMConfigRead)
async def update_llm_config(
    config_id: UUID,
    payload: LLMConfigUpdate,
    current_user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> LLMConfigRead:
    # PATCH 只更新传入字段，未传字段保持原值。
    return await LLMConfigService(session).update_config(
        user_id=current_user.id,
        config_id=config_id,
        payload=payload,
    )


@router.post("/{config_id}/default", response_model=LLMConfigRead)
async def set_default_llm_config(
    config_id: UUID,
    current_user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> LLMConfigRead:
    # 设置默认配置会先清空同用户的旧默认配置。
    return await LLMConfigService(session).set_default(
        user_id=current_user.id,
        config_id=config_id,
    )


@router.delete("/{config_id}", status_code=status.HTTP_204_NO_CONTENT)
async def archive_llm_config(
    config_id: UUID,
    current_user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> Response:
    # 删除采用软删除，历史消息仍保留当时的 provider/model 快照。
    await LLMConfigService(session).archive_config(
        user_id=current_user.id,
        config_id=config_id,
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)
