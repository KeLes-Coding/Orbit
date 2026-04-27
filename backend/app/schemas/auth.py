from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, EmailStr, Field


class UserCreate(BaseModel):
    # 注册请求：邮箱作为登录标识，密码只用于服务端生成哈希。
    email: EmailStr
    password: str = Field(min_length=8, max_length=128)
    display_name: str | None = Field(default=None, max_length=100)


class UserLogin(BaseModel):
    # 登录请求：错误响应不区分邮箱不存在和密码错误。
    email: EmailStr
    password: str = Field(min_length=1, max_length=128)


class UserRead(BaseModel):
    # 对外用户资料，不包含 password_hash、metadata 等内部字段。
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    email: EmailStr
    display_name: str | None
    avatar_url: str | None
    is_enabled: bool
    created_at: datetime
    updated_at: datetime


class AuthToken(BaseModel):
    # 登录成功响应：access_token 用于后续 Bearer 鉴权。
    access_token: str
    token_type: str = "bearer"
    user: UserRead
