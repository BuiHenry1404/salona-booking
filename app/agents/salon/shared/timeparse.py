from datetime import date, datetime, timedelta
from typing import List, Literal, Optional

from pydantic import BaseModel, Field, model_validator

from app.core.clock import TZ
from app.core.logging import get_logger
from app.core.slots import SLOT_MINUTES, next_slot_after
from app.models.shop import ShopHours
from app.services.shop import is_within

logger = get_logger(__name__)

PARSE_TIMEOUT_SECONDS = 8.0
MAX_DAYS_AHEAD = 90

# Bộ câu hỏi ĐÓNG mà lễ tân được phép hỏi lại khách.
# Hai giá trị cuối do `_guard` sinh ra.
MissingPiece = Literal[
    "giờ cụ thể",
    "sáng hay chiều",
    "ngày nào",
    "tuần này hay tuần sau",
    "ngày khác — giờ đó qua mất rồi",
    "ngày gần hơn",
]

# Buổi trong ngày — agent con điền enum này, thay cho bảng từ tiếng Việt cũ
# (`tokens._PERIOD_MAP`): không còn phụ thuộc gói `booking`.
PartOfDay = Literal["sang", "trua", "chieu", "toi"]

_PART_OF_DAY_LABEL = {
    "sang": "sáng",
    "trua": "trưa",
    "chieu": "chiều",
    "toi": "tối",
}

# Xác định được ngày rồi thì hai câu này thành vô nghĩa.
_DAY_QUESTIONS = {"ngày nào", "tuần này hay tuần sau"}


class ParsedTime(BaseModel):
    """Kết quả quy đổi. `start_at` khác None nghĩa là dùng được ngay."""

    start_at: Optional[datetime] = Field(
        default=None, description="Thời điểm ISO 8601 kèm múi giờ, hoặc null nếu chưa đủ thông tin"
    )
    missing: List[MissingPiece] = Field(
        default_factory=list,
        description="Còn thiếu thông tin gì, chọn trong danh sách cho sẵn",
    )
    partial_date: Optional[date] = Field(
        default=None, description="Ngày đã xác định được, dùng khi chưa biết giờ"
    )
    partial_hour: Optional[int] = Field(
        default=None,
        description="Giờ đã nghe được. 0-23 nếu biết sáng/chiều; 1-12 nếu chưa biết buổi",
    )
    partial_minute: int = Field(default=0, description="Phút, mặc định 0")
    day_from: Optional[Literal["explicit", "anchor"]] = Field(
        default=None,
        description="Nguồn gốc của ngày: 'explicit' (khách nêu) hay 'anchor' (lấy từ mốc neo đang bàn)",
    )
    source: Literal["code", "regex", "llm"] = "code"

    @model_validator(mode="after")
    def _a_known_day_is_never_asked_again(self):
        """Biết ngày rồi mà vẫn hỏi lại ngày là tự mâu thuẫn.
        ÉP về dạng đúng chứ KHÔNG ném lỗi.
        """
        if not (self.partial_date and _DAY_QUESTIONS & set(self.missing)):
            return self

        kept = [m for m in self.missing if m not in _DAY_QUESTIONS]
        if not kept and self.start_at is None:
            kept = ["giờ cụ thể"]
        self.missing = kept
        return self


def _guard(candidate: ParsedTime, now: datetime) -> ParsedTime:
    """Chỗ DUY NHẤT được phép nói một kết quả là dùng được.

    Cố ý KHÔNG kiểm giờ mở cửa: AppointmentService đã từ chối giờ ngoài
    shop_hours và find_free_slots đã lọc.
    """
    start = candidate.start_at
    if start is None:
        return candidate

    if start.tzinfo is None:
        start = start.replace(tzinfo=TZ)

    # Trong slot đang chạy ("bây giờ", "qua liền") thì hiểu là tới ngay, giữ chỗ ở mốc kế tiếp.
    if now - timedelta(minutes=SLOT_MINUTES) < start <= now:
        return candidate.model_copy(update={"start_at": next_slot_after(now), "missing": []})

    # Quá slot hiện tại thì đúng là đã qua: "10 giờ sáng" nói lúc 2 giờ chiều.
    if start <= now:
        return candidate.model_copy(
            update={"start_at": None, "missing": ["ngày khác — giờ đó qua mất rồi"]}
        )

    # Lưới bắt lỗi lệch năm hoặc quá xa (> 90 ngày).
    if start > now + timedelta(days=MAX_DAYS_AHEAD):
        logger.warning("timeparse_out_of_window", extra={"start_at": start.isoformat()})
        return candidate.model_copy(
            update={"start_at": None, "missing": ["ngày gần hơn"]}
        )

    return candidate.model_copy(update={"start_at": start})


