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

async def _make_user(test_db, phone="0912345678"):
    from app.services.auth import AuthService
    return await AuthService(test_db).create_user(phone, "matkhau123", "Cô Lan")


async def test_last_session_line_only_when_intent_left_open(test_db):
    """Có phiên cũ, CÓ `slots.intent` còn dở, KHÔNG có lịch mới sau đó →
    dòng "Lần trước" xuất hiện."""
    from app.agents.booking_graph.context import load_context

    user = await _make_user(test_db)
    now = now_utc()
    yesterday = to_local(now).date() - timedelta(days=1)
    await ConversationSessionRepository(test_db).upsert(
        user_id=str(user.id), day=yesterday,
        digest=StateDigest(wants=["làm tóc"], open=["chưa chọn giờ"]),
        slots=ConversationSlots(intent="book"),
        expires_at=now + timedelta(days=30),
    )

    context = await load_context(test_db, user, "xin chào")
    assert "Lần trước (hôm qua" in context["context_block"]


async def test_last_session_line_absent_without_open_intent(test_db):
    """Phiên cũ không có `slots.intent` (hoặc không có slots) → không có gì
    dang dở, không nhắc lại."""
    from app.agents.booking_graph.context import load_context

    user = await _make_user(test_db)
    now = now_utc()
    yesterday = to_local(now).date() - timedelta(days=1)
    await ConversationSessionRepository(test_db).upsert(
        user_id=str(user.id), day=yesterday,
        digest=StateDigest(wants=["làm tóc"]), slots=None,
        expires_at=now + timedelta(days=30),
    )

    context = await load_context(test_db, user, "xin chào")
    assert "Lần trước" not in context["context_block"]


async def test_last_session_line_absent_when_appointment_booked_since(test_db):
    """Khách ĐÃ đặt lịch mới sau lượt nén cuối của phiên cũ → ý định cũ coi
    như đã xong, không nhắc lại dù `slots.intent` vẫn còn "book"."""
    from app.agents.booking_graph.context import load_context
    from app.services.appointment import AppointmentService

    user = await _make_user(test_db)
    now = now_utc()
    yesterday = to_local(now).date() - timedelta(days=1)
    await ConversationSessionRepository(test_db).upsert(
        user_id=str(user.id), day=yesterday,
        digest=StateDigest(wants=["làm tóc"]),
        slots=ConversationSlots(intent="book"),
        expires_at=now + timedelta(days=30),
    )
    await AppointmentService(test_db).create(
        user, now + timedelta(days=1), note="làm tóc",
    )

    context = await load_context(test_db, user, "xin chào")
    assert "Lần trước" not in context["context_block"]


async def test_recent_visits_lists_three_past_appointments_newest_first(test_db):
    from app.agents.booking_graph.context import load_context
    from app.core.slots import quantize
    from app.repositories.appointment import AppointmentRepository

    user = await _make_user(test_db)
    now = now_utc()
    repo = AppointmentRepository(test_db)
    for days_ago, note in ((1, "gần nhất"), (3, "giữa"), (5, "xa nhất"), (8, "quá cũ")):
        await repo.insert_booked(
            user_id=str(user.id), user_name=user.full_name, phone=user.phone,
            start_at=quantize(now - timedelta(days=days_ago)), duration_minutes=60,
            note=note, created_via="chat",
        )

    context = await load_context(test_db, user, "xin chào")
    block = context["context_block"]
    assert "Khách đã đến:" in block
    gan_idx, giua_idx, xa_idx = (block.index(n) for n in ("gần nhất", "giữa", "xa nhất"))
    assert gan_idx < giua_idx < xa_idx
    assert "quá cũ" not in block
