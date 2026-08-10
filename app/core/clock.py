from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from app.core.config import settings

TZ = ZoneInfo(settings.timezone)


def now_utc() -> datetime:
    """Thời điểm hiện tại, luôn có tzinfo. Mọi chỗ cần 'bây giờ' phải gọi hàm này."""
    return datetime.now(timezone.utc)


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