def _to_24h(hour: int, part_of_day: Optional[str]) -> Optional[int]:
    """Quy giờ nói kiểu Việt (hoặc giờ 24h kèm buổi do Agent truyền) về giờ 24 (0..23).
    Trả None nếu không chắc chắn buổi (1..12 không kèm buổi)."""
    norm = _PART_OF_DAY_LABEL.get(part_of_day) if part_of_day else None
    if norm is None:
        if hour == 0:
            return 0
        return hour if 13 <= hour <= 23 else None
    if norm == "sáng":
        return hour if 1 <= hour <= 11 else None
    if norm == "trưa":
        if hour == 12:
            return 12
        if hour == 1:
            return 13
        return hour if 11 <= hour <= 13 else None
    if norm == "chiều":
        if hour == 12:
            return 12
        if 1 <= hour <= 6:
            return hour + 12
        return hour if 13 <= hour <= 18 else None
    if norm == "tối":
        if 6 <= hour <= 11:
            return hour + 12
        return hour if 18 <= hour <= 23 else None
    return None


def _period_candidates(hour: int) -> List[int]:
    """1–12 chưa biết buổi → hai ứng viên 24h; 0–23 đã biết → chính nó."""
    if hour >= 13 or hour == 0:
        return [hour]
    return sorted({hour, (hour + 12) % 24})


def apply_anchor(
    candidate: ParsedTime, anchor: Optional[datetime], hours: Optional[ShopHours]
) -> ParsedTime:
    """Điền phần thiếu (ngày và/hoặc buổi) từ mốc neo — bằng code, không hỏi lại khách."""
    if anchor is None or candidate.start_at is not None or candidate.partial_hour is None:
        return candidate
    missing = set(candidate.missing)
    if "giờ cụ thể" in missing:
        return candidate
    if missing - {"ngày nào", "sáng hay chiều"}:
        return candidate

    if not (0 <= candidate.partial_hour <= 23) or not (0 <= candidate.partial_minute <= 59):
        logger.warning(
            "anchor_bad_hour",
            extra={"partial_hour": candidate.partial_hour, "partial_minute": candidate.partial_minute},
        )
        return candidate

    local_anchor = anchor.astimezone(TZ)
    day_came_from_anchor = candidate.partial_date is None
    day = candidate.partial_date or local_anchor.date()
    minute = candidate.partial_minute or 0

    if "sáng hay chiều" in missing:
        options = _period_candidates(candidate.partial_hour)
        anchor_is_morning = local_anchor.hour < 12
        preferred = [h for h in options if (h < 12) == anchor_is_morning]
        others = [h for h in options if (h < 12) != anchor_is_morning]
        ordered = preferred + others
        if hours is not None:
            ordered = [
                h
                for h in ordered
                if is_within(hours, datetime(day.year, day.month, day.day, h, minute, tzinfo=TZ))
            ]
            if len(ordered) == 2:          # cả hai đều mở — không đoán
                return candidate
        if not ordered:
            return candidate
        hour = ordered[0]
    else:
        hour = candidate.partial_hour

    start = datetime(day.year, day.month, day.day, hour, minute, tzinfo=TZ)
    day_from = "anchor" if day_came_from_anchor else (candidate.day_from or "explicit")
    return candidate.model_copy(
        update={"start_at": start, "missing": [], "partial_date": day, "day_from": day_from}
    )


def apply_session_day(candidate: ParsedTime, session_day: Optional[date]) -> ParsedTime:
    """Điền ngày từ `state.tool_day` (dữ kiện ngày đã xác thực trong phiên hôm nay)
    khi chưa có đề xuất nào làm `anchor` đầy đủ."""
    if session_day is None or candidate.start_at is not None or candidate.partial_date is not None:
        return candidate
    if "tuần này hay tuần sau" in candidate.missing:
        return candidate
    if "ngày nào" not in candidate.missing or candidate.partial_hour is None:
        return candidate
    if not (0 <= candidate.partial_hour <= 23) or not (0 <= candidate.partial_minute <= 59):
        return candidate

    remaining = [m for m in candidate.missing if m != "ngày nào"]
    if not remaining:
        start = datetime(
            session_day.year,
            session_day.month,
            session_day.day,
            candidate.partial_hour,
            candidate.partial_minute,
            tzinfo=TZ,
        )
        return candidate.model_copy(
            update={
                "start_at": start,
                "partial_date": session_day,
                "missing": [],
                "day_from": "anchor",
            }
        )
    return candidate.model_copy(
        update={
            "partial_date": session_day,
            "missing": remaining,
            "day_from": "anchor",
        }
    )


