from app.core.config import settings


def test_booking_defaults():
    assert settings.booking_slot_minutes == 60
    assert settings.timezone == "Asia/Ho_Chi_Minh"


def test_optional_integrations_default_to_none():
    assert settings.telegram_bot_token is None
    assert settings.langfuse_public_key is None
