from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.user import User


class UserRepository:
    # Repository 只封装 users 表查询和写入，不处理密码或登录业务。
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_by_id(self, user_id: UUID) -> User | None:
        # 只返回未归档用户，软删除账号不再参与鉴权和业务查询。
        statement = select(User).where(User.id == user_id, User.archived_at.is_(None))
        result = await self.session.execute(statement)
        return result.scalar_one_or_none()

    async def get_by_email(self, email: str) -> User | None:
        # 邮箱登录大小写不敏感，和数据库的 lower(email) 唯一索引保持一致。
        statement = select(User).where(
            func.lower(User.email) == email.lower(),
            User.archived_at.is_(None),
        )
        result = await self.session.execute(statement)
        return result.scalar_one_or_none()

    async def create(
        self,
        *,
        email: str,
        password_hash: str,
        display_name: str | None,
    ) -> User:
        # 邮箱统一保存为小写，和大小写不敏感的唯一索引保持一致。
        user = User(
            email=email.lower(),
            password_hash=password_hash,
            display_name=display_name,
        )
        self.session.add(user)
        await self.session.flush()
        await self.session.refresh(user)
        return user
