import json
import re
from datetime import date, datetime
from typing import List, Optional

from langchain_core.tools import BaseTool, tool
from motor.motor_asyncio import AsyncIOMotorDatabase

from app.agents.booking_graph.context import format_vi_datetime, format_vi_hhmm
from app.agents.booking_graph.timeparse import ParsedTime, parse_vi_time
from app.core.clock import TZ, now_utc, to_local
from app.core.errors import AppError, ForbiddenError, NotFoundError
from app.core.logging import get_logger
from app.core.text import single_line, strip_diacritics
from app.models.appointment import NOTE_MAX
from app.models.user import User
from app.services.appointment import AppointmentService
from app.core.config import settings
from app.services.proposal import ProposalService
from app.services.shop import ShopService, fits_before_closing

# Đủ để phủ trọn một ngày ở bước 15 phút (96 mốc), có dư.
WHOLE_DAY = 200

# Chỉ có ngày, không có giờ: `fromisoformat("2026-10-20")` đọc ra NỬA ĐÊM, và
# nửa đêm thì không có trong danh sách trống → từng thành `slot_unavailable`
# kèm 8 giờ sáng… và model nói "3 giờ đã kín" (lỗi live 2).
_DATE_ONLY = re.compile(r"^\s*\d{4}-\d{2}-\d{2}\s*$")

logger = get_logger(__name__)


def _parse_local(value: str) -> datetime:
    """Đọc chuỗi ISO và LUÔN trả về datetime có múi giờ.

    Lớp chốt trong `timeparse` đã gắn múi giờ, nhưng `propose_appointment` vẫn
    nhận chuỗi từ model — nó có thể bỏ qua `parse_time` rồi tự dựng ISO. Thiếu
    offset thì `fromisoformat` cho datetime naive, đem so với `now_utc()`
    tz-aware là TypeError, mà tool chỉ bắt ValueError và AppError.

    Thiếu offset thì hiểu là giờ Việt Nam: khách và tiệm ở cùng một chỗ.
    """
    parsed = datetime.fromisoformat(value)
    return parsed.replace(tzinfo=TZ) if parsed.tzinfo is None else parsed


def _moment(dt: datetime) -> dict:
    """Một mốc thời gian, cấp CẢ HAI dạng.

    `iso` để model chuyền tiếp sang tool khác không diễn giải lại. `spoken` để
    model chép y vào câu nói với khách: `guard.check_clock` cờ mọi chuỗi
    `HH:MM` và mọi số viết bằng chữ, nên nếu tool không cấp dạng đọc thì model
    phải tự chế ra — đúng thứ guard sinh ra để chặn.

    Đây KHÔNG phải chỗ duy nhất tool được phép sinh chữ tiếng Việt:
    `_CLOSED_DAY_NAMES` và hai lời gọi `format_vi_hhmm` trực tiếp trong
    `get_shop_hours` cũng vậy. Điều đúng là: đây là hàm DÙNG CHUNG cho việc
    đó, để không mỗi tool tự bịa một kiểu.

    `to_local` nằm TRONG đây, không ở chỗ gọi: `spoken` luôn là giờ Việt Nam
    vì `format_vi_datetime` tự đổi, nên `iso` cũng phải vậy — một object mà
    hai trường khác múi giờ là bẫy cho người đọc sau. Nguồn dữ liệu (Mongo,
    `shop.busy_until`) lưu UTC, nên phải đổi ở đúng một chỗ.
    """
    local = to_local(dt)
    return {"iso": local.isoformat(), "spoken": format_vi_datetime(local)}


# --- tối đa 3 mốc cho model (lỗi live B2 #3: bot đọc 12 khung giờ) -------------
# Khách lớn tuổi không nhớ nổi một danh sách. Tool chỉ ĐƯA model tối đa 3 mốc —
# luật prompt thôi thì model vẫn đọc hết những gì nó có. Ưu tiên mốc chẵn giờ
# (dễ nghe, dễ nhớ) khi đủ 3; không đủ thì lấy cả mốc lẻ.
MAX_SLOTS_OFFERED = 3
# Buổi theo giờ bắt đầu, cùng cách gọi với `_clock_phrase` (12 giờ là trưa).
_PARTS_OF_DAY = {"sang": (0, 12), "trua": (11, 14), "chieu": (12, 18), "toi": (18, 24)}


