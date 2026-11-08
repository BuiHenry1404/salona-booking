"""Task 14: `conversation_sessions` — bản nén theo NGÀY, nguồn cho "Lần trước".

Không gọi LLM thật: `_compact` được test qua model giả (xem
`tests/test_conversation_state_service.py` cho pattern `patch_model`).
"""
import asyncio
from datetime import date, datetime, timedelta, timezone

import pytest

from app.core.clock import now_utc, to_local
from app.models.conversation import ConversationSlots, StateDigest
from app.repositories.conversation_session import ConversationSessionRepository
from app.services.conversation import ConversationService
from app.services.conversation_state import (SESSION_TTL_DAYS, StateOutput,
                                             ConversationStateService)

pytestmark = pytest.mark.asyncio


class FakeStructured:
    def __init__(self, reply):
        self.reply = reply
        self.prompts = []

    async def ainvoke(self, prompt):
        self.prompts.append(prompt)
        return self.reply


class FakeModel:
    def __init__(self, structured):
        self.structured = structured
        self.kwargs = None

    def with_structured_output(self, schema):
        return self.structured


@pytest.fixture
def patch_model(monkeypatch):
    def _install(reply):
        model = FakeModel(FakeStructured(reply))

        def _build(**kwargs):
            model.kwargs = kwargs
            return model
        monkeypatch.setattr("app.services.conversation_state.build_chat_model", _build)
        return model
    return _install


LONG = ("câu khá dài để tốn token " * 8).strip()


async def _seed(db, turns, text=LONG, user_id="u1"):
    svc = ConversationService(db)
    offset = len(await svc._all_messages(user_id))
    for j in range(turns):
        i = offset + j
        await svc.append(user_id, "user", f"[{i}] {text}")
        await asyncio.sleep(0.002)
        await svc.append(user_id, "assistant", f"[{i}] đáp {text}")
        await asyncio.sleep(0.002)
    return await svc._all_messages(user_id)


# --- Repository ---------------------------------------------------------

async def test_upsert_then_read_round_trips(test_db):
    repo = ConversationSessionRepository(test_db)
    digest = StateDigest(wants=["làm tóc"], open=["chưa chọn màu"])
    slots = ConversationSlots(intent="book")
    await repo.upsert(
        user_id="u1", day=date(2026, 10, 20), digest=digest, slots=slots,
        expires_at=datetime(2026, 11, 19, tzinfo=timezone.utc),
    )
    found = await repo.most_recent_before("u1", date(2026, 10, 26), within_days=7)
    assert found is not None
    assert found.day == date(2026, 10, 20)
    assert found.digest.wants == ["làm tóc"]
    assert found.slots.intent == "book"


async def test_upsert_twice_same_day_overwrites_not_duplicates(test_db):
    repo = ConversationSessionRepository(test_db)
    await repo.upsert(user_id="u1", day=date(2026, 10, 20), digest=StateDigest(wants=["a"]),
                      slots=None, expires_at=datetime(2026, 11, 19, tzinfo=timezone.utc))
    await repo.upsert(user_id="u1", day=date(2026, 10, 20), digest=StateDigest(wants=["b"]),
                      slots=None, expires_at=datetime(2026, 11, 19, tzinfo=timezone.utc))
    assert await test_db["conversation_sessions"].count_documents({"user_id": "u1"}) == 1
    found = await repo.most_recent_before("u1", date(2026, 10, 26), within_days=7)
    assert found.digest.wants == ["b"]


async def test_most_recent_before_never_returns_today(test_db):
    repo = ConversationSessionRepository(test_db)
    today = date(2026, 10, 26)
    await repo.upsert(user_id="u1", day=today, digest=StateDigest(wants=["hôm nay"]),
                      slots=None, expires_at=datetime(2026, 11, 25, tzinfo=timezone.utc))
    assert await repo.most_recent_before("u1", today, within_days=7) is None


async def test_most_recent_before_ignores_sessions_older_than_window(test_db):
    repo = ConversationSessionRepository(test_db)
    today = date(2026, 10, 26)
    await repo.upsert(user_id="u1", day=today - timedelta(days=10),
                      digest=StateDigest(wants=["quá cũ"]), slots=None,
                      expires_at=datetime(2026, 12, 25, tzinfo=timezone.utc))
    assert await repo.most_recent_before("u1", today, within_days=7) is None


