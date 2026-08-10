from typing import Literal, Union, Optional
from pydantic import SecretStr, field_validator, AnyUrl
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # Application
    app_name: str = "chatbot-api"
    env: Literal["dev", "stg", "prod"] = "dev"
    debug: bool = False
    
    # Database
    mongo_uri: AnyUrl
    mongo_db_name: str = "chatbot_db"
    
    # Security
    jwt_secret: SecretStr
    jwt_algorithm: str = "HS256"
    jwt_expire_minutes: int = 30
    api_key: SecretStr
    
    # CORS
    allowed_origins: Union[str, list[str]] = "http://localhost:3000"
    
    # Logging
    log_level: str = "INFO"
    
    # LLM Configuration
    llm_provider: Literal["openai", "azure", "anthropic", "gemini"] = "azure"
    
    # Azure OpenAI
    azure_openai_api_key: Optional[SecretStr] = None
    azure_openai_endpoint: Optional[str] = None
    azure_openai_deployment: Optional[str] = None
    azure_openai_model: str = "gpt-4.1-nano"
    azure_openai_api_version: str = "2025-04-01-preview"
    
    # OpenAI
    openai_api_key: Optional[SecretStr] = None
    openai_model: str = "gpt-4.1-nano"
    
    # Anthropic
    anthropic_api_key: Optional[SecretStr] = None
    anthropic_model: str = "claude-sonnet-4-20250514"
    
    # Gemini
    gemini_api_key: Optional[SecretStr] = None
    gemini_model: str = "gemini-2.5-flash-lite"
    
    # Booking
    booking_slot_minutes: int = 60
    timezone: str = "Asia/Ho_Chi_Minh"

    # SĐT tiệm — hiện lên khi máy chủ hỏng để khách còn gọi được người thật.
    # Bắt buộc có giá trị thật trước khi chạy production.
    shop_phone: str = ""

    # Postgres (memory ngữ nghĩa — dùng ở Plan 2)
    postgres_uri: Optional[str] = None
    embedding_dims: int = 1536
    azure_openai_embedding_model: str = "text-embedding-3-small"
    # Trên Azure, tên deployment do người tạo đặt và thường KHÁC tên model.
    # Để trống thì lấy tên model làm tên deployment.
    azure_openai_embedding_deployment: Optional[str] = None

    # Langfuse — để trống thì tắt trace
    langfuse_public_key: Optional[SecretStr] = None
    langfuse_secret_key: Optional[SecretStr] = None
    langfuse_host: Optional[str] = None

    # Telegram — để trống thì không chạy bot
    telegram_bot_token: Optional[SecretStr] = None
    telegram_admin_chat_ids: str = ""

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