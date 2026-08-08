# Task 2 · Memory adapter bọc Mem0

> Thuộc plan [Agent, memory và streaming](README.md). **Đọc [Ràng buộc toàn cục](README.md#ràng-buộc-toàn-cục) trước khi bắt đầu** — chúng áp cho mọi task, kể cả khi không nhắc lại ở đây.

**Files:**
- Create: `app/memory/__init__.py`, `app/memory/config.py`, `app/memory/adapter.py`, `tests/test_memory_adapter.py`
- Modify: `main.py`

**Interfaces:**
- Consumes: `settings` (Plan 1 task 1)
- Produces:
  - `app/memory/adapter.py`: `recall(user_id: str, query: str, limit: int = 5) -> list[str]`, `remember(user_id: str, messages: list[dict]) -> None`, `is_enabled() -> bool`, `reset_client_for_tests() -> None`
  - `app/memory/config.py`: `build_config() -> dict`, `verify_embedding_dims() -> None`

- [ ] **Step 1: Viết test (sẽ fail)**

Tạo `tests/test_memory_adapter.py`:

```python
import asyncio

import pytest

from app.memory import adapter


class FakeMem0:
    """Giả lập Mem0 đồng bộ, bám SÁT chữ ký thật.

    Cố tình khắt khe: `search` chỉ nhận keyword sau `query`, đòi `filters` và
    dùng `top_k`. Mem0 đã đổi API — truyền `user_id`/`limit` ở top level sẽ ném
    ValueError, mà adapter thì fail-soft nuốt hết mọi Exception. Fake dễ dãi
    nghĩa là test xanh trong khi production im lặng không nhớ được gì.
    """

    def __init__(self):
        self.store: dict[str, list[str]] = {}
        self.add_calls = 0

    def search(self, query, *, filters=None, top_k=20, **kwargs):
        user_id = (filters or {}).get("user_id")
        if not user_id:
            raise ValueError(
                "filters must contain at least one of: user_id, agent_id, run_id."
            )
        return {"results": [{"memory": m} for m in self.store.get(user_id, [])[:top_k]]}

    def add(self, messages, *, user_id=None, **kwargs):
        # `add` VẪN nhận user_id ở top level — chỉ `search`/`get_all` đổi.
        self.add_calls += 1
        self.store.setdefault(user_id, []).append(str(messages))


@pytest.fixture
def fake(monkeypatch):
    client = FakeMem0()
    monkeypatch.setattr(adapter, "_get_client", lambda: client)
    return client


@pytest.mark.asyncio
async def test_recall_returns_only_that_users_memories(fake):
    fake.store["userA"] = ["cô Lan thích buổi sáng"]
    fake.store["userB"] = ["chú Bảy thích buổi chiều"]

    assert await adapter.recall("userA", "khi nào") == ["cô Lan thích buổi sáng"]
    assert await adapter.recall("userB", "khi nào") == ["chú Bảy thích buổi chiều"]


@pytest.mark.asyncio
async def test_search_is_called_the_way_mem0_expects(monkeypatch):
    """Ghim đúng chữ ký của Mem0 hiện tại.

    `user_id` phải nằm trong `filters`, số lượng là `top_k`. Gọi kiểu cũ
    (`user_id=`, `limit=`) ném ValueError, và fail-soft sẽ biến nó thành
    "không nhớ được gì" chứ không thành lỗi nhìn thấy được.
    """
    seen = {}

    class Recorder:
        def search(self, query, *, filters=None, top_k=20, **kwargs):
            seen.update(query=query, filters=filters, top_k=top_k)
            return {"results": []}

    monkeypatch.setattr(adapter, "_get_client", lambda: Recorder())
    await adapter.recall("userA", "mai rảnh không", limit=3)

    assert seen == {"query": "mai rảnh không", "filters": {"user_id": "userA"}, "top_k": 3}


@pytest.mark.asyncio
async def test_memory_never_leaks_between_users(fake):
    fake.store["userA"] = ["bí mật của A"]
    assert "bí mật của A" not in await adapter.recall("userB", "gì đó")


@pytest.mark.asyncio
async def test_recall_returns_empty_when_backend_raises(monkeypatch):
    class Broken:
        def search(self, *a, **kw):
            raise RuntimeError("pgvector chết")

    monkeypatch.setattr(adapter, "_get_client", lambda: Broken())
    assert await adapter.recall("userA", "gì đó") == []


@pytest.mark.asyncio
async def test_recall_returns_empty_when_backend_is_slow(monkeypatch):
    class Slow:
        def search(self, *a, **kw):
            import time
            time.sleep(3)
            return {"results": [{"memory": "quá muộn"}]}

    monkeypatch.setattr(adapter, "_get_client", lambda: Slow())
    assert await adapter.recall("userA", "gì đó", timeout=0.2) == []


@pytest.mark.asyncio
async def test_remember_swallows_errors(monkeypatch):
    class Broken:
        def add(self, *a, **kw):
            raise RuntimeError("hỏng")

    monkeypatch.setattr(adapter, "_get_client", lambda: Broken())
    await adapter.remember("userA", [{"role": "user", "content": "xin chào"}])  # không được ném


@pytest.mark.asyncio
async def test_remember_passes_user_id_through(fake):
    await adapter.remember("userA", [{"role": "user", "content": "xin chào"}])
    assert fake.add_calls == 1
    assert "userA" in fake.store


def test_embedding_dims_declared_in_both_places():
    """Số chiều phải khai ở CẢ embedder lẫn vector_store, và phải bằng nhau.

    Lệch nhau thì pgvector im lặng nuốt lỗi ghi: API trả về thành công kèm
    memory ID nhưng không có gì được lưu, và không migrate tại chỗ được.
    """
    from app.core.config import settings
    from app.memory.config import build_config

    config = build_config()
    assert config["embedder"]["config"]["embedding_dims"] == settings.embedding_dims
    assert config["vector_store"]["config"]["embedding_model_dims"] == settings.embedding_dims


def test_azure_config_uses_deployment_names_not_model_names(monkeypatch):
    """Trên Azure, `model` được hiểu là tên deployment. Lệch tên → 404
    DeploymentNotFound, mà thông báo lỗi không nói rõ là do chỗ này."""
    from app.core.config import settings
    from app.memory.config import build_config

    monkeypatch.setattr(settings, "azure_openai_embedding_deployment", "embed-prod")
    config = build_config()

    assert config["embedder"]["config"]["model"] == "embed-prod"
    assert config["embedder"]["config"]["azure_kwargs"]["azure_deployment"] == "embed-prod"
    # Deployment của embedder KHÔNG được đè lên deployment của LLM.
    assert config["llm"]["config"]["azure_kwargs"]["azure_deployment"] != "embed-prod"


@pytest.mark.asyncio
async def test_disabled_when_postgres_uri_missing(monkeypatch):
    monkeypatch.setattr(adapter.settings, "postgres_uri", None)
    adapter.reset_client_for_tests()
    assert adapter.is_enabled() is False
    assert await adapter.recall("userA", "gì đó") == []
```

- [ ] **Step 2: Chạy test để xác nhận fail**

Run: `pytest tests/test_memory_adapter.py -v`
Expected: FAIL với `ModuleNotFoundError: No module named 'app.memory'`

- [ ] **Step 3: Viết `app/memory/config.py`**

```python
from urllib.parse import urlparse

from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger(__name__)


def build_config() -> dict:
    """Cấu hình Mem0. App chỉ cung cấp kết nối và số chiều; Mem0 tự tạo và
    quản lý collection trong Postgres — không tự thiết kế bảng."""
    parsed = urlparse(settings.postgres_uri)

    llm_deployment = settings.azure_openai_deployment or settings.azure_openai_model
    embed_deployment = (
        settings.azure_openai_embedding_deployment or settings.azure_openai_embedding_model
    )

    azure_kwargs = {
        "azure_deployment": llm_deployment,
        "azure_endpoint": settings.azure_openai_endpoint,
        "api_key": settings.azure_openai_api_key.get_secret_value(),
        "api_version": settings.azure_openai_api_version,
    }

    return {
        "llm": {
            # Với Azure, `model` phải là TÊN DEPLOYMENT chứ không phải tên model —
            # đây là chỗ tài liệu Mem0 hay bị đọc lướt. Trùng tên thì may, lệch
            # tên thì 404 DeploymentNotFound.
            "provider": "azure_openai",
            "config": {"model": llm_deployment, "azure_kwargs": azure_kwargs},
        },
        "embedder": {
            "provider": "azure_openai",
            "config": {
                "model": embed_deployment,
                # PHẢI khai ở CẢ HAI chỗ. Thiếu ở đây thì Mem0 để
                # `embedding_dims = None` trong khi pgvector đã tạo cột theo
                # `embedding_model_dims` bên dưới — lệch nhau là mất dữ liệu âm thầm.
                "embedding_dims": settings.embedding_dims,
                "azure_kwargs": {**azure_kwargs, "azure_deployment": embed_deployment},
            },
        },
        "vector_store": {
            "provider": "pgvector",
            "config": {
                "host": parsed.hostname,
                "port": parsed.port or 5432,
                "dbname": (parsed.path or "/salon_memory").lstrip("/"),
                "user": parsed.username,
                "password": parsed.password,
                "collection_name": "salon_memories",
                "embedding_model_dims": settings.embedding_dims,
                "hnsw": True,
            },
        },
    }


async def verify_embedding_dims() -> None:
    """Kiểm tra một lần lúc khởi động.

    Nếu embedding_model_dims lệch với số chiều thật, pgvector IM LẶNG nuốt lỗi
    ghi: API trả về thành công kèm memory ID nhưng không có gì được lưu. Không có
    đường migrate tại chỗ — phải xóa volume và tạo lại. Thà log lỗi to lúc khởi
    động còn hơn mất dữ liệu âm thầm nhiều tuần.
    """
    from app.memory.adapter import is_enabled

    if not is_enabled():
        return

    try:
        from mem0.embeddings.configs import EmbedderConfig  # noqa: F401
        from mem0.utils.factory import EmbedderFactory

        cfg = build_config()["embedder"]
        embedder = EmbedderFactory.create(cfg["provider"], cfg["config"], None)
        vector = embedder.embed("kiểm tra số chiều")
        actual = len(vector)
    except Exception as exc:
        logger.warning("embedding_dim_check_skipped", extra={"error": str(exc)})
        return

    if actual != settings.embedding_dims:
        logger.error(
            "EMBEDDING_DIMS_MISMATCH",
            extra={
                "configured": settings.embedding_dims,
                "actual": actual,
                "action": "Sửa EMBEDDING_DIMS trong .env rồi docker compose down -v",
            },
        )
```

- [ ] **Step 4: Viết `app/memory/adapter.py`**

```python
import asyncio
from typing import Any, List, Optional

from app.core.config import settings
from app.core.logging import get_logger
from app.memory.config import build_config

logger = get_logger(__name__)

RECALL_TIMEOUT_SECONDS = 2.0

_client: Optional[Any] = None


def is_enabled() -> bool:
    return bool(settings.postgres_uri and settings.azure_openai_api_key)


def reset_client_for_tests() -> None:
    global _client
    _client = None


def _get_client():
    """Mem0 khởi tạo tốn thời gian nên giữ một instance dùng chung."""
    global _client
    if _client is None:
        from mem0 import Memory
        _client = Memory.from_config(build_config())
    return _client


def _extract(raw: Any) -> List[str]:
    """Mem0 đổi hình dạng trả về giữa các bản: khi là list, khi là {"results": [...]}."""
    items = raw.get("results", []) if isinstance(raw, dict) else (raw or [])
    out = []
    for item in items:
        text = item.get("memory") or item.get("text") if isinstance(item, dict) else str(item)
        if text:
            out.append(text)
    return out


async def recall(
    user_id: str, query: str, limit: int = 5, timeout: float = RECALL_TIMEOUT_SECONDS
) -> List[str]:
    """Memory ngữ nghĩa của đúng user này.

    Fail-soft tuyệt đối: lỗi hoặc chậm thì trả rỗng và chat chạy tiếp. Mất phần
    cá nhân hóa còn hơn khách ngồi chờ hoặc thấy màn hình lỗi.
    """
    if not is_enabled():
        return []

    def _search():
        # Mem0 đã đổi API: entity id phải nằm trong `filters`, và `limit` đổi
        # thành `top_k`. Gọi kiểu cũ ném ValueError — mà nhánh except bên dưới
        # sẽ nuốt mất, nên sai ở đây rất khó phát hiện.
        return _get_client().search(query, filters={"user_id": user_id}, top_k=limit)

    try:
        raw = await asyncio.wait_for(asyncio.to_thread(_search), timeout=timeout)
        return _extract(raw)
    except asyncio.TimeoutError:
        logger.warning("memory_recall_timeout", extra={"user_id": user_id})
        return []
    except (TypeError, ValueError) as exc:
        # Không phải sự cố hạ tầng mà là gọi sai API — thường do Mem0 đổi chữ ký
        # giữa hai bản. Fail-soft vẫn giữ để khách chat được, nhưng log ở mức
        # error để nó không lẫn vào đám warning và trôi qua hàng tuần.
        logger.error(
            "memory_recall_bad_call",
            extra={"user_id": user_id, "error": str(exc),
                   "hint": "Kiểm tra chữ ký search(): filters={'user_id'}, top_k"},
        )
        return []
    except Exception as exc:
        logger.warning("memory_recall_failed", extra={"user_id": user_id, "error": str(exc)})
        return []


async def remember(user_id: str, messages: List[dict]) -> None:
    """Ghi lượt chat vừa rồi. Mem0 tự trích xuất, khử trùng lặp và quyết định
    thêm/cập nhật/xóa.

    Chạy nền sau khi khách đã nhận câu trả lời, nên lượt LLM nội bộ của Mem0
    không cộng vào thời gian chờ. Lỗi thì chỉ log — không ai đang đợi.
    """
    if not is_enabled() or not messages:
        return

    def _add():
        _get_client().add(messages, user_id=user_id)

    try:
        await asyncio.to_thread(_add)
    except Exception as exc:
        logger.warning("memory_remember_failed", extra={"user_id": user_id, "error": str(exc)})
```

- [ ] **Step 5: Tạo `app/memory/__init__.py`**

```python
from app.memory.adapter import is_enabled, recall, remember

__all__ = ["recall", "remember", "is_enabled"]
```

- [ ] **Step 6: Gọi kiểm tra số chiều lúc khởi động**

Trong `main.py`, ngay sau `await ensure_indexes(db)` thêm:

```python
    from app.memory.config import verify_embedding_dims
    await verify_embedding_dims()
```

- [ ] **Step 7: Chạy test để xác nhận pass**

Run: `pytest tests/test_memory_adapter.py -v`
Expected: PASS (10 passed) — quan trọng nhất là `test_search_is_called_the_way_mem0_expects`, `test_memory_never_leaks_between_users` và `test_embedding_dims_declared_in_both_places`

- [ ] **Step 8: Commit**

```bash
git add app/memory main.py tests/test_memory_adapter.py
git commit -m "feat: fail-soft Mem0 adapter with per-user isolation"
```
