from typing import List, Protocol, runtime_checkable

from app.core.logging import get_logger
from app.models.appointment import Appointment
from app.models.shop import ShopStatusView

logger = get_logger(__name__)


@runtime_checkable
class Notifier(Protocol):
    """Một kênh báo tin cho chủ tiệm. Socket.IO và Telegram đều cài giao diện này."""

    async def appointment_created(self, appointment: Appointment) -> None: ...
    async def appointment_cancelled(self, appointment: Appointment) -> None: ...
    async def shop_status_changed(self, status: ShopStatusView) -> None: ...


class NotificationService:
    """Cửa ra duy nhất để báo cho chủ tiệm.

    Nghiệp vụ không bao giờ gọi thẳng Socket.IO hay Telegram. Đi vòng qua đây là
    cách duy nhất giữ hai kênh không lệch nhau — và cũng là chỗ nuốt lỗi tập trung.
    """

    def __init__(self) -> None:
        self.channels: List[Notifier] = []

    def register(self, channel: Notifier) -> None:
        self.channels.append(channel)

    def clear(self) -> None:
        self.channels = []

    async def _fan_out(self, method: str, payload) -> None:
        for channel in self.channels:
            try:
                await getattr(channel, method)(payload)
            except Exception as exc:
                logger.warning(
                    "notification_channel_failed",
                    extra={"channel": type(channel).__name__, "method": method,
                           "error": str(exc)},
                )

    async def appointment_created(self, appointment: Appointment) -> None:
        await self._fan_out("appointment_created", appointment)

    async def appointment_cancelled(self, appointment: Appointment) -> None:
        await self._fan_out("appointment_cancelled", appointment)

    async def shop_status_changed(self, status: ShopStatusView) -> None:
        await self._fan_out("shop_status_changed", status)


notifications = NotificationService()
