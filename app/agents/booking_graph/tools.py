from datetime import date, datetime
from typing import List, Optional

from langchain_core.tools import BaseTool, tool
from motor.motor_asyncio import AsyncIOMotorDatabase

from app.agents.booking_graph.context import format_vi_datetime, format_vi_hhmm
from app.agents.booking_graph.timeparse import ParsedTime, parse_vi_time
from app.core.clock import TZ, now_utc, to_local
from app.core.errors import AppError
from app.core.text import single_line
from app.models.appointment import NOTE_MAX
from app.models.user import User
from app.services.appointment import AppointmentService
from app.services.conversation import ConversationService
from app.services.shop import ShopService

# Đủ để phủ trọn một ngày ở bước 15 phút (96 mốc), có dư.
WHOLE_DAY = 200


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


# Index CHÍNH LÀ giá trị trong closed_days: 0 = Chủ Nhật (app/models/shop.py).
# Ngược với datetime.weekday() của Python, nên tuyệt đối không dùng _WEEKDAYS.
_CLOSED_DAY_NAMES = [
    "Chủ Nhật", "Thứ Hai", "Thứ Ba", "Thứ Tư", "Thứ Năm", "Thứ Sáu", "Thứ Bảy",
]


def make_shop_tools(db: AsyncIOMotorDatabase, user: User) -> List[BaseTool]:
    shop = ShopService(db)

    @tool
    async def get_shop_status() -> str:
        """Whether the owner is busy or free, and if busy, what time they finish."""
        status = await shop.get_status()
        if not status.is_busy:
            return "Chủ tiệm đang rảnh."
        # Nói MỘT mốc giờ, không nói "còn N phút" — giống hệt thẻ trạng thái
        # trên giao diện. Nói hai kiểu ở hai chỗ là khách tưởng hai thông tin
        # khác nhau. Và câu trả lời của AI còn nằm lại trong lịch sử chat: "còn
        # 30 phút" đọc lại sau một tiếng là sai hẳn, "3:30 chiều" thì vẫn đúng.
        return f"Chủ tiệm đang bận, xong lúc {format_vi_datetime(status.busy_until)}."

    @tool
    async def get_shop_hours() -> str:
        """Opening hours and closed days. Call this when the customer asks what
        time the salon opens or closes, or whether it is open on a given day."""
        hours = await shop.get_hours()
        closed = [_CLOSED_DAY_NAMES[d] for d in sorted(hours.closed_days)
                  if 0 <= d < len(_CLOSED_DAY_NAMES)]
        closed_text = f"Nghỉ {', '.join(closed)}." if closed else "Mở cả tuần."
        return (
            f"Tiệm mở từ {format_vi_hhmm(hours.open_time)} "
            f"đến {format_vi_hhmm(hours.close_time)}. {closed_text}"
        )

    return [get_shop_status, get_shop_hours]


