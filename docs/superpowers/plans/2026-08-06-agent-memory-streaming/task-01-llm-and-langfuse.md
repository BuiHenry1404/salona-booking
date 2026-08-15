# Task 1 · Phụ thuộc và LLM client

> Thuộc plan [Agent, memory và streaming](README.md). **Đọc [Ràng buộc toàn cục](README.md#ràng-buộc-toàn-cục) trước khi bắt đầu** — chúng áp cho mọi task, kể cả khi không nhắc lại ở đây.

**Files:**
- Modify: `requirements.txt`
- Create: `app/agents/llm.py`, `app/core/langfuse.py`, `tests/test_langfuse.py`

**Interfaces:**
- Consumes: `settings` (Plan 1 task 1)
- Produces:
  - `app/agents/llm.py`: `build_chat_model(tags: list[str] | None = None, temperature: float = 0.0, streaming: bool = True) -> AzureChatOpenAI`
  - `app/core/langfuse.py`: `get_callbacks() -> list` — trả về `[]` khi chưa cấu hình, không bao giờ ném lỗi
  - `app/core/langfuse.py`: `get_trace_metadata(user_id, conversation_id) -> dict` — khóa `langfuse_user_id` / `langfuse_session_id` để nhét vào `config["metadata"]`

- [x] **Step 1: Thêm phụ thuộc**

Thêm vào `requirements.txt`:

```
langgraph>=0.2.60
langchain-openai>=0.2.14
langchain-core>=0.3.28
langfuse>=3.0
```

Ghim `>=3.0` chứ không phải `>=2.x`: v3 đổi cả đường import lẫn cách truyền credential, nên viết code đỡ cả hai bản chỉ tạo ra một nhánh không bao giờ được test.

Chạy: `pip install -r requirements.txt`

- [x] **Step 2: Viết test cho Langfuse shim (sẽ fail)**

Tạo `tests/test_langfuse.py`:

```python
from app.core.langfuse import get_callbacks, get_trace_metadata


def test_returns_empty_list_when_not_configured():
    """Thiếu key thì app vẫn chạy, chỉ là không trace."""
    assert get_callbacks() == []


def test_never_raises_even_with_odd_input():
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
```

- [x] **Step 3: Chạy test để xác nhận fail**

Run: `pytest tests/test_langfuse.py -v`
Expected: FAIL với `ModuleNotFoundError: No module named 'app.core.langfuse'`

- [x] **Step 4: Viết `app/core/langfuse.py`**

```python
from typing import Any, Dict, List, Optional

from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger(__name__)

_client_ready = False


def is_enabled() -> bool:
    return bool(settings.langfuse_public_key and settings.langfuse_secret_key)


def _load_handler_class():
    """v3: `langfuse.langchain`. v2 dùng `langfuse.callback` và ĐÃ BỎ."""
    try:
        from langfuse.langchain import CallbackHandler
        return CallbackHandler
    except Exception as exc:
        logger.warning("langfuse_import_failed", extra={"error": str(exc)})
        return None


def _init_client() -> bool:
    """Khởi tạo client MỘT lần. Credential nằm ở đây, không ở handler.

    Đây là điểm khác lớn nhất giữa v2 và v3: v2 truyền public_key/secret_key
    thẳng vào CallbackHandler, v3 thì handler không nhận tham số nào — truyền
    vào sẽ ném TypeError, và vì mọi ngoại lệ đều bị nuốt nên hậu quả là trace
    tắt hẳn mà log chỉ có một dòng warning.
    """
    global _client_ready
    if _client_ready:
        return True
    if not is_enabled():
        return False
    try:
        from langfuse import Langfuse

        Langfuse(
            public_key=settings.langfuse_public_key.get_secret_value(),
            secret_key=settings.langfuse_secret_key.get_secret_value(),
            host=settings.langfuse_host or "https://cloud.langfuse.com",
        )
        _client_ready = True
        return True
    except Exception as exc:
        logger.warning("langfuse_init_failed", extra={"error": str(exc)})
        return False


def get_callbacks() -> List[Any]:
    """Callback cho LangChain/LangGraph. Trả về [] nếu chưa cấu hình.

    Langfuse lỗi không bao giờ được làm hỏng một lượt chat — nuốt mọi ngoại lệ.
    """
    if not _init_client():
        return []

    handler_class = _load_handler_class()
    if handler_class is None:
        return []

    try:
        return [handler_class()]
    except Exception as exc:
        logger.warning("langfuse_handler_failed", extra={"error": str(exc)})
        return []


def get_trace_metadata(
    user_id: Optional[str] = None, conversation_id: Optional[str] = None
) -> Dict[str, str]:
    """Metadata gắn vào `config` của lượt chạy.

    Trong v3, danh tính đi qua metadata chứ không qua handler. Tên khóa phải
    đúng nguyên văn — Langfuse chỉ đọc tiền tố `langfuse_`, sai tên thì trace
    vẫn lên nhưng mất đường lần ngược từ một lịch sai về đúng cuộc hội thoại.
    """
    metadata: Dict[str, str] = {}
    if user_id:
        metadata["langfuse_user_id"] = user_id
    if conversation_id:
        metadata["langfuse_session_id"] = conversation_id
    return metadata
```

- [x] **Step 5: Chạy test để xác nhận pass**

Run: `pytest tests/test_langfuse.py -v`
Expected: PASS (4 passed) — với điều kiện `.env` không có `LANGFUSE_PUBLIC_KEY`

- [x] **Step 6: Viết `app/agents/llm.py`**

```python
from typing import List, Optional

from langchain_openai import AzureChatOpenAI

from app.core.config import settings


def build_chat_model(
    tags: Optional[List[str]] = None,
    temperature: float = 0.0,
    streaming: bool = True,
) -> AzureChatOpenAI:
    """Model cho một node cụ thể.

    `tags` là thứ cho phép lọc sự kiện stream: chỉ node respond được gắn
    tag "respond", và chỉ token mang tag đó mới được đẩy ra màn hình khách.
    Không có tags thì token JSON định tuyến của supervisor sẽ chạy ngang màn hình.

    `streaming=False` dành cho những lượt gọi sinh JSON chứ không sinh câu trả
    lời — parser thời gian ở task 4b là một. Không có gì để hiện dần cho khách xem.
    """
    if not settings.azure_openai_api_key or not settings.azure_openai_endpoint:
        raise RuntimeError("Thiếu AZURE_OPENAI_API_KEY hoặc AZURE_OPENAI_ENDPOINT")

    return AzureChatOpenAI(
        azure_endpoint=settings.azure_openai_endpoint,
        api_key=settings.azure_openai_api_key.get_secret_value(),
        azure_deployment=settings.azure_openai_deployment or settings.azure_openai_model,
        api_version=settings.azure_openai_api_version,
        temperature=temperature,
        tags=tags or [],
        streaming=streaming,
    )
```

- [x] **Step 7: Kiểm tra import sạch**

Run: `python -c "from app.agents.llm import build_chat_model; from app.core.langfuse import get_callbacks; print('ok')"`
Expected: in ra `ok`

- [x] **Step 8: Commit**

```bash
git add requirements.txt app/agents/llm.py app/core/langfuse.py tests/test_langfuse.py
git commit -m "feat: add LangChain Azure model factory and optional Langfuse tracing"
```
