"""TDD cho `shared/context.py` (Task 7) — bối cảnh của lượt: `load_snapshot`
dựng `TurnSnapshot` từ DB, `render_context` biến nó thành chữ theo đúng phần
mà mỗi audience cần (context engineering, chủ dự án duyệt 08/11), và
`SnapshotHolder` giữ một bối cảnh cho cả lượt, đọc lại khi được báo DB vừa đổi.
"""
from __future__ import annotations

import uuid
from datetime import timedelta

import pytest

from app.agents.salon.shared.context import (
    MEMORY_LABEL,
    UNTRUSTED_LABEL,
    CustomerView,
    SnapshotHolder,
    address_phrase,
    load_snapshot,
    render_context,
)
from app.agents.salon.shared.schemas import (
    Moment,
    ServiceFact,
    TimeFact,
    UnannouncedDecision,
)
from app.core.clock import TZ, now_utc, to_local
from app.models.service import Service
from app.models.user import User
from app.models.waiting_session import SessionOption, SessionQuestion, WaitingSession
from app.repositories.user import UserRepository
from app.services.appointment import AppointmentService
from app.services.proposal import ProposalService
from app.services.service_catalog import ServiceCatalogService
from app.services.shop import ShopService
from app.services.waiting_session import WaitingSessionService

HAIR = Service(id="toc-cat", category="toc", name="Cắt tóc", price_min=50000,
               price_max=50000, duration_minutes=30)
NAIL = Service(id="nail-son", category="nail", name="Sơn móng", price_min=80000,
                price_max=80000, duration_minutes=45)


_HEX_CHARS = set("0123456789abcdef")


def _hex24_tokens(text: str) -> set[str]:
    """Mọi chuỗi hex 24 ký tự (hình dạng `ObjectId.__str__`) xuất hiện trong
    `text`, tách bằng tay (không `re`): gom từng đoạn ký tự hex LIÊN TỤC,
    chỉ giữ đoạn dài ĐÚNG 24 — dài hơn/ngắn hơn không phải một id thật."""
    tokens: set[str] = set()
    current = ""
    for ch in text + " ":
        if ch in _HEX_CHARS:
            current += ch
        else:
            if len(current) == 24:
                tokens.add(current)
            current = ""
    return tokens


def _phone() -> str:
    return "0" + "".join(str((uuid.uuid4().int + i) % 10) for i in range(9))


async def _make_user(db, *, full_name="Nguyễn Thị Lan", self_address=None,
                      staff_note=None) -> User:
    return await UserRepository(db).create({
        "phone": _phone(),
        "hashed_password": "x",
        "full_name": full_name,
        "role": "user",
        "self_address": self_address,
        "staff_note": staff_note,
    })


def _tomorrow_at(hour: int, minute: int = 0):
    day = (to_local(now_utc()) + timedelta(days=1)).date()
    from datetime import datetime
    return datetime(day.year, day.month, day.day, hour, minute, tzinfo=TZ)


class _Ledger:
    """`LedgerReader` giả — chỉ hai hàm `load_snapshot` cần, không chạm DB."""

    def __init__(self, facts=None, unannounced=None):
        self._facts = facts or []
        self._unannounced = unannounced

    async def service_facts(self, *, thread_id: str):
        return self._facts

    async def unannounced(self, user_id: str, *, today):
        return self._unannounced


async def _snapshot(db, user, *, thread_id=None, services=None, asked_at=None):
    return await load_snapshot(
        db, user,
        asked_at=asked_at or now_utc(),
        thread_id=thread_id or str(uuid.uuid4()),
        services=services or _Ledger(),
    )


# ---------------------------------------------------------------------------
# address_phrase
# ---------------------------------------------------------------------------


