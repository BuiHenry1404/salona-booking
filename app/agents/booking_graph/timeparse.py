import asyncio
import re
from datetime import date, datetime, timedelta
from typing import List, Literal, Optional

from pydantic import BaseModel, Field

from app.agents.llm import build_chat_model
from app.core.clock import TZ
from app.core.logging import get_logger

logger = get_logger(__name__)

# Đo thật với Azure gpt-5.4-mini: một lượt parse mất 2,2–2,4 giây. Ngưỡng cũ là
# 2.0 nên MỌI lượt đi qua LLM đều hết giờ và trả về "thiếu giờ cụ thể" — nhánh
# LLM của parser coi như chết, chỉ còn regex sống, và khách nói "sáng mai 9 giờ"
# bị hỏi lại đúng câu vừa hỏi. Fail-soft che mất lỗi nên log chỉ có warning.
# 8 giây cho đủ biên: một lượt chat vốn đã mất 5–15 giây, thêm vài giây ở đây
# không đổi cảm nhận của khách, còn hỏi lại thì đổi.
PARSE_TIMEOUT_SECONDS = 8.0
MAX_DAYS_AHEAD = 90


class ParsedTime(BaseModel):
    """Kết quả quy đổi. `start_at` khác None nghĩa là dùng được ngay."""

    start_at: Optional[datetime] = Field(
        default=None, description="Thời điểm ISO 8601 kèm múi giờ, hoặc null nếu chưa đủ thông tin"
    )
    missing: List[str] = Field(
        default_factory=list,
        description="Còn thiếu thông tin gì, viết bằng tiếng Việt cho lễ tân hỏi lại khách",
    )
    partial_date: Optional[date] = Field(
        default=None, description="Ngày đã xác định được, dùng khi chưa biết giờ"
    )
    source: Literal["regex", "llm"] = "llm"


def _guard(candidate: ParsedTime, now: datetime) -> ParsedTime:
    """Chỗ DUY NHẤT được phép nói một kết quả là dùng được.

    Regex và LLM chỉ là hai nguồn đề xuất. Gộp kiểm tra về một chỗ nghĩa là
    thêm nguồn thứ ba sau này cũng không phải nhân đôi luật.

    Cố ý KHÔNG kiểm giờ mở cửa: AppointmentService đã từ chối giờ ngoài
    shop_hours và find_free_slots đã lọc. Kiểm ở đây nữa là giờ mở cửa nằm
    hai chỗ, đổi một chỗ quên chỗ kia.
    """
    start = candidate.start_at
    if start is None:
        return candidate

    # LLM rất hay bỏ offset. Để nguyên thì mọi phép so sánh với now đều ném
    # TypeError: can't compare offset-naive and offset-aware datetimes.
    if start.tzinfo is None:
        start = start.replace(tzinfo=TZ)

    if start <= now:
        return candidate.model_copy(
            update={"start_at": None, "missing": ["ngày khác — giờ đó qua mất rồi"]}
        )

    # Lưới bắt lỗi lệch năm. Model không chắc hôm nay là ngày nào thì nó suy từ
    # mốc thời gian trong dữ liệu huấn luyện, và một ngày của năm ngoái vẫn là
    # chuỗi ISO hợp lệ — không có gì khác trong hệ thống chặn được nó.
    if start > now + timedelta(days=MAX_DAYS_AHEAD):
        logger.warning("timeparse_out_of_window", extra={"start_at": start.isoformat()})
        return candidate.model_copy(
            update={"start_at": None, "missing": ["ngày gần hơn"]}
        )

    return candidate.model_copy(update={"start_at": start})


_DAY_OFFSETS = {
    "hôm nay": 0,
    "ngày mai": 1,
    "mai": 1,
    "ngày kia": 2,
    "mốt": 2,
}

# Ngày phải đứng trước giờ trong câu, đúng cách người Việt nói.
# \b hai đầu để "mai" không khớp vào giữa một từ khác — "email 3h chiều" mà
# thành "mai 3h chiều" thì đặt nhầm hẳn một ngày.
# Thứ tự trong alternation cũng quan trọng: "ngày mai" phải đứng trước "mai".
_DAY_PATTERN = r"\b(hôm nay|ngày mai|ngày kia|mai|mốt)\b"
# "giờ" phải đứng TRƯỚC "g" trong alternation: Python thử các nhánh theo thứ tự,
# "g" khớp ngay chữ đầu của "giờ", phần "iờ" còn lại rơi ra ngoài và nhóm buổi
# (vốn không bắt buộc) khớp rỗng — "mai 3 giờ chiều" thành 3 giờ không rõ buổi
# rồi bị _to_24h trả None. Sai im lặng, không lỗi gì cả.
_TIME_PATTERN = r"(\d{1,2})\s*(?:giờ|h|g)\s*(sáng|trưa|chiều|tối)?"

_FULL = re.compile(_DAY_PATTERN + r".{0,20}?" + _TIME_PATTERN, re.IGNORECASE)


