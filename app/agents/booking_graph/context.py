import asyncio
from datetime import datetime
from typing import List, Optional

from motor.motor_asyncio import AsyncIOMotorDatabase

from app.core.clock import now_utc, to_local
from app.models.appointment import Appointment
from app.models.shop import ShopStatusView
from app.models.user import User
from app.services.appointment import AppointmentService
from app.services.conversation import ConversationService
from app.services.shop import ShopService

_WEEKDAYS = ["Thứ Hai", "Thứ Ba", "Thứ Tư", "Thứ Năm", "Thứ Sáu", "Thứ Bảy", "Chủ Nhật"]


def format_vi_datetime(dt) -> str:
    """'Thứ Sáu 7/8, 3 giờ chiều' — cách người Việt lớn tuổi thực sự nói giờ.

    Không dùng dấu hai chấm: "3:00 chiều" là cách máy viết giờ. Chuỗi này đi
    thẳng vào lời thoại (câu chốt lịch của node confirm, danh sách lịch trong
    khối bối cảnh) nên nó phải đọc lên nghe được.
    """
    local = to_local(dt)
    hour = local.hour
    if hour < 12:
        period, display = "sáng", hour
    elif hour < 18:
        period, display = "chiều", hour - 12 if hour > 12 else 12
    else:
        period, display = "tối", hour - 12

    minute = local.minute
    if minute == 0:
        clock = f"{display} giờ"
    elif minute == 30:
        clock = f"{display} giờ rưỡi"      # không ai đọc "9 giờ 30"
    else:
        clock = f"{display} giờ {minute:02d}"
    return f"{_WEEKDAYS[local.weekday()]} {local.day}/{local.month}, {clock} {period}"


def build_context_block(
    user: User,
    status: ShopStatusView,
    upcoming: List[Appointment],
    now: Optional[datetime] = None,
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
        f"Bạn đang nói chuyện với: {user.full_name or 'khách'} ({user.phone}).",
    ]

    if status.is_busy:
        # Mốc giờ, không phải khoảng. Khối này đi vào prompt và AI sẽ nhắc lại
        # cho khách; "còn 30 phút" nằm lại trong lịch sử chat là sai vĩnh viễn.
        lines.append(f"Chủ tiệm: đang bận, xong lúc {format_vi_datetime(status.busy_until)}.")
    else:
        lines.append("Chủ tiệm: đang rảnh.")

    if upcoming:
        lines.append("Lịch sắp tới của khách:")
        for appt in upcoming[:5]:
            note = f" — {appt.note}" if appt.note else ""
            lines.append(f"  - {format_vi_datetime(appt.start_at)}{note}")
    else:
        lines.append("Khách chưa có lịch nào sắp tới.")

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

    status, upcoming, history, pending = await asyncio.gather(
        ShopService(db).get_status(),
        AppointmentService(db).upcoming_for(user),
        conversations.history(user_id),
        conversations.get_pending(user_id),
    )

    return {
        "context_block": build_context_block(user, status, upcoming),
        "history": history,
        "pending_confirmation": pending,
    }
