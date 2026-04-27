from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class LLMConfigCreate(BaseModel):
    # 创建模型配置时允许传入明文 api_key，服务层会加密后再入库。
    name: str = Field(min_length=1, max_length=100)
    provider: str = Field(min_length=1, max_length=50)
    model: str = Field(min_length=1, max_length=120)
    base_url: str | None = None
    api_key: str | None = Field(default=None, max_length=4096)
    provider_options: dict = Field(default_factory=dict)
    is_default: bool = False


class LLMConfigUpdate(BaseModel):
    # 更新请求全部字段可选；只修改前端显式传入的字段。
    name: str | None = Field(default=None, min_length=1, max_length=100)
    provider: str | None = Field(default=None, min_length=1, max_length=50)
    model: str | None = Field(default=None, min_length=1, max_length=120)
    base_url: str | None = None
    api_key: str | None = Field(default=None, max_length=4096)
    provider_options: dict | None = None
    is_enabled: bool | None = None
    is_default: bool | None = None


class LLMConfigRead(BaseModel):
    # 对外响应不返回 API Key 密文，只用 has_api_key 告诉前端是否已配置。
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    user_id: UUID
    name: str
    provider: str
    model: str
    base_url: str | None
    provider_options: dict
    is_default: bool
    is_enabled: bool
    has_api_key: bool = False
    created_at: datetime
    updated_at: datetime
