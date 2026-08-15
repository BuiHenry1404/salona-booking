# Task 4b · Parser thời gian tiếng Việt

> Thuộc plan [Agent, memory và streaming](README.md). **Đọc [Ràng buộc toàn cục](README.md#ràng-buộc-toàn-cục) trước khi bắt đầu** — chúng áp cho mọi task, kể cả khi không nhắc lại ở đây.

Spec: [`2026-08-08-vi-time-parser-design.md`](../../specs/2026-08-08-vi-time-parser-design.md)

**Files:**
- Create: `app/agents/booking_graph/timeparse.py`, `tests/test_timeparse.py`, `tests/test_timeparse_llm.py`
- Modify: `pyproject.toml` (`[tool.pytest.ini_options]`)

**Interfaces:**
- Consumes: `build_chat_model(tags, temperature, streaming)` (task 1), `TZ` (Plan 1 task 3)
- Produces:
  - `ParsedTime` (Pydantic): `start_at: datetime | None`, `missing: list[str]`, `partial_date: date | None`, `source: "regex" | "llm"`
  - `parse_vi_time(text: str, now: datetime) -> ParsedTime`
  - `PARSE_TIMEOUT_SECONDS: float = 2.0`

- [x] **Step 1: Đăng ký marker `llm`**

Cấu hình pytest của dự án nằm ở `[tool.pytest.ini_options]` trong `pyproject.toml`,
không phải `pytest.ini`. Tạo thêm `pytest.ini` sẽ khiến pytest bỏ qua hẳn khối
trong `pyproject.toml` — mất `asyncio_mode = "auto"` và mọi test async đỏ hết.
Sửa tại chỗ:

```toml
addopts = "-v --tb=short --strict-markers -m \"not llm\""
markers = [
    ...,
    "llm: gọi Azure OpenAI thật, tốn tiền và cần mạng",
]
```

Bảng câu tiếng Việt thật ở step 8 phải nằm ngoài lần chạy mặc định. Để nó trong CI là có ngày build đỏ vì Azure nghẽn chứ không phải vì code sai.

- [x] **Step 2: Viết test cho lớp chốt (sẽ fail)**

Tạo `tests/test_timeparse.py`:

```python
from datetime import date, datetime, timedelta

import pytest

from app.agents.booking_graph.timeparse import (ParsedTime, _guard,
                                                _match_regex, parse_vi_time)
from app.core.clock import TZ

NOW = datetime(2026, 8, 7, 14, 30, tzinfo=TZ)   # Thứ Sáu 7/8/2026, 2:30 chiều


def at(day, hour, minute=0):
    return datetime(2026, 8, day, hour, minute, tzinfo=TZ)


class TestGuard:
    def test_lets_a_sensible_time_through(self):
        result = _guard(ParsedTime(start_at=at(8, 15), source="llm"), NOW)
        assert result.start_at == at(8, 15)

    def test_attaches_vietnam_time_when_the_model_forgets_the_offset(self):
        """LLM rất hay trả '2026-08-08T15:00:00' không kèm offset."""
        naive = datetime(2026, 8, 8, 15, 0)
        result = _guard(ParsedTime(start_at=naive, source="llm"), NOW)

        assert result.start_at is not None
        assert result.start_at.utcoffset() is not None
        assert result.start_at == at(8, 15)

    def test_a_time_already_past_is_rejected(self):
        """'hôm nay 10 giờ sáng' nói lúc 2 giờ chiều là giờ không đặt được."""
        result = _guard(ParsedTime(start_at=at(7, 10), source="regex"), NOW)

        assert result.start_at is None
        assert result.missing == ["ngày khác — giờ đó qua mất rồi"]

    def test_a_year_off_date_is_rejected(self):
        """Kiểu hỏng nguy hiểm nhất: model không chắc hôm nay là ngày nào nên
        suy từ dữ liệu huấn luyện. '2025-08-08' là chuỗi hợp lệ hoàn toàn."""
        result = _guard(
            ParsedTime(start_at=datetime(2025, 8, 8, 15, 0, tzinfo=TZ), source="llm"), NOW
        )
        assert result.start_at is None

    def test_a_date_too_far_ahead_is_rejected(self):
        """Không ai đặt lịch nail trước ba tháng."""
        far = NOW + timedelta(days=120)
        result = _guard(ParsedTime(start_at=far, source="llm"), NOW)

        assert result.start_at is None
        assert result.missing == ["ngày gần hơn"]

    def test_ninety_days_ahead_is_still_allowed(self):
        edge = NOW + timedelta(days=89)
        assert _guard(ParsedTime(start_at=edge, source="llm"), NOW).start_at == edge

    def test_missing_information_passes_through_untouched(self):
        candidate = ParsedTime(
            start_at=None, partial_date=date(2026, 8, 8), missing=["giờ cụ thể"], source="llm"
        )
        result = _guard(candidate, NOW)

        assert result.missing == ["giờ cụ thể"]
        assert result.partial_date == date(2026, 8, 8)

    def test_guard_does_not_check_opening_hours(self):
        """Giờ mở cửa do AppointmentService và find_free_slots lo. Kiểm ở đây nữa
        là nó nằm hai chỗ, đổi một chỗ quên chỗ kia."""
        three_am = _guard(ParsedTime(start_at=at(8, 3), source="llm"), NOW)
        assert three_am.start_at == at(8, 3)


class TestRegexMatches:
    """Ca dương: khớp trọn vẹn thì regex trả kết quả, không gọi LLM."""

    @pytest.mark.parametrize(
        "text,expected",
        [
            ("mai 3h chiều", at(8, 15)),
            ("mai 3 giờ chiều", at(8, 15)),
            ("ngày mai 3g chiều", at(8, 15)),
            ("hôm nay 4h chiều", at(7, 16)),
            ("ngày kia 15h", at(9, 15)),
            ("mốt 9 giờ sáng", at(9, 9)),
            ("mai 8h sáng làm tóc", at(8, 8)),
            ("cho cô đặt mai 7 giờ tối nhé", at(8, 19)),
            ("mai 12 giờ trưa", at(8, 12)),
        ],
    )
    def test_matches(self, text, expected):
        assert _match_regex(text, NOW) == expected


class TestRegexDefers:
    """Ca ÂM — phần quan trọng nhất của cả file.

    Regex chỉ được trả lời khi câu khớp TRỌN VẸN. Ai nới nó ra sau này thì
    chính mấy ca này đỏ lên trước, đúng chỗ cần chặn.
    """

    @pytest.mark.parametrize(
        "text,ly_do",
        [
            ("3h chiều", "thiếu ngày — hôm nay hay mai"),
            ("sáng mai", "thiếu giờ"),
            ("chiều mai", "thiếu giờ"),
            ("mai", "thiếu giờ"),
            ("mai 3h", "thiếu buổi — 3 sáng hay 3 chiều"),
            ("thứ Năm", "tuần này hay tuần sau là phán đoán"),
            ("thứ Năm 3h chiều", "vẫn là phán đoán tuần nào"),
            ("tuần sau", "quá mơ hồ"),
            ("mùng 2", "regex đụng vào là hỏng"),
            ("cuối tuần", "quá mơ hồ"),
            ("ngày 8/8 lúc 3h chiều", "ngày tường minh không nằm trong tập đóng"),
            ("chủ tiệm rảnh không con", "không có gì để parse"),
            ("email cho cô lúc 3h chiều", "'mai' nằm trong 'email', không phải ngày mai"),
        ],
    )
    def test_defers_to_the_llm(self, text, ly_do):
        assert _match_regex(text, NOW) is None, ly_do


class TestParseViTime:
    @pytest.mark.asyncio
    async def test_regex_path_never_calls_the_model(self, monkeypatch):
        def explode(*a, **kw):
            raise AssertionError("không được gọi LLM khi regex đã khớp")

        monkeypatch.setattr(
            "app.agents.booking_graph.timeparse.build_chat_model", explode
        )
        result = await parse_vi_time("mai 3h chiều", NOW)

        assert result.start_at == at(8, 15)
        assert result.source == "regex"

    @pytest.mark.asyncio
    async def test_llm_path_is_used_when_regex_defers(self, monkeypatch):
        class FakeModel:
            def with_structured_output(self, schema):
                return self

            async def ainvoke(self, messages, **kwargs):
                return ParsedTime(start_at=at(8, 9), source="llm")

        monkeypatch.setattr(
            "app.agents.booking_graph.timeparse.build_chat_model",
            lambda **kw: FakeModel(),
        )
        result = await parse_vi_time("sáng mai lúc 9 giờ", NOW)

        assert result.start_at == at(8, 9)
        assert result.source == "llm"

    @pytest.mark.asyncio
    async def test_llm_result_still_goes_through_the_guard(self, monkeypatch):
        """Một lớp kiểm tra, hai chỗ sinh dữ liệu."""
        class FakeModel:
            def with_structured_output(self, schema):
                return self

            async def ainvoke(self, messages, **kwargs):
                return ParsedTime(
                    start_at=datetime(2025, 8, 8, 15, 0, tzinfo=TZ), source="llm"
                )

        monkeypatch.setattr(
            "app.agents.booking_graph.timeparse.build_chat_model",
            lambda **kw: FakeModel(),
        )
        assert (await parse_vi_time("thứ Sáu tuần sau", NOW)).start_at is None

    @pytest.mark.asyncio
    async def test_model_failure_asks_the_customer_instead_of_guessing(self, monkeypatch):
        class Broken:
            def with_structured_output(self, schema):
                return self

            async def ainvoke(self, *a, **kw):
                raise RuntimeError("Azure nghẽn")

        monkeypatch.setattr(
            "app.agents.booking_graph.timeparse.build_chat_model", lambda **kw: Broken()
        )
        result = await parse_vi_time("thứ Năm tuần sau", NOW)

        assert result.start_at is None
        assert result.missing == ["giờ cụ thể"]

    @pytest.mark.asyncio
    async def test_a_slow_model_does_not_keep_the_customer_waiting(self, monkeypatch):
        import asyncio

        class Slow:
            def with_structured_output(self, schema):
                return self

            async def ainvoke(self, *a, **kw):
                await asyncio.sleep(5)
                return ParsedTime(start_at=at(8, 15), source="llm")

        monkeypatch.setattr(
            "app.agents.booking_graph.timeparse.build_chat_model", lambda **kw: Slow()
        )
        result = await parse_vi_time("thứ Năm tuần sau", NOW, timeout=0.2)
        assert result.start_at is None

    @pytest.mark.asyncio
    async def test_parser_model_is_not_tagged_respond(self, monkeypatch):
        """Nếu model của parser mang tag respond, khách sẽ thấy
        `{"start_at": "2026-08-...` chạy ngang giữa cuộc trò chuyện."""
        seen = {}

        class FakeModel:
            def with_structured_output(self, schema):
                return self

            async def ainvoke(self, *a, **kw):
                return ParsedTime(start_at=at(8, 9), source="llm")

        def spy(**kwargs):
            seen.update(kwargs)
            return FakeModel()

        monkeypatch.setattr("app.agents.booking_graph.timeparse.build_chat_model", spy)
        await parse_vi_time("thứ Năm tuần sau", NOW)

        assert "respond" not in seen["tags"]
        assert seen["streaming"] is False
```

- [x] **Step 3: Chạy test để xác nhận fail**

Run: `pytest tests/test_timeparse.py -v`
Expected: FAIL với `ModuleNotFoundError: No module named 'app.agents.booking_graph.timeparse'`

- [x] **Step 4: Viết phần khai báo và lớp chốt**

Tạo `app/agents/booking_graph/timeparse.py`:

```python
import asyncio
import re
from datetime import date, datetime, timedelta
from typing import List, Literal, Optional

from pydantic import BaseModel, Field

from app.agents.llm import build_chat_model
from app.core.clock import TZ
from app.core.logging import get_logger

logger = get_logger(__name__)

PARSE_TIMEOUT_SECONDS = 2.0
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
```

- [x] **Step 5: Viết tầng regex**

Thêm vào `app/agents/booking_graph/timeparse.py`:

```python
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
```

- [x] **Step 6: Viết tầng LLM và hàm công khai**

Thêm vào `app/agents/booking_graph/timeparse.py`:

```python
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
```

- [x] **Step 7: Chạy test để xác nhận pass**

Run: `pytest tests/test_timeparse.py -v`
Expected: PASS (36 passed) — quan trọng nhất là cả lớp `TestRegexDefers` và `test_a_year_off_date_is_rejected`

- [x] **Step 8: Viết bảng câu tiếng Việt thật (chỉ chạy khi gọi tay)**

Tạo `tests/test_timeparse_llm.py`:

```python
"""Gọi Azure OpenAI thật. Mặc định bị loại khỏi pytest bởi addopts trong
pytest.ini. Chạy tay bằng: pytest tests/test_timeparse_llm.py -m llm -v

Đây là chỗ đo xem prompt có thật sự hiểu tiếng Việt hay không. Sửa prompt xong
phải chạy lại file này trước khi tin là đã tốt hơn.
"""
from datetime import datetime

import pytest

from app.agents.booking_graph.timeparse import parse_vi_time
from app.core.clock import TZ

pytestmark = [pytest.mark.llm, pytest.mark.asyncio]

NOW = datetime(2026, 8, 7, 14, 30, tzinfo=TZ)   # Thứ Sáu 7/8/2026, 2:30 chiều


@pytest.mark.parametrize(
    "text,expected_iso",
    [
        ("thứ Bảy này 2 giờ chiều", "2026-08-08T14:00:00+07:00"),
        ("thứ Hai tuần sau lúc 9 giờ sáng", "2026-08-10T09:00:00+07:00"),
        ("ngày 10/8 lúc 4 giờ chiều", "2026-08-10T16:00:00+07:00"),
        ("trưa mai 12 giờ", "2026-08-08T12:00:00+07:00"),
        ("chiều mai lúc 5 giờ", "2026-08-08T17:00:00+07:00"),
    ],
)
async def test_understands_real_vietnamese(text, expected_iso):
    result = await parse_vi_time(text, NOW, timeout=10.0)
    assert result.start_at is not None, f"không đọc được: {text}"
    assert result.start_at.isoformat() == expected_iso


@pytest.mark.parametrize(
    "text,phai_hoi_lai",
    [
        ("thứ Năm", "tuần này hay tuần sau"),
        ("sáng mai", "giờ cụ thể"),
        ("3 giờ", "sáng hay chiều"),
        ("khi nào cũng được", "chưa có gì cụ thể"),
    ],
)
async def test_asks_back_instead_of_guessing(text, phai_hoi_lai):
    """Đoán thay khách là kiểu hỏng tệ nhất: cụ già tới tiệm lúc đóng cửa."""
    result = await parse_vi_time(text, NOW, timeout=10.0)
    assert result.start_at is None, f"không được đoán với: {text}"
    assert result.missing, f"phải nói rõ thiếu gì ({phai_hoi_lai}) với: {text}"


async def test_a_question_with_no_time_returns_nothing():
    result = await parse_vi_time("chủ tiệm rảnh không con", NOW, timeout=10.0)
    assert result.start_at is None
```

- [x] **Step 9: Chạy bảng câu thật một lần**

Run: `pytest tests/test_timeparse_llm.py -m llm -v`
Expected: PASS. Câu nào đỏ thì sửa `_PROMPT` ở step 6 rồi chạy lại — **không** nới test cho vừa với kết quả.

Sau đó xác nhận nó bị loại khỏi lần chạy mặc định:

Run: `pytest tests/ -v --collect-only -q | grep timeparse_llm`
Expected: không ra dòng nào

- [x] **Step 10: Commit**

```bash
git add app/agents/booking_graph/timeparse.py pytest.ini \
        tests/test_timeparse.py tests/test_timeparse_llm.py
git commit -m "feat: Vietnamese time parser with regex fast path and guarded LLM fallback"
```
