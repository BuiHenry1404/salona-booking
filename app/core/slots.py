from datetime import datetime, timedelta, timezone

from app.core.clock import ensure_aware
from app.core.errors import AppError

SLOT_MINUTES = 15
_KEY_FORMAT = "%Y-%m-%dT%H:%M"


class MisalignedSlotError(AppError):
    message = "Giờ đặt phải rơi đúng mốc 15 phút"


def quantize(dt: datetime) -> datetime:
    """Làm tròn xuống mốc 15 phút gần nhất."""
    dt = ensure_aware(dt).astimezone(timezone.utc)
    return dt.replace(minute=dt.minute - dt.minute % SLOT_MINUTES, second=0, microsecond=0)


def next_slot_after(dt: datetime) -> datetime:
    """Mốc 15 phút gần nhất SAU dt. Đúng mốc thì nhảy sang mốc kế.

    `quantize` làm tròn xuống nên không dùng được cho câu "bây giờ": nó luôn
    trả về một thời điểm đã qua vài giây tới vài phút.
    """
    dt = ensure_aware(dt).astimezone(timezone.utc)
    floor = dt.replace(minute=dt.minute - dt.minute % SLOT_MINUTES, second=0, microsecond=0)
    return floor + timedelta(minutes=SLOT_MINUTES)


def slot_keys_for(start_at: datetime, duration_minutes: int) -> list[str]:
    """Các mốc 15 phút mà một lịch chiếm.

    Đây là thứ được đặt unique partial index — MongoDB áp unique cho từng phần tử
    mảng xuyên document, nên hai lịch chồng giờ bị chính DB từ chối, nguyên tử,
    không cần transaction.
    """
    if start_at.tzinfo is None:
        raise MisalignedSlotError("Thiếu múi giờ")

    start = start_at.astimezone(timezone.utc)
    if start.minute % SLOT_MINUTES or start.second or start.microsecond:
        raise MisalignedSlotError()
    if duration_minutes <= 0 or duration_minutes % SLOT_MINUTES:
        raise MisalignedSlotError("Thời lượng phải là bội số 15 phút")

    count = duration_minutes // SLOT_MINUTES
    return [
        (start + timedelta(minutes=SLOT_MINUTES * i)).strftime(_KEY_FORMAT)
        for i in range(count)
    ]
