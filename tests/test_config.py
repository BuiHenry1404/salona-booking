from app.core.config import settings


def test_booking_defaults():
    assert settings.booking_slot_minutes == 60
    assert settings.timezone == "Asia/Ho_Chi_Minh"


def test_optional_integrations_default_to_none():
    """Kiểm mặc định của trường, không kiểm `.env` của máy đang chạy.

    Đọc `settings` ở đây thì bật Langfuse lên là test đỏ — một tính năng hợp lệ
    không được làm hỏng suite.
    """
    from app.core.config import Settings

    assert Settings.model_fields["telegram_bot_token"].default is None
    assert Settings.model_fields["langfuse_public_key"].default is None


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


def test_orchestrator_settings_have_defaults():
    # Kiểm mặc định của TRƯỜNG (như test_optional_integrations_default_to_none), không đọc .env máy đang chạy
    from app.core.config import Settings
    d = {name: f.default for name, f in Settings.model_fields.items() if name.startswith("orch_")}
    assert d == {"orch_orchestrate_timeout_seconds": 110,
                 "orch_task_timeout_seconds": 40, "orch_llm_calls_per_user_per_hour": 150, "orch_ledger_ttl_days": 7}


def test_summary_keep_must_cover_one_whole_turn():
    """Ruling R11: phần giữ của `SummarizationMiddleware` phải lớn hơn số message
    tối đa của một lượt, không thì bản tóm tắt cắt vào lượt đang chạy."""
    import pytest
    from pydantic import ValidationError

    from app.core.config import Settings

    with pytest.raises(ValidationError, match="salon_summary_keep_messages"):
        Settings(salon_summary_keep_messages=10)
    assert Settings(salon_summary_keep_messages=36).salon_summary_keep_messages == 36
