import asyncio
from datetime import timedelta

import pytest

from app.core.clock import now_utc
from app.services.dedupe import TURN_TTL_SECONDS, MessageDedupe, MongoBackend, RedisBackend

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


class RedisThatDiesAfterBegin(FakeRedis):
    """Thành công cho MỌI lời gọi cho tới khi `.die()` được gọi, sau đó ném
    lỗi như `BrokenRedis` — mô phỏng Redis rớt kết nối GIỮA một lượt đang
    chạy (khác `BrokenRedis`: lỗi ngay từ `begin()` đầu tiên)."""

    def __init__(self):
        super().__init__()
        self._dead = False

    def die(self):
        self._dead = True

    async def set(self, *a, **kw):
        if self._dead:
            raise ConnectionError("redis down")
        return await super().set(*a, **kw)

    async def get(self, *a, **kw):
        if self._dead:
            raise ConnectionError("redis down")
        return await super().get(*a, **kw)

    async def delete(self, *a, **kw):
        if self._dead:
            raise ConnectionError("redis down")
        return await super().delete(*a, **kw)


class RecordingRedis(FakeRedis):
    """Ghi lại thứ tự gọi — dùng để khẳng định `begin()` SET NX trước GET."""

    def __init__(self):
        super().__init__()
        self.calls: list[tuple[str, str]] = []

    async def set(self, name, value, nx=False, ex=None):
        self.calls.append(("set", name))
        return await super().set(name, value, nx=nx, ex=ex)

    async def get(self, name):
        self.calls.append(("get", name))
        return await super().get(name)

    async def delete(self, name):
        self.calls.append(("delete", name))
        return await super().delete(name)


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


# --- Review fix 1: Mongo `finish` phải upsert ---------------------------


async def test_redis_dying_mid_turn_does_not_lose_the_finish_payload(test_db):
    """`begin()` thắng trên Redis (Mongo chưa có document nào cho cặp này),
    rồi Redis rớt TRƯỚC `finish()`. `finish()` rơi xuống Mongo nhưng không có
    document "running" nào để khớp — nếu `update_one` không upsert thì đây là
    no-op im lặng, payload mất, và lượt gửi lại kế tiếp (Redis vẫn rớt) sẽ
    `insert_one` thành công, trả "new" thay vì phát lại kết quả đã có."""
    redis_client = RedisThatDiesAfterBegin()
    dedupe = MessageDedupe(test_db, redis_client)

    assert await dedupe.begin("u1", "m1") == "new"
    redis_client.die()

    await dedupe.finish("u1", "m1", {"answer": "done"})

    # Vẫn Redis lỗi -> rơi xuống Mongo -> phải thấy "done", không phải chạy
    # lượt thứ hai.
    assert await dedupe.begin("u1", "m1") == {"answer": "done"}


# --- Review fix 2: document "running" bị rác trong Mongo không kẹt mãi ---


async def test_stale_running_mongo_doc_is_taken_over_after_ttl(test_db):
    """Một document "running" bị crash/restart giữa chừng (hoặc `abandon()`
    lỡ không chạy tới) không được kẹt tới 24h (TTL của document) — quá
    `TURN_TTL_SECONDS` (cùng hằng số với khoá Redis) thì một lượt gửi lại
    phải được coi là lượt MỚI, không bị chặn vĩnh viễn."""
    backend = MongoBackend(test_db)
    stale_at = now_utc() - timedelta(seconds=TURN_TTL_SECONDS + 5)
    await backend.collection.insert_one({
        "user_id": "u1", "message_id": "m1", "status": "running",
        "complete": None, "created_at": stale_at,
        "expires_at": now_utc() + timedelta(hours=24),
    })

    assert await backend.begin("u1", "m1") == "new"
    # Giành lại xong -> created_at được cập nhật thành "vừa nãy" -> một bản
    # gửi lại NGAY SAU ĐÓ là một lượt thật sự đang chạy, không phải rác nữa.
    assert await backend.begin("u1", "m1") == "running"


async def test_fresh_running_mongo_doc_is_not_taken_over(test_db):
    """Chưa quá `TURN_TTL_SECONDS` thì vẫn là "running" bình thường — chỉ
    document THỰC SỰ cũ mới bị giành lại."""
    backend = MongoBackend(test_db)
    assert await backend.begin("u1", "m1") == "new"
    assert await backend.begin("u1", "m1") == "running"


# --- Review fix 3: race SET NX / GET trong RedisBackend.begin ------------


async def test_redis_begin_acquires_the_lock_before_checking_for_a_payload(test_db):
    """Thứ tự cũ (GET msg RỒI MỚI SET NX) có một cửa sổ: bản trùng GET thấy
    rỗng, rồi TRONG LÚC nó còn await, bản gốc kịp `finish()` (set msg + xoá
    turn) — bản trùng SET NX sau đó thấy khoá đã được giải phóng nên THẮNG,
    chạy lượt thứ hai. Khẳng định thứ tự đã đổi: SET NX luôn là thao tác ĐẦU
    TIÊN của `begin()`."""
    client = RecordingRedis()
    backend = RedisBackend(client)

    await backend.begin("u1", "m1")

    assert client.calls[0] == ("set", "turn:u1:m1")


async def test_redis_begin_does_not_race_a_finish_between_its_own_steps(test_db):
    """Hành vi cuối-đến-cuối tương đương kịch bản race: một bản trùng tới
    SAU KHI bản gốc đã `finish()` thì phải thấy kết quả đã xong, một bản trùng
    tới TRONG LÚC bản gốc còn chạy thì phải thấy "running" — không bao giờ cả
    hai cùng "new"."""
    client = FakeRedis()
    backend = RedisBackend(client)

    assert await backend.begin("u1", "m1") == "new"
    # Bản trùng tới trong lúc lượt đầu còn chạy (chưa finish).
    assert await backend.begin("u1", "m1") == "running"

    await backend.finish("u1", "m1", {"answer": "done"})

    # Bản trùng tới SAU khi đã finish — phát lại, không phải "new" nữa.
    assert await backend.begin("u1", "m1") == {"answer": "done"}