async def test_address_phrase_defaults_to_anh_chi(test_db):
    """Tên đầy đủ "Nguyễn Thị Lan" không mang tiền tố xưng hô và khách chưa
    tự xưng -> mặc định "anh chị" (CONTEXT.md mục Xưng hô, không đoán giới
    tính từ tên)."""
    user = await _make_user(test_db, full_name="Nguyễn Thị Lan")
    assert address_phrase(user) == "anh chị"


async def test_address_phrase_uses_name_prefix_when_present(test_db):
    """Tên admin gõ CÓ tiền tố xưng hô ("Cô Lan") -> suy ra "chị Lan" (ca
    "ghi rõ ra chị Lan" của task brief)."""
    user = await _make_user(test_db, full_name="Cô Lan")
    assert address_phrase(user) == "chị Lan"


async def test_address_phrase_self_address_wins(test_db):
    user = await _make_user(test_db, full_name="Nguyễn Thị Lan", self_address="chị")
    assert address_phrase(user).startswith("chị")


# ---------------------------------------------------------------------------
# load_snapshot / render_context
# ---------------------------------------------------------------------------


async def test_first_line_states_today(test_db):
    user = await _make_user(test_db)
    asked_at = now_utc()
    snapshot = await _snapshot(test_db, user, asked_at=asked_at)

    text = render_context(
        snapshot, audience="service", turn_facts=[], memory_text=None, now=asked_at,
    )

    local = to_local(asked_at)
    first_line = text.splitlines()[0]
    assert first_line.startswith(f"Today: {local.date().isoformat()}")
    assert local.strftime("%H:%M") in first_line


@pytest.mark.parametrize(
    "audience,expected,forbidden",
    [
        ("orchestrator",
         ["Address", "Shop hours", "Calendar", "Upcoming", "Pending proposal",
          "Unannounced", "Waiting sessions", "Services identified", "Facts this turn"],
         []),
        ("appointment",
         ["Calendar", "Shop hours", "Upcoming", "Pending proposal"],
         ["Address", "Unannounced", "Waiting sessions", "Services identified",
          MEMORY_LABEL]),
        ("shop_info",
         ["Calendar", "Shop hours"],
         ["Upcoming", "Pending proposal", "Address", MEMORY_LABEL]),
        ("service", [], ["Address", "Calendar", "Shop hours", "Upcoming",
                          "Pending proposal", MEMORY_LABEL]),
    ],
)
async def test_render_for_each_audience_includes_only_its_parts(
    test_db, audience, expected, forbidden,
):
    user = await _make_user(test_db)
    asked_at = now_utc()
    snapshot = await _snapshot(test_db, user, asked_at=asked_at)

    text = render_context(
        snapshot, audience=audience, turn_facts=[], memory_text="khách thích màu đỏ",
        now=asked_at,
    )

    for label in expected:
        assert label in text, f"{label!r} missing for {audience}"
    for label in forbidden:
        assert label not in text, f"{label!r} leaked into {audience}"


async def test_ids_and_isos_come_from_objects(test_db):
    """Mọi id trong khối bối cảnh phải là id THẬT của một lịch/đề xuất đang
    có (task brief: "mọi id trong chuỗi ∈ id của upcoming / pending") — không
    bịa chuỗi trông giống id. Gom id-shaped token bằng tay (không `re`), rồi
    kiểm tập đó là TẬP CON của id các object đã tạo (⊆, không phải ngược
    lại: khối bối cảnh không bắt buộc phải nhắc lại MỌI object, chỉ không
    được nhắc tới object nào khác).

    Các iso so bằng GIÁ TRỊ thời điểm (`datetime.fromisoformat`), không so
    chuỗi: cùng một mốc đọc lại từ Mongo có offset khác bản trong bộ nhớ
    (cùng khoảnh khắc, khác cách viết)."""
    from datetime import datetime as _dt

    user = await _make_user(test_db)
    catalog = ServiceCatalogService(test_db)
    await catalog.upsert_many([HAIR])
    appts = AppointmentService(test_db)
    appt = await appts.create(user, _tomorrow_at(10), None, service_ids=[HAIR.id])
    proposals = ProposalService(test_db)
    proposal = await proposals.create(user, "book", _tomorrow_at(15), None,
                                      service_ids=[HAIR.id])

    snapshot = await _snapshot(test_db, user)
    text = render_context(
        snapshot, audience="orchestrator", turn_facts=[], memory_text=None,
        now=now_utc(),
    )

    known_ids = {str(appt.id), str(proposal.id)}
    assert _hex24_tokens(text) <= known_ids
    # Không chỉ là tập con rỗng — cả hai id phải thật sự có mặt:
    assert str(appt.id) in text
    assert str(proposal.id) in text

    assert snapshot.upcoming[0].id == str(appt.id)
    assert _dt.fromisoformat(snapshot.upcoming[0].at.iso) == appt.start_at
    assert snapshot.pending is not None
    assert snapshot.pending.id == str(proposal.id)
    assert _dt.fromisoformat(snapshot.pending.at.iso) == proposal.start_at


