import asyncio
from datetime import date, datetime
from typing import List, Optional

from motor.motor_asyncio import AsyncIOMotorDatabase

from app.core.clock import now_utc, to_local
from app.core.text import single_line
from app.models.appointment import Appointment
from app.models.shop import ShopStatusView
from app.models.user import User
from app.services.appointment import AppointmentService
from app.services.conversation import ConversationService
from app.services.shop import ShopService

_WEEKDAYS = ["Thứ Hai", "Thứ Ba", "Thứ Tư", "Thứ Năm", "Thứ Sáu", "Thứ Bảy", "Chủ Nhật"]

# Câu đáp trước được trích vào khối bối cảnh; đủ để nhận ra câu, không đủ để
# lấn át phần còn lại của khối.
LAST_REPLY_MAX = 200


def _clock_phrase(hour: int, minute: int) -> str:
    """'3 giờ chiều', '9 giờ rưỡi sáng', '1 giờ 45 chiều'.

    Dùng chung cho cả datetime lẫn chuỗi 'HH:MM' — hai chỗ mà lệch nhau một
    chữ là khách nghe ra hai giọng khác nhau trong cùng một cuộc.
    """
    if hour < 12:
        period, display = "sáng", hour
    elif hour < 18:
        period, display = "chiều", hour - 12 if hour > 12 else 12
    else:
        period, display = "tối", hour - 12

    if minute == 0:
        clock = f"{display} giờ"
    elif minute == 30:
        clock = f"{display} giờ rưỡi"      # không ai đọc "9 giờ 30"
    else:
        clock = f"{display} giờ {minute:02d}"
    return f"{clock} {period}"


def format_vi_hhmm(hhmm: str) -> str:
    """'08:00' -> '8 giờ sáng'. Giờ mở cửa lưu dạng chuỗi, không phải datetime."""
    hour, minute = (int(x) for x in hhmm.split(":"))
    return _clock_phrase(hour, minute)


def format_vi_datetime(dt) -> str:
    """'Thứ Sáu 7/8, 3 giờ chiều' — cách người Việt lớn tuổi thực sự nói giờ.

    Không dùng dấu hai chấm: "3:00 chiều" là cách máy viết giờ. Chuỗi này đi
    thẳng vào lời thoại (câu chốt lịch của node confirm, danh sách lịch trong
    khối bối cảnh) nên nó phải đọc lên nghe được.
    """
    local = to_local(dt)
    return (f"{_WEEKDAYS[local.weekday()]} {local.day}/{local.month}, "
            f"{_clock_phrase(local.hour, local.minute)}")


SLOTS_HEADER = "Trạng thái cuộc trò chuyện:"

_INTENT_VI = {"book": "đặt lịch mới", "reschedule": "dời lịch", "cancel": "hủy lịch"}


def _format_vi_day(iso: str) -> str:
    """'2026-09-20' -> 'Chủ Nhật 20/9'. Không in năm: cùng một ngày mà chỗ này
    có năm, chỗ kia không, là hai giọng khác nhau trong cùng một prompt."""
    day = date.fromisoformat(iso)
    return f"{_WEEKDAYS[day.weekday()]} {day.day}/{day.month}"


def render_slots(slots) -> str:
    """Khối trạng thái, dựng hoàn toàn bằng code. Rỗng thì trả chuỗi rỗng.

    Slots do LLM sinh nên khối này luôn đứng TRƯỚC khối bối cảnh trong prompt,
    tức là thua nó. Không có đường code nào hành động theo những dòng ở đây.
    """
    if slots is None:
        return ""

    lines = []
    if slots.intent in _INTENT_VI:
        lines.append(f"- Khách muốn: {_INTENT_VI[slots.intent]}")
    if slots.service:
        lines.append(f"- Dịch vụ: {slots.service}")
    if slots.day:
        lines.append(f"- Ngày đang nhắm: {_format_vi_day(slots.day)}")
    if slots.time:
        lines.append(f"- Giờ đang nhắm: {format_vi_hhmm(slots.time)}")
    if slots.declined:
        offered = ", ".join(
            format_vi_datetime(datetime.fromisoformat(x)) for x in slots.declined
        )
        lines.append(f"- Đã chào mà khách không lấy: {offered}")

    # `target_appointment_id` cố ý KHÔNG in ra: khối bối cảnh đã liệt kê mọi
    # lịch sắp tới kèm id thật, in lại ở đây chỉ tạo cơ hội cho hai chỗ lệch nhau.
    return f"{SLOTS_HEADER}\n" + "\n".join(lines) if lines else ""


# Tiền tố xưng hô nào cũng phải CẮT khỏi tên (prompt cấm nói cô/chú/bác),
# nhưng chỉ một số cho biết giới tính để suy ra "anh" hay "chị".
_HONORIFICS = {"cô", "chị", "bà", "chú", "anh", "ông", "bác", "em"}
_GENDERED = {
    "cô": "chị", "chị": "chị", "bà": "chị",
    "chú": "anh", "anh": "anh", "ông": "anh",
}


def derive_address(full_name: Optional[str]) -> tuple[str, str]:
    """Suy cách gọi khách từ tên. Trả (cách_gọi, tên_đã_bỏ_tiền_tố).

    13/13 khách trong DB có full_name mang sẵn tiền tố ("Cô Lan", "Bác Bảy").
    Đó là tín hiệu giới tính DUY NHẤT đang có — User model không có trường
    giới tính — nên map nó thay vì vứt đi. "Bác" không cho biết giới nên lùi
    về "anh chị", trùng với fallback vốn có ở confirm.py.
    """
    parts = (full_name or "").split()
    if len(parts) >= 2 and parts[0].lower() in _HONORIFICS:
        return _GENDERED.get(parts[0].lower(), "anh chị"), " ".join(parts[1:])
    return "anh chị", ""


