from typing import Annotated

from fastapi import Depends, HTTPException, Query, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import verify_access_token
from app.db.session import get_db_session
from app.models.user import User
from app.repositories.user import UserRepository


bearer_scheme = HTTPBearer(auto_error=False)


async def _resolve_user_from_token(
    token: str,
    session: AsyncSession,
) -> User:
    user_id = verify_access_token(token)
    if user_id is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
        )
    user = await UserRepository(session).get_by_id(user_id)
    if user is None or not user.is_enabled:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User not found or disabled",
        )
    return user


async def get_current_user(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer_scheme)],
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> User:
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing credentials",
        )

    return await _resolve_user_from_token(credentials.credentials, session)


async def get_current_user_allow_query(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer_scheme)],
    session: Annotated[AsyncSession, Depends(get_db_session)],
    token: Annotated[str | None, Query()] = None,
) -> User:
    # 优先用 Header，兜底用 query param —— 浏览器 <img> 标签不走 fetch，无法带 Header。
    if credentials and credentials.scheme.lower() == "bearer":
        return await _resolve_user_from_token(credentials.credentials, session)
    if token:
        return await _resolve_user_from_token(token, session)
    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Missing credentials",
    )
