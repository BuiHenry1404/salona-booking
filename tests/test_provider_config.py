"""Cấu hình provider cho `build_chat_model` — đường duy nhất dựng model chat.

Không test gọi AI thật: chỉ kiểm cấu hình được dựng đúng, lỗi nêu đúng TÊN biến
còn thiếu, và không bao giờ in giá trị secret.
"""
import pytest
from pydantic import SecretStr

from app.core.config import settings

FAKE_KEY = SecretStr("test-key-khong-dung-that")
BASE_URL = "http://localhost:9999/v1"


@pytest.fixture
def azure_ready(monkeypatch):
    monkeypatch.setattr(settings, "llm_provider", "azure")
    monkeypatch.setattr(settings, "azure_openai_api_key", FAKE_KEY)
    monkeypatch.setattr(settings, "azure_openai_endpoint", "https://example.openai.azure.com")
    monkeypatch.setattr(settings, "azure_openai_deployment", "dep")


@pytest.fixture
def compat_ready(monkeypatch):
    monkeypatch.setattr(settings, "llm_provider", "openai_compatible")
    monkeypatch.setattr(settings, "openai_compatible_base_url", BASE_URL)
    monkeypatch.setattr(settings, "openai_compatible_api_key", FAKE_KEY)
    monkeypatch.setattr(settings, "openai_compatible_model", "fake-model")


def _trivial_tool():
    return [{
        "type": "function",
        "function": {
            "name": "get_shop_status",
            "description": "Trạng thái tiệm",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    }]


# ---------------------------------------------------------------------------
# Azure backward compatibility
# ---------------------------------------------------------------------------

def test_azure_provider_still_builds_azure_model(azure_ready):
    from langchain_openai import AzureChatOpenAI

    from app.agents.llm import build_chat_model

    model = build_chat_model(tags=["supervisor"], temperature=0.0)
    assert isinstance(model, AzureChatOpenAI)
    assert model.streaming is True
    assert "supervisor" in (model.tags or [])
    assert model.bind_tools(_trivial_tool()) is not None


def test_azure_missing_config_names_the_missing_vars(monkeypatch):
    from app.agents.llm import build_chat_model

    monkeypatch.setattr(settings, "llm_provider", "azure")
    monkeypatch.setattr(settings, "azure_openai_api_key", None)
    monkeypatch.setattr(settings, "azure_openai_endpoint", None)

    with pytest.raises(RuntimeError) as exc:
        build_chat_model()
    msg = str(exc.value)
    assert "AZURE_OPENAI_API_KEY" in msg
    assert "AZURE_OPENAI_ENDPOINT" in msg


# ---------------------------------------------------------------------------
# OpenAI-compatible
# ---------------------------------------------------------------------------

def test_openai_compatible_builds_chatopenai_pointing_at_the_base_url(compat_ready):
    from langchain_openai import ChatOpenAI

    from app.agents.llm import build_chat_model

    model = build_chat_model(tags=["respond"], temperature=0.0, streaming=False)
    assert isinstance(model, ChatOpenAI)
    assert model.openai_api_base == BASE_URL
    assert model.model_name == "fake-model"
    # Key được set nhưng không bao giờ in ra.
    assert model.openai_api_key is not None
    assert model.streaming is False
    assert "respond" in (model.tags or [])


def test_openai_compatible_supports_tool_binding(compat_ready):
    from app.agents.llm import build_chat_model

    model = build_chat_model()
    bound = model.bind_tools(_trivial_tool())
    assert bound is not model  # đã bọc tool schema vào runnable


def test_openai_compatible_missing_config_lists_only_missing_names(monkeypatch):
    from app.agents.llm import build_chat_model

    monkeypatch.setattr(settings, "llm_provider", "openai_compatible")
    monkeypatch.setattr(settings, "openai_compatible_base_url", BASE_URL)
    monkeypatch.setattr(settings, "openai_compatible_api_key", None)
    monkeypatch.setattr(settings, "openai_compatible_model", None)

    with pytest.raises(RuntimeError) as exc:
        build_chat_model()
    msg = str(exc.value)
    assert "OPENAI_COMPATIBLE_API_KEY" in msg
    assert "OPENAI_COMPATIBLE_MODEL" in msg
    # biến CÓ sẵn thì không bị nêu là thiếu
    assert "OPENAI_COMPATIBLE_BASE_URL" not in msg


# ---------------------------------------------------------------------------
# Lựa chọn provider
# ---------------------------------------------------------------------------

def test_unsupported_provider_fails_with_a_clear_message(monkeypatch):
    from app.agents.llm import build_chat_model

    monkeypatch.setattr(settings, "llm_provider", "anthropic")
    with pytest.raises(RuntimeError) as exc:
        build_chat_model()
    assert "anthropic" in str(exc.value)


def test_new_settings_default_to_none():
    """Trường mới phải là Optional — máy không cấu hình vẫn khởi động app."""
    from app.core.config import Settings

    assert Settings.model_fields["openai_compatible_base_url"].default is None
    assert Settings.model_fields["openai_compatible_api_key"].default is None
    assert Settings.model_fields["openai_compatible_model"].default is None