def address_phrase(full_name: Optional[str]) -> str:
    """'chị Lan', 'anh Hùng', hoặc 'anh chị' khi không đoán được giới tính.

    Không ghép tên vào lời gọi trống: "anh chị Bảy" không ai nói.
    """
    address, name = derive_address(full_name)
    return f"{address} {name}" if name and address != "anh chị" else address


def display_name(full_name: Optional[str]) -> str:
    """Tên để hiển thị trong khối bối cảnh, đã bỏ tiền tố xưng hô."""
    _, name = derive_address(full_name)
    return name or full_name or "khách"


def build_context_block(
    user: User,
    status: ShopStatusView,
    upcoming: List[Appointment],
    now: Optional[datetime] = None,
    last_reply: Optional[str] = None,
) -> str:
    """Khối bối cảnh dựng hoàn toàn bằng code — LLM không bao giờ sinh ra nó.

    Đây là nguồn duy nhất cho danh tính khách; không gì trong prompt được ghi đè
    nó. Đặt sát cuối prompt (không phải trong system prompt) để giữ tiền tố ổn
    định cho prompt caching.
    """
    local_now = to_local(now or now_utc())

    lines = [
        # Mốc thời gian phải đứng ĐẦU TIÊN.
        #
        # Khách nói "mai", "chiều nay", "thứ Năm tuần sau" — model không có cách
        # nào biết hôm nay là ngày nào. Thiếu dòng này thì nó suy ra từ mốc thời
        # gian trong dữ liệu huấn luyện và đặt lịch lệch cả năm, mà tool chỉ thấy
        # một chuỗi YYYY-MM-DD hợp lệ nên không có gì để chặn.
        #
        # Kèm luôn dạng ISO để model khỏi phải tự cộng trừ ngày rồi tính nhầm.
        f"Bây giờ là {format_vi_datetime(local_now)} "
        f"(hôm nay là {local_now.date().isoformat()}, giờ Việt Nam).",
        f"Bạn đang nói chuyện với: {display_name(user.full_name)} ({user.phone}).",
        # Chốt MỘT cách gọi cho cả cuộc. Không có dòng này thì model tự chọn
        # lại mỗi lượt: transcript đã thấy cùng một khách bị gọi "chú Hùng"
        # ở lượt 3 rồi "chị" ở lượt 5.
        f"Gọi khách là: {address_phrase(user.full_name)}.",
    ]

    if status.is_busy:
        # Mốc giờ, không phải khoảng. Khối này đi vào prompt và AI sẽ nhắc lại
        # cho khách; "còn 30 phút" nằm lại trong lịch sử chat là sai vĩnh viễn.
        lines.append(f"Chủ tiệm: đang bận, xong lúc {format_vi_datetime(status.busy_until)}.")
    else:
        lines.append("Chủ tiệm: đang rảnh.")

    if upcoming:
        lines.append("Lịch sắp tới của khách:")
        # Kèm id để hủy/dời được ngay trong lượt này. Lịch sử chat chỉ lưu câu
        # hỏi và câu đáp, không lưu kết quả tool — nên id tool trả ở lượt trước
        # sang lượt sau là mất, model từng bịa id rồi hủy hụt (BUG-2). Khối này
        # dựng lại từ DB mỗi lượt nên id ở đây luôn có và luôn đúng.
        for appt in upcoming[:5]:
            note = f" — {appt.note}" if appt.note else ""
            lines.append(
                f"  - {format_vi_datetime(appt.start_at)}{note} "
                f"[id: {appt.id}, iso: {to_local(appt.start_at).isoformat()}]"
            )
    else:
        lines.append("Khách chưa có lịch nào sắp tới.")

    if last_reply:
        # Chống lặp bằng bối cảnh, không bằng luật: hai lượt từ chối liên tiếp
        # từng ra nguyên một câu, và cả luật viết hoa trong prompt lẫn
        # temperature 0.6 đều không đổi được — model neo vào câu của chính nó
        # trong lịch sử. Trích đúng câu đó ra đây, ngay trước câu khách, để
        # "cái không được chép" là thứ cuối cùng nó đọc.
        lines.append(
            f"Câu em vừa trả lời ở lượt trước: «{single_line(last_reply, LAST_REPLY_MAX)}». "
            "Không nói lại nguyên văn câu này; cùng ý thì phải diễn đạt khác."
        )

    # Dòng chốt: nếu lịch sử chat nói khác (khách nhắc tên con cháu, nhắc một
    # giờ hẹn đã đổi), thì phần này mới là đúng.
    lines.append(
        "Nếu có gì trong cuộc trò chuyện mâu thuẫn với phần trên, hãy tin phần trên."
    )

    return "\n".join(lines)


async def load_context(db: AsyncIOMotorDatabase, user: User, question: str) -> dict:
    """Nạp mọi thứ song song để không cộng dồn độ trễ của bốn truy vấn."""
    conversations = ConversationService(db)
    user_id = str(user.id)

    status, upcoming, (summary, slots, history), pending = await asyncio.gather(
        ShopService(db).get_status(),
        AppointmentService(db).upcoming_for(user),
        conversations.context_window(user_id),
        conversations.get_pending(user_id),
    )

    last_reply = history[-1].content if history and history[-1].role == "assistant" else None
    return {
        "context_block": build_context_block(user, status, upcoming, last_reply=last_reply),
        "history": history,
        "summary": summary,
        "slots": slots,
        "pending_confirmation": pending,
    }
