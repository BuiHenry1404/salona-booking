from datetime import timedelta

from app.models.appointment import Appointment
from app.models.shop import ShopStatusView
from app.services.service_catalog import ServiceCatalogService


class SocketNotifier:
    """Kênh Socket.IO của chỗ tỏa tin.

    `shop_status_changed` phải broadcast tới MỌI user đang online, không chỉ admin:
    thẻ trạng thái trên màn hình chat của khách phải đổi mà không cần tải lại.

    `db` (tuỳ chọn): cần để tra tên dịch vụ (Task 3) — không có thì
    `appointment_created` vẫn phát được, chỉ thiếu tên dịch vụ (`services`
    rỗng); `end_at` luôn đúng vì tính thẳng từ `duration_minutes`, không cần
    tra cứu. Test cũ dựng notifier không truyền `db`.
    """

    def __init__(self, socketio_service, db=None):
        self.socketio = socketio_service
        self.db = db

    async def _service_refs(self, service_ids) -> list:
        if not service_ids or self.db is None:
            return []
        services = await ServiceCatalogService(self.db).get_many(service_ids)
        return [{"id": s.id, "name": s.name} for s in services]

    async def appointment_created(self, appointment: Appointment) -> None:
        end_at = appointment.start_at + timedelta(minutes=appointment.duration_minutes)
        await self.socketio.emit_to_admins("appointment_created", {
            "id": str(appointment.id),
            "start_at": appointment.start_at.isoformat(),
            "end_at": end_at.isoformat(),
            "user_name": appointment.user_name,
            "phone": appointment.phone,
            "note": appointment.note,
            "services": await self._service_refs(appointment.service_ids),
        })

    async def appointment_cancelled(self, appointment: Appointment) -> None:
        await self.socketio.emit_to_admins("appointment_cancelled", {
            "id": str(appointment.id),
            "start_at": appointment.start_at.isoformat(),
        })

    async def shop_status_changed(self, status: ShopStatusView) -> None:
        await self.socketio.broadcast("shop_status_changed", {
            "is_busy": status.is_busy,
            "busy_until": status.busy_until.isoformat() if status.busy_until else None,
            "minutes_left": status.minutes_left,
        })
