from datetime import timedelta
from typing import Optional

from motor.motor_asyncio import AsyncIOMotorDatabase

from app.core.clock import now_utc, to_local
from app.core.logging import get_logger
from app.services.appointment import AppointmentService
from app.services.notifications import notifications
from app.services.shop import ShopService
from app.telegram.client import (MAIN_KEYBOARD, admin_chat_ids,
                                 duration_keyboard)

from app.agents.booking_graph.context import format_vi_datetime

logger = get_logger(__name__)

GREETING = (
    "Dạ chào chủ tiệm. Bấm nút bên dưới để xem lịch hoặc đổi trạng thái bận rảnh ạ."
)
NUDGE = "Dạ chủ tiệm bấm một trong bốn nút bên dưới giúp con ạ."


class TelegramHandlers:
    """Xử lý update từ Telegram. Hoàn toàn tất định — bấm nút nào gọi service nấy,
    không có AI, không tốn token, test không cần LLM."""

    def __init__(self, db: AsyncIOMotorDatabase, client):
        self.db = db
        self.client = client
        self.shop = ShopService(db)
        self.appointments = AppointmentService(db)

    async def handle_update(self, update: dict) -> None:
        try:
            if "callback_query" in update:
                await self._handle_callback(update["callback_query"])
            elif "message" in update:
                await self._handle_message(update["message"])
        except Exception as exc:
            logger.warning("telegram_update_failed", extra={"error": str(exc)})

    @staticmethod
    def _chat_id(payload: dict) -> Optional[int]:
        return ((payload or {}).get("chat") or {}).get("id")

    def _authorised(self, chat_id: Optional[int]) -> bool:
        return chat_id is not None and chat_id in admin_chat_ids()

    async def _handle_message(self, message: dict) -> None:
        chat_id = self._chat_id(message)
        # Bỏ qua IM LẶNG: không trả lời gì để người lạ dò ra bot cũng không biết
        # nó làm gì. Bot đọc được tên và số điện thoại khách.
        if not self._authorised(chat_id):
            return

        text = (message.get("text") or "").strip()

        if text in ("/start", "/help"):
            await self.client.send_message(chat_id, GREETING, keyboard=MAIN_KEYBOARD)
        elif text == "Hôm nay":
            await self._send_schedule(chat_id, days_ahead=0, label="hôm nay")
        elif text == "Ngày mai":
            await self._send_schedule(chat_id, days_ahead=1, label="ngày mai")
        elif text == "Tôi đang bận":
            await self.client.send_message(
                chat_id, "Chủ tiệm bận khoảng bao lâu ạ?", keyboard=duration_keyboard()
            )
        elif text == "Tôi rảnh rồi":
            status = await self.shop.set_free()
            await notifications.shop_status_changed(status)
            # Không gửi tin thứ hai ở đây — chỗ tỏa tin duy nhất (NotificationService)
            # sẽ gửi qua TelegramNotifier. Bàn phím persistent vẫn còn trong chat.
        else:
            await self.client.send_message(chat_id, NUDGE, keyboard=MAIN_KEYBOARD)

    async def _handle_callback(self, query: dict) -> None:
        chat_id = self._chat_id(query.get("message") or {})
        if not self._authorised(chat_id):
            return

        data = query.get("data") or ""
        if not data.startswith("busy:"):
            await self.client.answer_callback(query["id"])
            return

        # Trả lời TRƯỚC khi làm việc. Telegram cho callback query hạn 10 giây;
        # `shop_status_changed` tỏa tin ra cả Socket.IO lẫn Telegram, mỗi kênh
        # một vòng HTTP — để sau thì nút quay mãi và chủ tiệm bấm lại lần nữa.
        await self.client.answer_callback(query["id"], "Đã ghi nhận")

        minutes = int(data.split(":", 1)[1])
        status = await self.shop.set_busy(minutes)
        await notifications.shop_status_changed(status)
        # Không gửi tin thứ hai ở đây — chỗ tỏa tin duy nhất (NotificationService)
        # sẽ gửi qua TelegramNotifier. Bàn phím persistent vẫn còn trong chat.

    async def _send_schedule(self, chat_id: int, days_ahead: int, label: str) -> None:
        day = (to_local(now_utc()) + timedelta(days=days_ahead)).date()
        appointments = await self.appointments.day_schedule(day)

        if not appointments:
            await self.client.send_message(
                chat_id, f"Dạ {label} không có lịch nào ạ.", keyboard=MAIN_KEYBOARD
            )
            return

        lines = [f"Lịch {label} — {len(appointments)} khách:"]
        for appt in appointments:
            note = f" — {appt.note}" if appt.note else ""
            lines.append(
                f"• {format_vi_datetime(appt.start_at)} · {appt.user_name or 'khách'}"
                f"{note}\n  {appt.phone or ''}"
            )
        await self.client.send_message(chat_id, "\n".join(lines), keyboard=MAIN_KEYBOARD)
