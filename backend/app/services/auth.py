from fastapi import HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import create_access_token, hash_password, verify_password
from app.models.user import User
from app.repositories.user import UserRepository
from app.schemas.auth import AuthToken, UserCreate, UserLogin, UserRead


class AuthService:
    # 认证服务只负责业务编排：校验用户、处理密码、签发访问令牌。
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.users = UserRepository(session)

    async def register(self, payload: UserCreate) -> User:
        # 注册前先按邮箱查重；邮箱唯一性最终仍由数据库索引兜底。
        existing_user = await self.users.get_by_email(str(payload.email))
        if existing_user is not None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="该邮箱已经注册",
            )

        # 数据库只保存密码哈希，不保存用户提交的明文密码。
        user = await self.users.create(
            email=str(payload.email),
            password_hash=hash_password(payload.password),
            display_name=payload.display_name,
        )
        # 用户创建成功后立即提交；登录态仍由用户显式登录时签发。
        await self.session.commit()
        return user

    async def login(self, payload: UserLogin) -> AuthToken:
        # 登录失败统一返回同一错误，避免暴露邮箱是否存在。
        user = await self.users.get_by_email(str(payload.email))
        if user is None or not verify_password(payload.password, user.password_hash):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="邮箱或密码错误",
            )
        # 被禁用的账号不能继续签发新令牌。
        if not user.is_enabled:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="账号已被禁用",
            )

        return self._build_token(user)

    def _build_token(self, user: User) -> AuthToken:
        # 令牌里只放 user_id，用户资料始终从数据库读取，便于禁用账号即时生效。
        return AuthToken(
            access_token=create_access_token(user.id),
            user=UserRead.model_validate(user),
        )
