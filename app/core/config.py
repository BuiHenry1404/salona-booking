from typing import Literal, Union, Optional
from pydantic import SecretStr, field_validator, AnyUrl
from pydantic_settings import BaseSettings, PydanticBaseSettingsSource

# File override chỉ tồn tại bên trong container — docker-compose mount vào đây.
# Trên máy dev không có file này nên pydantic bỏ qua, không lỗi.
DOCKER_ENV_FILE = "/run/config/env.docker"


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
    refresh_token_days: int
    # Prod chạy HTTPS thì BẮT BUỘC true. Dev chạy HTTP nên phải tắt được,
    # vì trình duyệt không gửi cookie Secure qua http://.
    cookie_secure: bool
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
    booking_max_per_hour: int
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

    # Hai file, file sau thắng file trước. `.env` là cấu hình gốc;
    # `DOCKER_ENV_FILE` chỉ đè vài khoá khác biệt khi chạy trong container
    # (host của Mongo chẳng hạn), nên không phải chép cả file làm hai bản.
    model_config = {
        "env_file": (".env", DOCKER_ENV_FILE),
        "case_sensitive": False
    }

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls,
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        """Cấu hình CHỈ đến từ file .env — biến môi trường của shell bị bỏ qua.

        Mặc định của pydantic-settings là biến môi trường thắng file. Trên máy
        dev, biến `AZURE_OPENAI_*` được export sẵn ở shell (từ dự án khác) đã
        âm thầm đè lên `.env`: sửa file không thấy tác dụng gì, mà cũng không
        có lỗi nào để lần ra. Một nguồn cấu hình thì đọc file là biết đang
        chạy với gì.

        Đánh đổi: không còn đặt cấu hình bằng `docker run -e` hay biến của CI
        được nữa. Chỗ nào cần khác `.env` thì ghi vào `DOCKER_ENV_FILE`.
        """
        return (init_settings, dotenv_settings)


settings = Settings()
