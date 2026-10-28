# Pure-Python `parse_time` (0 Sub-LLM, 0 Regex) & Working Memory Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Eliminate the blind sub-LLM (`_ask_model`) and the last regex (`_FULL`) in `timeparse.py`, and persist tool outputs (`parse_time`, `find_free_slots`, `propose_appointment`, `cancel_appointment`) and confirmation outcomes immediately into `ConversationSlots` (`state.slots` Working Memory) on every turn so context is never lost between turns.

**Architecture:**
1. `app/agents/booking_graph/timeparse.py` becomes 100% pure Python (`0` LLM calls, `0` `re.compile`), extracting dates, weekdays, relative offsets, periods, and `"bây giờ"` expressions via `tokens.py` and merging them with session `anchor` / `session_day` (`state.slots.day`), then running `_guard`.
2. `ConversationStateService.record_tool_facts` (`app/services/conversation_state.py`) atomically writes `intent`, `day`, `time`, `service`, `told`, and `declined` into `conversations.state.slots` on every tool execution and confirmation decision, without waiting for the 800-token compaction threshold.
3. `parse_time` (`app/agents/booking_graph/tools.py`) automatically resolves its fallback `anchor` / `session_day` from the latest proposal or `state.slots` when the agent omits `anchor`, and `build_context_block` (`app/agents/booking_graph/context.py`) includes a 7-day reference calendar so the main LLM never miscalculates dates.

**Tech Stack:** Python 3.12, FastAPI, LangGraph, Motor (AsyncIO MongoDB), Pydantic v2, Pytest.

## Global Constraints

- **Ngân sách Regex toàn `app/agents/booking_graph/`:** Tối đa **2 regex** (`guard.py: 2`, `timeparse.py: 0`, mọi file khác `0`).
- **Agent has NO create/write tool:** `propose_appointment` và `cancel_appointment` chỉ tạo đề xuất trong `proposals`; chỉ `ProposalService.approve` trong `confirm.py` được phép ghi lịch.
- **Giữ nguyên lưới phủ quyết `reply.py` và `relatives.py` (`0 regex`):** Không phá vỡ bất kỳ ràng buộc nào trong `AGENTS.md` và `tests/test_confirm_flow.py`.
- **Fail-soft:** Mọi thao tác ghi Working Memory (`record_tool_facts`) phải bọc `try/except` log warning để lỗi DB thoáng qua không làm hỏng kết quả tool trong lượt hiện tại.

---

### Task 1: Pure-Python `timeparse.py` (0 Sub-LLM, 0 Regex) & Regex Budget Lock (`2` regex)

**Files:**
- Modify: `app/agents/booking_graph/tokens.py`
- Modify: `app/agents/booking_graph/timeparse.py`
- Modify: `tests/test_timeparse.py`
- Modify: `tests/test_regex_budget.py`

**Interfaces:**
- Produces in `app/agents/booking_graph/tokens.py`:
  - `extract_slash_dates(text: str, default_year: int) -> List[date]`
  - `has_this_week(text: str, weekday_idx: Optional[int] = None) -> bool`
  - `scan_periods(text: str) -> List[str]`
- Produces in `app/agents/booking_graph/timeparse.py`:
  - `parse_vi_time(text: str, now: datetime, timeout: float = PARSE_TIMEOUT_SECONDS, anchor: Optional[datetime] = None, hours: Optional[ShopHours] = None, *, session_day: Optional[date] = None, day_iso: Optional[str] = None, hour: Optional[int] = None, minute: Optional[int] = None, period: Optional[str] = None) -> ParsedTime` (0 LLM calls, 0 regex)

- [ ] **Step 1: Write the failing tests in `tests/test_regex_budget.py` and `tests/test_timeparse.py`**

Update `tests/test_regex_budget.py` to lock the budget at 2 regexes (`guard.py: 2`, `timeparse.py` removed from `EXPECTED_BUDGET`):

