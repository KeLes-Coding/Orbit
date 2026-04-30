from functools import lru_cache

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # 统一使用 ORBIT_ 前缀读取环境变量，避免和系统变量或其他项目变量冲突。
    model_config = SettingsConfigDict(env_prefix="ORBIT_", env_file=".env", extra="ignore")

    app_name: str = "Orbit Backend"
    env: str = "local"
    api_prefix: str = "/api/v1"
    database_url: str = "postgresql+asyncpg://postgres:postgres@127.0.0.1:5432/orbit"
    auth_secret_key: str = "orbit-local-dev-secret-change-me-32-bytes-min"
    encryption_secret_key: str = "orbit-local-encryption-secret"
    access_token_expire_minutes: int = 60 * 24 * 7
    # 会话标题生成可使用独立小模型；未配置时会退回使用当前会话模型配置。
    title_provider: str | None = None
    title_model: str | None = None
    title_base_url: str | None = None
    title_api_key: str | None = None
    # 标题生成在首条消息链路上同步执行，超时要短，避免拖慢用户发起对话。
    title_timeout_seconds: float = 10
    title_max_chars: int = 48
    # LLM 调试日志默认关闭；排查 provider 返回结构时再打开，避免日常日志泄露对话内容。
    llm_debug_logging: bool = False
    llm_debug_max_chars: int = 4000
    llm_debug_log_path: str = "logs/llm-debug.log"
    cors_origins: list[str] = Field(
        default_factory=lambda: ["http://localhost:5173", "http://127.0.0.1:5173"]
    )

    @field_validator("cors_origins", mode="before")
    @classmethod
    def parse_cors_origins(cls, value: object) -> object:
        # .env 中用逗号分隔多个前端地址，例如 http://localhost:5173,http://127.0.0.1:5173。
        if isinstance(value, str):
            return [item.strip() for item in value.split(",") if item.strip()]
        return value


@lru_cache
def get_settings() -> Settings:
    # 配置对象进程内只初始化一次，避免每次依赖注入都重复读取环境变量。
    return Settings()


settings = get_settings()