async def test_free_text_is_single_lined(test_db):
    user = await _make_user(test_db, staff_note="Khách quen,\nthích ngồi gần cửa")
    snapshot = await _snapshot(test_db, user)
    assert "\n" not in (snapshot.customer.staff_note or "")

    text = render_context(
        snapshot, audience="orchestrator", turn_facts=[], memory_text=None,
        now=now_utc(),
    )
    assert "Khách quen, thích ngồi gần cửa" in text


async def test_memory_sits_under_its_label(test_db):
    user = await _make_user(test_db)
    snapshot = await _snapshot(test_db, user)

    text = render_context(
        snapshot, audience="orchestrator", turn_facts=[], memory_text="thích màu đỏ",
        now=now_utc(),
    )

    lines = text.splitlines()
    idx = lines.index(MEMORY_LABEL)
    assert "thích màu đỏ" in lines[idx + 1]
    assert UNTRUSTED_LABEL not in text


async def test_expired_pending_is_not_listed(test_db):
    user = await _make_user(test_db)
    proposals = ProposalService(test_db)
    proposal = await proposals.create(user, "book", _tomorrow_at(15), None)
    await test_db["proposals"].update_one(
        {"_id": proposal.id}, {"$set": {"expires_at": now_utc() - timedelta(minutes=1)}}
    )

    snapshot = await _snapshot(test_db, user)

    assert snapshot.pending is None
    text = render_context(
        snapshot, audience="orchestrator", turn_facts=[], memory_text=None,
        now=now_utc(),
    )
    assert str(proposal.id) not in text


async def test_closed_day_is_shown_in_vietnamese_and_marked_in_calendar(test_db):
    """`ShopHours.closed_days` dùng quy ước 0=Chủ Nhật..6=Thứ Bảy (app/models/
    shop.py), KHÁC quy ước `date.weekday()` của Python (0=Thứ Hai). `closed_days=[0]`
    nghĩa là tiệm nghỉ CHỦ NHẬT — khối bối cảnh phải gọi đúng tên "Chủ Nhật"
    (không phải "Thứ Hai"), và trong Lịch 14 ngày phải đánh dấu đúng NGÀY
    CHỦ NHẬT là nghỉ, không phải ngày Thứ Hai."""
    user = await _make_user(test_db)
    await ShopService(test_db).set_hours("08:00", "19:00", closed_days=[0])

    snapshot = await _snapshot(test_db, user)
    text = render_context(
        snapshot, audience="orchestrator", turn_facts=[], memory_text=None,
        now=now_utc(),
    )

    shop_block = text.split("Shop hours / status:", 1)[1].split("\n\n", 1)[0]
    assert "Chủ Nhật" in shop_block
    assert "Thứ Hai" not in shop_block

    sunday_label = next(label for day, label in snapshot.calendar_14d if day.weekday() == 6)
    monday_label = next(
        (label for day, label in snapshot.calendar_14d if day.weekday() == 0), None,
    )
    assert "(closed)" in sunday_label
    if monday_label is not None:
        assert "(closed)" not in monday_label


