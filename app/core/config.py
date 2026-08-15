from typing import Literal, Union, Optional
from pydantic import SecretStr, field_validator, AnyUrl
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # Application
    app_name: str
    env: Literal["dev", "stg", "prod"]
    debug: bool

    # Database
    mongo_uri: AnyUrl
    mongo_db_name: str

    # Security
    jwt_secret: SecretStr
    jwt_algorithm: str
    jwt_expire_minutes: int
    api_key: SecretStr
    login_max_attempts: int
    login_window_seconds: int

    # CORS
    allowed_origins: Union[str, list[str]]

    # Logging
    log_level: str

    # LLM Configuration
    llm_provider: Literal["openai", "azure", "anthropic", "gemini"]

    # Azure OpenAI
    azure_openai_api_key: Optional[SecretStr] = None
    azure_openai_endpoint: Optional[str] = None
    azure_openai_deployment: Optional[str] = None
    azure_openai_model: str
    azure_openai_api_version: str

    # OpenAI
    openai_api_key: Optional[SecretStr] = None
    openai_model: str

    # Anthropic
    anthropic_api_key: Optional[SecretStr] = None
    anthropic_model: str

    # Gemini
    gemini_api_key: Optional[SecretStr] = None
    gemini_model: str

    # Booking
    booking_slot_minutes: int
    timezone: str

    # SĐT tiệm — hiện lên khi máy chủ hỏng để khách còn gọi được người thật.
    # Bắt buộc có giá trị thật trước khi chạy production.
    shop_phone: str

    # Langfuse — để trống thì tắt trace
    langfuse_public_key: Optional[SecretStr] = None
    langfuse_secret_key: Optional[SecretStr] = None
    langfuse_host: Optional[str] = None

    # Telegram — để trống thì không chạy bot
    telegram_bot_token: Optional[SecretStr] = None
    telegram_admin_chat_ids: str

    @field_validator("allowed_origins", mode="before")
    @classmethod
    def parse_cors_origins(cls, v):
        if isinstance(v, str):
            # Split comma-separated string into list
            origins = [origin.strip() for origin in v.split(",") if origin.strip()]
            return origins
        return v if isinstance(v, list) else [str(v)]

    model_config = {
        "env_file": ".env",
        "case_sensitive": False
    }


settings = Settings()
