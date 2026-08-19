import pytest

from app.core.langfuse import get_callbacks, get_trace_metadata


@pytest.fixture
def not_configured(monkeypatch):
    """Dựng trạng thái "chưa cấu hình" thay vì trông vào `.env` của máy.

    Máy dev có thể đang bật Langfuse thật (stack tự dựng ở
    docker-compose.langfuse.yml) — bật một tính năng hợp lệ không được làm
    suite đỏ. `_client_ready` phải reset vì nó nhớ lần khởi tạo trước.
    """
    import app.core.langfuse as mod

    monkeypatch.setattr(mod.settings, "langfuse_public_key", None)
    monkeypatch.setattr(mod.settings, "langfuse_secret_key", None)
    monkeypatch.setattr(mod, "_client_ready", False)


def test_returns_empty_list_when_not_configured(not_configured):
    """Thiếu key thì app vẫn chạy, chỉ là không trace."""
    assert get_callbacks() == []


def test_never_raises_even_with_odd_input(not_configured):
    assert get_callbacks() == []
    assert get_trace_metadata(None, None) == {}


def test_metadata_uses_the_keys_langfuse_actually_reads():
    """Langfuse v3 KHÔNG còn nhận user_id/session_id ở constructor của handler.

    Chúng phải đi qua `config["metadata"]` với đúng tiền tố `langfuse_`. Sai tên
    khóa thì trace vẫn lên nhưng không lần ngược được về khách nào — mà đó chính
    là lý do bật trace.
    """
    assert get_trace_metadata("u1", "c1") == {
        "langfuse_user_id": "u1",
        "langfuse_session_id": "c1",
    }


def test_handler_is_constructed_without_credentials(monkeypatch):
    """v3: credential nằm ở client `Langfuse(...)`, handler không nhận nữa.

    Truyền public_key vào CallbackHandler() sẽ ném TypeError, và nhánh nuốt lỗi
    biến nó thành "không trace gì cả" — hỏng im lặng đúng kiểu khó phát hiện nhất.
    """
    import app.core.langfuse as mod

    seen = {}

    class FakeHandler:
        def __init__(self, **kwargs):
            seen["kwargs"] = kwargs

    monkeypatch.setattr(mod, "is_enabled", lambda: True)
    monkeypatch.setattr(mod, "_init_client", lambda: True)
    monkeypatch.setattr(mod, "_load_handler_class", lambda: FakeHandler)

    assert len(get_callbacks()) == 1
    assert seen["kwargs"] == {}


def test_the_handler_class_can_actually_be_imported():
    """Nạp được CallbackHandler thật, không chỉ nhánh nuốt lỗi.

    `langfuse.langchain` import gói `langchain` chứ không chỉ langchain-core.
    Thiếu nó thì `_load_handler_class` trả None và trace tắt im lặng — có key
    đúng vẫn không thấy trace nào, chỉ một dòng warning trong log.
    """
    from app.core.langfuse import _load_handler_class

    assert _load_handler_class() is not None