async def test_times_in_context_block_use_vn_local_offset(test_db):
    """`Today:` là giờ Việt Nam; mọi iso khác trong cùng khối (Upcoming,
    Pending proposal, Waiting sessions) phải CÙNG quy ước +07:00, không phải
    UTC Mongo trả về — hai quy ước lệch nhau trong cùng một khối là chỗ model
    tính sai giờ (CONTEXT.md bẫy 8)."""
    user = await _make_user(test_db)
    catalog = ServiceCatalogService(test_db)
    await catalog.upsert_many([HAIR])
    appts = AppointmentService(test_db)
    await appts.create(user, _tomorrow_at(10), None, service_ids=[HAIR.id])
    proposals = ProposalService(test_db)
    await proposals.create(user, "book", _tomorrow_at(15), None, service_ids=[HAIR.id])
    waiting = WaitingSessionService(test_db)
    now = now_utc()
    question = SessionQuestion(field="time", options=[], text="Mấy giờ ạ?")
    await waiting.open(WaitingSession(
        user_id=str(user.id), agent="service", task="pick", memory={}, question=question,
        asked_at=now, expires_at=now + timedelta(minutes=10), opened_turn_id="t1",
    ))

    snapshot = await _snapshot(test_db, user)
    text = render_context(
        snapshot, audience="orchestrator", turn_facts=[], memory_text=None,
        now=now_utc(),
    )

    upcoming_block = text.split("Upcoming:", 1)[1].split("\n\n", 1)[0]
    pending_block = text.split("Pending proposal:", 1)[1].split("\n\n", 1)[0]
    sessions_block = text.split("Waiting sessions:", 1)[1].split("\n\n", 1)[0]
    assert "+07:00" in upcoming_block
    assert "+07:00" in pending_block
    assert "+07:00" in sessions_block


def test_old_session_document_reads_as_one_field():
    assert SessionQuestion.model_validate({"field": "time", "text": "x"}).fields == ["time"]


async def test_context_lists_session_fields(test_db):
    user = await _make_user(test_db)
    waiting = WaitingSessionService(test_db)
    now = now_utc()
    question = SessionQuestion(fields=["day", "time"], options=[], text="Ngày nào, mấy giờ ạ?")
    await waiting.open(WaitingSession(
        user_id=str(user.id), agent="appointment", task="book", memory={}, question=question,
        asked_at=now, expires_at=now + timedelta(minutes=10), opened_turn_id="t1",
    ))

    snapshot = await _snapshot(test_db, user)
    text = render_context(
        snapshot, audience="orchestrator", turn_facts=[], memory_text=None, now=now_utc(),
    )

    sessions_block = text.split("Waiting sessions:", 1)[1].split("\n\n", 1)[0]
    assert "fields=day,time" in sessions_block


async def test_snapshot_lists_only_active_sessions(test_db):
    user = await _make_user(test_db)
    waiting = WaitingSessionService(test_db)
    now = now_utc()
    question = SessionQuestion(field="time", options=[SessionOption(iso="x", spoken="3 giờ chiều")],
                                text="Mấy giờ ạ?")
    active = WaitingSession(
        user_id=str(user.id), agent="appointment", task="book", memory={},
        question=question, asked_at=now, expires_at=now + timedelta(minutes=10),
        opened_turn_id="t1",
    )
    expired = WaitingSession(
        user_id=str(user.id), agent="shop_info", task="hours", memory={},
        question=question, asked_at=now - timedelta(minutes=20),
        expires_at=now - timedelta(minutes=10), opened_turn_id="t2",
    )
    await waiting.open(active)
    await waiting.open(expired)

    snapshot = await _snapshot(test_db, user)

    assert set(snapshot.sessions) == {"appointment"}
    text = render_context(
        snapshot, audience="orchestrator", turn_facts=[], memory_text=None,
        now=now_utc(),
    )
    sessions_block = text.split("Waiting sessions:", 1)[1].split("\n\n", 1)[0]
    assert "appointment" in sessions_block
    assert "shop_info" not in sessions_block


