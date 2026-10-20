# Conversation State Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Đổi `Digest` thành `ConversationState` và cho nó mang thêm `slots` (ý định, dịch vụ, ngày, giờ, lịch đang nhắc tới, các giờ khách đã từ chối) do chính lượt nén sẵn có sinh ra.

**Architecture:** Không thêm node, không thêm lời gọi LLM, không thêm collection. Lượt nén nền đang chạy sau `complete` đổi structured output từ `{bullets}` thành `{summary, slots}`. Code lọc lại từng field trước khi lưu; khối slots được code dựng thành tiếng Việt và nhồi chung chỗ với summary, ngay sau system prompt. Không có đường code nào hành động theo slots.

**Tech Stack:** Python 3.12, Pydantic v2, LangGraph/LangChain, Motor (MongoDB), pytest + pytest-asyncio.

**Spec:** `docs/superpowers/specs/2026-09-19-conversation-state-design.md`

## Global Constraints

- Nhánh: `feat/conversation-state`, merge vào `henry/develop`. **Không commit thẳng lên `main`.**
- Chạy test: `PYTHONPATH=. .venv/bin/python -m pytest -q` (14 test bị deselect vì cần Azure thật — đó là bình thường). Mốc trước khi bắt đầu: **738 test backend xanh**.
- `tags` của model nén **không được chứa `"respond"`** (`CONTEXT.md` bẫy #8) và phải `streaming=False`.
- Thứ tự prompt bất di bất dịch (`CONTEXT.md` bẫy #9): **System → [summary + slots] → lịch sử → khối bối cảnh → tin mới**.
- Xưng hô: "em" / "anh" / "chị". **Cấm tuyệt đối** "con", "cô", "chú", "bác" ở mọi chuỗi khách có thể thấy.
- `NOTE_MAX = 80` (từ `app/models/appointment.py`).
- Mọi thứ ở tầng này **fail-soft**: lỗi thì log rồi đi tiếp, không bao giờ ném ra ngoài lượt chat.
- **Không migrate Mongo.** Dữ liệu tự hủy theo ngày.
- Đã có `tests/test_slots.py` cho *khung giờ* (`app/core/slots.py`). File test mới phải tên `tests/test_conversation_slots.py` — đừng nhập hai thứ này làm một.

## Sai lệch so với spec (có chủ ý)

Spec viết `intent: Optional[Literal["book","reschedule","cancel"]]`. Plan này để **`intent: Optional[str]`** trong model và ép về ba giá trị ở tầng sanitize. Lý do: `Literal` làm Pydantic **ném lỗi lúc parse structured output** khi model trả giá trị thứ tư, và lỗi đó rơi vào `except` của `_compact` → `bump_state_failures` → đốt một nấc cầu chì vì một chuyện đáng lẽ chỉ cần bỏ một field. Cùng lý do, `day` và `time` giữ nguyên `str` thô trong model.

## File Structure

| File | Trách nhiệm |
|---|---|
| `app/models/conversation.py` (sửa) | `ConversationSlots` (thùng chứa **thô**, không validate) và `ConversationState` (thay `Digest`) |
| `app/services/conversation_state.py` (đổi tên từ `app/services/digest.py`) | Lượt nén + `sanitize_slots()` — hàm **thuần**, nhận `now` và `valid_ids` qua tham số |
| `app/services/conversation.py` (sửa) | `get_state` / `set_state` / `bump_state_failures` / `context_window` |
| `app/agents/booking_graph/context.py` (sửa) | `render_slots()` — dựng khối tiếng Việt; `load_context` trả thêm `slots` |
| `app/agents/booking_graph/agents.py` (sửa) | `STATE_HEADER`, nhồi summary + slots vào prompt |
| `app/agents/booking_graph/phrase.py` (sửa) | Chỉ đổi tên `digest` → `summary`. **Không nhận slots.** |
| `app/agents/booking_graph/state.py`, `events.py` (sửa) | `summary` + `slots` chảy qua graph |
| `tests/test_conversation_state_model.py` (đổi tên) | Model |
| `tests/test_conversation_state_service.py` (đổi tên) | Lượt nén |
| `tests/test_conversation_slots.py` (tạo) | `sanitize_slots` + `render_slots` |

---

### Task 1: Đổi tên Digest → ConversationState (không đổi hành vi)

Thuần cơ học. Không thêm field, không đổi logic. Test cũ phải xanh y nguyên sau khi đổi tên — đó chính là phép thử của task này.

**Files:**
- Đổi tên: `app/services/digest.py` → `app/services/conversation_state.py`
- Đổi tên: `tests/test_digest_model.py` → `tests/test_conversation_state_model.py`
- Đổi tên: `tests/test_digest_service.py` → `tests/test_conversation_state_service.py`
- Sửa: `app/models/conversation.py`, `app/services/conversation.py`, `app/agents/booking_graph/{state,context,agents,phrase,events,prompts}.py`
- Sửa: mọi test còn nhắc `digest`

**Interfaces:**
- Produces: `ConversationState(day, covers_until, summary: List[str], updated_at, failures)`; `ConversationStateService(db).maybe_compact(user_id) -> bool`; `ConversationService.get_state/set_state/bump_state_failures`; `ConversationService.context_window(user_id) -> tuple[List[str], List[ChatMessage]]`; `GraphState["summary"]: List[str]`; hằng `STATE_PROMPT`, `STATE_TIMEOUT_SECONDS`, `STATE_HEADER`, `StateOutput`.

- [ ] **Step 1: Chụp mốc xanh trước khi động vào gì**

```bash
cd /home/henryb1/Desktop/HenryB1/data/salona-booking
git checkout feat/conversation-state
PYTHONPATH=. .venv/bin/python -m pytest -q 2>&1 | tail -3
```

Ghi lại con số. Kỳ vọng: `738 passed, 14 deselected` (hoặc sát mức đó). Nếu đã đỏ sẵn thì **dừng** và báo — đừng đổi tên trên nền đỏ.

- [ ] **Step 2: Đổi tên file bằng git mv (giữ lịch sử)**

```bash
git mv app/services/digest.py app/services/conversation_state.py
git mv tests/test_digest_model.py tests/test_conversation_state_model.py
git mv tests/test_digest_service.py tests/test_conversation_state_service.py
```

- [ ] **Step 3: Đổi định danh Python trên toàn repo**

```bash
grep -rl --include=*.py 'Digest\|digest\|DIGEST' app tests scripts | xargs sed -i \
  -e 's/DigestBullets/StateOutput/g' \
  -e 's/DigestService/ConversationStateService/g' \
  -e 's/\bDigest\b/ConversationState/g' \
  -e 's/DIGEST_PROMPT/STATE_PROMPT/g' \
  -e 's/DIGEST_TIMEOUT_SECONDS/STATE_TIMEOUT_SECONDS/g' \
  -e 's/DIGEST_HEADER/STATE_HEADER/g' \
  -e 's/app\.services\.digest/app.services.conversation_state/g' \
  -e 's/get_digest/get_state/g' \
  -e 's/set_digest/set_state/g' \
  -e 's/bump_digest_failures/bump_state_failures/g' \
  -e 's/digest_failed/state_failed/g' \
  -e 's/digest_llm_failed/state_llm_failed/g' \
  -e 's/digest_empty/state_empty/g' \
  -e 's/digest_skipped/state_skipped/g' \
  -e 's/digest_compacted/state_compacted/g' \
  -e 's/digest_schedule_failed/state_schedule_failed/g'
```

- [ ] **Step 4: Đổi ba chỗ sed không chạm được — sửa tay**

Ba nhóm còn lại là **chuỗi khoá Mongo** và **tên field**, sed toàn cục sẽ đổi nhầm biến cục bộ, nên làm tay:

1. `app/models/conversation.py` — trong class `ConversationState`, đổi `bullets: List[str]` thành `summary: List[str]`. Trong class `Conversation`, đổi `digest: Optional[ConversationState] = None` thành `state: Optional[ConversationState] = None`.
2. `app/services/conversation.py` — ba chuỗi Mongo:
   - projection `{"digest": 1}` → `{"state": 1}`
   - `raw = (doc or {}).get("digest")` → `.get("state")`
   - `{"$set": {"digest": ...}}` → `{"$set": {"state": ...}}`
   - `{"$inc": {"digest.failures": 1}}` → `{"$inc": {"state.failures": 1}}`
   - trong `context_window`: `digest.bullets` → `state.summary`
3. Mọi chỗ còn dùng `.bullets` hoặc `bullets=` — đổi thành `.summary` / `summary=`:

```bash
grep -rn '\bbullets\b' app tests scripts
```

Đổi hết, trừ hằng `MAX_BULLETS` và `BULLET_MAX_CHARS` (giữ nguyên tên — chúng nói về *gạch đầu dòng*, vẫn đúng nghĩa).

- [ ] **Step 5: Đổi tag Langfuse và chữ trong prompt**

Trong `app/services/conversation_state.py`: `build_chat_model(tags=["digest"], ...)` → `tags=["state"]`.

Trong `app/agents/booking_graph/prompts.py`, dòng cuối của khối chống lặp:

```python
Facts listed under the conversation state count as already said."""
```

Trong `app/agents/booking_graph/agents.py`, hằng header giữ nguyên **nội dung tiếng Việt** (khách không thấy, nhưng model đọc — đổi chữ là đổi hành vi, không phải đổi tên):

```python
STATE_HEADER = "Diễn biến phần trước của cuộc trò chuyện hôm nay:"
```

- [ ] **Step 6: Soi lại không còn sót**

```bash
grep -rni 'digest' app tests scripts docs/superpowers/plans/2026-09-19-conversation-state.md
```

Kỳ vọng: **không có kết quả nào trong `app/`, `tests/`, `scripts/`**. Kết quả trong `docs/` của spec/plan cũ (`2026-09-14-conversation-digest-design.md`, `2026-09-14-conversation-digest.md`) là **biên bản cũ, giữ nguyên, không sửa**.

- [ ] **Step 7: Chạy toàn bộ test**

```bash
PYTHONPATH=. .venv/bin/python -m pytest -q 2>&1 | tail -3
```

Kỳ vọng: **đúng con số của Step 1**. Lệch một test cũng là đổi tên hụt — tìm và sửa, đừng sửa test cho khớp.

- [ ] **Step 8: Commit**

```bash
git add -A app tests scripts
git commit -m "refactor: đổi tên Digest thành ConversationState, bullets thành summary

Tên cũ đúng khi nó chỉ chứa gạch đầu dòng. Không migrate Mongo: dữ liệu
tự hủy theo ngày nên bản nén cũ chỉ mất một lần rồi sinh lại.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 2: Model `ConversationSlots` (thùng chứa thô)

**Files:**
- Modify: `app/models/conversation.py`
- Test: `tests/test_conversation_state_model.py`

**Interfaces:**
- Consumes: `ConversationState` từ Task 1.
- Produces: `ConversationSlots(intent, service, day, time, target_appointment_id, declined)` — **mọi field đều lỏng**, không validator nào ném lỗi; `ConversationState.slots: Optional[ConversationSlots] = None`.

- [ ] **Step 1: Viết test đỏ**

Thêm vào `tests/test_conversation_state_model.py`:

```python
from app.models.conversation import ConversationSlots, ConversationState


def test_slots_defaults_to_none_on_an_old_document():
    """Document lưu trước khi có slots vẫn đọc lên được — không migrate."""
    state = ConversationState(
        day=date(2026, 9, 19),
        covers_until=datetime(2026, 9, 19, 3, 0, tzinfo=timezone.utc),
        summary=["Khách muốn làm tóc."],
    )
    assert state.slots is None


def test_slots_never_raises_on_rubbish_from_the_model():
    """Model là THÙNG CHỨA, không phải hàng rào. Ném lỗi ở đây là biến một
    field sai thành một lượt nén hỏng, và đốt một nấc cầu chì."""
    slots = ConversationSlots(
        intent="booking",              # không thuộc ba giá trị hợp lệ
        day="thứ Năm tuần sau",        # không phải ISO
        time="25:99",
        declined=["không phải giờ"],
    )
    assert slots.intent == "booking"
    assert slots.day == "thứ Năm tuần sau"


def test_slots_all_fields_optional():
    slots = ConversationSlots()
    assert slots.intent is None and slots.declined == []
```

- [ ] **Step 2: Chạy để chắc là nó đỏ**

```bash
PYTHONPATH=. .venv/bin/python -m pytest tests/test_conversation_state_model.py -q
```

Kỳ vọng: FAIL — `ImportError: cannot import name 'ConversationSlots'`.

- [ ] **Step 3: Viết model**

Trong `app/models/conversation.py`, đặt **ngay trên** class `ConversationState`:

```python
class ConversationSlots(BaseModel):
    """Dữ kiện dạng trường của cuộc trò chuyện hôm nay, do lượt nén (LLM) sinh.

    Đây là THÙNG CHỨA THÔ, cố ý không validate gì: nó là đích của
    `with_structured_output`, nên một field sai kiểu sẽ làm Pydantic ném lỗi
    và đánh trượt cả lượt nén — mất luôn phần summary vốn đúng, lại còn đốt
    một nấc cầu chì. Hàng rào nằm ở `sanitize_slots()`, chạy sau khi parse.

    Không có đường code nào đặt/dời/hủy lịch dựa trên các field này. Chúng chỉ
    được in thành chữ trong prompt, và luôn THUA khối bối cảnh.
    """

    intent: Optional[str] = None                 # "book" | "reschedule" | "cancel"
    service: Optional[str] = None                # text tự do, ≤ NOTE_MAX
    day: Optional[str] = None                    # "YYYY-MM-DD"
    time: Optional[str] = None                   # "HH:MM"
    target_appointment_id: Optional[str] = None
    declined: List[str] = Field(default_factory=list)   # ISO local, giờ khách đã lắc
```

Và thêm vào `ConversationState`:

```python
    slots: Optional[ConversationSlots] = None    # document cũ → None, không migrate
```

- [ ] **Step 4: Chạy test**

```bash
PYTHONPATH=. .venv/bin/python -m pytest tests/test_conversation_state_model.py -q
```

Kỳ vọng: PASS.

- [ ] **Step 5: Commit**

```bash
git add app/models/conversation.py tests/test_conversation_state_model.py
git commit -m "feat: thêm ConversationSlots vào ConversationState

Thùng chứa thô, không validate — hàng rào nằm ở sanitize_slots.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 3: `sanitize_slots()` — hàng rào của code

**Files:**
- Modify: `app/services/conversation_state.py`
- Create: `tests/test_conversation_slots.py`

**Interfaces:**
- Consumes: `ConversationSlots` (Task 2).
- Produces: `sanitize_slots(raw: Optional[ConversationSlots], *, now: datetime, valid_ids: Set[str], user_id: str = "") -> Optional[ConversationSlots]`. Hàm **thuần**: không chạm DB, không gọi `now_utc()` bên trong — `now` là UTC, tự đổi sang giờ VN bằng `to_local`. Trả `None` khi mọi field rỗng.

- [ ] **Step 1: Viết test đỏ**

Tạo `tests/test_conversation_slots.py`:

```python
"""sanitize_slots: LLM sinh slots, code lọc lại. Không chạm DB, không chạm LLM.

Đừng nhầm với tests/test_slots.py — file đó nói về KHUNG GIỜ (app/core/slots.py).
"""
from datetime import datetime, timezone

from app.models.conversation import ConversationSlots
from app.services.conversation_state import sanitize_slots

# 2026-09-19 10:00 UTC = 17:00 giờ VN (UTC+7).
NOW = datetime(2026, 9, 19, 10, 0, tzinfo=timezone.utc)
IDS = {"aaaaaaaaaaaaaaaaaaaaaaaa"}


def _clean(**kwargs):
    return sanitize_slots(ConversationSlots(**kwargs), now=NOW, valid_ids=IDS)


def test_keeps_a_fully_valid_set():
    slots = _clean(intent="book", service="làm móng bột", day="2026-09-20",
                   time="15:00", target_appointment_id="aaaaaaaaaaaaaaaaaaaaaaaa")
    assert slots.intent == "book"
    assert slots.day == "2026-09-20"
    assert slots.time == "15:00"
    assert slots.target_appointment_id == "aaaaaaaaaaaaaaaaaaaaaaaa"


def test_unknown_intent_is_dropped():
    assert _clean(intent="booking", service="làm tóc").intent is None


def test_a_day_in_the_past_is_dropped():
    assert _clean(day="2026-09-18", service="làm tóc").day is None


def test_today_is_not_the_past():
    assert _clean(day="2026-09-19", service="làm tóc").day == "2026-09-19"


def test_a_time_already_gone_today_is_dropped():
    """17:00 giờ VN rồi thì '9 giờ sáng hôm nay' không còn là thứ đang nhắm."""
    assert _clean(day="2026-09-19", time="09:00").time is None


def test_a_time_already_gone_survives_on_a_later_day():
    assert _clean(day="2026-09-20", time="09:00").time == "09:00"


def test_an_unparseable_day_or_time_is_dropped():
    slots = _clean(day="thứ Năm tuần sau", time="25:99", service="làm tóc")
    assert slots.day is None and slots.time is None


def test_an_appointment_id_the_customer_does_not_own_is_dropped():
    """Model từng bịa id (BUG-2). Đối chiếu DB, không tin chuỗi model gõ."""
    assert _clean(target_appointment_id="bbbbbbbbbbbbbbbbbbbbbbbb").target_appointment_id is None


def test_service_is_capped_and_flattened():
    slots = _clean(service="làm  tóc\nnhuộm " + "x" * 200)
    assert len(slots.service) <= 80
    assert "\n" not in slots.service


def test_declined_keeps_the_last_six_parseable_entries():
    raw = [f"2026-09-2{i}T09:00:00+07:00" for i in range(8)]
    slots = _clean(declined=[*raw, "không phải giờ"])
    assert len(slots.declined) == 6
    assert slots.declined[-1] == raw[-1]


def test_everything_empty_becomes_none():
    assert _clean() is None
    assert sanitize_slots(None, now=NOW, valid_ids=IDS) is None


def test_each_dropped_field_is_logged(caplog):
    """Không có log này thì slot sai âm thầm biến mất — đúng lối fail-soft đã
    che ba lỗi nặng nhất của dự án."""
    with caplog.at_level("INFO"):
        _clean(intent="booking", day="2026-01-01")
    dropped = [r for r in caplog.records if r.message == "state_slot_dropped"]
    assert {r.field for r in dropped} == {"intent", "day"}
```

- [ ] **Step 2: Chạy để chắc là nó đỏ**

```bash
PYTHONPATH=. .venv/bin/python -m pytest tests/test_conversation_slots.py -q
```

Kỳ vọng: FAIL — `ImportError: cannot import name 'sanitize_slots'`.

- [ ] **Step 3: Viết hàm**

Thêm vào `app/services/conversation_state.py` (import bổ sung `date`, `time as dtime`, `Set`, `ConversationSlots`, `NOTE_MAX` từ `app.models.appointment`):

```python
ALLOWED_INTENTS = {"book", "reschedule", "cancel"}
MAX_DECLINED = 6


def _drop(user_id: str, field: str, value) -> None:
    logger.info("state_slot_dropped", extra={
        "user_id": user_id, "field": field, "value": single_line(str(value), 40),
    })


def sanitize_slots(
    raw: Optional[ConversationSlots],
    *,
    now: datetime,
    valid_ids: Set[str],
    user_id: str = "",
) -> Optional[ConversationSlots]:
    """Lọc slots do LLM sinh. Bỏ TỪNG field sai, không đánh trượt cả lượt nén.

    Thuần: `now` (UTC) và `valid_ids` truyền vào, không tự gọi DB hay đồng hồ —
    nhờ vậy test được mọi mốc thời gian mà không phải giả lập clock.
    """
    if raw is None:
        return None

    local_now = to_local(now)

    intent = raw.intent if raw.intent in ALLOWED_INTENTS else None
    if raw.intent and intent is None:
        _drop(user_id, "intent", raw.intent)

    service = single_line(raw.service, NOTE_MAX) or None
    if raw.service and not service:
        _drop(user_id, "service", raw.service)

    day: Optional[date] = None
    if raw.day:
        try:
            day = date.fromisoformat(raw.day)
        except ValueError:
            _drop(user_id, "day", raw.day)
        else:
            if day < local_now.date():
                _drop(user_id, "day", raw.day)
                day = None

    clock: Optional[dtime] = None
    if raw.time:
        try:
            clock = datetime.strptime(raw.time, "%H:%M").time()
        except ValueError:
            _drop(user_id, "time", raw.time)
        else:
            # Chỉ bỏ khi chắc chắn đã qua: cùng ngày hôm nay và giờ đã trôi.
            # Không có ngày thì không suy ra được, giữ lại.
            if day == local_now.date() and clock <= local_now.time():
                _drop(user_id, "time", raw.time)
                clock = None

    appointment_id = raw.target_appointment_id
    if appointment_id and appointment_id not in valid_ids:
        _drop(user_id, "target_appointment_id", appointment_id)
        appointment_id = None

    declined: List[str] = []
    for item in raw.declined or []:
        try:
            datetime.fromisoformat(item)
        except (ValueError, TypeError):
            _drop(user_id, "declined", item)
            continue
        declined.append(item)
    declined = declined[-MAX_DECLINED:]

    cleaned = ConversationSlots(
        intent=intent,
        service=service,
        day=day.isoformat() if day else None,
        time=clock.strftime("%H:%M") if clock else None,
        target_appointment_id=appointment_id,
        declined=declined,
    )
    # Rỗng hoàn toàn thì trả None: khối slots sẽ không được in ra, và lượt nén
    # KHÔNG bị tính là hỏng — hội thoại tán gẫu thì rỗng mới là đúng.
    if cleaned == ConversationSlots():
        return None
    return cleaned
```

- [ ] **Step 4: Chạy test**

```bash
PYTHONPATH=. .venv/bin/python -m pytest tests/test_conversation_slots.py -q
```

Kỳ vọng: PASS (13 test).

- [ ] **Step 5: Commit**

```bash
git add app/services/conversation_state.py tests/test_conversation_slots.py
git commit -m "feat: sanitize_slots — code lọc lại slots do LLM sinh

Bỏ từng field sai kèm log state_slot_dropped, không đánh trượt cả lượt
nén. target_appointment_id đối chiếu DB (chữa lối bịa id của BUG-2).

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 4: Đường ghi — lượt nén sinh slots

**Files:**
- Modify: `app/services/conversation_state.py` (`StateOutput`, `STATE_PROMPT`, `_compact`)
- Test: `tests/test_conversation_state_service.py`

**Interfaces:**
- Consumes: `sanitize_slots` (Task 3), `AppointmentRepository.upcoming_for_user(user_id, now)`.
- Produces: `StateOutput(summary: List[str], slots: Optional[ConversationSlots])`; `ConversationState.slots` được lưu vào Mongo sau mỗi lượt nén thành công.

- [ ] **Step 1: Viết test đỏ**

Thêm vào `tests/test_conversation_state_service.py`:

```python
from app.models.conversation import ConversationSlots


async def test_compaction_stores_sanitized_slots(db, patch_model):
    patch_model(StateOutput(
        summary=["Khách muốn làm móng bột."],
        slots=ConversationSlots(intent="book", service="làm móng bột",
                                day="2999-01-01", time="15:00"),
    ))
    await _seed(db, turns=12)

    assert await ConversationStateService(db).maybe_compact("u1") is True

    state = await ConversationService(db).get_state("u1")
    assert state.slots.intent == "book"
    assert state.slots.service == "làm móng bột"
    assert state.slots.day == "2999-01-01"


async def test_a_hallucinated_appointment_id_does_not_reach_mongo(db, patch_model):
    """Khách không có lịch nào → mọi id model gõ ra đều là bịa."""
    patch_model(StateOutput(
        summary=["Khách hỏi dời lịch."],
        slots=ConversationSlots(intent="reschedule",
                                target_appointment_id="aaaaaaaaaaaaaaaaaaaaaaaa"),
    ))
    await _seed(db, turns=12)

    await ConversationStateService(db).maybe_compact("u1")

    state = await ConversationService(db).get_state("u1")
    assert state.slots.target_appointment_id is None
    assert state.slots.intent == "reschedule"      # field khác không bị vạ lây


async def test_empty_slots_are_not_a_failure(db, patch_model):
    """Tán gẫu thì không có slot nào — không được đốt cầu chì vì chuyện đó."""
    patch_model(StateOutput(summary=["Khách chào hỏi."], slots=None))
    await _seed(db, turns=12)

    assert await ConversationStateService(db).maybe_compact("u1") is True

    state = await ConversationService(db).get_state("u1")
    assert state.slots is None
    assert state.failures == 0


async def test_the_prompt_asks_for_slots(db, patch_model):
    model = patch_model()
    await _seed(db, turns=12)
    await ConversationStateService(db).maybe_compact("u1")

    prompt = model.structured.prompts[0]
    assert "slots" in prompt.lower()
    assert "YYYY-MM-DD" in prompt
```

Trong fixture `patch_model`, đổi `DigestBullets(bullets=[...])` mặc định (đã thành `StateOutput(summary=[...])` sau Task 1) để nó **cũng** chấp nhận `slots` — chỉ cần model giả trả về `StateOutput`, không cần sửa fixture gì thêm.

- [ ] **Step 2: Chạy để chắc là nó đỏ**

```bash
PYTHONPATH=. .venv/bin/python -m pytest tests/test_conversation_state_service.py -q
```

Kỳ vọng: FAIL — `StateOutput` chưa nhận `slots`.

- [ ] **Step 3: Mở rộng `StateOutput`**

Trong `app/services/conversation_state.py`, đổi class đầu ra:

```python
class StateOutput(BaseModel):
    """Đầu ra có cấu trúc của lượt nén: văn xuôi + dữ kiện dạng trường.

    Validator ÉP về giới hạn thay vì ném lỗi — lớp gọi fail-soft, ném lỗi là
    vứt luôn phần model đã nén đúng. `slots` cố ý KHÔNG validate ở đây:
    `sanitize_slots()` lo, và nó cần `now` cùng danh sách id thật.
    """

    summary: List[str]
    slots: Optional[ConversationSlots] = None

    @field_validator("summary")
    @classmethod
    def _coerce(cls, value: List[str]) -> List[str]:
        cleaned = []
        for raw in value:
            line = single_line((raw or "").lstrip("-•* ").strip(), BULLET_MAX_CHARS)
            if line:
                cleaned.append(line)
        return cleaned[:MAX_BULLETS]
```

Thêm import ở đầu file: `from app.models.conversation import ConversationSlots` và
`from app.repositories.appointment import AppointmentRepository`.

- [ ] **Step 4: Bổ sung phần slots vào `STATE_PROMPT`**

Chèn **ngay trước** dòng `EXISTING DIGEST:` (nay là `EXISTING SUMMARY:`):

```
Also fill in `slots`, the structured state of this conversation right now.
Every field is optional — leave it out when the customer has not said it.
- intent: exactly one of "book", "reschedule", "cancel".
- service: what they want done, in Vietnamese, at most {max_chars} characters.
- day: the day they are aiming for, as YYYY-MM-DD. Never a weekday name.
- time: the time they are aiming for, as HH:MM on a 24-hour clock.
- target_appointment_id: only an id that appears verbatim in the messages.
- declined: times the salon offered and the customer turned down, each as a
  full ISO timestamp with the +07:00 offset.
Do NOT guess. An empty slots object is the correct answer for small talk.
```

- [ ] **Step 5: Nối `sanitize_slots` vào `_compact`**

Trong `ConversationStateService.__init__`, thêm kho lịch để lấy id thật:

```python
    def __init__(self, db: AsyncIOMotorDatabase):
        self.conversations = ConversationService(db)
        self.appointments = AppointmentRepository(db)
```

Trong `_compact`, ngay trước lời gọi `set_state`:

```python
        # id thật của khách, lấy từ DB — model không được tự cấp id.
        # Dùng repository (nhận user_id) chứ không phải AppointmentService
        # (nhận User): ở đây chỉ có user_id, nạp cả User là thừa một truy vấn.
        now = now_utc()
        upcoming = await self.appointments.upcoming_for_user(user_id, now=now)
        slots = sanitize_slots(
            result.slots,
            now=now,
            valid_ids={str(a.id) for a in upcoming},
            user_id=user_id,
        )
```

Rồi đưa vào bản ghi:

```python
        await self.conversations.set_state(user_id, ConversationState(
            day=to_local(now).date(),
            covers_until=older[-1].created_at,
            summary=result.summary,
            slots=slots,
            failures=0,
        ))
```

Và thêm số slot vào log `state_compacted`: `"slots": bool(slots)`.

- [ ] **Step 6: Chạy test của task và cả bộ**

```bash
PYTHONPATH=. .venv/bin/python -m pytest tests/test_conversation_state_service.py -q
PYTHONPATH=. .venv/bin/python -m pytest -q 2>&1 | tail -3
```

Kỳ vọng: cả hai PASS; tổng số test tăng đúng bằng số test mới thêm.

- [ ] **Step 7: Commit**

```bash
git add app/services/conversation_state.py tests/test_conversation_state_service.py
git commit -m "feat: lượt nén sinh luôn slots, đã lọc bằng code

Cùng một lời gọi LLM, thêm ~50 token đầu ra. id lịch đối chiếu
upcoming_for_user trước khi lưu.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 5: Đường đọc — dựng khối và nhồi vào prompt

**Files:**
- Modify: `app/agents/booking_graph/context.py` (`render_slots`, `load_context`)
- Modify: `app/agents/booking_graph/state.py`, `events.py`, `agents.py`
- Test: `tests/test_conversation_slots.py`, `tests/test_subagents.py`

**Interfaces:**
- Consumes: `ConversationSlots` đã sạch (Task 3/4), `format_vi_datetime`, `format_vi_hhmm`, `_WEEKDAYS` (sẵn trong `context.py`).
- Produces: `render_slots(slots: Optional[ConversationSlots]) -> str` (chuỗi rỗng khi không có gì); `GraphState["slots"]: Optional[ConversationSlots]`; `load_context` trả thêm khoá `"slots"`.

- [ ] **Step 1: Viết test đỏ cho renderer**

Thêm vào `tests/test_conversation_slots.py`:

```python
from app.agents.booking_graph.context import render_slots


def test_render_omits_fields_that_are_none():
    out = render_slots(ConversationSlots(intent="book", service="làm móng bột"))
    assert "Khách muốn: đặt lịch mới" in out
    assert "Dịch vụ: làm móng bột" in out
    assert "Ngày đang nhắm" not in out


def test_render_speaks_vietnamese_not_iso():
    out = render_slots(ConversationSlots(day="2026-09-20", time="15:00"))
    assert "Chủ Nhật 20/9" in out
    assert "3 giờ chiều" in out
    assert "2026-09-20" not in out


def test_render_lists_the_times_the_customer_turned_down():
    out = render_slots(ConversationSlots(
        declined=["2026-09-20T09:00:00+07:00", "2026-09-20T14:00:00+07:00"]))
    assert "9 giờ sáng" in out and "2 giờ chiều" in out


def test_render_is_empty_when_there_is_nothing():
    assert render_slots(None) == ""
    assert render_slots(ConversationSlots()) == ""


def test_render_never_uses_a_forbidden_honorific():
    """Xưng hô chốt cứng: không 'con', 'cô', 'chú', 'bác' ở bất cứ đâu."""
    out = render_slots(ConversationSlots(intent="cancel", service="làm tóc",
                                         day="2026-09-20", time="15:00"))
    for word in (" con ", " cô ", " chú ", " bác "):
        assert word not in f" {out} "
```

- [ ] **Step 2: Chạy để chắc là nó đỏ**

```bash
PYTHONPATH=. .venv/bin/python -m pytest tests/test_conversation_slots.py -q
```

Kỳ vọng: FAIL — `cannot import name 'render_slots'`.

- [ ] **Step 3: Viết renderer**

Thêm vào `app/agents/booking_graph/context.py`, ngay dưới `format_vi_datetime`:

```python
SLOTS_HEADER = "Trạng thái cuộc trò chuyện:"

_INTENT_VI = {"book": "đặt lịch mới", "reschedule": "dời lịch", "cancel": "hủy lịch"}


def _format_vi_day(iso: str) -> str:
    """'2026-09-20' -> 'Chủ Nhật 20/9'. Không in năm: cùng một ngày mà chỗ này
    có năm, chỗ kia không, là hai giọng khác nhau trong cùng một prompt."""
    day = date.fromisoformat(iso)
    return f"{_WEEKDAYS[day.weekday()]} {day.day}/{day.month}"


def render_slots(slots) -> str:
    """Khối trạng thái, dựng hoàn toàn bằng code. Rỗng thì trả chuỗi rỗng.

    Slots do LLM sinh nên khối này luôn đứng TRƯỚC khối bối cảnh trong prompt,
    tức là thua nó. Không có đường code nào hành động theo những dòng ở đây.
    """
    if slots is None:
        return ""

    lines = []
    if slots.intent in _INTENT_VI:
        lines.append(f"- Khách muốn: {_INTENT_VI[slots.intent]}")
    if slots.service:
        lines.append(f"- Dịch vụ: {slots.service}")
    if slots.day:
        lines.append(f"- Ngày đang nhắm: {_format_vi_day(slots.day)}")
    if slots.time:
        lines.append(f"- Giờ đang nhắm: {format_vi_hhmm(slots.time)}")
    if slots.declined:
        offered = ", ".join(
            format_vi_datetime(datetime.fromisoformat(x)) for x in slots.declined
        )
        lines.append(f"- Đã chào mà khách không lấy: {offered}")

    # `target_appointment_id` cố ý KHÔNG in ra: khối bối cảnh đã liệt kê mọi
    # lịch sắp tới kèm id thật, in lại ở đây chỉ tạo cơ hội cho hai chỗ lệch nhau.
    return f"{SLOTS_HEADER}\n" + "\n".join(lines) if lines else ""
```

Thêm `date` vào import `datetime` ở đầu file nếu chưa có.

- [ ] **Step 4: Chạy test renderer**

```bash
PYTHONPATH=. .venv/bin/python -m pytest tests/test_conversation_slots.py -q
```

Kỳ vọng: PASS (18 test).

- [ ] **Step 5: Cho slots chảy qua graph**

`app/agents/booking_graph/state.py` — thêm cạnh `summary`:

```python
    summary: List[str]
    slots: Optional[Any]         # ConversationSlots đã sạch; None khi không có
```

`app/agents/booking_graph/context.py` — `load_context` lấy slots cùng lượt đọc state. Đổi `ConversationService.context_window` để trả cả slots:

```python
    async def context_window(self, user_id: str):
        """Thứ LLM đọc: (dữ kiện đã nén, slots, tin nguyên văn sau mốc nén)."""
        state = await self.get_state(user_id)
        after = state.covers_until if state else None
        return (
            (state.summary if state else []),
            (state.slots if state else None),
            await self.history(user_id, after=after),
        )
```

Rồi trong `load_context`:

```python
    status, upcoming, (summary, slots, history), pending = await asyncio.gather(...)
    return {
        "context_block": build_context_block(user, status, upcoming, last_reply=last_reply),
        "history": history,
        "summary": summary,
        "slots": slots,
        "pending_confirmation": pending,
    }
```

**`context_window` đổi từ 2 phần tử sang 3 — bốn chỗ giải nén phải sửa theo**, nếu không sẽ đỏ với `ValueError: not enough values to unpack`:

```bash
grep -rn 'context_window' tests app
```

- `tests/test_conversation.py` (3 chỗ): `bullets, msgs = ...` → `summary, slots, msgs = ...`
- `tests/test_conversation_state_service.py` (1 chỗ): sửa y như trên

`app/agents/booking_graph/events.py` — thêm vào dict `state`:

```python
            "summary": context.get("summary", []),
            "slots": context.get("slots"),
```

- [ ] **Step 6: Viết test đỏ cho thứ tự prompt**

Thêm vào `tests/test_subagents.py`, cạnh `test_state_sits_right_after_the_system_prompt`:

```python
from app.models.conversation import ConversationSlots


@pytest.mark.asyncio
async def test_slots_ride_in_the_same_block_as_the_summary(patch_model):
    """Thứ tự khoá cứng (CONTEXT.md bẫy #9): system → [summary + slots] →
    lịch sử → bối cảnh → tin mới. Slots KHÔNG được rơi xuống sau lịch sử."""
    from langchain_core.messages import SystemMessage

    model = patch_model([AIMessage(content="Dạ.")])
    node = make_subagent_node("prompt", [], tag="respond")
    state = {
        **a_state("còn không em"),
        "summary": ["Khách muốn làm tóc."],
        "slots": ConversationSlots(intent="book", service="làm móng bột"),
    }
    state["messages"] = [HumanMessage(content="hôm qua"),
                         HumanMessage(content="còn không em")]

    await node(state)

    sent = model.calls[0]
    assert isinstance(sent[0], SystemMessage)
    assert "- Khách muốn làm tóc." in sent[1].content
    assert "- Dịch vụ: làm móng bột" in sent[1].content     # CÙNG một message
    assert sent[2].content == "hôm qua"                     # lịch sử vẫn đi sau
    assert sent[-2].content.startswith("Bạn đang nói chuyện với")
    assert sent[-1].content == "còn không em"


@pytest.mark.asyncio
async def test_slots_alone_still_make_a_block(patch_model):
    """Không có summary mà có slots thì khối vẫn phải được gửi."""
    model = patch_model([AIMessage(content="Dạ.")])
    node = make_subagent_node("prompt", [], tag="respond")
    state = {**a_state("còn không em"), "summary": [],
             "slots": ConversationSlots(service="làm móng bột")}

    await node(state)

    assert "- Dịch vụ: làm móng bột" in model.calls[0][1].content
```

- [ ] **Step 7: Chạy để chắc là nó đỏ**

```bash
PYTHONPATH=. .venv/bin/python -m pytest tests/test_subagents.py -q
```

Kỳ vọng: FAIL — `agents.py` chưa in slots.

- [ ] **Step 8: Nhồi vào prompt**

Trong `app/agents/booking_graph/agents.py`, thay khối dựng message:

```python
        # Khối trạng thái đứng NGAY SAU system: đổi vài lượt một lần, ổn định
        # hơn khối bối cảnh (đổi mỗi lượt) nên đặt trước để tiền tố cache sống
        # lâu. Summary và slots đi CHUNG một message — tách đôi là thêm một
        # ranh giới nữa cho thứ tự bẫy #9 có thể trượt.
        parts = []
        summary = state.get("summary") or []
        if summary:
            parts.append(STATE_HEADER + "\n" + "\n".join(f"- {s}" for s in summary))
        rendered = render_slots(state.get("slots"))
        if rendered:
            parts.append(rendered)
        state_messages = [HumanMessage(content="\n\n".join(parts))] if parts else []

        messages = [
            SystemMessage(content=prompt),
            *state_messages,
            *history,
            HumanMessage(content=state.get("context_block", "")),
            *question,
        ]
```

Import `render_slots` từ `app.agents.booking_graph.context`.

`app/agents/booking_graph/phrase.py` **không đổi** ngoài tên biến `digest`→`summary` đã làm ở Task 1 — nó vẫn chỉ gửi summary. Câu chốt lịch phải nói bằng số liệu code cấp; slots do LLM sinh không được lẻn vào đó.

- [ ] **Step 9: Chạy cả bộ**

```bash
PYTHONPATH=. .venv/bin/python -m pytest -q 2>&1 | tail -3
```

Kỳ vọng: PASS toàn bộ.

- [ ] **Step 10: Commit**

```bash
git add app tests
git commit -m "feat: dựng khối slots bằng code và nhồi chung chỗ với summary

Đứng trước lịch sử và trước khối bối cảnh, nên luôn thua khối bối cảnh.
phrase.py không nhận slots.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 6: Hàng rào, tài liệu, kịch bản chạy thật

**Files:**
- Test: `tests/test_graph_wiring.py`
- Modify: `scripts/llm_scenarios.py`
- Modify: `CONTEXT.md`, `NOTE.md`

**Interfaces:**
- Consumes: mọi thứ từ Task 1–5.
- Produces: `test_no_code_path_books_from_slots`; kịch bản `LLM-32`.

- [ ] **Step 1: Viết hàng rào**

Thêm vào `tests/test_graph_wiring.py`:

```python
import inspect

from app.agents.booking_graph import confirm, guard, tools


def test_no_code_path_books_from_slots():
    """Anh em với test_there_is_NO_tool_that_writes_an_appointment.

    Slots do LLM sinh. Chúng chỉ được in thành chữ trong prompt. Giây phút
    confirm/guard/tools đọc chúng là giá trị model gõ ra đi thẳng vào DB —
    phá đúng chốt 'giá trị lấy từ DB, không từ chuỗi model gõ lại'.
    """
    for module in (confirm, guard, tools):
        source = inspect.getsource(module)
        assert "slots" not in source, (
            f"{module.__name__} đọc slots — xem CONTEXT.md bẫy #22"
        )
```

- [ ] **Step 2: Chạy — phải xanh ngay**

```bash
PYTHONPATH=. .venv/bin/python -m pytest tests/test_graph_wiring.py -q
```

Kỳ vọng: PASS. Nếu đỏ thì Task 5 đã rò slots vào chỗ không được phép — sửa module đó, không sửa test.

- [ ] **Step 3: Thêm kịch bản chạy thật**

Trong `scripts/llm_scenarios.py`, thêm vào cuối `SCENARIOS`:

```python
    ("LLM-32", "Hội thoại dài, kiểm slots sau lượt nén",
     ["Em chào chị, tiệm mình làm móng bột không ạ",
      "Giá tầm bao nhiêu vậy chị",
      "Tiệm mở cửa mấy giờ thế",
      "Chủ nhật có làm không chị",
      "Thế còn thứ hai thì sao",
      "À mà chị ơi nhuộm tóc nữa có được không",
      "Thôi em làm móng bột thôi vậy",
      "Mai chị xem giúp em còn giờ nào trống với",
      "9 giờ sáng em bận rồi",
      "2 giờ chiều em cũng không đi được",
      "Thế 4 giờ chiều được không chị",
      "Mà nãy em nói làm gì ấy nhỉ, chị nhắc lại giúp em"]),
```

Câu cuối là phép thử: lúc đó lượt nén đã chạy, mười câu đầu đã rơi ra ngoài cửa sổ 8 tin, nên câu trả lời đúng **chỉ có thể** đến từ slots. Lễ tân phải nói được "làm móng bột", và không được chào lại 9 giờ sáng hay 2 giờ chiều.

- [ ] **Step 4: Chạy thật**

```bash
docker compose up -d mongo
PYTHONPATH=. .venv/bin/python -m uvicorn main:app &
PYTHONPATH=. .venv/bin/python scripts/llm_scenarios.py LLM-32
```

Đọc bằng mắt. Ba thứ phải đúng: (1) câu cuối nhắc lại đúng "làm móng bột"; (2) không chào lại giờ đã bị từ chối; (3) xưng "em", gọi "chị", không có "con"/"cô"/"chú"/"bác".

**Bước này bắt buộc.** pytest sẽ xanh kể cả khi slots sai hết — mọi lỗi ở tầng này đều fail-soft, và ba lỗi nặng nhất của dự án tới giờ đều không làm test nào đỏ. Nếu Azure không sẵn sàng thì ghi rõ "chưa chạy được" trong báo cáo, **đừng** coi như đã xong.

- [ ] **Step 5: Ghi bẫy #22 vào CONTEXT.md**

Cuối mục "21 cái bẫy" (đổi tiêu đề thành "22 cái bẫy"), thêm:

```markdown
**Conversation state**

22. Slots trong `ConversationState` do **LLM sinh**, không phải code. Chúng luôn **thua** khối bối cảnh, và không có đường code nào đặt/dời/hủy lịch dựa trên chúng — `test_no_code_path_books_from_slots` canh. `target_appointment_id` phải đối chiếu `upcoming_for_user` trước khi lưu; model từng bịa id (BUG-2).
```

Trong bảng "Chốt cứng", đổi dòng Memory: `**2 tầng + trạng thái hội thoại trong phiên**, bỏ tầng vector` và ghi thêm vế "slots là bản nén dạng trường của tầng 2, không phải tầng 3".

Trong sơ đồ "Một lượt chat đi qua đâu", đổi `digest + lịch sử hôm nay` thành `summary + slots + lịch sử hôm nay`.

- [ ] **Step 6: Cập nhật NOTE.md**

Đổi ngày cập nhật thành 2026-09-19, ghi vào mục "Đang ở đâu" rằng đã xong tầng Conversation State (đổi tên `Digest`, thêm slots), kèm con số test mới. Gạch quan sát "neo có thể ra giờ đã qua" khỏi danh sách bốn quan sát chưa quyết nếu Task 3 đã phủ nó, hoặc ghi rõ phần nào còn lại.

- [ ] **Step 7: Chạy lại toàn bộ và commit**

```bash
PYTHONPATH=. .venv/bin/python -m pytest -q 2>&1 | tail -3
git add -A
git commit -m "test+docs: hàng rào slots, kịch bản LLM-32, bẫy #22

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

- [ ] **Step 8: Merge vào henry/develop**

```bash
git checkout henry/develop
git merge --no-ff feat/conversation-state
PYTHONPATH=. .venv/bin/python -m pytest -q 2>&1 | tail -3
git push origin henry/develop
```

`main` chỉ nhận qua PR #6 từ `henry/develop`. **Không merge vào `main` ở đây.**
