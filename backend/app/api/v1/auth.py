from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user
from app.db.session import get_db_session
from app.models.user import User
from app.schemas.auth import AuthToken, UserCreate, UserLogin, UserRead
from app.services.auth import AuthService


router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/register", response_model=UserRead, status_code=201)
async def register(
    payload: UserCreate,
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> User:
    # 注册只创建账号；访问令牌统一由登录接口签发。
    return await AuthService(session).register(payload)


@router.post("/login", response_model=AuthToken)
async def login(
    payload: UserLogin,
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> AuthToken:
    # 登录只校验邮箱密码，不在响应中暴露 password_hash 等内部字段。
    return await AuthService(session).login(payload)


@router.get("/me", response_model=UserRead)
async def get_me(current_user: Annotated[User, Depends(get_current_user)]) -> User:
    # 返回当前登录用户，供前端刷新页面后恢复登录态。
    return current_user