async def parse_vi_time(
    now: datetime,
    *,
    day: Optional[str] = None,
    hour: Optional[int] = None,
    minute: int = 0,
    part_of_day: Optional[PartOfDay] = None,
    is_now: bool = False,
    ambiguous_week: bool = False,
    anchor: Optional[datetime] = None,
    session_day: Optional[date] = None,
    hours: Optional[ShopHours] = None,
) -> ParsedTime:
    """Quy tham số thời gian có cấu trúc ra `ParsedTime` chuẩn bằng Python thuần (0 LLM, 0 regex)."""
    if is_now:
        return _guard(
            ParsedTime(
                start_at=now,
                partial_date=now.astimezone(TZ).date(),
                day_from="explicit",
                source="code",
            ),
            now,
        )

    parsed_day: Optional[date] = None
    if day:
        try:
            parsed_day = date.fromisoformat(day.strip())
        except ValueError:
            logger.warning("timeparse_bad_day", extra={"day": day[:40]})

    if minute is None:
        minute = 0

    if hour is not None and (not (0 <= hour <= 23) or not (0 <= minute <= 59)):
        logger.warning("timeparse_bad_clock", extra={"hour": hour, "minute": minute})
        return ParsedTime(
            partial_date=parsed_day,
            missing=["giờ cụ thể"],
            day_from="explicit" if parsed_day else None,
            source="code",
        )

    norm_period = _PART_OF_DAY_LABEL.get(part_of_day) if part_of_day else None
    resolved_hour = _to_24h(hour, part_of_day) if hour is not None else None

    if parsed_day is not None:
        if resolved_hour is not None:
            start = datetime(
                parsed_day.year, parsed_day.month, parsed_day.day, resolved_hour, minute, tzinfo=TZ
            )
            candidate = ParsedTime(
                start_at=start,
                partial_date=parsed_day,
                partial_hour=resolved_hour,
                partial_minute=minute,
                missing=[],
                day_from="explicit",
                source="code",
            )
        elif hour is not None:
            candidate = ParsedTime(
                partial_date=parsed_day,
                partial_hour=hour,
                partial_minute=minute,
                missing=["sáng hay chiều"],
                day_from="explicit",
                source="code",
            )
        else:
            candidate = ParsedTime(
                partial_date=parsed_day,
                missing=["giờ cụ thể"],
                day_from="explicit",
                source="code",
            )
    elif ambiguous_week:
        missing_list: List[MissingPiece] = ["tuần này hay tuần sau"]
        if hour is not None and resolved_hour is None:
            missing_list.append("sáng hay chiều")
        candidate = ParsedTime(
            partial_hour=resolved_hour if resolved_hour is not None else hour,
            partial_minute=minute,
            missing=missing_list,
            source="code",
        )
    elif hour is not None:
        if resolved_hour is not None:
            candidate = ParsedTime(
                partial_hour=resolved_hour,
                partial_minute=minute,
                missing=["ngày nào"],
                source="code",
            )
        else:
            candidate = ParsedTime(
                partial_hour=hour,
                partial_minute=minute,
                missing=["ngày nào", "sáng hay chiều"],
                source="code",
            )
    else:
        if norm_period is not None:
            fallback_day = anchor.astimezone(TZ).date() if anchor else session_day
            if fallback_day is not None:
                candidate = ParsedTime(
                    partial_date=fallback_day,
                    missing=["giờ cụ thể"],
                    day_from="anchor",
                    source="code",
                )
            else:
                candidate = ParsedTime(missing=["ngày nào"], source="code")
        else:
            candidate = ParsedTime(missing=["giờ cụ thể"], source="code")

    candidate = apply_anchor(candidate, anchor, hours)
    candidate = apply_session_day(candidate, session_day)
    return _guard(candidate, now)
