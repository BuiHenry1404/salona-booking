import asyncio

import pytest

from app.services.dedupe import MessageDedupe

pytestmark = pytest.mark.asyncio


class FakeRedis:
    """Fake tối giản thay cho client Redis thật — chỉ ba thao tác dùng tới,
    không dùng fakeredis (brief cấm) để khỏi kéo thêm phụ thuộc test-only."""

    def __init__(self):
        self.store: dict[str, str] = {}

    async def set(self, name, value, nx=False, ex=None):
        if nx and name in self.store:
            return None
        self.store[name] = value
        return True

    async def get(self, name):
        return self.store.get(name)

    async def delete(self, name):
        self.store.pop(name, None)


class BrokenRedis:
    """Mọi thao tác ném lỗi — mô phỏng Redis mất kết nối giữa chừng."""

    async def set(self, *a, **kw):
        raise ConnectionError("redis down")

    async def get(self, *a, **kw):
        raise ConnectionError("redis down")

    async def delete(self, *a, **kw):
        raise ConnectionError("redis down")


async def test_first_message_is_new_then_running_then_done(test_db):
    dedupe = MessageDedupe(test_db, FakeRedis())

    assert await dedupe.begin("u1", "m1") == "new"
    assert await dedupe.begin("u1", "m1") == "running"

    await dedupe.finish("u1", "m1", {"answer": "dạ"})
    assert await dedupe.begin("u1", "m1") == {"answer": "dạ"}


async def test_same_message_id_while_running_is_ignored(test_db):
    dedupe = MessageDedupe(test_db, FakeRedis())

    assert await dedupe.begin("u1", "m1") == "new"
    # Vẫn "running" dù gọi lại nhiều lần — không mở thêm lượt nào khác.
    assert await dedupe.begin("u1", "m1") == "running"
    assert await dedupe.begin("u1", "m1") == "running"


async def test_finished_message_replays_the_complete_payload(test_db):
    dedupe = MessageDedupe(test_db, FakeRedis())
    payload = {"answer": "Dạ được ạ", "proposal": None}

    await dedupe.begin("u1", "m1")
    await dedupe.finish("u1", "m1", payload)

    assert await dedupe.begin("u1", "m1") == payload
    # Gọi lại nhiều lần vẫn replay đúng payload, không coi là "running".
    assert await dedupe.begin("u1", "m1") == payload


async def test_redis_error_falls_back_to_mongo(test_db):
    dedupe = MessageDedupe(test_db, BrokenRedis())

    # Kết quả giống hệt kịch bản chạy thẳng Mongo (không có Redis).
    assert await dedupe.begin("u1", "m1") == "new"
    assert await dedupe.begin("u1", "m1") == "running"

    await dedupe.finish("u1", "m1", {"answer": "ok"})
    assert await dedupe.begin("u1", "m1") == {"answer": "ok"}


async def test_missing_message_id_never_dedupes(test_db):
    dedupe = MessageDedupe(test_db, FakeRedis())

    assert await dedupe.begin("u1", None) == "new"
    assert await dedupe.begin("u1", None) == "new"
    assert await dedupe.begin("u1", "") == "new"

    # Không có gì được lưu — finish/abandon với message_id rỗng cũng là no-op.
    await dedupe.finish("u1", None, {"answer": "x"})
    await dedupe.abandon("u1", "")
    assert await test_db["message_inbox"].count_documents({}) == 0


async def test_abandon_frees_the_running_slot_for_a_retry(test_db):
    dedupe = MessageDedupe(test_db, FakeRedis())

    assert await dedupe.begin("u1", "m1") == "new"
    await dedupe.abandon("u1", "m1")
    assert await dedupe.begin("u1", "m1") == "new"


async def test_mongo_backend_alone_behaves_the_same_without_redis(test_db):
    """Không cấu hình Redis (redis_client=None) — đường mặc định ở dev."""
    dedupe = MessageDedupe(test_db, None)

    assert await dedupe.begin("u1", "m1") == "new"
    assert await dedupe.begin("u1", "m1") == "running"
    await dedupe.finish("u1", "m1", {"answer": "ok"})
    assert await dedupe.begin("u1", "m1") == {"answer": "ok"}