async def test_holder_reloads_after_mark_stale(test_db):
    user = await _make_user(test_db)
    proposals = ProposalService(test_db)
    proposal = await proposals.create(user, "book", _tomorrow_at(15), None)
    asked_at = now_utc()

    holder = SnapshotHolder(
        test_db, user, asked_at=asked_at, thread_id=str(uuid.uuid4()), services=_Ledger(),
    )
    before = await holder.current()
    assert before.pending is not None
    assert before.pending.id == str(proposal.id)

    result = await proposals.approve(user, str(proposal.id), turn_at=asked_at + timedelta(seconds=1))
    assert result.outcome == "approved"
    assert result.appointment is not None

    holder.mark_stale()
    after = await holder.current()

    assert after.pending is None
    assert len(after.upcoming) == 1
    assert after.upcoming[0].id == str(result.appointment.id)


async def test_facts_this_turn_and_known_services_render_for_orchestrator(test_db):
    user = await _make_user(test_db)
    asked_at = now_utc()
    moment = Moment(iso=asked_at.isoformat(), spoken="9 giờ sáng")
    ledger = _Ledger(
        facts=[ServiceFact(service_id=NAIL.id, name=NAIL.name, price_min=NAIL.price_min,
                            price_max=NAIL.price_max, duration_minutes=NAIL.duration_minutes)],
        unannounced=UnannouncedDecision(
            turn_id="t0", outcome="approved", proposal_kind="book", at=moment,
        ),
    )
    snapshot = await _snapshot(test_db, user, services=ledger, asked_at=asked_at)

    text = render_context(
        snapshot, audience="orchestrator",
        turn_facts=[TimeFact(role="asked", moment=moment)],
        memory_text=None, now=asked_at,
    )

    assert NAIL.id in text and NAIL.name in text
    assert "approved" in text
    assert "Facts this turn:" in text
    assert moment.iso in text


async def test_holder_sees_a_session_opened_after_mark_stale(test_db):
    user = await _make_user(test_db)
    holder = SnapshotHolder(
        test_db, user, asked_at=now_utc(), thread_id=str(uuid.uuid4()), services=_Ledger(),
    )
    before = await holder.current()
    assert before.sessions == {}

    waiting = WaitingSessionService(test_db)
    now = now_utc()
    question = SessionQuestion(field="service", options=[], text="Dịch vụ gì ạ?")
    await waiting.open(WaitingSession(
        user_id=str(user.id), agent="service", task="pick", memory={}, question=question,
        asked_at=now, expires_at=now + timedelta(minutes=10), opened_turn_id="t1",
    ))

    holder.mark_stale()
    after = await holder.current()

    assert set(after.sessions) == {"service"}


async def test_holder_never_caches_a_load_started_before_mark_stale(monkeypatch):
    """Task 13: một lần đọc bắt đầu TRƯỚC `mark_stale()` (DB đổi giữa chừng,
    vd hai tool cùng vòng) không được thành bối cảnh của cả lượt."""
    from app.agents.salon.shared import context as context_module

    holder = SnapshotHolder(None, None, asked_at=now_utc(), thread_id="t", services=_Ledger())
    loads: list[str] = []

    async def fake_load(db, user, **kwargs):
        loads.append("load")
        if len(loads) == 1:
            holder.mark_stale()   # DB vừa đổi trong lúc lần đọc này đang chạy
            return "stale"
        return "fresh"

    monkeypatch.setattr(context_module, "load_snapshot", fake_load)

    assert await holder.current() == "fresh"
    assert await holder.current() == "fresh"
    assert len(loads) == 2
