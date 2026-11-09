"""Tool của agent Đặt lịch (spec §3.1, §6.2; docs/architech-agent.md luật 1).

KHÔNG tool nào ghi lịch: `propose_appointment` / `propose_cancel` chỉ tạo ĐỀ
XUẤT (`ProposalService.create`) — module DUY NHẤT trong `app/agents/salon`
được gọi nó (`tests/test_write_fence.py`). Lịch chỉ được ghi khi khách đồng ý
ở lượt sau, qua `orchestrator/decide.py::apply_decision`.

Dựng lại MỖI LẦN GỌI agent con (`make_appointment_tools(ctx)`): `user` và
`service_ids` đóng trong closure (model không gõ được id khách khác hay thời
lượng), enum `appointment_id` là lịch sắp tới của CHÍNH khách này, lượt này.

Viết mới, không import `app/agents/booking` — chỉ đọc `booking/tools.py` để
giữ đúng các mã lỗi và bài học đã trả giá (giờ chỉ có ngày, `already_yours`,
mọi lỗi về giờ mang `asked`).
"""
from __future__ import annotations

import json
from datetime import date, datetime
from typing import Literal, Optional

from langchain_core.tools import BaseTool, StructuredTool
from pydantic import BaseModel, Field, create_model

from app.agents.salon.subagents.dispatch import RunContext
from app.agents.salon.subagents.time_tool import parse_time
from app.core.clock import TZ, now_utc, to_local
from app.core.errors import AppError, ForbiddenError, NotFoundError
from app.core.text import single_line
from app.core.vi_format import format_vi_datetime, format_vi_hhmm
from app.models.appointment import NOTE_MAX
from app.models.shop import ShopHours
from app.services.appointment import AppointmentService
from app.services.proposal import ProposalService
from app.services.service_catalog import ServiceCatalogService
from app.services.shop import ShopService, fits_before_closing

# Đủ phủ trọn một ngày ở bước 15 phút (96 mốc), có dư — mặc định 12 mốc của
# `find_free_slots` chỉ tới gần trưa.
WHOLE_DAY = 200
# Khách lớn tuổi không nhớ nổi một danh sách: tối đa 3 mốc mỗi lần.
MAX_SLOTS_OFFERED = 3

# Ánh xạ theo LỚP exception, không theo chuỗi `message` (viết cho REST).
_ERROR_CODES = {NotFoundError: "appointment_not_found", ForbiddenError: "not_your_appointment"}


def _moment(dt: datetime) -> dict:
    """Mốc giờ VN, cả `iso` (chép sang tool khác) lẫn `spoken` (đọc cho khách)."""
    local = to_local(dt)
    return {"iso": local.isoformat(), "spoken": format_vi_datetime(local)}


def _is_date_only(value: str) -> bool:
    """`YYYY-MM-DD` không giờ — `fromisoformat` đọc ra nửa đêm, từng thành "kín"."""
    parts = value.strip().split("-")
    return (len(value.strip()) == 10 and [len(p) for p in parts] == [4, 2, 2]
            and all(p.isascii() and p.isdigit() for p in parts))


def _parse_local(value: str) -> datetime:
    """ISO luôn có múi giờ; thiếu offset thì hiểu là giờ VN."""
    parsed = datetime.fromisoformat(value.strip())
    return parsed.replace(tzinfo=TZ) if parsed.tzinfo is None else parsed


def _around(value: str, day: date) -> Optional[datetime]:
    """"15:00" (trong `day`) hoặc ISO đầy đủ; đọc không được → None."""
    try:
        if "T" in value:
            return _parse_local(value)
        hour, minute = (int(x) for x in value.strip().split(":")[:2])
        return datetime(day.year, day.month, day.day, hour, minute, tzinfo=TZ)
    except (ValueError, TypeError):
        return None


def _pool(slots: list[datetime]) -> list[datetime]:
    """Ưu tiên mốc chẵn giờ (dễ nghe, dễ nhớ) khi đủ 3; không đủ thì lấy hết."""
    on_hour = [s for s in slots if to_local(s).minute == 0]
    return on_hour if len(on_hour) >= MAX_SLOTS_OFFERED else slots


def nearest_slots(slots: list[datetime], target: datetime) -> list[datetime]:
    """3 mốc gần `target` nhất, xếp theo giờ."""
    return sorted(sorted(_pool(slots), key=lambda s: abs(s - target))[:MAX_SLOTS_OFFERED])


def spread_slots(slots: list[datetime]) -> list[datetime]:
    """3 mốc rải đều (đầu, giữa, cuối) khi không có giờ nào để bám."""
    pool = _pool(slots)
    if len(pool) <= MAX_SLOTS_OFFERED:
        return list(pool)
    last = len(pool) - 1
    picks = sorted({round(i * last / (MAX_SLOTS_OFFERED - 1)) for i in range(MAX_SLOTS_OFFERED)})
    return [pool[i] for i in picks]