```python
"""Hàng rào số lượng regex (`re.compile`) trong tầng AI Agent (`app/agents/booking_graph/`).

Spec `2026-10-28-llm-context-input-design.md`: cắt xuống còn đúng 2 regex:
- `guard.py`: 2 (`_TOOL_GROUP`, `_TOOL_BARE` trong `strip_tool_names`)
- Mọi module khác trong `app/agents/booking_graph/` (kể cả `timeparse.py`): 0 regex.
"""
import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parents[1] / "app" / "agents" / "booking_graph"

EXPECTED_BUDGET = {
    "guard.py": 2,
}


def test_agent_regex_budget_is_at_most_two():
    import ast
    actual = {}
    re_importers = set()
    for path in sorted(ROOT.rglob("*.py")):
        rel = str(path.relative_to(ROOT))
        src = path.read_text(encoding="utf-8")
        count = len(re.findall(r"re\.compile\(", src))
        if count:
            actual[rel] = count
        tree = ast.parse(src, filename=rel)
        for node in ast.walk(tree):
            if isinstance(node, ast.Import) and any(a.name == "re" for a in node.names):
                re_importers.add(rel)
            elif isinstance(node, ast.ImportFrom) and node.module == "re":
                re_importers.add(rel)
    assert actual == EXPECTED_BUDGET, f"Vượt ngân sách regex tầng agent: {actual}"
    assert re_importers == set(EXPECTED_BUDGET.keys()), (
        f"Module ngoài ngân sách import re: {re_importers}"
    )
```

Update `tests/test_timeparse.py` to test pure-Python `parse_vi_time` (including `session_day`, structured args, Vietnamese phrases, and `0` regex in `timeparse.py`):

```python
from datetime import date, datetime, timedelta

import pytest

from app.agents.booking_graph.timeparse import (
    PARSE_TIMEOUT_SECONDS,
    ParsedTime,
    _guard,
    apply_anchor,
    parse_vi_time,
)
from app.core.clock import TZ
from app.models.shop import ShopHours

NOW = datetime(2026, 8, 7, 14, 30, tzinfo=TZ)   # Thứ Sáu 7/8/2026, 2:30 chiều


def at(day, hour, minute=0):
    return datetime(2026, 8, day, hour, minute, tzinfo=TZ)
```

(In `tests/test_timeparse.py`, replace `TestRegexMatches`, `TestRegexDefers`, `TestParseViTime`, `TestParseViTimeSurvivesABadAnchorHour`, `TestAnchorDayWinsWhenTheTextNamesNoDay`, and `test_timeparse_only_compiles_the_single_fast_path_regex` with pure-Python tests covering all cases without `monkeypatch` on `_ask_model` or `build_chat_model`, and asserting `len(re.findall(r"re\.compile\(", source)) == 0`.)

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_regex_budget.py tests/test_timeparse.py -v`
Expected: FAIL (`timeparse.py` still has `re.compile` and `_ask_model`).

- [ ] **Step 3: Implement pure-Python date/time extraction in `app/agents/booking_graph/tokens.py` and `app/agents/booking_graph/timeparse.py`**

In `app/agents/booking_graph/tokens.py`, add helpers `extract_slash_dates`, `scan_periods`, and `has_this_week`:

```python
def extract_slash_dates(text: str, default_year: int) -> List[Tuple[int, int, int]]:
    """Trích các bộ `(year, month, day)` từ chuỗi dạng `D/M` hoặc `D/M/YYYY`."""
    out: List[Tuple[int, int, int]] = []
    for raw in (text or "").split():
        cleaned = raw.strip(".,!?;:()[]\"'«»")
        if "/" in cleaned:
            parts = cleaned.split("/")
            if len(parts) in (2, 3) and all(p.isascii() and p.isdigit() for p in parts):
                d, m = int(parts[0]), int(parts[1])
                y = int(parts[2]) if len(parts) == 3 else default_year
                if y < 100:
                    y += 2000
                if 1 <= m <= 12 and 1 <= d <= 31:
                    out.append((y, m, d))
    return out


def scan_periods(text: str) -> List[str]:
    """Các buổi được nêu trong câu (`'sáng'`, `'trưa'`, `'chiều'`, `'tối'`)."""
    out: List[str] = []
    for w in words(text):
        p = _PERIOD_MAP.get(w)
        if p and p not in out:
            out.append(p)
    return out


def has_this_week(text: str) -> bool:
    """Câu khách có chỉ rõ tuần hiện tại (`'tuần này'`, `'thứ ... này'`, `'chủ nhật này'`, `'tới'`)."""
    pb = padded_bare(text)
    if has_phrase(pb, ("tuan nay", "hom nay", "bua nay")):
        return True
    ws = [strip_diacritics(w) for w in words(text)]
    for i, w in enumerate(ws):
        if w == "thu" and i + 2 < len(ws) and ws[i + 1] in _WEEKDAY_BARE_MAP and ws[i + 2] in ("nay", "toi"):
            return True
        if w == "chu" and i + 2 < len(ws) and ws[i + 1] == "nhat" and ws[i + 2] in ("nay", "toi"):
            return True
        if w == "cn" and i + 1 < len(ws) and ws[i + 1] in ("nay", "toi"):
            return True
    return False
