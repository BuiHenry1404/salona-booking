from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from app.core.config import settings

TZ = ZoneInfo(settings.timezone)


def now_utc() -> datetime:
    """Thời điểm hiện tại, luôn có tzinfo. Mọi chỗ cần 'bây giờ' phải gọi hàm này."""
    return datetime.now(timezone.utc)


def ensure_aware(dt: datetime) -> datetime:
    """Gắn múi giờ Việt Nam cho datetime thiếu tzinfo.

    Client gửi "2026-08-13T15:00:00" không kèm offset là chuyện thường. Nếu để
    nguyên rồi gọi .astimezone(), Python lấy múi giờ của MÁY CHỦ: cùng một
    request sẽ thành 15:00 trên máy dev (giờ VN) nhưng 22:00 trong container
    (giờ UTC). Diễn giải phải tường minh và nằm ở đây, không rải ra nơi khác.
    """
    return dt.replace(tzinfo=TZ) if dt.tzinfo is None else dt


def to_local(dt: datetime) -> datetime:
    """Đổi sang giờ Việt Nam để hiển thị hoặc diễn giải ngôn ngữ tự nhiên."""
    return dt.astimezone(TZ)


def local_day_bounds(day: date) -> tuple[datetime, datetime]:
    """Mốc UTC đầu và cuối của một ngày theo giờ Việt Nam.

    Dùng để truy vấn 'lịch hôm nay' mà không lệch múi giờ.
    """
    start_local = datetime.combine(day, time.min, tzinfo=TZ)
    return (
        start_local.astimezone(timezone.utc),
        (start_local + timedelta(days=1)).astimezone(timezone.utc),
    )
