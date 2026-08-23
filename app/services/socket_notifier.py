from app.models.appointment import Appointment
from app.models.shop import ShopStatusView


class SocketNotifier:
    """Kênh Socket.IO của chỗ tỏa tin.

    `shop_status_changed` phải broadcast tới MỌI user đang online, không chỉ admin:
    thẻ trạng thái trên màn hình chat của khách phải đổi mà không cần tải lại.
    """

    def __init__(self, socketio_service):
        self.socketio = socketio_service

    async def appointment_created(self, appointment: Appointment) -> None:
        await self.socketio.emit_to_admins("appointment_created", {
            "id": str(appointment.id),
            "start_at": appointment.start_at.isoformat(),
            "user_name": appointment.user_name,
            "phone": appointment.phone,
            "note": appointment.note,
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