def make_booking_tools(db: AsyncIOMotorDatabase, user: User) -> List[BaseTool]:
    """Tool đóng kín `user` trong closure.

    `user_id` KHÔNG phải tham số của tool: AI không thể bị dụ thao tác lịch của
    người khác, kể cả khi khách gõ "hủy lịch của bà Lan".
    """
    service = AppointmentService(db)
    conversations = ConversationService(db)

    @tool
    async def parse_time(text: str) -> str:
        """Turn what the customer said about time into a concrete date and time.
        Call this BEFORE find_free_slots and propose_appointment, every time the
        customer mentions a time. Never compute a date yourself.
        `text` must be the FULL phrase, joining what the customer said on earlier
        turns: if they said "sáng mai" then answered "9 giờ", pass
        "sáng mai 9 giờ", not "9 giờ". This tool reads only the string you give
        it — it cannot see earlier turns.
        Example `text`: "mai 3h chiều", "thứ Năm tuần sau", "sáng mai".
        Result has `missing` -> ask the customer for exactly that ONE missing
        piece, one piece per turn. For missing ["sáng hay chiều"] ask
        "Dạ 3 giờ chiều hay 3 giờ sáng ạ chị?" and nothing else."""
        parsed: ParsedTime = await parse_vi_time(text, now_utc())
        # Trả JSON gọn thay vì câu tiếng Việt: agent cần chuỗi ISO nguyên vẹn
        # để chuyển thẳng sang propose_appointment, không được diễn giải lại.
        return parsed.model_dump_json(
            include={"start_at", "partial_date", "missing"}, exclude_none=False
        )

    @tool
    async def find_free_slots(day: str) -> str:
        """Free slots on one day. `day` is YYYY-MM-DD, taken from the
        "Bây giờ là..." line in the context block — never guess the date.
        Use ONLY what this tool returns — never invent a free slot.
        If the time they asked for is taken, offer the two free slots nearest
        to it.
        Only when the customer says the salon may choose ("lúc nào vắng thì
        xếp em", "khi nào rảnh cũng được") call this for today — or tomorrow
        if today is finished — then offer two or three slots. If they have NOT
        named a day, ask which day first. Never assume today."""
        try:
            slots = await service.find_free_slots(date.fromisoformat(day))
        except ValueError:
            return "Ngày không hợp lệ, cần dạng YYYY-MM-DD."
        if not slots:
            return "Ngày đó không còn giờ trống."
        return "Các giờ còn trống: " + ", ".join(format_vi_datetime(s) for s in slots)

    @tool
    async def propose_appointment(start_at: str, note: Optional[str] = None) -> str:
        """Hold the slot temporarily and prepare the confirmation question.
        `start_at` is ISO 8601, copied UNCHANGED from parse_time's result.
        Call this tool, then ask the customer to confirm. NO tool writes an
        appointment directly — it is written only when the customer agrees on
        the NEXT turn."""
        # `note` ở đây do model tự gõ lại theo lời khách, và được lưu vào
        # `pending_confirmation` + in thẳng vào chuỗi trả về — cả hai đường
        # này không đi qua `Appointment`, nên cái cap ở `Appointment._clean_note`
        # (single_line + NOTE_MAX) không tự động áp dụng. Phải lọc lại ở đây.
        note = single_line(note, NOTE_MAX)
        try:
            start = _parse_local(start_at)
        except ValueError:
            return "Thời gian không hợp lệ."

        # Kiểm trước khi hỏi khách. Hỏi "3 giờ chiều đúng không chị?" rồi mới báo
        # giờ đó có người là bắt khách chọn lại hai lần.
        # `find_free_slots` lọc sẵn cả quá khứ, ngoài giờ mở cửa, ngày nghỉ và
        # giờ đã có người — một truy vấn thay cho bốn lần kiểm tay.
        #
        # `limit` phải phủ TRỌN ngày. Mặc định của service là 12 mốc, tức chỉ
        # tới gần 11 giờ trưa; để nguyên thì mọi giờ chiều đều bị báo "không đặt
        # được" dù còn trống.
        free = await service.find_free_slots(to_local(start).date(), limit=WHOLE_DAY)
        if start not in free:
            if not free:
                return "Ngày đó không còn giờ trống. Hãy hỏi khách chọn ngày khác."
            # Gợi ý ba mốc GẦN giờ khách xin nhất, không phải ba mốc đầu ngày:
            # khách xin 4 giờ chiều mà gợi ý 8, 8:15, 8:30 sáng là gợi ý vô ích.
            nearest = sorted(sorted(free, key=lambda s: abs(s - start))[:3])
            suggestion = ", ".join(format_vi_datetime(s) for s in nearest)
            return f"Giờ đó không đặt được. Các giờ còn trống gần nhất: {suggestion}."

        # Lưu vào Mongo để lượt sau đọc lại. Đây là điểm mấu chốt: giá trị đem đi
        # ghi lịch lấy từ DB, KHÔNG phải từ chuỗi model gõ lại — nên model không
        # thể chép sai giờ giữa hai lượt.
        await conversations.set_pending(
            str(user.id), {"start_at": start.isoformat(), "note": note},
        )
        note_text = f", {note}" if note else ""
        return (
            f"Đã giữ chỗ {format_vi_datetime(start)}{note_text}. "
            "Hãy nhắc lại đầy đủ ngày giờ và hỏi khách xác nhận."
        )

    @tool
    async def list_my_appointments() -> str:
        """The customer's own upcoming appointments. Call this before cancelling,
        to get the appointment id.
        When the customer asks about their OWN appointments ("chị có lịch lúc
        nào", "xem giùm em"), call this IMMEDIATELY — never ask for a date
        first, the tool filters by the logged-in customer. If they have none,
        say so plainly."""
        appointments = await service.upcoming_for(user)
        if not appointments:
            return "Khách chưa có lịch nào sắp tới."
        lines = []
        for appt in appointments:
            note_text = f" — {appt.note}" if appt.note else ""
            lines.append(f"{format_vi_datetime(appt.start_at)}{note_text} [id: {appt.id}]")
        return "\n".join(lines)

    @tool
    async def cancel_appointment(appointment_id: str) -> str:
        """Cancel one appointment. Call list_my_appointments first to get the id.
        If the customer has two or more, ask which one before cancelling."""
        try:
            await service.cancel(user, appointment_id)
        except AppError as exc:
            return exc.message
        return "Đã hủy lịch."

    return [
        parse_time,
        find_free_slots,
        propose_appointment,
        list_my_appointments,
        cancel_appointment,
    ]