```

In `app/agents/booking_graph/timeparse.py`, remove `import re`, `build_chat_model`, `_FULL`, `_match_regex`, `_PROMPT`, `_ask_model`, and implement pure-Python `_resolve_pure_python(...)` and `parse_vi_time(...)` supporting both structured args (`day_iso`, `hour`, `minute`, `period`, `session_day`) and `text` + `anchor`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_regex_budget.py tests/test_timeparse.py tests/test_timeparse_llm.py -m "llm or not llm" -v`
Expected: PASS (100% green, including all tests in `test_timeparse_llm.py` running in < 0.1s without Azure).

- [ ] **Step 5: Commit**

```bash
git add app/agents/booking_graph/tokens.py app/agents/booking_graph/timeparse.py tests/test_regex_budget.py tests/test_timeparse.py
git commit -m "feat(timeparse): replace sub-LLM and regex with pure-python resolver"
```

---

### Task 2: Working Memory Service (`ConversationStateService.record_tool_facts` & `apply_turn_analysis`)

**Files:**
- Modify: `app/services/conversation_state.py`
- Modify: `tests/test_conversation_state_service.py`
- Modify: `tests/test_conversation_slots.py`

**Interfaces:**
- Produces in `app/services/conversation_state.py`:
  - `ConversationStateService.record_tool_facts(self, user_id: str, *, intent: Optional[str] = None, day: Optional[str] = None, time: Optional[str] = None, service: Optional[str] = None, told: Optional[str] = None, declined_iso: Optional[str] = None, clear_active: bool = False) -> None`
  - Enhances `ConversationStateService.apply_turn_analysis(self, user_id: str, a: TurnAnalysis, last_bot_text: str) -> None` to also extract `day` and `time` from `a.mentioned_times` when unambiguous and not yet set.

- [ ] **Step 1: Write the failing tests in `tests/test_conversation_slots.py`**

Add tests in `tests/test_conversation_slots.py` under `TestApplyTurnAnalysis` and a new class `TestRecordToolFacts`:

```python
class TestRecordToolFacts:
    """Working Memory (`state.slots`) được cập nhật tức thì từ tool và confirm,
    không cần đợi nén 800 tokens."""

    async def test_records_day_time_intent_service_and_told(self, test_db):
        svc = ConversationStateService(test_db)
        await svc.record_tool_facts(
            "u1",
            intent="book",
            day="2026-10-28",
            time="09:00",
            service="làm móng gel",
            told="Ngày 28/10 còn trống: 9 giờ sáng, 10 giờ sáng",
        )
        state = await ConversationService(test_db).get_state("u1")
        assert state.slots.intent == "book"
        assert state.slots.day == "2026-10-28"
        assert state.slots.time == "09:00"
        assert state.slots.service == "làm móng gel"
        assert state.slots.told == ["Ngày 28/10 còn trống: 9 giờ sáng, 10 giờ sáng"]

    async def test_records_declined_and_keeps_day(self, test_db):
        svc = ConversationStateService(test_db)
        await svc.record_tool_facts("u1", day="2026-10-28", time="09:00")
        await svc.record_tool_facts(
            "u1",
            day="2026-10-28",
            declined_iso="2026-10-28T09:00:00+07:00",
        )
        state = await ConversationService(test_db).get_state("u1")
        assert state.slots.day == "2026-10-28"
        assert state.slots.declined == ["2026-10-28T09:00:00+07:00"]

    async def test_clear_active_wipes_intent_day_time_after_approval(self, test_db):
        svc = ConversationStateService(test_db)
        await svc.record_tool_facts("u1", intent="book", day="2026-10-28", time="09:00", service="cắt tóc")
        await svc.record_tool_facts("u1", clear_active=True)
        state = await ConversationService(test_db).get_state("u1")
        assert state.slots.intent is None
        assert state.slots.day is None
        assert state.slots.time is None
        assert state.slots.service == "cắt tóc"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_conversation_slots.py -k "TestRecordToolFacts" -v`