async def test_most_recent_before_picks_newest_within_window(test_db):
    repo = ConversationSessionRepository(test_db)
    today = date(2026, 10, 26)
    await repo.upsert(user_id="u1", day=today - timedelta(days=5),
                      digest=StateDigest(wants=["cũ hơn"]), slots=None,
                      expires_at=datetime(2026, 11, 20, tzinfo=timezone.utc))
    await repo.upsert(user_id="u1", day=today - timedelta(days=2),
                      digest=StateDigest(wants=["mới hơn"]), slots=None,
                      expires_at=datetime(2026, 11, 23, tzinfo=timezone.utc))
    found = await repo.most_recent_before("u1", today, within_days=7)
    assert found.digest.wants == ["mới hơn"]


# --- `_compact` upserts today's session ----------------------------------

async def test_compaction_upserts_todays_session(test_db, patch_model):
    patch_model(StateOutput(digest=StateDigest(wants=["làm tóc"], open=["chưa chọn giờ"]),
                            slots=ConversationSlots(intent="book")))
    await _seed(test_db, 12)
    svc = ConversationStateService(test_db)

    assert await svc.maybe_compact("u1") is True

    today = to_local(now_utc()).date()
    session = await ConversationSessionRepository(test_db).most_recent_before(
        "u1", today + timedelta(days=1), within_days=1
    )
    assert session is not None
    assert session.day == today
    assert session.digest.wants == ["làm tóc"]
    assert session.slots.intent == "book"
    # TTL ~30 ngày kể từ lượt nén.
    assert (session.expires_at - now_utc()) > timedelta(days=SESSION_TTL_DAYS - 1)


async def test_compaction_overwrites_same_days_session_not_duplicate(test_db, patch_model):
    patch_model(StateOutput(digest=StateDigest(wants=["lần 1"])))
    await _seed(test_db, 12)
    svc = ConversationStateService(test_db)
    await svc.maybe_compact("u1")

    patch_model(StateOutput(digest=StateDigest(wants=["lần 2"])))
    await _seed(test_db, 12)
    await svc.maybe_compact("u1")

    assert await test_db["conversation_sessions"].count_documents({"user_id": "u1"}) == 1


# --- `last_session` rule (service layer, mirrors context.py's gate) ------

async def test_last_session_returns_none_without_prior_day(test_db):
    assert await ConversationStateService(test_db).last_session("u1") is None


async def test_last_session_finds_session_from_yesterday(test_db):
    repo = ConversationSessionRepository(test_db)
    now = now_utc()
    yesterday = to_local(now).date() - timedelta(days=1)
    await repo.upsert(user_id="u1", day=yesterday, digest=StateDigest(wants=["làm tóc"]),
                      slots=ConversationSlots(intent="book"), expires_at=now + timedelta(days=30))
    found = await ConversationStateService(test_db).last_session("u1", now=now)
    assert found is not None
    assert found.day == yesterday


# --- load_context gating ("Lần trước" chỉ hiện khi còn ý định dang dở) ----
#
# Task 6.3 (M6-cutover, phát hiện ngoài danh sách file của kế hoạch): bốn test
# ở đây từng gọi `load_context` của gói supervisor graph cũ (đã xoá) để kiểm
# dòng "Lần trước" trong `context_block`.
# `app.agents.booking.context` (gói mới, Task 5.x) KHÔNG có hàm tương đương —
# `load_turn_context`/`render_context` của gói mới không đọc
# `ConversationSessionRepository` và không còn dòng "Lần trước" nào (chỉ còn
# "Recent visits: ..." bằng tiếng Anh, khác hẳn cách trình bày). Tính năng
# "nhắc ý định dang dở của hôm qua" vì vậy CHƯA được dời sang gói mới — đây là
# một khoảng trống tính năng thật (không phải lỗi xoá nhầm của task này), nên
# bốn test xoá ở đây thay vì "move" (test policy §4 không áp dụng: không có gì
# để move/re-import tới, hàm bị kiểm không còn chạy trên traffic thật). Xem
# báo cáo Task 1 (2026-11-08-orchestrator-speaks) để theo dõi việc dời tính
# năng này sang gói mới ở một task sau.


async def test_short_session_without_compaction_still_saves_episodic_memory_preserving_digest(test_db):
    """Phiên ngắn (< 800 tokens, chưa chạy _compact) khi gọi record_tool_facts
    vẫn lưu bản conversation_sessions của hôm nay và không xoá digest đã có."""
    repo = ConversationSessionRepository(test_db)
    svc = ConversationStateService(test_db)

    today = to_local(now_utc()).date()
    booking_day = (today + timedelta(days=1)).isoformat()
    await svc.record_tool_facts("u1", intent="book", day=booking_day, service="nhuộm tóc")

    session = await repo.most_recent_before("u1", today + timedelta(days=1), within_days=1)
    assert session is not None
    assert session.slots.intent == "book"
    assert session.slots.service == "nhuộm tóc"
    assert session.slots.day == booking_day

