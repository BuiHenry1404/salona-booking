from app.core.config import settings


def test_booking_defaults():
    assert settings.booking_slot_minutes == 60
    assert settings.timezone == "Asia/Ho_Chi_Minh"


def test_optional_integrations_default_to_none():
    assert settings.telegram_bot_token is None
    assert settings.langfuse_public_key is None


def test_shell_environment_cannot_override_the_env_file(monkeypatch):
    """Biến môi trường của shell bị bỏ qua hoàn toàn.

    Máy dev hay export sẵn AZURE_OPENAI_* từ dự án khác. Mặc định của
    pydantic-settings là biến môi trường thắng file, nên sửa .env sẽ không
    thấy tác dụng gì mà cũng chẳng có lỗi nào để lần ra.
    """
    from app.core.config import Settings

    monkeypatch.setenv("APP_NAME", "bi-shell-de-len")
    monkeypatch.setenv("AZURE_OPENAI_ENDPOINT", "https://khong-phai-cai-nay")

    fresh = Settings()

    assert fresh.app_name != "bi-shell-de-len"
    assert fresh.azure_openai_endpoint != "https://khong-phai-cai-nay"