Expected: FAIL with `AttributeError: 'ConversationStateService' object has no attribute 'record_tool_facts'`.

- [ ] **Step 3: Implement `record_tool_facts` and `apply_turn_analysis` slot extraction in `app/services/conversation_state.py`**

In `app/services/conversation_state.py`, add `record_tool_facts` on `ConversationStateService` and update `apply_turn_analysis` so `mentioned_times` can populate empty `day` / `time` on `state.slots`:

```python
    async def record_tool_facts(
        self,
        user_id: str,
        *,
        intent: Optional[str] = None,
        day: Optional[str] = None,
        time: Optional[str] = None,
        service: Optional[str] = None,
        told: Optional[str] = None,
        declined_iso: Optional[str] = None,
        clear_active: bool = False,
    ) -> None:
        """Ghi tức thì dữ kiện từ tool (`parse_time`, `find_free_slots`,
        `propose_appointment`, `cancel_appointment`) và node `confirm` vào
        Working Memory (`state.slots`) của hôm nay — fail-soft, không bao giờ ném."""
        if not user_id:
            return
        try:
            now = now_utc()
            state = await self.conversations.get_state(user_id)
            current = state.slots if state and state.slots else None
            fields: dict = {"state.updated_at": now}

            if clear_active:
                fields["state.slots.intent"] = None
                fields["state.slots.day"] = None
                fields["state.slots.time"] = None
            else:
                if intent in ALLOWED_INTENTS:
                    fields["state.slots.intent"] = intent
                if day:
                    fields["state.slots.day"] = day
                if time:
                    fields["state.slots.time"] = time

            if service:
                cleaned_service = single_line(service, NOTE_MAX)
                if cleaned_service:
                    fields["state.slots.service"] = cleaned_service

            if told:
                cleaned_told = clean_free_text_item(told)
                if cleaned_told:
                    merged_told = list(current.told) if current else []
                    if cleaned_told not in merged_told:
                        merged_told.append(cleaned_told)
                    fields["state.slots.told"] = merged_told[-MAX_TOLD:]

            if declined_iso:
                merged_declined = list(current.declined) if current else []
                norm = to_local(ensure_aware(datetime.fromisoformat(declined_iso))).isoformat()
                if norm not in merged_declined:
                    merged_declined.append(norm)
                fields["state.slots.declined"] = merged_declined[-MAX_DECLINED:]

            if len(fields) > 1:
                await self._set_today_fields(user_id, fields, now)
        except Exception as exc:
            logger.warning("record_tool_facts_failed", extra={"user_id": user_id, "error": str(exc)[:200]})
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_conversation_slots.py tests/test_conversation_state_service.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app/services/conversation_state.py tests/test_conversation_slots.py
git commit -m "feat(state): add immediate working memory updates via record_tool_facts"
```

---

### Task 3: Wire Tool Outputs & Confirm Outcomes into Working Memory + Auto-Anchor in `parse_time`

**Files:**
- Modify: `app/agents/booking_graph/tools.py`
- Modify: `app/agents/booking_graph/confirm.py`
- Modify: `tests/test_tools.py`
- Modify: `tests/test_confirm.py`

**Interfaces:**
- Consumes: `ConversationStateService.record_tool_facts` from Task 2, `parse_vi_time(..., session_day=...)` from Task 1.
- Produces:
  - In `app/agents/booking_graph/tools.py`:
    - `parse_time` auto-populates `anchor` / `session_day` from `ProposalService(db).latest(user)` or `ConversationService(db).get_state(str(user.id)).slots` when `anchor` is omitted, and records resolved `day` / `time` into `state.slots`.
    - `find_free_slots` records `day` and `told` into `state.slots`.
    - `propose_appointment` records `intent`, `day`, `time`, `service` into `state.slots`.
    - `cancel_appointment` records `intent="cancel"`, `day`, `time` into `state.slots`.
  - In `app/agents/booking_graph/confirm.py`:
    - `execute_proposal` clears active booking target (`clear_active=True`) when outcome is `"booked"`, `"moved"`, or `"cancelled"`.
    - Reject/supersede (both text and button `"no"`) records `declined_iso` and keeps `day` in `state.slots`.

- [ ] **Step 1: Write the failing tests in `tests/test_tools.py` and `tests/test_confirm.py`**

Add tests in `tests/test_tools.py` under `TestParseTimeAnchor`:

```python
    async def test_parse_time_remembers_day_across_turns_via_working_memory(self, test_db):
        """Lượt 1 khách nói 'sáng mai' (chưa có giờ) -> parse_time lưu partial_date
        vào state.slots.day. Lượt 2 khách chỉ nói '10 giờ 15 sáng' mà agent không
        truyền anchor hay ngày -> parse_time tự nối với state.slots.day."""
        user = await a_user(test_db)
        tool = by_name(make_booking_tools(test_db, user), "parse_time")

        out1 = json.loads(await tool.ainvoke({"text": "sáng mai"}))
        assert out1["start_at"] is None
        assert out1["partial_date"] is not None

        out2 = json.loads(await tool.ainvoke({"text": "10 giờ 15 sáng"}))
        assert out2["start_at"] is not None
        assert out2["start_at"].startswith(out1["partial_date"])
        assert "T10:15:00+07:00" in out2["start_at"]

    async def test_find_free_slots_and_propose_populate_working_memory(self, test_db):
        from app.services.conversation import ConversationService

        user = await a_user(test_db)
        btools = make_booking_tools(test_db, user)
        start = tomorrow_at(15)
        day_iso = start.date().isoformat()

        await by_name(btools, "find_free_slots").ainvoke({"day": day_iso, "part_of_day": "chiều"})
        state1 = await ConversationService(test_db).get_state(str(user.id))
        assert state1.slots.day == day_iso
        assert state1.slots.told

        await by_name(btools, "propose_appointment").ainvoke(
            {"start_at": start.isoformat(), "note": "làm móng bột"}
        )
        state2 = await ConversationService(test_db).get_state(str(user.id))
        assert state2.slots.intent == "book"
        assert state2.slots.day == day_iso
        assert state2.slots.time == "15:00"
        assert state2.slots.service == "làm móng bột"
```

Add test in `tests/test_confirm.py`:

```python
async def test_rejecting_a_proposal_records_declined_in_working_memory(test_db, classifier):
    from app.services.conversation import ConversationService

    user = await a_user(test_db)
    pid = await book_proposal(test_db, user, hour=15)
    graph = one_node_graph(test_db, user)
    await ask(graph, pid)

    await answer(graph, "dạ ko em")

    state = await ConversationService(test_db).get_state(str(user.id))
    assert state is not None and state.slots is not None
    assert state.slots.day == tomorrow_at(15).date().isoformat()
    assert tomorrow_at(15).isoformat() in state.slots.declined
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_tools.py -k "working_memory" tests/test_confirm.py -k "working_memory" -v`
Expected: FAIL (`state` is `None` because tools and confirm do not call `record_tool_facts` yet).

- [ ] **Step 3: Implement Working Memory wiring in `app/agents/booking_graph/tools.py` and `app/agents/booking_graph/confirm.py`**

In `app/agents/booking_graph/tools.py`:
- Instantiate `state_svc = ConversationStateService(db)` in `make_booking_tools`.
- In `parse_time`: if `anchor_dt is None`, check `proposals.latest(user)` (if `pending` or closed within `DECLINED_MAX_AGE_MINUTES`, use `latest.start_at` as `anchor_dt`) and `state_svc.conversations.get_state(str(user.id))` (extract `session_day` from `state.slots.day`, and if `anchor_dt is None` and `state.slots.time` is set, combine `session_day` + `state.slots.time` into `anchor_dt`).
- After `parse_vi_time` returns `parsed`: call `await state_svc.record_tool_facts(...)` with `day` and `time`.
- In `find_free_slots`: call `await state_svc.record_tool_facts(str(user.id), day=day, told=...)`.
- In `propose_appointment`: call `await state_svc.record_tool_facts(str(user.id), intent="reschedule" if old else "book", day=to_local(start).date().isoformat(), time=to_local(start).strftime("%H:%M"), service=note)`.
- In `cancel_appointment`: call `await state_svc.record_tool_facts(str(user.id), intent="cancel", day=to_local(appt.start_at).date().isoformat(), time=to_local(appt.start_at).strftime("%H:%M"))`.