def _pool(slots: List[datetime]) -> List[datetime]:
    on_hour = [s for s in slots if to_local(s).minute == 0]
    return on_hour if len(on_hour) >= MAX_SLOTS_OFFERED else slots


def nearest_slots(slots: List[datetime], target: datetime) -> List[datetime]:
    """3 mốc GẦN `target` nhất, xếp theo giờ."""
    return sorted(sorted(_pool(slots), key=lambda s: abs(s - target))[:MAX_SLOTS_OFFERED])


def spread_slots(slots: List[datetime]) -> List[datetime]:
    """3 mốc rải đều (đầu, giữa, cuối) — không có giờ nào để bám."""
    pool = _pool(slots)
    if len(pool) <= MAX_SLOTS_OFFERED:
        return list(pool)
    last = len(pool) - 1
    picks = sorted({round(i * last / (MAX_SLOTS_OFFERED - 1)) for i in range(MAX_SLOTS_OFFERED)})
    return [pool[i] for i in picks]


def _in_part(slot: datetime, part: str) -> bool:
    lo, hi = _PARTS_OF_DAY[part]
    return lo <= to_local(slot).hour < hi


def _around(value: str, day: date) -> Optional[datetime]:
    """"14:00" (giờ trong `day`) hoặc ISO đầy đủ; đọc không được → None."""
    try:
        if "T" in value:
            return _parse_local(value)
        hour, minute = (int(x) for x in value.strip().split(":")[:2])
        return datetime(day.year, day.month, day.day, hour, minute, tzinfo=TZ)
    except (ValueError, TypeError):
        logger.warning("slots_around_ignored", extra={"around": (value or "")[:40]})
        return None


# Ánh xạ theo LỚP exception, không theo chuỗi `message`: `AppError.message`
# viết cho tầng REST và frontend, đổi lúc nào không ai báo cho tầng này.
_ERROR_CODES = {
    NotFoundError: "appointment_not_found",
    ForbiddenError: "not_your_appointment",
}


def _error_code(exc: AppError) -> str:
    """Mặc định `"unavailable"` là LƯỚI AN TOÀN, không phải mã model sẽ gặp.

    Hai tool duy nhất gọi hàm này đều chỉ bọc `own_booked`, mà nó chỉ ném
    `NotFoundError` hoặc `ForbiddenError` — id rác đã bị `ObjectId.is_valid()`
    chặn từ `get_by_id` và thành `NotFoundError`. Nên nhánh mặc định hiện
    KHÔNG tới được, và `BOOKING_PROMPT` cố ý không mô tả nó: dạy model một mã
    không bao giờ xuất hiện là tốn prompt và nói sai về hệ thống.

    Giữ default vì bỏ đi là biến một fallback thành `KeyError`. Thêm lớp
    `AppError` mới thì phải chọn: ánh xạ nó ở đây, hoặc mô tả `unavailable`
    trong `BOOKING_PROMPT`.
    """
    return _ERROR_CODES.get(type(exc), "unavailable")


# Index CHÍNH LÀ giá trị trong closed_days: 0 = Chủ Nhật (app/models/shop.py).
# Ngược với datetime.weekday() của Python, nên tuyệt đối không dùng _WEEKDAYS.
_CLOSED_DAY_NAMES = [
    "Chủ Nhật", "Thứ Hai", "Thứ Ba", "Thứ Tư", "Thứ Năm", "Thứ Sáu", "Thứ Bảy",
]


def make_shop_tools(db: AsyncIOMotorDatabase, user: User) -> List[BaseTool]:
    shop = ShopService(db)

    @tool
    async def get_shop_status() -> str:
        """Whether the owner is busy or free, and if busy, what time they finish.
        Returns JSON. When `busy` is true, say the finish time using
        `until.spoken` exactly as written — never a countdown in minutes."""
        status = await shop.get_status()
        if not status.is_busy:
            return json.dumps({"busy": False})
        # Mốc giờ tuyệt đối, không "còn N phút": câu đáp nằm lại trong lịch sử
        # chat và đọc lại sau một tiếng là sai hẳn.
        return json.dumps({"busy": True, "until": _moment(status.busy_until)},
                          ensure_ascii=False)

    @tool
    async def get_shop_hours() -> str:
        """Opening hours and closed days, as JSON. Call this when the customer
        asks what time the salon opens or closes, or whether it is open on a
        given day. Say the times using `open.spoken` and `close.spoken` exactly
        as written. An empty `closed_days` means the salon opens every day."""
        hours = await shop.get_hours()
        closed = [_CLOSED_DAY_NAMES[d] for d in sorted(hours.closed_days)
                  if 0 <= d < len(_CLOSED_DAY_NAMES)]
        return json.dumps({
            "open": {"hhmm": hours.open_time, "spoken": format_vi_hhmm(hours.open_time)},
            "close": {"hhmm": hours.close_time, "spoken": format_vi_hhmm(hours.close_time)},
            "closed_days": closed,
        }, ensure_ascii=False)

    return [get_shop_status, get_shop_hours]


