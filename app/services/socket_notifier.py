from app.models.appointment import Appointment
from app.models.shop import ShopStatusView
from app.services.appointment import describe_appointments
from app.services.service_catalog import ServiceCatalogService


class SocketNotifier:
    """Kênh Socket.IO của chỗ tỏa tin.

    `shop_status_changed` phải broadcast tới MỌI user đang online, không chỉ admin:
    thẻ trạng thái trên màn hình chat của khách phải đổi mà không cần tải lại.

    `db` (tuỳ chọn): cần để tra tên dịch vụ qua `describe_appointments` (Task
    3) — không có thì `appointment_created`/`appointment_cancelled` vẫn phát
    được, chỉ thiếu tên dịch vụ (`services` rỗng); `end_at` luôn đúng vì tính
    thẳng từ `duration_minutes`, không cần tra cứu. Test cũ dựng notifier
    không truyền `db`.
    """

    def __init__(self, socketio_service, db=None):
        self.socketio = socketio_service
        self.db = db

    async def _describe(self, appointment: Appointment):
        catalog = ServiceCatalogService(self.db) if self.db is not None else None
        [detail] = await describe_appointments(catalog, [appointment])
        return detail

    async def appointment_created(self, appointment: Appointment) -> None:
        detail = await self._describe(appointment)
        await self.socketio.emit_to_admins("appointment_created", {
            "id": detail.id,
            "start_at": detail.start_at.isoformat(),
            "end_at": detail.end_at.isoformat(),
            "user_name": appointment.user_name,
            "phone": appointment.phone,
            "note": appointment.note,
            "services": [s.model_dump() for s in detail.services],
        })

    async def appointment_cancelled(self, appointment: Appointment) -> None:
        # Cùng hình với appointment_created (services/end_at) — ruling chủ dự
        # án: cả hai sự kiện mang MỘT hình lịch, không phải hai hình rời nhau.
        detail = await self._describe(appointment)
        await self.socketio.emit_to_admins("appointment_cancelled", {
            "id": detail.id,
            "start_at": detail.start_at.isoformat(),
            "end_at": detail.end_at.isoformat(),
            "services": [s.model_dump() for s in detail.services],
        })

    async def shop_status_changed(self, status: ShopStatusView) -> None:
        await self.socketio.broadcast("shop_status_changed", {
            "is_busy": status.is_busy,
            "busy_until": status.busy_until.isoformat() if status.busy_until else None,
            "minutes_left": status.minutes_left,
        })
