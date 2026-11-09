from app.core.clock import to_local
from app.models.appointment import Appointment
from app.models.shop import ShopStatusView
from app.services.appointment import describe_appointments
from app.services.service_catalog import ServiceCatalogService
from app.telegram.client import admin_chat_ids

from app.core.vi_format import format_vi_datetime, format_vi_hhmm


class TelegramNotifier:
    """Kênh Telegram của chỗ tỏa tin. Chỉ gửi, không nhận — phần nhận là vòng
    lặp polling ở task 4.

    `db` (tuỳ chọn): cần để tra tên dịch vụ qua `describe_appointments` (Task
    3, dùng chung với REST/`SocketNotifier`) — không có thì tin lịch mới vẫn
    gửi được, chỉ thiếu dòng tên dịch vụ. Test cũ dựng notifier không truyền
    `db`.
    """

    def __init__(self, client, db=None):
        self.client = client
        self.db = db

    async def _broadcast(self, text: str) -> None:
        for chat_id in admin_chat_ids():
            await self.client.send_message(chat_id, text)

    async def appointment_created(self, appointment: Appointment) -> None:
        catalog = ServiceCatalogService(self.db) if self.db is not None else None
        [detail] = await describe_appointments(catalog, [appointment])

        note = f" — {appointment.note}" if appointment.note else ""
        service_line = (
            "\nDịch vụ: " + ", ".join(service.name for service in detail.services)
            if detail.services else ""
        )
        end_hhmm = to_local(detail.end_at).strftime("%H:%M")
        await self._broadcast(
            "Có lịch mới ạ:\n"
            f"{format_vi_datetime(appointment.start_at)} · "
            f"{appointment.user_name or 'khách'}{note}"
            f"{service_line}\n"
            f"xong lúc {format_vi_hhmm(end_hhmm)}\n"
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