def make_booking_tools(db: AsyncIOMotorDatabase, user: User) -> List[BaseTool]:
    """Tool đóng kín `user` trong closure.

    `user_id` KHÔNG phải tham số của tool: AI không thể bị dụ thao tác lịch của
    người khác, kể cả khi khách gõ "hủy lịch của bà Lan".
    """
    service = AppointmentService(db)
    proposals = ProposalService(db)

    @tool
    async def parse_time(text: str, anchor: Optional[str] = None) -> str:
        """Turn what the customer said about time into a concrete date and time.
        Call this BEFORE find_free_slots and propose_appointment, every time the
        customer mentions a time. Never compute a date yourself.
        `text` must be the FULL phrase, joining what the customer said on
        earlier turns: if they named a day one turn and an hour the next, pass
        both together, not the hour alone. This tool reads only the string you
        give it — it cannot see earlier turns.
        `anchor`: pass it whenever the customer is changing or answering about a
        time already on the table — the appointment being moved (its `iso:` in
        the context block), the slot just proposed (`held.iso` in
        propose_appointment's result), or the proposal the customer just
        declined (the `iso` on the context block line tagged
        [declined: ...]). The tool then fills a missing day or part of the
        day from it, and keeps its day when the text names no day.
        Result has `missing` -> ask the customer for exactly that ONE missing
        piece, one piece per turn, and nothing else. The value "sáng hay chiều"
        is quoted from the tool's own output, not an example: it means the hour
        is known but the part of the day is not."""
        anchor_dt = None
        if anchor:
            try:
                anchor_dt = _parse_local(anchor)
            except ValueError:
                # Neo bịa thì bỏ neo, không bỏ cả lượt.
                logger.warning("anchor_ignored", extra={"anchor": anchor[:60]})
        hours = await service.shop.get_hours()
        parsed: ParsedTime = await parse_vi_time(text, now_utc(), anchor=anchor_dt, hours=hours)
        # Trả JSON gọn thay vì câu tiếng Việt: agent cần chuỗi ISO nguyên vẹn
        # để chuyển thẳng sang propose_appointment, không được diễn giải lại.
        return parsed.model_dump_json(
            include={"start_at", "partial_date", "missing"}, exclude_none=False
        )

    @tool
    async def find_free_slots(day: str, around: Optional[str] = None,
                              part_of_day: Optional[str] = None) -> str:
        """Free slots on one day, as JSON — at most 3 of them. `day` is
        YYYY-MM-DD, taken from the "Bây giờ là..." line in the context block —
        never guess the date.
        `around`: the time the customer asked for (parse_time's `start_at`, or
        HH:MM) — you get the 3 free slots nearest to it. `part_of_day`: the part
        of the day they named (sáng / trưa / chiều / tối) — you get up to 3
        slots spread across it. With neither, 3 slots spread across the day.
        `more` is true when other free slots exist that day; offer those only
        if the customer asks for other times.
        Use ONLY what this tool returns — never invent a free slot.
        Say each time using its `spoken` value exactly as written.
        An empty `slots` list means no slot is available for that request —
        ask them for another day or another time.
        Only when the customer says the salon may pick the time for them, call
        this for today — or tomorrow if today is finished. If they have NOT
        named a day, ask which day first. Never assume today."""
        try:
            parsed_day = date.fromisoformat(day)
        except ValueError:
            return json.dumps({"error": "bad_day_format"})
        free = await service.find_free_slots(parsed_day, limit=WHOLE_DAY)
        part = strip_diacritics((part_of_day or "").strip().lower())
        if part in _PARTS_OF_DAY:
            free = [s for s in free if _in_part(s, part)]
        target = _around(around, parsed_day) if around else None
        chosen = nearest_slots(free, target) if target else spread_slots(free)
        return json.dumps({"day": day, "slots": [_moment(s) for s in chosen],
                           "more": len(free) > len(chosen)}, ensure_ascii=False)

    @tool
    async def propose_appointment(
        start_at: str,
        note: Optional[str] = None,
        replaces_appointment_id: Optional[str] = None,
    ) -> str:
        """Hold the slot temporarily, as JSON. `start_at` is ISO 8601, copied
        UNCHANGED from parse_time's result.
        NO tool writes an appointment directly — it is written only when the
        customer agrees on the NEXT turn.
        On success the result carries `held`; say `held.spoken` exactly as
        written. Every error about the time carries `asked`: the time that was
        actually checked — talk about THAT time, never another hour.
        `error: "slot_unavailable"` means someone already booked `asked`; it
        carries `nearest`, the free slots closest to it; an empty `nearest`
        means no slot is available for that request — ask them for another
        day or another time. `outside_hours` (with `open`/`close`),
        `closed_day` and `time_passed` mean the salon cannot take `asked` at
        all — it is NOT taken by anyone. `missing_time` means you sent a date
        with no clock time — ask the customer what time.
        `note` is the service only (what they want done) — never the time or
        the day; those live in `start_at`.
        `replaces_appointment_id`: pass it ONLY when the customer wants to MOVE
        (reschedule) an appointment they already have. Copy the id from the
        context block. Leave it out for a brand-new booking."""
        # `note` do model gõ lại theo lời khách và được lưu vào đề xuất
        # (`proposals`) — đường này không đi qua `Appointment`, nên cap ở
        # `Appointment._clean_note` không tự áp dụng. Lọc lại ở đây.
        note = single_line(note, NOTE_MAX)
        if _DATE_ONLY.match(start_at or ""):
            return json.dumps({"error": "missing_time"})
        try:
            start = _parse_local(start_at)
        except ValueError:
            return json.dumps({"error": "bad_time_format"})

        # Giờ không thể đặt vì LỊCH TIỆM (quá khứ, ngày nghỉ, ngoài giờ mở cửa)
        # trả mã riêng TRƯỚC khi xem giờ trống — không thì nó rơi vào
        # `slot_unavailable` ("có người đặt rồi") và model báo kín một giờ chưa
        # ai đặt (lỗi live 2). Mã nào cũng mang `asked`: giờ ĐÃ kiểm.
        asked = _moment(start)
        if start <= now_utc():
            return json.dumps({"error": "time_passed", "asked": asked}, ensure_ascii=False)
        hours = await service.shop.get_hours()
        if (to_local(start).weekday() + 1) % 7 in hours.closed_days:
            return json.dumps({"error": "closed_day", "asked": asked}, ensure_ascii=False)
        if not fits_before_closing(hours, start, settings.booking_slot_minutes):
            return json.dumps({
                "error": "outside_hours", "asked": asked,
                "open": {"hhmm": hours.open_time, "spoken": format_vi_hhmm(hours.open_time)},
                "close": {"hhmm": hours.close_time, "spoken": format_vi_hhmm(hours.close_time)},
            }, ensure_ascii=False)

        # Dời lịch: kiểm id TRƯỚC khi giữ chỗ. Id bịa mà vẫn giữ chỗ thì khách
        # chốt "ừ" xong là ra hai lịch — đúng BUG-1.
        old = None
        if replaces_appointment_id:
            try:
                old = await service.own_booked(user, replaces_appointment_id)
            except AppError as exc:
                return json.dumps({"error": _error_code(exc)})

        # Khách đã có ĐÚNG lịch ở giờ này rồi. Phải tách khỏi `slot_unavailable`:
        # đó là nguyên nhân duy nhất mà câu đáp đúng NGƯỢC HẲN với "chọn giờ
        # khác đi" — giờ họ xin chính là giờ họ đang giữ. Trước 2026-09-20 ca
        # này rơi chung vào `slot_unavailable` nên model nói "em chưa giữ được
        # 4 giờ chiều" trong khi lịch đó đang tồn tại (thấy thật trong
        # transcript nghiệm thu).
        #
        # Chỉ kiểm khi KHÔNG dời lịch: đang dời thì `find_free_slots` đã nhả
        # các mốc của lịch cũ ra nên giờ đó tính là trống, đúng như mong đợi.
        if old is None:
            mine = await service.repo.find_active_at(str(user.id), start)
            if mine:
                return json.dumps({
                    "error": "already_yours",
                    "existing": {"id": str(mine.id), "at": _moment(mine.start_at)},
                }, ensure_ascii=False)

        # Kiểm trước khi hỏi khách. `find_free_slots` lọc sẵn quá khứ, ngoài
        # giờ mở cửa, ngày nghỉ và giờ đã có người — một truy vấn thay bốn lần
        # kiểm tay. `limit` phải phủ TRỌN ngày, mặc định 12 mốc chỉ tới gần
        # 11 giờ trưa. Đang dời thì mốc của chính lịch cũ tính là trống.
        free = await service.find_free_slots(
            to_local(start).date(), limit=WHOLE_DAY,
            ignore_appointment_id=str(old.id) if old else None,
        )
        if start not in free:
            # Ba mốc GẦN giờ khách xin nhất, không phải ba mốc đầu ngày: khách
            # xin 4 giờ chiều mà gợi ý 8 giờ sáng là gợi ý vô ích.
            nearest = nearest_slots(free, start)
            return json.dumps({"error": "slot_unavailable", "asked": asked,
                               "nearest": [_moment(s) for s in nearest]},
                              ensure_ascii=False)

        # Đề xuất cất vào DB nghiệp vụ (`proposals`), không vào state model:
        # giá trị đem đi ghi lịch lấy từ đó, KHÔNG từ chuỗi model gõ lại.
        # `proposal_id` đi qua `ConfirmMiddleware` vào state graph (middleware
        # gỡ nó khỏi JSON trước khi model đọc) — node `confirm` duyệt bằng id.
        proposal = await proposals.create(user, "move" if old else "book", start, note,
                                          target=old)

        result = {"held": _moment(start), "note": note}
        if old:
            result["replaces"] = {"id": str(old.id), "was": _moment(old.start_at)}
        result["proposal_id"] = str(proposal.id)
        return json.dumps(result, ensure_ascii=False)

    @tool
    async def list_my_appointments() -> str:
        """The customer's own upcoming appointments, as JSON, each with its id.
        The context block already lists them with the same ids; call this only
        when the appointment you need is not shown there.
        When the customer asks about their OWN appointments, call this
        IMMEDIATELY — never ask for a date first, the tool filters by the
        logged-in customer. An empty `appointments` list means they have none;
        say so plainly."""
        appointments = await service.upcoming_for(user)
        return json.dumps({"appointments": [
            {"id": str(a.id), "at": _moment(a.start_at), "note": a.note}
            for a in appointments
        ]}, ensure_ascii=False)

    @tool
    async def cancel_appointment(appointment_id: str) -> str:
        """Prepare to cancel one appointment, as JSON. Call this AS SOON AS the
        customer asks to cancel — do not ask them to confirm before calling it.
        Nothing is cancelled yet: the tool only records the intent, and the
        appointment is cancelled only when they agree on the NEXT turn. Never
        say it is already cancelled before that.
        `appointment_id` is the id on that appointment's line in the context
        block — copy it exactly. Never invent or guess an id; if the context
        block shows none, call list_my_appointments first. If the customer has
        two or more appointments, ask which one first."""
        # Không hủy ở đây. Hủy giống đặt: tool tạo đề xuất hủy, node confirm
        # mới làm thật khi khách đồng ý. Kiểm id trước để không giữ một ý hủy
        # vô nghĩa; đề xuất chụp lại lịch lúc này (dời/hủy chỗ khác → không duyệt).
        try:
            appt = await service.own_booked(user, appointment_id)
        except AppError as exc:
            return json.dumps({"error": _error_code(exc)})
        proposal = await proposals.create(user, "cancel", appt.start_at, None, target=appt)
        return json.dumps({"will_cancel": {"id": str(appt.id),
                                           "at": _moment(appt.start_at)},
                           "proposal_id": str(proposal.id)},
                          ensure_ascii=False)

    return [
        parse_time,
        find_free_slots,
        propose_appointment,
        list_my_appointments,
        cancel_appointment,
    ]