def _to_24h(hour: int, period: Optional[str]) -> Optional[int]:
    """Quy giờ nói kiểu Việt về giờ 24. Trả None nếu không chắc chắn."""
    if period is None:
        # Không có buổi thì chỉ chấp nhận dạng 24 giờ tường minh. "mai 3h" có
        # thể là 3 sáng hoặc 3 chiều — đoán là đặt nhầm lịch cho một cụ 70 tuổi.
        return hour if 13 <= hour <= 23 else None
    if period == "sáng":
        return hour if 1 <= hour <= 11 else None
    if period == "trưa":
        return 12 if hour == 12 else (hour + 12 if hour == 1 else None)
    if period == "chiều":
        if hour == 12:
            return 12
        return hour + 12 if 1 <= hour <= 6 else None
    if period == "tối":
        return hour + 12 if 6 <= hour <= 11 else None
    return None


def _match_regex(text: str, now: datetime) -> Optional[datetime]:
    """Đường tắt cho những câu rõ ràng nhất, không tốn lượt LLM nào.

    LUẬT: chỉ trả kết quả khi khớp TRỌN VẸN — có từ chỉ ngày VÀ có giờ xác
    định. Khớp một phần coi như không khớp. Không đoán, không điền thiếu,
    không trả missing. Thiếu bất cứ thứ gì thì trả None và nhường cho LLM.

    Đừng nới luật này. Regex mà bắt đầu suy luận thì nó thành parser thứ hai
    làm được một nửa: thêm mẫu bắt "thứ Năm", rồi mẫu đó nuốt luôn "thứ Năm
    tuần sau" và trả sai ngày — mà LLM không bao giờ được gọi để sửa.
    """
    match = _FULL.search(text.lower())
    if match is None:
        return None

    day_word, raw_hour, period = match.group(1), int(match.group(2)), match.group(3)
    hour = _to_24h(raw_hour, period)
    if hour is None:
        return None

    local_today = now.astimezone(TZ).date()
    day = local_today + timedelta(days=_DAY_OFFSETS[day_word])
    return datetime(day.year, day.month, day.day, hour, 0, tzinfo=TZ)


_WEEKDAYS_VI = ["Thứ Hai", "Thứ Ba", "Thứ Tư", "Thứ Năm", "Thứ Sáu", "Thứ Bảy", "Chủ Nhật"]

_PROMPT = """Bạn quy câu nói về thời gian của khách Việt Nam ra ngày giờ chuẩn.

Bây giờ là {weekday} {day}/{month}/{year}, {hour:02d}:{minute:02d} (hôm nay là {today}).
Tiệm mở cửa 8 giờ sáng đến 7 giờ tối.

Quy tắc:
- Mọi giờ đều là giờ Việt Nam, offset +07:00.
- Đủ ngày và giờ thì điền start_at. Ví dụ: "thứ Năm tuần sau lúc 2 giờ chiều".
- THIẾU thông tin thì để start_at null và ghi rõ thiếu gì vào missing.
  "sáng mai" → partial_date là ngày mai, missing là ["giờ cụ thể"].
  "3 giờ" → missing là ["sáng hay chiều"].
  "thứ Năm" → missing là ["thứ Năm tuần này hay tuần sau"].
- TUYỆT ĐỐI không đoán thay khách. Đoán sai thì cụ già tới tiệm lúc không ai mở cửa.
- Câu không nhắc gì tới thời gian thì để start_at null và missing rỗng.

Câu của khách: {text}"""


async def _ask_model(text: str, now: datetime) -> ParsedTime:
    local = now.astimezone(TZ)
    prompt = _PROMPT.format(
        weekday=_WEEKDAYS_VI[local.weekday()],
        day=local.day,
        month=local.month,
        year=local.year,
        hour=local.hour,
        minute=local.minute,
        today=local.date().isoformat(),
        text=text,
    )
    # tags KHÔNG chứa "respond": bộ phát sự kiện chỉ chuyển tiếp token mang tag
    # đó ra màn hình khách, và JSON của parser mà lọt ra thì thành rác chạy ngang.
    # streaming=False vì đây là JSON, không có gì để hiện dần.
    model = build_chat_model(tags=["timeparse"], temperature=0.0, streaming=False)
    return await model.with_structured_output(ParsedTime).ainvoke(prompt)


async def parse_vi_time(
    text: str, now: datetime, timeout: float = PARSE_TIMEOUT_SECONDS
) -> ParsedTime:
    """Quy câu nói về thời gian ra một thời điểm chuẩn.

    Regex trước, LLM sau, và cả hai đều đi qua cùng một lớp chốt.

    Fail-soft: LLM lỗi hoặc chậm thì trả về "thiếu giờ cụ thể" để lễ tân hỏi
    lại khách. Khách mất thêm một lượt hỏi đáp — chấp nhận được. Đặt nhầm giờ
    thì không.
    """
    matched = _match_regex(text, now)
    if matched is not None:
        return _guard(ParsedTime(start_at=matched, source="regex"), now)

    try:
        candidate = await asyncio.wait_for(_ask_model(text, now), timeout=timeout)
    except asyncio.TimeoutError:
        logger.warning("timeparse_timeout", extra={"text": text})
        return ParsedTime(missing=["giờ cụ thể"], source="llm")
    except Exception as exc:
        logger.warning("timeparse_failed", extra={"text": text, "error": str(exc)})
        return ParsedTime(missing=["giờ cụ thể"], source="llm")

    return _guard(candidate.model_copy(update={"source": "llm"}), now)