def _closed_on(hours: ShopHours, dt: datetime) -> bool:
    # `closed_days`: 0 = Chủ Nhật; `weekday()`: 0 = Thứ Hai.
    return (to_local(dt).weekday() + 1) % 7 in hours.closed_days


def _dumps(payload: dict) -> str:
    return json.dumps(payload, ensure_ascii=False)


def make_appointment_tools(ctx: RunContext) -> list[BaseTool]:
    user = ctx.user
    service_ids = list(ctx.service_ids)
    appointments = AppointmentService(ctx.db)
    proposals = ProposalService(ctx.db)
    catalog = ServiceCatalogService(ctx.db)
    shop = ShopService(ctx.db)
    upcoming_ids = [appt.id for appt in ctx.snapshot.upcoming]
    # Một đề xuất mỗi lần gọi agent con. Giữ chỗ TRƯỚC mọi `await` để hai
    # tool call song song không cùng lọt; lần thử hỏng thì nhả ra.
    reserved = {"proposal": False}

    def _refusal() -> Optional[str]:
        if not ctx.allow_propose:
            return "one_proposal_per_turn"
        if reserved["proposal"]:
            return "one_proposal_per_call"
        return None

    async def _status_of(target: datetime, free: list[datetime], duration: int) -> str:
        """Vì sao giờ khách hỏi (không) trống — chỉ `taken` mới là "có người đặt"."""
        if target in free:
            return "free"
        if target <= now_utc():
            return "time_passed"
        hours = await shop.get_hours()
        if _closed_on(hours, target):
            return "closed_day"
        if not fits_before_closing(hours, target, duration):
            return "outside_hours"
        return "taken"

    async def find_free_slots(day: str, around: Optional[str] = None) -> str:
        try:
            parsed_day = date.fromisoformat(day.strip())
        except ValueError:
            return _dumps({"error": "bad_day_format"})
        duration = await catalog.duration_for(service_ids)
        free = await appointments.find_free_slots(parsed_day, limit=WHOLE_DAY,
                                                  duration_minutes=duration)
        payload: dict = {"day": parsed_day.isoformat()}
        target = _around(around, parsed_day) if around else None
        if target is not None:
            payload["asked"] = _moment(target)
            payload["asked_status"] = await _status_of(target, free, duration)
        chosen = nearest_slots(free, target) if target else spread_slots(free)
        payload["slots"] = [_moment(slot) for slot in chosen]
        payload["more"] = len(free) > len(chosen)
        return _dumps(payload)

    async def list_my_appointments() -> str:
        upcoming = await appointments.upcoming_for(user)
        return _dumps({"appointments": [
            {"id": str(appt.id), "at": _moment(appt.start_at), "note": appt.note}
            for appt in upcoming
        ]})

    async def _propose(start: datetime, asked: dict, note: Optional[str],
                       replaces_appointment_id: Optional[str]) -> dict:
        # Giờ không đặt được vì LỊCH TIỆM trả mã riêng TRƯỚC khi xem chỗ trống —
        # không thì rơi vào `slot_unavailable` ("có người đặt") một giờ chưa ai đặt.
        if start <= now_utc():
            return {"error": "time_passed", "asked": asked}
        hours = await shop.get_hours()
        if _closed_on(hours, start):
            return {"error": "closed_day", "asked": asked}

        # Dời lịch: kiểm id TRƯỚC khi đề xuất (id lạ mà vẫn đề xuất → hai lịch).
        old = None
        if replaces_appointment_id:
            try:
                old = await appointments.own_booked(user, replaces_appointment_id)
            except AppError as exc:
                return {"error": _ERROR_CODES.get(type(exc), "unavailable"), "asked": asked}

        # Dời mà không mang dịch vụ mới → giữ thời lượng của lịch cũ (spec §6.2).
        duration = (old.duration_minutes if old is not None and not service_ids
                    else await catalog.duration_for(service_ids))
        if not fits_before_closing(hours, start, duration):
            return {
                "error": "outside_hours", "asked": asked,
                "open": {"hhmm": hours.open_time, "spoken": format_vi_hhmm(hours.open_time)},
                "close": {"hhmm": hours.close_time, "spoken": format_vi_hhmm(hours.close_time)},
            }

        # Khách đã có ĐÚNG lịch ở giờ này — ngược hẳn với "chọn giờ khác".
        if old is None:
            mine = await appointments.repo.find_active_at(str(user.id), start)
            if mine:
                return {"error": "already_yours", "asked": asked,
                        "existing": {"id": str(mine.id), "at": _moment(mine.start_at)}}

        free = await appointments.find_free_slots(
            to_local(start).date(), limit=WHOLE_DAY,
            ignore_appointment_id=str(old.id) if old is not None else None,
            duration_minutes=duration,
        )
        if start not in free:
            return {"error": "slot_unavailable", "asked": asked,
                    "nearest": [_moment(slot) for slot in nearest_slots(free, start)]}

        proposal = await proposals.create(
            user, "move" if old is not None else "book", start, note, target=old,
            service_ids=service_ids or None,
        )
        payload = {"proposal_id": str(proposal.id), "kind": proposal.kind, "held": asked,
                   "note": note, "service_ids": list(proposal.service_ids)}
        if old is not None:
            payload["replaces"] = {"id": str(old.id), "was": _moment(old.start_at)}
        return payload

    async def propose_appointment(start_at: str, note: Optional[str] = None,
                                  replaces_appointment_id: Optional[str] = None) -> str:
        # `note` do model gõ, lưu vào đề xuất → cap như `Appointment.note`.
        note = single_line(note, NOTE_MAX)
        if _is_date_only(start_at):
            return _dumps({"error": "missing_time"})
        try:
            start = _parse_local(start_at)
        except ValueError:
            return _dumps({"error": "bad_time_format"})
        asked = _moment(start)
        refusal = _refusal()
        if refusal:
            return _dumps({"error": refusal, "asked": asked})
        reserved["proposal"] = True
        try:
            payload = await _propose(start, asked, note, replaces_appointment_id)
        except BaseException:
            reserved["proposal"] = False
            raise
        if "error" in payload:
            reserved["proposal"] = False
        return _dumps(payload)

    async def propose_cancel(appointment_id: str) -> str:
        try:
            appt = await appointments.own_booked(user, appointment_id)
        except AppError as exc:
            return _dumps({"error": _ERROR_CODES.get(type(exc), "unavailable")})
        asked = _moment(appt.start_at)
        refusal = _refusal()
        if refusal:
            return _dumps({"error": refusal, "asked": asked})
        reserved["proposal"] = True
        try:
            proposal = await proposals.create(user, "cancel", appt.start_at, None, target=appt)
        except BaseException:
            reserved["proposal"] = False
            raise
        return _dumps({"proposal_id": str(proposal.id), "kind": "cancel",
                       "will_cancel": {"id": str(appt.id), "at": asked}})

    appointment_id_type = Literal[tuple(upcoming_ids)] if upcoming_ids else None

    propose_fields: dict = {
        "start_at": (str, Field(description="ISO 8601 copied unchanged from parse_time's `iso` "
                                            "or a tool's slot `iso`.")),
        "note": (Optional[str], Field(default=None, description=(
            "The service only (what they want done, for whom) — never the time or day."))),
    }
    if appointment_id_type is not None:
        propose_fields["replaces_appointment_id"] = (
            Optional[appointment_id_type],
            Field(default=None, description="Set ONLY to move an existing appointment: its id "
                                            "from the context block. Leave null for a new booking."),
        )

    tools: list[BaseTool] = [
        parse_time,
        StructuredTool.from_function(
            coroutine=find_free_slots, name="find_free_slots",
            description=(
                "Free slots on one day, as JSON — at most 3. `day` is YYYY-MM-DD. `around` is "
                "the time asked for (parse_time's `iso`, or HH:MM): you get the free slots "
                "nearest to it, plus `asked` and `asked_status` (free, taken, time_passed, "
                "closed_day, outside_hours — only `taken` means someone booked it). Without "
                "`around`, slots spread across the day. `more` is true when other free slots "
                "exist. An empty `slots` means nothing is free that day. Slot length follows "
                "the services of this task."),
            args_schema=create_model(
                "FindFreeSlotsArgs", __base__=BaseModel,
                day=(str, Field(description="YYYY-MM-DD")),
                around=(Optional[str], Field(default=None, description="ISO or HH:MM")),
            ),
        ),
        StructuredTool.from_function(
            coroutine=list_my_appointments, name="list_my_appointments",
            description=("The customer's own upcoming appointments, as JSON, each with its id. "
                         "The context block already lists them; call this only when needed."),
            args_schema=create_model("ListMyAppointmentsArgs", __base__=BaseModel),
        ),
        StructuredTool.from_function(
            coroutine=propose_appointment, name="propose_appointment",
            description=(
                "Create a PROPOSAL to book (or move) at `start_at` — it does NOT book; the "
                "customer approves it later. At most one proposal per call. Success carries "
                "`held`. Every error about the time carries `asked`, the time actually checked: "
                "`slot_unavailable` (someone booked it; `nearest` lists free slots), "
                "`outside_hours`, `closed_day`, `time_passed` (the salon cannot take it — "
                "nobody booked it), `already_yours` (the customer already has that slot), "
                "`one_proposal_per_call` / `one_proposal_per_turn` (stop proposing). "
                "`missing_time`: only a date was sent."),
            args_schema=create_model("ProposeAppointmentArgs", __base__=BaseModel,
                                     **propose_fields),
        ),
    ]
    if appointment_id_type is not None:
        tools.append(StructuredTool.from_function(
            coroutine=propose_cancel, name="propose_cancel",
            description=("Create a PROPOSAL to cancel one appointment — nothing is cancelled "
                         "until the customer approves it later. Success carries `will_cancel`."),
            args_schema=create_model(
                "ProposeCancelArgs", __base__=BaseModel,
                appointment_id=(appointment_id_type, Field(
                    description="The appointment's id from the context block.")),
            ),
        ))
    return tools
