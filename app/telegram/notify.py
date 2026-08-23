from app.models.appointment import Appointment
from app.models.shop import ShopStatusView
from app.telegram.client import admin_chat_ids

from app.agents.booking_graph.context import format_vi_datetime


class TelegramNotifier:
    """Kênh Telegram của chỗ tỏa tin. Chỉ gửi, không nhận — phần nhận là vòng
    lặp polling ở task 4."""

    def __init__(self, client):
        self.client = client

    async def _broadcast(self, text: str) -> None:
        for chat_id in admin_chat_ids():
            await self.client.send_message(chat_id, text)

    async def appointment_created(self, appointment: Appointment) -> None:
        note = f" — {appointment.note}" if appointment.note else ""
        await self._broadcast(
            "Có lịch mới ạ:\n"
            f"{format_vi_datetime(appointment.start_at)} · "
            f"{appointment.user_name or 'khách'}{note}\n"
            f"{appointment.phone or ''}"
        )

    async def appointment_cancelled(self, appointment: Appointment) -> None:
        await self._broadcast(
            "Khách vừa hủy lịch:\n"
            f"{format_vi_datetime(appointment.start_at)} · "
            f"{appointment.user_name or 'khách'}"
        )

    async def shop_status_changed(self, status: ShopStatusView) -> None:
        if status.is_busy:
            until = (
                format_vi_datetime(status.busy_until)
                if status.busy_until else "chưa rõ"
            )
            await self._broadcast(
                f"Đã chuyển sang Đang bận, xong lúc {until}."
            )
        else:
            await self._broadcast("Đã chuyển sang Đang rảnh.")
