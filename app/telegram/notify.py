from datetime import timedelta

from app.core.clock import to_local
from app.models.appointment import Appointment
from app.models.shop import ShopStatusView
from app.services.service_catalog import ServiceCatalogService
from app.telegram.client import admin_chat_ids

from app.core.vi_format import format_vi_datetime, format_vi_hhmm


class TelegramNotifier:
    """Kênh Telegram của chỗ tỏa tin. Chỉ gửi, không nhận — phần nhận là vòng
    lặp polling ở task 4.

    `db` (tuỳ chọn): cần để tra tên dịch vụ (Task 3) — không có thì tin lịch
    mới vẫn gửi được, chỉ thiếu dòng tên dịch vụ. Test cũ dựng notifier không
    truyền `db`.
    """

    def __init__(self, client, db=None):
        self.client = client
        self.db = db

    async def _broadcast(self, text: str) -> None:
        for chat_id in admin_chat_ids():
            await self.client.send_message(chat_id, text)

    async def _service_line(self, service_ids) -> str:
        if not service_ids or self.db is None:
            return ""
        services = await ServiceCatalogService(self.db).get_many(service_ids)
        return "\nDịch vụ: " + ", ".join(service.name for service in services)

    async def appointment_created(self, appointment: Appointment) -> None:
        note = f" — {appointment.note}" if appointment.note else ""
        end_at = appointment.start_at + timedelta(minutes=appointment.duration_minutes)
        end_hhmm = to_local(end_at).strftime("%H:%M")
        await self._broadcast(
            "Có lịch mới ạ:\n"
            f"{format_vi_datetime(appointment.start_at)} · "
            f"{appointment.user_name or 'khách'}{note}"
            f"{await self._service_line(appointment.service_ids)}\n"
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
