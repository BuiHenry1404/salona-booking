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


def _shift_month(year: int, month: int, delta: int) -> tuple[int, int]:
    """Dịch (year, month) đi `delta` tháng. Đếm bằng chỉ số tháng tuyệt đối để
    không phải xử lý riêng chuyện qua năm."""
    index = year * 12 + (month - 1) + delta
    return index // 12, index % 12 + 1


def month_key(dt: datetime) -> str:
    """Nhãn 'YYYY-MM' của một thời điểm, tính theo giờ Việt Nam.

    Mongo lưu start_at là UTC. 23:00 ngày 31/8 giờ VN là 16:00 UTC cùng ngày,
    còn 01:00 ngày 1/9 giờ VN đã là 18:00 UTC ngày 31/8 — quy đổi trước khi
    cắt tháng, nếu không lịch đầu/cuối tháng sẽ bị đếm nhầm sang tháng khác.
    """
    local = to_local(dt)
    return f"{local.year:04d}-{local.month:02d}"


def recent_month_keys(months: int, now: datetime) -> list[str]:
    """`months` nhãn tháng gần nhất theo giờ VN, cũ -> mới, gồm cả tháng hiện tại."""
    local = to_local(now)
    keys = []
    for delta in range(-(months - 1), 1):
        year, month = _shift_month(local.year, local.month, delta)
        keys.append(f"{year:04d}-{month:02d}")
    return keys


def month_window_bounds(months: int, now: datetime) -> tuple[datetime, datetime]:
    """Mốc UTC [start, end) phủ đúng `months` tháng gần nhất theo giờ VN.

    end là 00:00 giờ VN ngày 1 của tháng KẾ SAU tháng hiện tại, nên toàn bộ
    tháng hiện tại — kể cả lịch đặt trước cho những ngày còn lại — nằm trong
    cửa sổ.
    """
    local = to_local(now)
    first_year, first_month = _shift_month(local.year, local.month, -(months - 1))
    end_year, end_month = _shift_month(local.year, local.month, 1)
    start_local = datetime(first_year, first_month, 1, tzinfo=TZ)
    end_local = datetime(end_year, end_month, 1, tzinfo=TZ)
    return start_local.astimezone(timezone.utc), end_local.astimezone(timezone.utc)