In `app/agents/booking_graph/confirm.py`:
- In `execute_proposal`: when `result.outcome == "approved"`, call `await ConversationStateService(db).record_tool_facts(str(user.id), clear_active=True)`.
- In `_handle_button` (`button == "no"`) and `make_confirm_node` (`decision in ("change", "reject")` or second `unclear`): call `await ConversationStateService(db).record_tool_facts(str(user.id), day=to_local(proposal.start_at).date().isoformat(), declined_iso=to_local(proposal.start_at).isoformat())`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_tools.py tests/test_confirm.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app/agents/booking_graph/tools.py app/agents/booking_graph/confirm.py tests/test_tools.py tests/test_confirm.py
git commit -m "feat(booking_graph): persist tool outputs and confirm state into working memory"
```

---

### Task 4: Multi-Turn Context Persistence End-to-End Verification

**Files:**
- Modify: `tests/test_confirm_flow.py`

**Interfaces:**
- Consumes: `parse_time`, `propose_appointment`, `confirm`, and `ConversationStateService.record_tool_facts` from Tasks 1–3.
- Produces: End-to-end multi-turn regression tests in `tests/test_confirm_flow.py` proving that after a deferral/rejection and an intermediate turn, `Trạng thái cuộc trò chuyện:` (`state.slots`) and `parse_time` retain the target date and declined times without needing 800-token compaction.

- [ ] **Step 1: Write the end-to-end test in `tests/test_confirm_flow.py`**

Add `test_working_memory_preserves_day_and_declined_slot_across_multiple_turns` at the end of `tests/test_confirm_flow.py`:

```python
async def test_working_memory_preserves_day_and_declined_slot_across_multiple_turns(
    test_db, user, models
):
    """Kiểm chứng lỗ hổng mất context giữa các lượt:
    - Lượt 1: Đề xuất 3 giờ chiều mai -> interrupt.
    - Lượt 2: Khách từ chối ('dạ ko em') -> đề xuất đóng, dòng [declined: ...] hiện ở Lượt 2,
      đồng thời Working Memory (state.slots) lưu day = ngày mai & declined = [15:00].
    - Lượt 3: Khách hỏi chuyện phụ ('tiệm mở cửa mấy giờ') -> sang Lượt 4, dòng [declined: ...]
      đã hết hạn 1 lượt, NHƯNG Working Memory (Trạng thái cuộc trò chuyện) vẫn giữ nguyên
      'Ngày đang nhắm' và 'Đã chào mà khách không lấy', và parse_time('4 giờ chiều')
      tự động nối đúng vào ngày mai mà không cần truyền anchor."""
    await propose_turn(test_db, user, models, hour=15)

    # Lượt 2: Khách từ chối
    models.agent.replies += [AIMessage("Dạ vâng, chị Lan muốn đổi sang giờ nào ạ?")]
    await turn(test_db, user, "dạ ko em")

    # Lượt 3: Khách hỏi chuyện phụ
    models.sup.replies += [AIMessage("shop")]
    models.agent.replies += [AIMessage("Dạ tiệm mở cửa từ 8 giờ sáng đến 7 giờ tối ạ.")]
    await turn(test_db, user, "tiệm mở cửa mấy giờ em")

    # Lượt 4: Khách nói "vậy 4 giờ chiều nha" (không nhắc lại ngày mai)
    parse_tool = next(t for t in agents_booking_tools(test_db, user) if t.name == "parse_time")
    parsed = json.loads(await parse_tool.ainvoke({"text": "vậy 4 giờ chiều nha"}))
    assert parsed["start_at"] == tomorrow_at(16).isoformat()

    models.sup.replies += [AIMessage("booking")]
    models.agent.replies += [AIMessage("Dạ chị muốn đặt 4 giờ chiều mai đúng không ạ?")]
    await turn(test_db, user, "vậy 4 giờ chiều nha")

    # Agent ở Lượt 4 phải nhìn thấy Trạng thái cuộc trò chuyện chứa Ngày đang nhắm & giờ đã từ chối
    full_prompt = "\n".join(str(m.content) for m in models.agent.calls[-1])
    assert "Trạng thái cuộc trò chuyện:" in full_prompt
    assert "Ngày đang nhắm:" in full_prompt
    assert "Đã chào mà khách không lấy:" in full_prompt
```

- [ ] **Step 2: Run the new test and the full test suite**

Run: `pytest tests/test_confirm_flow.py -k "test_working_memory_preserves_day" -v && pytest`
Expected: PASS (all 720+ tests green, 0 failures).

- [ ] **Step 3: Commit**

```bash
git add tests/test_confirm_flow.py
git commit -m "test(confirm_flow): verify working memory persistence across multi-turn deferral"
```
