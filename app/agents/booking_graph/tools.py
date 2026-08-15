from datetime import date, datetime
from typing import List, Optional

from langchain_core.tools import BaseTool, tool
from motor.motor_asyncio import AsyncIOMotorDatabase

from app.agents.booking_graph.context import format_vi_datetime
from app.agents.booking_graph.timeparse import ParsedTime, parse_vi_time
from app.core.clock import TZ, now_utc, to_local
from app.core.errors import AppError
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


def make_status_tools(db: AsyncIOMotorDatabase, user: User) -> List[BaseTool]:
    shop = ShopService(db)

    @tool
    async def get_shop_status() -> str:
        """Xem chủ tiệm đang bận hay đang rảnh, và nếu bận thì mấy giờ xong."""
        status = await shop.get_status()
        if not status.is_busy:
            return "Chủ tiệm đang rảnh."
        # Nói MỘT mốc giờ, không nói "còn N phút" — giống hệt thẻ trạng thái
        # trên giao diện. Nói hai kiểu ở hai chỗ là khách tưởng hai thông tin
        # khác nhau. Và câu trả lời của AI còn nằm lại trong lịch sử chat: "còn
        # 30 phút" đọc lại sau một tiếng là sai hẳn, "3:30 chiều" thì vẫn đúng.
        return f"Chủ tiệm đang bận, xong lúc {format_vi_datetime(status.busy_until)}."

    return [get_shop_status]


def make_booking_tools(db: AsyncIOMotorDatabase, user: User) -> List[BaseTool]:
    """Tool đóng kín `user` trong closure.

    `user_id` KHÔNG phải tham số của tool: AI không thể bị dụ thao tác lịch của
    người khác, kể cả khi khách gõ "hủy lịch của bà Lan".
    """
    service = AppointmentService(db)
    conversations = ConversationService(db)

    @tool
    async def parse_time(text: str) -> str:
        """Quy câu nói về thời gian của khách ra ngày giờ chuẩn.
        Gọi tool này TRƯỚC find_free_slots và propose_appointment, mỗi khi khách
        nhắc tới thời gian. Không tự tính ngày.
        Ví dụ text: "mai 3h chiều", "thứ Năm tuần sau", "sáng mai"."""
        parsed: ParsedTime = await parse_vi_time(text, now_utc())
        # Trả JSON gọn thay vì câu tiếng Việt: agent cần chuỗi ISO nguyên vẹn
        # để chuyển thẳng sang propose_appointment, không được diễn giải lại.
        return parsed.model_dump_json(
            include={"start_at", "partial_date", "missing"}, exclude_none=False
        )

    @tool
    async def find_free_slots(day: str) -> str:
        """Xem các giờ còn trống trong một ngày. `day` theo định dạng YYYY-MM-DD,
        tính từ mốc "Bây giờ là..." trong phần bối cảnh — không được đoán ngày."""
        try:
            slots = await service.find_free_slots(date.fromisoformat(day))
        except ValueError:
            return "Ngày không hợp lệ, cần dạng YYYY-MM-DD."
        if not slots:
            return "Ngày đó không còn giờ trống."
        return "Các giờ còn trống: " + ", ".join(format_vi_datetime(s) for s in slots)

    @tool
    async def propose_appointment(start_at: str, note: Optional[str] = None) -> str:
        """Giữ chỗ tạm thời và chuẩn bị câu hỏi xác nhận cho khách.
        `start_at` dạng ISO 8601, lấy NGUYÊN từ kết quả parse_time.
        Gọi tool này rồi hỏi khách xác nhận. KHÔNG có tool nào ghi lịch trực tiếp —
        lịch chỉ được ghi khi khách trả lời đồng ý ở lượt sau."""
        try:
            start = _parse_local(start_at)
        except ValueError:
            return "Thời gian không hợp lệ."

        # Kiểm trước khi hỏi khách. Hỏi "3 giờ chiều đúng không cô?" rồi mới báo
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
            gan_nhat = sorted(sorted(free, key=lambda s: abs(s - start))[:3])
            goi_y = ", ".join(format_vi_datetime(s) for s in gan_nhat)
            return f"Giờ đó không đặt được. Các giờ còn trống gần nhất: {goi_y}."

        # Lưu vào Mongo để lượt sau đọc lại. Đây là điểm mấu chốt: giá trị đem đi
        # ghi lịch lấy từ DB, KHÔNG phải từ chuỗi model gõ lại — nên model không
        # thể chép sai giờ giữa hai lượt.
        await conversations.set_pending(
            str(user.id), {"start_at": start.isoformat(), "note": note}
        )
        note_text = f", {note}" if note else ""
        return (
            f"Đã giữ chỗ {format_vi_datetime(start)}{note_text}. "
            "Hãy nhắc lại đầy đủ ngày giờ và hỏi khách xác nhận."
        )

    @tool
    async def list_my_appointments() -> str:
        """Xem các lịch sắp tới của chính khách đang chat. Gọi tool này trước khi
        hủy lịch, để lấy đúng mã lịch."""
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
        """Hủy một lịch. Phải gọi list_my_appointments trước để lấy mã lịch.
        Nếu khách có từ hai lịch trở lên, phải hỏi rõ hủy lịch nào."""
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
