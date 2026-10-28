# Structured `parse_time` (0 Sub-LLM, 0 Regex) & Cross-Turn / Same-Turn Working Memory Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan phase-by-phase. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Eliminate the blind sub-LLM (`_ask_model`) and the last regex (`_FULL`) in `timeparse.py` by switching `parse_time` to structured parameters resolved in pure Python, and persist verified tool outputs and confirmation outcomes immediately into Working Memory (`state.slots` + `state.tool_day`), Episodic Memory (`conversation_sessions`), and same-turn `GraphState` so context is never lost across turns or between nodes/agents in the same turn.

**Architecture (Split into 6 Small Phases to Prevent Hallucination):**
- **Phase 1 (`context.py` only):** Add `format_14_day_calendar` to `build_context_block` and add `session.slots` fallback to `render_last_session_line`.
- **Phase 2 (`conversation.py`, `conversation_state.py` only):** Add code-only `state.tool_day` to `ConversationState` and implement `ConversationStateService.record_tool_facts(...) -> Optional[ConversationSlots]` (updating `state.slots`, `state.tool_day`, and syncing `conversation_sessions` with `updated.digest` and `updated.slots`).
- **Phase 3 (`confirm.py`, `agents.py` only):** Wire `record_tool_facts` into `confirm.py` (including returning `"slots": updated_slots` into `GraphState` when `confirm` routes `gate_next="booking"` in the same turn) and add `_merge_turn_analysis_slots` in `agents.py` so `supervisor` (`analyze_turn`) shares extracted `service` and `stated` with `booking`/`shop`/`social` in the same turn.
- **Phase 4 (`timeparse.py` only):** Replace `_ask_model` and `_FULL` with pure-Python structured `parse_vi_time` + `apply_session_day` + `day_from="anchor"`, and lock `tests/test_regex_budget.py` at `2` regexes (`guard.py: 2`).
- **Phase 5 (`tools.py` only):** Update `parse_time` tool schema to structured arguments, implement safe auto-anchor lookup (only from unapproved `ProposalService.latest` or `state.tool_day`, never `approved` proposals or LLM slots), and wire `record_tool_facts` into `parse_time`, `find_free_slots`, `propose_appointment`, and `cancel_appointment`.
- **Phase 6 (`tests/test_confirm_flow.py` only):** End-to-end multi-turn and same-turn cross-agent regression tests.

**Tech Stack:** Python 3.12, FastAPI, LangGraph, Motor (AsyncIO MongoDB), Pydantic v2, Pytest.

## Global Constraints

- **Ngân sách Regex toàn `app/agents/booking_graph/`:** Tối đa **2 regex** (`guard.py: 2`, `timeparse.py: 0`, mọi file khác `0`).
- **Code không bao giờ hành động dựa trên Slots do LLM sinh:** `apply_turn_analysis` không ghi `mentioned_times` vào `state.slots`; `parse_time` chỉ lấy mốc ngày dự phòng từ `ProposalService.latest` (chưa `approved`) hoặc `state.tool_day` (chỉ do `record_tool_facts` ghi, `_compact` không bao giờ chạm tới).
- **Agent has NO create/write tool:** `propose_appointment` và `cancel_appointment` chỉ tạo đề xuất trong `proposals`; chỉ `ProposalService.approve` trong `confirm.py` được phép ghi lịch.
- **Giữ nguyên `render_slots` nói tiếng Việt tự nhiên (không in mã ISO thô)** và giữ nguyên các lưới `0 regex` trong `supervisor.py`, `relatives.py`, `reply.py`.
- **Fail-soft:** Mọi thao tác ghi Working/Episodic Memory (`record_tool_facts`) phải bọc `try/except` log warning để lỗi DB thoáng qua không làm hỏng kết quả tool trong lượt hiện tại.

---

### Phase 1: 14-Day Reference Calendar & Episodic Memory Fallback (`context.py`)

**Files (1 production file, 1 test file):**
- Modify: `app/agents/booking_graph/context.py`
- Modify: `tests/test_context_block.py`

**Interfaces:**
- Produces in `app/agents/booking_graph/context.py`:
  - `format_14_day_calendar(today: date) -> str` included in `build_context_block` right after the `"Bây giờ là ..."` line.
  - `render_last_session_line(session: ConversationSession, *, today: date) -> str` falling back to `session.slots` (`intent`, `service`, `day`, `time`, `stated`) when `session.digest` has no `wants`/`open`.

- [x] **Step 1: Write the failing tests in `tests/test_context_block.py`**

Add `test_empty_digest_falls_back_to_slots_summary` inside `TestRenderLastSessionLine` and `test_block_includes_14_day_reference_calendar` at the end of `tests/test_context_block.py`:

```python
    def test_empty_digest_falls_back_to_slots_summary(self):
        session = ConversationSession(
            user_id="u1",
            day=date(2026, 10, 25),
            digest=None,
            slots=ConversationSlots(
                intent="book",
                service="làm móng bột",
                day="2026-10-28",
                time="15:00",
            ),
            expires_at=datetime(2026, 11, 25, tzinfo=timezone.utc),
        )
        line = render_last_session_line(session, today=date(2026, 10, 26))
        assert "làm móng bột" in line
        assert "Thứ Tư 28/10" in line
        assert "3 giờ chiều" in line


def test_block_includes_14_day_reference_calendar():
    now = datetime(2026, 8, 7, 14, 30, tzinfo=TZ)
    block = build_context_block(a_user(), ShopStatusView(is_busy=False), [], now=now)
    assert "Lịch 14 ngày tới:" in block
    assert "Thứ Sáu 7/8=2026-08-07" in block
    assert "Thứ Bảy 8/8=2026-08-08" in block
    assert "Thứ Năm 20/8=2026-08-20" in block
```

- [x] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_context_block.py -k "14_day or falls_back_to_slots" -v`
Expected: FAIL (`Lịch 14 ngày tới:` not in `block`, and `làm móng bột` not in `line`).

- [x] **Step 3: Implement `format_14_day_calendar` and `render_last_session_line` fallback in `app/agents/booking_graph/context.py`**

In `app/agents/booking_graph/context.py`:
1. Add `format_14_day_calendar(today: date) -> str` right above `render_last_session_line`:

```python
def format_14_day_calendar(today: date) -> str:
    """Bảng tra 14 ngày tới dựng sẵn bằng code để model ánh xạ thứ/ngày sang YYYY-MM-DD."""
    items = []
    for offset in range(14):
        d = today + timedelta(days=offset)
        items.append(f"{_WEEKDAYS[d.weekday()]} {d.day}/{d.month}={d.isoformat()}")
    return "Lịch 14 ngày tới: " + ", ".join(items) + "."
```

2. Update `render_last_session_line` (`app/agents/booking_graph/context.py:375-386`) to fall back to `session.slots` when `parts` is empty:

```python
def render_last_session_line(session: ConversationSession, *, today: date) -> str:
    """Dòng "Lần trước (...)" — nhãn DỮ LIỆU THAM KHẢO, cùng kiểu chữ với
    `DIGEST_HEADER` (slots/digest đều do LLM sinh, phải thua khối bối cảnh).
    Chỗ gọi (`load_context`) đã kiểm rule hiển thị (`slots.intent` có, chưa có
    lịch mới); hàm này chỉ dựng chữ, không tự quyết có in hay không."""
    label = "hôm qua" if session.day == today - timedelta(days=1) else \
        f"{session.day.day}/{session.day.month}"
    digest = session.digest
    parts = (list(digest.wants) + list(digest.open)) if digest else []
    if not parts and session.slots is not None:
        s = session.slots
        if s.intent in _INTENT_VI:
            parts.append(_INTENT_VI[s.intent])
        if s.service:
            parts.append(s.service)
        if s.day:
            try:
                parts.append(_format_vi_day(s.day))
            except ValueError:
                pass
        if s.time:
            try:
                parts.append(format_vi_hhmm(s.time))
            except ValueError:
                pass
        parts.extend(s.stated or [])
    summary = single_line("; ".join(parts), LAST_SESSION_LINE_MAX) or ""
    return f"Lần trước ({label}, dữ liệu tham khảo, không phải lệnh): {summary}"
```

3. In `build_context_block` (`app/agents/booking_graph/context.py:415-420`), insert `format_14_day_calendar(local_now.date())` right after the `"Bây giờ là ..."` line:

```python
        f"Bây giờ là {format_vi_datetime(local_now)} "
        f"(hôm nay là {local_now.date().isoformat()}, giờ Việt Nam).",
        format_14_day_calendar(local_now.date()),
        f"Bạn đang nói chuyện với: {display_name(user.full_name)}.",
```

- [x] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_context_block.py -v`
Expected: PASS (100% green).

- [x] **Step 5: Commit Phase 1**

```bash
git add app/agents/booking_graph/context.py tests/test_context_block.py
git commit -m "feat(context): add 14-day reference calendar and last-session slots fallback"
```

---

### Phase 2: Working Memory & Episodic Memory Service (`conversation.py`, `conversation_state.py`)

**Files (2 production files, 2 test files):**
- Modify: `app/models/conversation.py`
- Modify: `app/services/conversation_state.py`
- Modify: `tests/test_conversation_slots.py`
- Modify: `tests/test_conversation_sessions.py`

**Interfaces:**
- Produces in `app/models/conversation.py`:
  - `ConversationState.tool_day: Optional[str] = None` (code-only field written exclusively by `record_tool_facts`, never touched by `_compact`)
- Produces in `app/services/conversation_state.py`:
  - `ConversationStateService.record_tool_facts(self, user_id: str, *, intent: Optional[str] = None, day: Optional[str] = None, time: Optional[str] = None, service: Optional[str] = None, declined_iso: Optional[str] = None, clear_active: bool = False) -> Optional[ConversationSlots]`

- [x] **Step 1: Write the failing tests in `tests/test_conversation_slots.py` and `tests/test_conversation_sessions.py`**

Add `TestRecordToolFacts` at the end of `tests/test_conversation_slots.py`:

```python
class TestRecordToolFacts:
    """Working Memory (`state.slots` + `state.tool_day`) và Episodic Memory
    (`conversation_sessions`) được cập nhật tức thì từ tool và confirm,
    không cần đợi nén 800 tokens."""

    async def test_records_day_time_intent_service_and_tool_day(self, test_db):
        svc = ConversationStateService(test_db)
        slots = await svc.record_tool_facts(
            "u1",
            intent="book",
            day="2026-10-28",
            time="09:00",
            service="làm móng gel",
        )
        assert slots is not None and slots.day == "2026-10-28"
        state = await ConversationService(test_db).get_state("u1")
        assert state.slots.intent == "book"
        assert state.slots.day == "2026-10-28"
        assert state.tool_day == "2026-10-28"
        assert state.slots.time == "09:00"
        assert state.slots.service == "làm móng gel"

    async def test_records_declined_and_keeps_day(self, test_db):
        svc = ConversationStateService(test_db)
        await svc.record_tool_facts("u1", day="2026-10-28", time="09:00")
        slots = await svc.record_tool_facts(
            "u1",
            day="2026-10-28",
            declined_iso="2026-10-28T09:00:00+07:00",
        )
        assert slots is not None and slots.declined == ["2026-10-28T09:00:00+07:00"]
        state = await ConversationService(test_db).get_state("u1")
        assert state.slots.day == "2026-10-28"
        assert state.tool_day == "2026-10-28"
        assert state.slots.declined == ["2026-10-28T09:00:00+07:00"]

    async def test_clear_active_wipes_intent_day_time_and_tool_day_and_syncs_session(self, test_db):
        from datetime import timedelta
        from app.core.clock import now_utc, to_local
        from app.repositories.conversation_session import ConversationSessionRepository

        svc = ConversationStateService(test_db)
        await svc.record_tool_facts(
            "u1", intent="book", day="2026-10-28", time="09:00", service="cắt tóc"
        )
        today = to_local(now_utc()).date()
        s1 = await ConversationSessionRepository(test_db).most_recent_before(
            "u1", today + timedelta(days=1), within_days=1
        )
        assert s1 is not None and s1.slots.intent == "book"

        await svc.record_tool_facts("u1", clear_active=True)
        state = await ConversationService(test_db).get_state("u1")
        assert state.slots.intent is None
        assert state.slots.day is None
        assert state.slots.time is None
        assert state.tool_day is None
        assert state.slots.service == "cắt tóc"

        s2 = await ConversationSessionRepository(test_db).most_recent_before(
            "u1", today + timedelta(days=1), within_days=1
        )
        assert s2 is not None and s2.slots.intent is None
```

Add `test_short_session_without_compaction_still_saves_episodic_memory_preserving_digest` at the end of `tests/test_conversation_sessions.py`:

```python
async def test_short_session_without_compaction_still_saves_episodic_memory_preserving_digest(test_db):
    """Phiên ngắn (< 800 tokens, chưa chạy _compact) khi gọi record_tool_facts
    vẫn lưu bản conversation_sessions của hôm nay và không xoá digest đã có."""
    repo = ConversationSessionRepository(test_db)
    svc = ConversationStateService(test_db)

    await svc.record_tool_facts("u1", intent="book", day="2026-10-29", service="nhuộm tóc")

    today = to_local(now_utc()).date()
    session = await repo.most_recent_before("u1", today + timedelta(days=1), within_days=1)
    assert session is not None
    assert session.slots.intent == "book"
    assert session.slots.service == "nhuộm tóc"
    assert session.slots.day == "2026-10-29"
```

- [x] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_conversation_slots.py -k "TestRecordToolFacts" tests/test_conversation_sessions.py -k "short_session_without_compaction" -v`
Expected: FAIL (`AttributeError: 'ConversationStateService' object has no attribute 'record_tool_facts'`).

- [x] **Step 3: Implement `tool_day` and `record_tool_facts` in `app/models/conversation.py` and `app/services/conversation_state.py`**

1. In `app/models/conversation.py:113`, add `tool_day: Optional[str] = None` to `ConversationState`:

```python
    failures: int = 0                # cầu chì: nén hỏng liên tiếp
    slots: Optional[ConversationSlots] = None    # document cũ → None, không migrate
    tool_day: Optional[str] = None   # ngày YYYY-MM-DD chỉ do code (record_tool_facts) ghi, _compact không chạm
```

2. In `app/services/conversation_state.py`:
   - Move module-level constants `ALLOWED_INTENTS = {"book", "reschedule", "cancel"}`, `MAX_DECLINED = 6`, and `MAX_DAYS_AHEAD = 90` from line 322 to the top of the file right after `SESSION_TTL_DAYS = 30` (around line 46) so all service methods see them cleanly.
   - In `_set_today_fields` (`app/services/conversation_state.py:128-130`), include `"tool_day": None` in `fresh`:
     ```python
     fresh = {"day": day, "covers_until": datetime(1970, 1, 1, tzinfo=now.tzinfo),
              "summary": [], "digest": None, "customer_quotes": [],
              "updated_at": now, "failures": 0, "slots": {}, "tool_day": None}
     ```
   - Add `record_tool_facts` to `ConversationStateService` right after `apply_turn_analysis`:
     ```python
     async def record_tool_facts(
         self,
         user_id: str,
         *,
         intent: Optional[str] = None,
         day: Optional[str] = None,
         time: Optional[str] = None,
         service: Optional[str] = None,
         declined_iso: Optional[str] = None,
         clear_active: bool = False,
     ) -> Optional[ConversationSlots]:
         """Ghi tức thì dữ kiện đã xác thực từ tool (`parse_time`, `find_free_slots`,
         `propose_appointment`, `cancel_appointment`) và node `confirm` vào
         Working Memory (`state.slots` + `state.tool_day`), đồng bộ ngay sang
         Episodic Memory (`conversation_sessions`), và trả về `slots` mới nhất —
         fail-soft, không bao giờ ném."""
         if not user_id:
             return None
         try:
             now = now_utc()
             local_today = to_local(now).date()
             state = await self.conversations.get_state(user_id)
             current = state.slots if state and state.slots else None
             fields: dict = {"state.updated_at": now}

             if clear_active:
                 fields["state.slots.intent"] = None
                 fields["state.slots.day"] = None
                 fields["state.slots.time"] = None
                 fields["state.tool_day"] = None
             else:
                 if intent in ALLOWED_INTENTS:
                     fields["state.slots.intent"] = intent
                 if day:
                     try:
                         parsed_d = date.fromisoformat(day)
                         if local_today <= parsed_d <= local_today + timedelta(days=MAX_DAYS_AHEAD):
                             fields["state.slots.day"] = parsed_d.isoformat()
                             fields["state.tool_day"] = parsed_d.isoformat()
                     except ValueError:
                         pass
                 if time:
                     try:
                         parsed_t = datetime.strptime(time, "%H:%M").time()
                         fields["state.slots.time"] = parsed_t.strftime("%H:%M")
                     except ValueError:
                         pass

             if service:
                 cleaned_service = single_line(service, NOTE_MAX)
                 if cleaned_service:
                     fields["state.slots.service"] = cleaned_service

             if declined_iso:
                 try:
                     merged_declined = list(current.declined) if current else []
                     norm = to_local(ensure_aware(datetime.fromisoformat(declined_iso))).isoformat()
                     if norm not in merged_declined:
                         merged_declined.append(norm)
                     fields["state.slots.declined"] = merged_declined[-MAX_DECLINED:]
                 except ValueError:
                     pass

             if len(fields) > 1:
                 await self._set_today_fields(user_id, fields, now)
                 updated = await self.conversations.get_state(user_id)
                 if updated and updated.slots is not None:
                     await self.sessions.upsert(
                         user_id=user_id,
                         day=local_today,
                         digest=updated.digest,
                         slots=updated.slots,
                         expires_at=now + timedelta(days=SESSION_TTL_DAYS),
                     )
                     return updated.slots
             return current
         except Exception as exc:
             logger.warning(
                 "record_tool_facts_failed",
                 extra={"user_id": user_id, "error": str(exc)[:200]},
             )
             return None
     ```

- [x] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_conversation_slots.py tests/test_conversation_state_service.py tests/test_conversation_sessions.py -v`
Expected: PASS (100% green).

- [x] **Step 5: Commit Phase 2**

```bash
git add app/models/conversation.py app/services/conversation_state.py tests/test_conversation_slots.py tests/test_conversation_sessions.py
git commit -m "feat(state): add record_tool_facts, code-only tool_day, and session sync"
```

---

### Phase 3: Same-Turn & Cross-Turn Context Handoff in `confirm.py` and `agents.py`

**Files (2 production files, 2 test files):**
- Modify: `app/agents/booking_graph/confirm.py`
- Modify: `app/agents/booking_graph/agents.py`
- Modify: `tests/test_confirm.py`
- Modify: `tests/test_subagents.py`

**Interfaces:**
- Consumes: `ConversationStateService.record_tool_facts` from Phase 2.
- Produces:
  - In `app/agents/booking_graph/confirm.py`:
    - `execute_proposal` clears active booking target (`clear_active=True`) when `result.outcome == "approved"`.
    - `_handle_button` (`button == "no"`) and `make_confirm_node` (`decision in ("change", "reject")` or second `"unclear"`) record `declined_iso` and `day` into Working Memory and return `"slots": updated_slots` in the node result so `booking` running in the **same turn** sees the updated slots immediately.
  - In `app/agents/booking_graph/agents.py`:
    - `_merge_turn_analysis_slots(slots: Optional[ConversationSlots], analysis: Optional[dict]) -> Optional[ConversationSlots]` merges `service` and `stated` extracted by `supervisor` (`analyze_turn`) into the `slots` passed to `agent.ainvoke` in the **same turn**.

- [x] **Step 1: Write the failing tests in `tests/test_confirm.py` and `tests/test_subagents.py`**

Add tests at the end of `tests/test_confirm.py`:

```python
async def test_rejecting_a_proposal_records_declined_in_working_memory_and_returns_slots(test_db, classifier):
    from app.services.conversation import ConversationService

    user = await a_user(test_db)
    pid = await book_proposal(test_db, user, hour=15)
    graph = one_node_graph(test_db, user)
    await ask(graph, pid)

    out = await answer(graph, "dạ ko em")
    assert out.get("slots") is not None
    assert tomorrow_at(15).isoformat() in out["slots"].declined

    state = await ConversationService(test_db).get_state(str(user.id))
    assert state is not None and state.slots is not None
    assert state.slots.day == tomorrow_at(15).date().isoformat()
    assert state.tool_day == tomorrow_at(15).date().isoformat()
    assert tomorrow_at(15).isoformat() in state.slots.declined


async def test_approving_a_proposal_clears_active_working_memory(test_db, classifier):
    from app.services.conversation import ConversationService
    from app.services.conversation_state import ConversationStateService

    user = await a_user(test_db)
    await ConversationStateService(test_db).record_tool_facts(
        str(user.id), intent="book", day=tomorrow_at(15).date().isoformat(), time="15:00"
    )
    pid = await book_proposal(test_db, user, hour=15)
    graph = one_node_graph(test_db, user)
    await ask(graph, pid)

    await answer(graph, "ừ đặt đi em")

    state = await ConversationService(test_db).get_state(str(user.id))
    assert state is not None and state.slots is not None
    assert state.slots.intent is None
    assert state.slots.day is None
    assert state.slots.time is None
    assert state.tool_day is None
```

Add `test_make_agent_node_merges_same_turn_supervisor_analysis_into_slots` at the end of `tests/test_subagents.py`:

```python
async def test_make_agent_node_merges_same_turn_supervisor_analysis_into_slots():
    """Dữ kiện `service` và `stated` do supervisor (`analyze_turn`) trích xuất ở
    đầu lượt phải có mặt ngay trong `slots` gửi cho agent ở chính lượt đó."""
    model = scripted([AIMessage(content="Dạ.")])
    state = {
        **a_state("chị muốn sơn thạch, chỉ rảnh sau 5 giờ chiều"),
        "analysis": {
            "route": "booking",
            "service": "sơn thạch",
            "stated": ["chỉ rảnh sau 5 giờ chiều"],
        },
    }
    await node_for(model, tools=())(state)
    sent = "\n".join(str(m.content) for m in model.calls[0])
    assert "- Dịch vụ: sơn thạch" in sent
    assert "- Khách đã nói rõ: chỉ rảnh sau 5 giờ chiều" in sent
```

- [x] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_confirm.py -k "working_memory" tests/test_subagents.py -k "merges_same_turn" -v`
Expected: FAIL.

- [x] **Step 3: Implement Working Memory updates in `app/agents/booking_graph/confirm.py` and same-turn slot merge in `app/agents/booking_graph/agents.py`**

1. In `app/agents/booking_graph/confirm.py`:
   - Import `to_local` from `app.core.clock` and `ConversationStateService` from `app.services.conversation_state`.
   - In `execute_proposal` (`app/agents/booking_graph/confirm.py:151-155`):
     ```python
     result = await ProposalService(db).approve(user, proposal_id, turn_at=turn_at)
     if result.outcome == "not_approvable":
         return None
     if result.outcome == "approved":
         await ConversationStateService(db).record_tool_facts(
             str(user.id), clear_active=True
         )
     context = await _resolve_context(db, user, result.proposal, context_block)
     return confirm_fact_for(result, address_phrase(user), context=context)
     ```
   - In `_handle_button` (`button == "no"`, `app/agents/booking_graph/confirm.py:235-247`):
     ```python
     if button == "no":
         proposal = await proposals.get(user, proposal_id)
         if proposal is None or proposal.status != "pending" or not await proposals.reject(user, proposal_id):
             return _stale_button_reply(address)
         local_p = to_local(proposal.start_at)
         updated_slots = await ConversationStateService(db).record_tool_facts(
             str(user.id),
             day=local_p.date().isoformat(),
             declined_iso=local_p.isoformat(),
         )
         text = _button_text(BUTTON_NO_REPLY, address)
         when = format_vi_datetime(proposal.start_at)
         out = {"draft": text, "fallback": text, "answer_source": "code",
                "gate_next": "phrase", "proposal_id": None,
                "confirm_fact": {"kind": "clarify", "when": when, "note": None,
                                 "address": address, "error": None},
                "context_block": with_line(state.get("context_block", ""),
                                           declined_line(proposal))}
         if updated_slots is not None:
             out["slots"] = updated_slots
         return out
     ```
   - In `make_confirm_node` (`app/agents/booking_graph/confirm.py:373-390`):
     ```python
         elif decision == "change":
             await proposals.supersede(user, proposal_id)
         else:
             await proposals.reject(user, proposal_id)

         local_p = to_local(proposal.start_at)
         updated_slots = await ConversationStateService(db).record_tool_facts(
             str(user.id),
             day=local_p.date().isoformat(),
             declined_iso=local_p.isoformat(),
         )

         logger.info("confirm_declined", extra={"proposal_id": proposal_id, "decision": decision})
         out = {"proposal_id": None, "gate_next": "booking",
                "context_block": with_line(state.get("context_block", ""),
                                           declined_line(proposal)),
                **analysed}
         if updated_slots is not None:
             out["slots"] = updated_slots
         return out
     ```

2. In `app/agents/booking_graph/agents.py`:
   - Import `ConversationSlots` from `app.models.conversation` and `MAX_STATED` from `app.services.conversation_state`.
   - Add `_merge_turn_analysis_slots` right above `make_agent_node`:
     ```python
     def _merge_turn_analysis_slots(
         slots: Optional[ConversationSlots], analysis: Optional[dict]
     ) -> Optional[ConversationSlots]:
         """Gộp nhẹ `service` và `stated` từ `TurnAnalysis` của chính lượt này vào
         `slots` gửi cho Agent — để Agent (`booking`, `shop`, `social`) thấy ngay
         dữ kiện vừa phân tích ở `supervisor` mà không phải đợi `apply_analysis`
         chạy sau `complete`."""
         if not analysis:
             return slots
         new_service = analysis.get("service")
         new_stated = [s for s in (analysis.get("stated") or []) if s]
         if not new_service and not new_stated:
             return slots
         base = slots.model_copy(deep=True) if slots is not None else ConversationSlots()
         if new_service:
             base.service = new_service
         for item in new_stated:
             if item not in base.stated:
                 base.stated.append(item)
         base.stated = base.stated[-MAX_STATED:]
         return base
     ```
   - In `make_agent_node` (`app/agents/booking_graph/agents.py:195`), pass:
     ```python
             "slots": _merge_turn_analysis_slots(state.get("slots"), state.get("analysis")),
     ```

- [x] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_confirm.py tests/test_subagents.py tests/test_run_turn_thread.py -v`
Expected: PASS (100% green).

- [x] **Step 5: Commit Phase 3**

```bash
git add app/agents/booking_graph/confirm.py app/agents/booking_graph/agents.py tests/test_confirm.py tests/test_subagents.py
git commit -m "feat(booking_graph): pass updated slots across confirm and supervisor in same turn"
```

---

### Phase 4: Pure-Python Structured `timeparse.py` (0 Sub-LLM, 0 Regex) & Regex Budget Lock (`2` regex)

**Files (1 production file, 2 test files):**
- Modify: `app/agents/booking_graph/timeparse.py`
- Modify: `tests/test_timeparse.py`
- Modify: `tests/test_regex_budget.py`

**Interfaces:**
- Produces in `app/agents/booking_graph/timeparse.py`:
  - `ParsedTime` with `day_from: Optional[Literal["explicit", "anchor"]] = None` and `source: Literal["code", "regex", "llm"] = "code"`
  - `apply_session_day(candidate: ParsedTime, session_day: Optional[date]) -> ParsedTime`
  - `parse_vi_time(now: datetime, *, day: Optional[str] = None, hour: Optional[int] = None, minute: int = 0, period: Optional[str] = None, is_now: bool = False, ambiguous_week: bool = False, anchor: Optional[datetime] = None, session_day: Optional[date] = None, hours: Optional[ShopHours] = None) -> ParsedTime` (0 LLM calls, 0 regex)

- [x] **Step 1: Write the failing tests in `tests/test_regex_budget.py` and `tests/test_timeparse.py`**

Replace `tests/test_regex_budget.py` with:

```python
"""Hàng rào số lượng regex (`re.compile`) trong tầng AI Agent (`app/agents/booking_graph/`).

Spec `2026-10-28-llm-context-input-design.md`: cắt xuống còn đúng 2 regex:
- `guard.py`: 2 (`_TOOL_GROUP`, `_TOOL_BARE` trong `strip_tool_names`)
- Mọi module khác trong `app/agents/booking_graph/` (kể cả `timeparse.py`): 0 regex.
"""
import ast
import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parents[1] / "app" / "agents" / "booking_graph"

EXPECTED_BUDGET = {
    "guard.py": 2,
}


def test_agent_regex_budget_is_at_most_two():
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

Replace `tests/test_timeparse.py` with:

```python
import inspect
import re
from datetime import date, datetime, timedelta

import pytest

from app.agents.booking_graph import timeparse
from app.agents.booking_graph.timeparse import (
    PARSE_TIMEOUT_SECONDS,
    ParsedTime,
    _guard,
    _to_24h,
    apply_anchor,
    apply_session_day,
    parse_vi_time,
)
from app.core.clock import TZ
from app.models.shop import ShopHours

NOW = datetime(2026, 8, 7, 14, 30, tzinfo=TZ)   # Thứ Sáu 7/8/2026, 2:30 chiều
HOURS = ShopHours(open_time="08:00", close_time="19:00")
ANCHOR_MORNING = datetime(2026, 9, 15, 9, 0, tzinfo=TZ)
ANCHOR_AFTERNOON = datetime(2026, 9, 15, 15, 0, tzinfo=TZ)


def at(day, hour, minute=0):
    return datetime(2026, 8, day, hour, minute, tzinfo=TZ)


class TestGuard:
    def test_lets_a_sensible_time_through(self):
        result = _guard(ParsedTime(start_at=at(8, 15)), NOW)
        assert result.start_at == at(8, 15)

    def test_attaches_vietnam_time_when_the_model_forgets_the_offset(self):
        naive = datetime(2026, 8, 8, 15, 0)
        result = _guard(ParsedTime(start_at=naive), NOW)
        assert result.start_at is not None
        assert result.start_at.utcoffset() is not None
        assert result.start_at == at(8, 15)

    def test_a_time_already_past_is_rejected(self):
        result = _guard(ParsedTime(start_at=at(7, 10)), NOW)
        assert result.start_at is None
        assert result.missing == ["ngày khác — giờ đó qua mất rồi"]

    def test_a_year_off_date_is_rejected(self):
        result = _guard(
            ParsedTime(start_at=datetime(2025, 8, 8, 15, 0, tzinfo=TZ)), NOW
        )
        assert result.start_at is None

    def test_a_date_too_far_ahead_is_rejected(self):
        far = NOW + timedelta(days=120)
        result = _guard(ParsedTime(start_at=far), NOW)
        assert result.start_at is None
        assert result.missing == ["ngày gần hơn"]

    def test_ninety_days_ahead_is_still_allowed(self):
        edge = NOW + timedelta(days=89)
        assert _guard(ParsedTime(start_at=edge), NOW).start_at == edge

    def test_missing_information_passes_through_untouched(self):
        candidate = ParsedTime(
            start_at=None, partial_date=date(2026, 8, 8), missing=["giờ cụ thể"]
        )
        result = _guard(candidate, NOW)
        assert result.missing == ["giờ cụ thể"]
        assert result.partial_date == date(2026, 8, 8)

    def test_guard_does_not_check_opening_hours(self):
        three_am = _guard(ParsedTime(start_at=at(8, 3)), NOW)
        assert three_am.start_at == at(8, 3)


class TestTo24h:
    @pytest.mark.parametrize(
        "hour,period,expected",
        [
            (3, "chiều", 15),
            (15, "chiều", 15),
            (12, "trưa", 12),
            (12, "chiều", 12),
            (1, "trưa", 13),
            (13, "trưa", 13),
            (9, "sáng", 9),
            (7, "tối", 19),
            (19, "tối", 19),
            (15, None, 15),
            (0, None, 0),
            (3, None, None),
            (9, None, None),
        ],
    )
    def test_converts_12h_and_24h_correctly(self, hour, period, expected):
        assert _to_24h(hour, period) == expected


class TestParseViTimeStructured:
    async def test_full_day_and_hour_with_period(self):
        out = await parse_vi_time(NOW, day="2026-08-08", hour=3, period="chiều")
        assert out.start_at == at(8, 15)
        assert out.partial_date == date(2026, 8, 8)
        assert out.missing == []
        assert out.day_from == "explicit"
        assert out.source == "code"

    async def test_full_day_and_24h_hour_with_period(self):
        out = await parse_vi_time(NOW, day="2026-08-08", hour=15, minute=30, period="chiều")
        assert out.start_at == at(8, 15, 30)
        assert out.missing == []

    async def test_day_only_asks_for_specific_hour(self):
        out = await parse_vi_time(NOW, day="2026-08-08", period="sáng")
        assert out.start_at is None
        assert out.partial_date == date(2026, 8, 8)
        assert out.missing == ["giờ cụ thể"]
        assert out.day_from == "explicit"

    async def test_day_and_ambiguous_hour_asks_for_period(self):
        out = await parse_vi_time(NOW, day="2026-08-08", hour=3)
        assert out.start_at is None
        assert out.partial_date == date(2026, 8, 8)
        assert out.partial_hour == 3
        assert out.missing == ["sáng hay chiều"]

    async def test_hour_with_period_without_day_asks_for_day(self):
        out = await parse_vi_time(NOW, hour=3, period="chiều")
        assert out.start_at is None
        assert out.partial_hour == 15
        assert out.missing == ["ngày nào"]

    async def test_ambiguous_hour_without_day_asks_for_day_and_period(self):
        out = await parse_vi_time(NOW, hour=3)
        assert out.start_at is None
        assert out.partial_hour == 3
        assert out.missing == ["ngày nào", "sáng hay chiều"]

    async def test_ambiguous_week_asks_which_week(self):
        out = await parse_vi_time(NOW, ambiguous_week=True, hour=3, period="chiều")
        assert out.start_at is None
        assert out.missing == ["tuần này hay tuần sau"]

    async def test_ambiguous_week_with_ambiguous_hour_keeps_both_questions(self):
        out = await parse_vi_time(NOW, ambiguous_week=True, hour=3, anchor=ANCHOR_MORNING, hours=HOURS)
        assert out.start_at is None
        assert out.missing == ["tuần này hay tuần sau", "sáng hay chiều"]

    async def test_is_now_bumps_to_next_15_minute_slot(self):
        now = datetime(2026, 8, 7, 14, 32, 10, tzinfo=TZ)
        out = await parse_vi_time(now, is_now=True)
        assert out.start_at == at(7, 14, 45)
        assert out.missing == []
        assert out.day_from == "explicit"

    async def test_empty_call_asks_for_specific_time(self):
        out = await parse_vi_time(NOW)
        assert out.start_at is None
        assert out.missing == ["giờ cụ thể"]

    async def test_out_of_range_hour_or_minute_fails_soft(self):
        out = await parse_vi_time(NOW, hour=25, anchor=ANCHOR_MORNING, hours=HOURS)
        assert out.start_at is None
        assert out.missing == ["giờ cụ thể"]

        out_min = await parse_vi_time(NOW, day="2026-08-08", hour=10, minute=75)
        assert out_min.start_at is None
        assert out_min.partial_date == date(2026, 8, 8)
        assert out_min.missing == ["giờ cụ thể"]


class TestRightNow:
    def test_right_now_becomes_the_next_slot_instead_of_being_called_past(self):
        now = datetime(2026, 8, 7, 14, 32, 10, tzinfo=TZ)
        result = _guard(ParsedTime(start_at=now), now)
        assert result.start_at == at(7, 14, 45)
        assert result.missing == []

    def test_a_time_inside_the_current_slot_also_moves_up(self):
        now = datetime(2026, 8, 7, 14, 32, 10, tzinfo=TZ)
        result = _guard(ParsedTime(start_at=at(7, 14, 30)), now)
        assert result.start_at == at(7, 14, 45)

    def test_the_bumped_time_lands_on_a_15_minute_mark(self):
        from app.core.slots import slot_keys_for

        now = datetime(2026, 8, 7, 14, 32, 10, tzinfo=TZ)
        result = _guard(ParsedTime(start_at=now), now)
        assert result.start_at is not None
        assert slot_keys_for(result.start_at, 60)

    def test_exactly_on_a_slot_mark_moves_to_the_next_one(self):
        now = datetime(2026, 8, 7, 14, 30, 0, tzinfo=TZ)
        result = _guard(ParsedTime(start_at=now), now)
        assert result.start_at == at(7, 14, 45)

    def test_a_genuinely_past_time_is_still_rejected(self):
        now = datetime(2026, 8, 7, 14, 32, 10, tzinfo=TZ)
        result = _guard(ParsedTime(start_at=at(7, 10)), now)
        assert result.start_at is None
        assert result.missing == ["ngày khác — giờ đó qua mất rồi"]


class TestMissingIsAClosedVocabulary:
    def test_rejects_a_question_outside_the_vocabulary(self):
        with pytest.raises(ValueError):
            ParsedTime(missing=["thứ ba tuần sau là ngày nào cụ thể"])

    @pytest.mark.parametrize("question", [
        "giờ cụ thể",
        "sáng hay chiều",
        "ngày nào",
        "tuần này hay tuần sau",
        "ngày khác — giờ đó qua mất rồi",
        "ngày gần hơn",
    ])
    def test_accepts_every_question_the_code_actually_asks(self, question):
        assert ParsedTime(missing=[question]).missing == [question]


class TestKnownDayIsNeverAskedAgain:
    def test_the_day_question_is_dropped_when_the_day_is_known(self):
        parsed = ParsedTime(partial_date=date(2026, 9, 15), missing=["ngày nào"])
        assert parsed.partial_date == date(2026, 9, 15)
        assert "ngày nào" not in parsed.missing

    def test_the_week_question_is_dropped_too(self):
        parsed = ParsedTime(
            partial_date=date(2026, 9, 15), missing=["tuần này hay tuần sau"]
        )
        assert parsed.partial_date == date(2026, 9, 15)
        assert "tuần này hay tuần sau" not in parsed.missing

    def test_dropping_the_day_question_leaves_the_time_question(self):
        parsed = ParsedTime(partial_date=date(2026, 9, 15), missing=["ngày nào"])
        assert parsed.missing == ["giờ cụ thể"]

    def test_a_known_start_needs_no_question_at_all(self):
        parsed = ParsedTime(
            start_at=datetime(2026, 9, 15, 15, 0, tzinfo=TZ),
            partial_date=date(2026, 9, 15),
            missing=["ngày nào"],
        )
        assert parsed.missing == []

    def test_allows_asking_the_time_when_the_day_is_known(self):
        parsed = ParsedTime(partial_date=date(2026, 9, 17), missing=["giờ cụ thể"])
        assert parsed.partial_date == date(2026, 9, 17)
        assert parsed.missing == ["giờ cụ thể"]

    def test_allows_asking_which_day_when_no_day_is_known(self):
        assert ParsedTime(missing=["ngày nào"]).missing == ["ngày nào"]


class TestApplyAnchorAndSessionDay:
    def test_no_anchor_changes_nothing(self):
        c = ParsedTime(missing=["ngày nào"], partial_hour=10)
        assert apply_anchor(c, None, HOURS) == c

    def test_missing_day_takes_the_anchor_day_and_marks_day_from_anchor(self):
        c = ParsedTime(missing=["ngày nào"], partial_hour=10, partial_minute=0)
        out = apply_anchor(c, ANCHOR_MORNING, HOURS)
        assert out.start_at == datetime(2026, 9, 15, 10, 0, tzinfo=TZ)
        assert out.missing == []
        assert out.day_from == "anchor"

    def test_missing_period_with_explicit_day_keeps_day_from_explicit(self):
        c = ParsedTime(
            missing=["sáng hay chiều"],
            partial_date=date(2026, 9, 15),
            partial_hour=10,
            day_from="explicit",
        )
        out = apply_anchor(c, ANCHOR_MORNING, HOURS)
        assert out.start_at == datetime(2026, 9, 15, 10, 0, tzinfo=TZ)
        assert out.missing == []
        assert out.day_from == "explicit"

    def test_missing_period_afternoon_anchor(self):
        c = ParsedTime(missing=["sáng hay chiều"], partial_date=date(2026, 9, 15), partial_hour=4)
        out = apply_anchor(c, ANCHOR_AFTERNOON, HOURS)
        assert out.start_at == datetime(2026, 9, 15, 16, 0, tzinfo=TZ)

    def test_anchor_period_outside_hours_uses_the_other_period(self):
        c = ParsedTime(missing=["sáng hay chiều"], partial_date=date(2026, 9, 15), partial_hour=7)
        out = apply_anchor(c, ANCHOR_MORNING, HOURS)
        assert out.start_at is None and out.missing == ["sáng hay chiều"]

    def test_anchor_period_outside_hours_but_other_period_open(self):
        c = ParsedTime(missing=["sáng hay chiều"], partial_date=date(2026, 9, 15), partial_hour=6)
        out = apply_anchor(c, ANCHOR_MORNING, HOURS)
        assert out.start_at == datetime(2026, 9, 15, 18, 0, tzinfo=TZ)

    def test_missing_both_day_and_period(self):
        c = ParsedTime(missing=["ngày nào", "sáng hay chiều"], partial_hour=10)
        out = apply_anchor(c, ANCHOR_MORNING, HOURS)
        assert out.start_at == datetime(2026, 9, 15, 10, 0, tzinfo=TZ)
        assert out.day_from == "anchor"

    def test_missing_the_hour_itself_cannot_be_resolved(self):
        c = ParsedTime(missing=["giờ cụ thể"], partial_date=date(2026, 9, 15))
        assert apply_anchor(c, ANCHOR_MORNING, HOURS).start_at is None

    def test_no_hours_given_trusts_the_anchor_period(self):
        c = ParsedTime(missing=["sáng hay chiều"], partial_date=date(2026, 9, 15), partial_hour=10)
        assert apply_anchor(c, ANCHOR_MORNING, None).start_at == datetime(2026, 9, 15, 10, 0, tzinfo=TZ)

    def test_out_of_range_hour_is_ignored_not_a_crash(self):
        c = ParsedTime(missing=["sáng hay chiều"], partial_date=date(2026, 9, 15), partial_hour=25)
        assert apply_anchor(c, ANCHOR_MORNING, HOURS) == c

    def test_out_of_range_minute_is_ignored_not_a_crash(self):
        c = ParsedTime(
            missing=["sáng hay chiều"], partial_date=date(2026, 9, 15),
            partial_hour=10, partial_minute=70,
        )
        assert apply_anchor(c, ANCHOR_MORNING, HOURS) == c

    def test_week_unknown_is_not_resolved(self):
        c = ParsedTime(missing=["tuần này hay tuần sau"], partial_hour=10)
        assert apply_anchor(c, ANCHOR_MORNING, HOURS) == c

    def test_week_unknown_combined_with_period_is_not_resolved(self):
        c = ParsedTime(missing=["tuần này hay tuần sau", "sáng hay chiều"], partial_hour=3)
        assert apply_anchor(c, ANCHOR_MORNING, HOURS) == c

    def test_session_day_fills_missing_day_when_hour_and_period_are_known(self):
        c = ParsedTime(missing=["ngày nào"], partial_hour=10, partial_minute=15)
        out = apply_session_day(c, date(2026, 8, 8))
        assert out.start_at == at(8, 10, 15)
        assert out.partial_date == date(2026, 8, 8)
        assert out.missing == []
        assert out.day_from == "anchor"

    def test_session_day_fills_partial_date_when_period_still_missing(self):
        c = ParsedTime(missing=["ngày nào", "sáng hay chiều"], partial_hour=10)
        out = apply_session_day(c, date(2026, 8, 8))
        assert out.start_at is None
        assert out.partial_date == date(2026, 8, 8)
        assert out.missing == ["sáng hay chiều"]
        assert out.day_from == "anchor"


def test_timeout_constant_kept_for_compat():
    assert PARSE_TIMEOUT_SECONDS >= 5.0


def test_timeparse_uses_zero_regex_and_zero_llm():
    source = inspect.getsource(timeparse)
    assert len(re.findall(r"re\.compile\(", source)) == 0
    assert "build_chat_model" not in source
```

- [x] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_regex_budget.py tests/test_timeparse.py -v`
Expected: FAIL.

- [x] **Step 3: Implement pure-Python structured `app/agents/booking_graph/timeparse.py`**

Replace `app/agents/booking_graph/timeparse.py` with:

```python
from datetime import date, datetime, timedelta
from typing import List, Literal, Optional

from pydantic import BaseModel, Field, model_validator

from app.agents.booking_graph import tokens
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


def _to_24h(hour: int, period: Optional[str]) -> Optional[int]:
    """Quy giờ nói kiểu Việt (hoặc giờ 24h kèm buổi do Agent truyền) về giờ 24 (0..23).
    Trả None nếu không chắc chắn buổi (1..12 không kèm buổi)."""
    norm = tokens._PERIOD_MAP.get((period or "").strip().lower()) if period else None
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
    period: Optional[str] = None,
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

    norm_period = tokens._PERIOD_MAP.get((period or "").strip().lower()) if period else None
    resolved_hour = _to_24h(hour, norm_period) if hour is not None else None

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
```

- [x] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_regex_budget.py tests/test_timeparse.py -v`
Expected: PASS (100% green).

- [x] **Step 5: Commit Phase 4**

```bash
git add app/agents/booking_graph/timeparse.py tests/test_regex_budget.py tests/test_timeparse.py
git commit -m "feat(timeparse): replace sub-LLM and regex with structured pure-python resolver"
```

---

### Phase 5: Structured `parse_time` Tool, Safe Auto-Anchor & Tool Fact Recording (`tools.py`)

**Files (1 production file, 2 test files):**
- Modify: `app/agents/booking_graph/tools.py`
- Modify: `tests/test_tools.py`
- Modify: `tests/test_timeparse_llm.py`

**Interfaces:**
- Consumes: `ConversationStateService.record_tool_facts` from Phase 2, structured `parse_vi_time` from Phase 4.
- Produces in `app/agents/booking_graph/tools.py`:
  - `parse_time(day: Optional[str] = None, hour: Optional[int] = None, minute: int = 0, period: Optional[str] = None, is_now: bool = False, ambiguous_week: bool = False, anchor: Optional[str] = None) -> str`
  - Auto-anchor strictly from unapproved `ProposalService(db).latest(user)` (`pending` or `DECLINED_STATUSES` within `DECLINED_MAX_AGE_MINUTES`, never `approved`) or `state.tool_day`.
  - Records facts into `state_svc.record_tool_facts` on `parse_time`, `find_free_slots` (`day` only), `propose_appointment`, and `cancel_appointment`.

- [x] **Step 1: Update `tests/test_tools.py` and `tests/test_timeparse_llm.py`**

In `tests/test_tools.py`:
1. Update the 3 existing `parse_time` calls:
   - `test_parse_time_returns_json_the_agent_can_hand_straight_on`:
     ```python
     result = await by_name(make_booking_tools(test_db, user), "parse_time").ainvoke(
         {"day": tomorrow_at(15).date().isoformat(), "hour": 3, "period": "chiều"}
     )
     ```
   - `test_parse_time_says_what_is_missing_instead_of_guessing`:
     ```python
     result = await by_name(make_booking_tools(test_db, user), "parse_time").ainvoke(
         {"day": tomorrow_at(9).date().isoformat(), "period": "sáng"}
     )
     ```
   - `TestParseTimeAnchor.test_bad_anchor_is_ignored_not_fatal`:
     ```python
     out = await by_name(make_booking_tools(test_db, user), "parse_time").ainvoke(
         {"hour": 10, "anchor": "không phải iso"}
     )
     ```
2. Add the 3 new tests inside `TestParseTimeAnchor`:

```python
    async def test_parse_time_remembers_day_across_turns_via_tool_day_and_flags_day_from_anchor(self, test_db):
        """Lượt 1 khách nói 'sáng mai' (chưa có giờ) -> parse_time lưu partial_date
        vào state.tool_day & state.slots.day. Lượt 2 khách chỉ nói '10 giờ 15 sáng'
        mà agent quên truyền day/anchor -> parse_time tự nối với state.tool_day
        và báo day_from='anchor'."""
        user = await a_user(test_db)
        tool = by_name(make_booking_tools(test_db, user), "parse_time")
        day_iso = tomorrow_at(9).date().isoformat()

        out1 = json.loads(await tool.ainvoke({"day": day_iso, "period": "sáng"}))
        assert out1["start_at"] is None
        assert out1["partial_date"] == day_iso

        out2 = json.loads(await tool.ainvoke({"hour": 10, "minute": 15, "period": "sáng"}))
        assert out2["start_at"] is not None
        assert out2["start_at"].startswith(day_iso)
        assert "T10:15:00+07:00" in out2["start_at"]
        assert out2["day_from"] == "anchor"

    async def test_parse_time_never_uses_an_approved_proposal_as_auto_anchor(self, test_db):
        """Đề xuất đã được duyệt (approved) là lịch đã chốt xong — sang câu mới
        khách hỏi '10 giờ sáng' không kèm ngày thì PHẢI hỏi 'ngày nào', tuyệt đối
        không tự neo vào đề xuất đã approved."""
        from app.core.clock import now_utc
        from app.services.proposal import ProposalService

        user = await a_user(test_db)
        proposals = ProposalService(test_db)
        p = await proposals.create(user, "book", tomorrow_at(15), "làm tóc")
        await proposals.approve(user, str(p.id), turn_at=now_utc() + timedelta(seconds=5))

        tool = by_name(make_booking_tools(test_db, user), "parse_time")
        out = json.loads(await tool.ainvoke({"hour": 10, "period": "sáng"}))
        assert out["start_at"] is None
        assert out["missing"] == ["ngày nào"]

    async def test_find_free_slots_and_propose_populate_working_memory(self, test_db):
        from app.services.conversation import ConversationService

        user = await a_user(test_db)
        btools = make_booking_tools(test_db, user)
        start = tomorrow_at(15)
        day_iso = start.date().isoformat()

        await by_name(btools, "find_free_slots").ainvoke({"day": day_iso, "part_of_day": "chiều"})
        state1 = await ConversationService(test_db).get_state(str(user.id))
        assert state1.slots.day == day_iso
        assert state1.tool_day == day_iso
        assert state1.slots.told == []

        await by_name(btools, "propose_appointment").ainvoke(
            {"start_at": start.isoformat(), "note": "làm móng bột"}
        )
        state2 = await ConversationService(test_db).get_state(str(user.id))
        assert state2.slots.intent == "book"
        assert state2.slots.day == day_iso
        assert state2.slots.time == "15:00"
        assert state2.slots.service == "làm móng bột"
```

Replace `tests/test_timeparse_llm.py` with:

```python
"""Gọi Azure OpenAI thật. Mặc định bị loại khỏi pytest bởi addopts trong
`[tool.pytest.ini_options]` của pyproject.toml. Chạy tay bằng:
pytest tests/test_timeparse_llm.py -m llm -v

Đo xem Booking Agent (với Lịch 14 ngày trong context_block) trích xuất tham số
cho `parse_time` và giải ra `ParsedTime` có chính xác với tiếng Việt thật không.
"""
from datetime import datetime
from typing import Optional

import pytest
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.tools import tool

from app.agents.booking_graph.context import format_14_day_calendar, format_vi_datetime
from app.agents.booking_graph.prompts import BOOKING_PROMPT
from app.agents.booking_graph.timeparse import ParsedTime, parse_vi_time
from app.agents.llm import build_chat_model
from app.core.clock import TZ
from app.models.shop import ShopHours

pytestmark = [pytest.mark.llm, pytest.mark.asyncio]

NOW = datetime(2026, 8, 7, 14, 30, tzinfo=TZ)   # Thứ Sáu 7/8/2026, 2:30 chiều
HOURS = ShopHours(open_time="08:00", close_time="19:00")


async def _llm_parse(text: str) -> ParsedTime:
    @tool
    async def parse_time(
        day: Optional[str] = None,
        hour: Optional[int] = None,
        minute: int = 0,
        period: Optional[str] = None,
        is_now: bool = False,
        ambiguous_week: bool = False,
        anchor: Optional[str] = None,
    ) -> str:
        """Turn what the customer said about time into a concrete date and time."""
        return "{}"

    ctx = (
        f"Bây giờ là {format_vi_datetime(NOW)} (hôm nay là {NOW.date().isoformat()}, giờ Việt Nam).\n"
        f"{format_14_day_calendar(NOW.date())}\n"
        "Bạn đang nói chuyện với: Lan.\nGọi khách là: chị Lan."
    )
    model = build_chat_model(tags=["timeparse"], streaming=False).bind_tools([parse_time])
    ai = await model.ainvoke([
        SystemMessage(content=BOOKING_PROMPT),
        HumanMessage(content=f"{ctx}\n\nKhách nói: {text}"),
    ])
    if not ai.tool_calls:
        return ParsedTime(missing=[])
    args = ai.tool_calls[0]["args"]
    return await parse_vi_time(
        NOW,
        day=args.get("day"),
        hour=args.get("hour"),
        minute=args.get("minute") or 0,
        period=args.get("period"),
        is_now=bool(args.get("is_now")),
        ambiguous_week=bool(args.get("ambiguous_week")),
        hours=HOURS,
    )


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
    result = await _llm_parse(text)
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
    result = await _llm_parse(text)
    assert result.start_at is None, f"không được đoán với: {text}"
    assert result.missing, f"phải nói rõ thiếu gì ({phai_hoi_lai}) với: {text}"


async def test_a_question_with_no_time_returns_nothing():
    result = await _llm_parse("chủ tiệm rảnh không")
    assert result.start_at is None


@pytest.mark.parametrize(
    "text",
    [
        "bây giờ",
        "giờ chị qua được không",
        "chị qua liền được không",
        "giờ này qua được không",
    ],
)
async def test_right_now_resolves_to_the_next_slot(text):
    result = await _llm_parse(text)
    assert result.start_at is not None, f"vẫn hỏi lại thay vì hiểu: {text} ({result.missing})"
    assert result.start_at.isoformat() == "2026-08-07T14:45:00+07:00"
```

- [x] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_tools.py -k "parse_time or working_memory" -v`
Expected: FAIL.

- [x] **Step 3: Implement structured `parse_time` and `record_tool_facts` wiring in `app/agents/booking_graph/tools.py`**

In `app/agents/booking_graph/tools.py`:
1. Update imports at the top:
   ```python
   from datetime import date, datetime, timedelta
   ...
   from app.agents.booking_graph.context import (
       DECLINED_MAX_AGE_MINUTES,
       DECLINED_STATUSES,
       format_vi_datetime,
       format_vi_hhmm,
   )
   from app.agents.booking_graph.timeparse import ParsedTime, parse_vi_time
   from app.core.clock import TZ, ensure_aware, now_utc, to_local
   ...
   from app.services.conversation_state import ConversationStateService
   ```
2. In `make_booking_tools(db, user)`:
   - Instantiate `state_svc = ConversationStateService(db)`.
   - Replace `parse_time` with:
     ```python
     @tool
     async def parse_time(
         day: Optional[str] = None,
         hour: Optional[int] = None,
         minute: int = 0,
         period: Optional[str] = None,
         is_now: bool = False,
         ambiguous_week: bool = False,
         anchor: Optional[str] = None,
     ) -> str:
         """Turn what the customer said about time into a concrete date and time.
         Call this BEFORE find_free_slots and propose_appointment, every time the
         customer mentions a time. Never compute a date yourself.
         Extract the structured fields from the conversation and the 14-day
         calendar in the context block:
         - `day`: YYYY-MM-DD copied from the 14-day calendar in the context block
           when a date is known (from this turn or an earlier turn). Leave None
           if no date has been named yet.
         - `ambiguous_week`: set True when the customer names a weekday without
           specifying this week or next week. Leave `day` None in that case.
         - `hour`: clock hour as an integer (1-12 when paired with `period` or
           when the part of the day is unknown; 0-23 when in 24-hour format).
         - `minute`: clock minute (0, 15, 30, 45; default 0).
         - `period`: part of the day if stated ("sáng", "trưa", "chiều", "tối").
         - `is_now`: set True when the customer asks to come right now / immediately.
         - `anchor`: pass it whenever the customer is changing or answering about a
           time already on the table — the appointment being moved (its `iso:` in
           the context block), the slot just proposed (`held.iso` in
           propose_appointment's result), or the proposal the customer just
           declined (the `iso` on the context block line tagged
           [declined: ...]). The tool then fills a missing day or part of the
           day from it, and sets `day_from: "anchor"` so you state the full date
           back to the customer.
         Result has `missing` -> ask the customer for exactly that ONE missing
         piece, one piece per turn, and nothing else. The value "sáng hay chiều"
         is quoted from the tool's own output, not an example: it means the hour
         is known but the part of the day is not."""
         now = now_utc()
         anchor_dt = None
         if anchor:
             try:
                 anchor_dt = _parse_local(anchor)
             except ValueError:
                 logger.warning("anchor_ignored", extra={"anchor": anchor[:60]})

         session_day: Optional[date] = None
         if anchor_dt is None:
             latest = await proposals.latest(user)
             if latest is not None:
                 if latest.status == "pending":
                     anchor_dt = to_local(latest.start_at)
                 elif (
                     latest.status in DECLINED_STATUSES
                     and now - ensure_aware(latest.updated_at)
                     <= timedelta(minutes=DECLINED_MAX_AGE_MINUTES)
                 ):
                     anchor_dt = to_local(latest.start_at)
             state = await state_svc.conversations.get_state(str(user.id))
             if state and state.tool_day:
                 try:
                     session_day = date.fromisoformat(state.tool_day)
                 except ValueError:
                     session_day = None

         hours = await service.shop.get_hours()
         parsed: ParsedTime = await parse_vi_time(
             now,
             day=day,
             hour=hour,
             minute=minute,
             period=period,
             is_now=is_now,
             ambiguous_week=ambiguous_week,
             anchor=anchor_dt,
             session_day=session_day,
             hours=hours,
         )

         rec_day = (
             to_local(parsed.start_at).date().isoformat()
             if parsed.start_at is not None
             else (parsed.partial_date.isoformat() if parsed.partial_date is not None else None)
         )
         rec_time = (
             to_local(parsed.start_at).strftime("%H:%M")
             if parsed.start_at is not None
             else (
                 f"{parsed.partial_hour:02d}:{parsed.partial_minute:02d}"
                 if parsed.partial_hour is not None
                 and 0 <= parsed.partial_hour <= 23
                 and "sáng hay chiều" not in parsed.missing
                 else None
             )
         )
         if rec_day or rec_time:
             await state_svc.record_tool_facts(str(user.id), day=rec_day, time=rec_time)

         payload = parsed.model_dump(
             mode="json",
             include={"start_at", "partial_date", "missing"},
             exclude_none=False,
         )
         if parsed.day_from == "anchor":
             payload["day_from"] = "anchor"
         return json.dumps(payload, ensure_ascii=False)
     ```
   - In `find_free_slots`: after `parsed_day = date.fromisoformat(day)` succeeds, call:
     ```python
     await state_svc.record_tool_facts(str(user.id), day=parsed_day.isoformat())
     ```
   - In `propose_appointment`: right after `proposal = await proposals.create(...)`, call:
     ```python
     local_start = to_local(start)
     await state_svc.record_tool_facts(
         str(user.id),
         intent="reschedule" if old else "book",
         day=local_start.date().isoformat(),
         time=local_start.strftime("%H:%M"),
         service=note,
     )
     ```
   - In `cancel_appointment`: right after `proposal = await proposals.create(...)`, call:
     ```python
     local_appt = to_local(appt.start_at)
     await state_svc.record_tool_facts(
         str(user.id),
         intent="cancel",
         day=local_appt.date().isoformat(),
         time=local_appt.strftime("%H:%M"),
     )
     ```

- [x] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_tools.py tests/test_prompts.py -v`
Expected: PASS (100% green).

- [x] **Step 5: Commit Phase 5**

```bash
git add app/agents/booking_graph/tools.py tests/test_tools.py tests/test_timeparse_llm.py
git commit -m "feat(tools): wire structured parse_time, safe auto-anchor, and tool fact recording"
```

---

### Phase 6: Multi-Turn & Cross-Agent End-to-End Regression Verification (`tests/test_confirm_flow.py`)

**Files (1 test file):**
- Modify: `tests/test_confirm_flow.py`

**Interfaces:**
- Consumes: `make_booking_tools`, `parse_time`, `propose_appointment`, `confirm`, `agents`, and `ConversationStateService.record_tool_facts` from Phases 1–5.
- Produces: End-to-end multi-turn and same-turn cross-agent regression tests in `tests/test_confirm_flow.py`.

- [x] **Step 1: Add end-to-end tests in `tests/test_confirm_flow.py`**

Add `test_working_memory_preserves_day_and_declined_slot_across_multiple_turns` and `test_same_turn_confirm_decline_passes_updated_slots_to_booking_agent` at the end of `tests/test_confirm_flow.py`:

```python
async def test_working_memory_preserves_day_and_declined_slot_across_multiple_turns(
    test_db, user, models
):
    """Kiểm chứng sửa lỗ hổng mất context giữa các lượt và giữa các agent:
    - Lượt 1 (`booking`): Đề xuất 3 giờ chiều mai -> interrupt.
    - Lượt 2 (`confirm` -> `booking`): Khách từ chối ('dạ ko em') -> đề xuất đóng,
      Working Memory (state.slots + state.tool_day) lưu day = ngày mai & declined = [15:00].
    - Lượt 3 (`shop`): Khách hỏi chuyện phụ ('tiệm mở cửa mấy giờ') -> sang Lượt 4,
      dòng [declined: ...] đã hết hạn 1 lượt, NHƯNG Working Memory (Trạng thái cuộc trò chuyện)
      vẫn giữ nguyên 'Ngày đang nhắm' và 'Đã chào mà khách không lấy', và
      parse_time(hour=4, period='chiều') tự động nối đúng vào ngày mai (kèm day_from='anchor')."""
    from app.agents.booking_graph.tools import make_booking_tools

    await propose_turn(test_db, user, models, hour=15)

    # Lượt 2: Khách từ chối -> ngay trong lượt 2, booking agent đã thấy slots.declined
    models.agent.replies += [AIMessage("Dạ vâng, chị Lan muốn đổi sang giờ nào ạ?")]
    await turn(test_db, user, "dạ ko em")
    turn2_prompt = "\n".join(str(m.content) for m in models.agent.calls[-1])
    assert "Đã chào mà khách không lấy:" in turn2_prompt

    # Lượt 3: Khách hỏi chuyện phụ (qua shop agent)
    models.sup.replies += [AIMessage("shop")]
    models.agent.replies += [AIMessage("Dạ tiệm mở cửa từ 8 giờ sáng đến 7 giờ tối ạ.")]
    await turn(test_db, user, "tiệm mở cửa mấy giờ em")

    # Lượt 4: Khách nói "vậy 4 giờ chiều nha" (không nhắc lại ngày mai)
    parse_tool = next(t for t in make_booking_tools(test_db, user) if t.name == "parse_time")
    parsed = json.loads(await parse_tool.ainvoke({"hour": 4, "period": "chiều"}))
    assert parsed["start_at"] == tomorrow_at(16).isoformat()
    assert parsed["day_from"] == "anchor"

    models.sup.replies += [AIMessage("booking")]
    models.agent.replies += [AIMessage("Dạ chị muốn đặt 4 giờ chiều mai đúng không ạ?")]
    await turn(test_db, user, "vậy 4 giờ chiều nha")

    # Agent ở Lượt 4 phải nhìn thấy Trạng thái cuộc trò chuyện chứa Ngày đang nhắm & giờ đã từ chối
    full_prompt = "\n".join(str(m.content) for m in models.agent.calls[-1])
    assert "Trạng thái cuộc trò chuyện:" in full_prompt
    assert "Ngày đang nhắm:" in full_prompt
    assert "Đã chào mà khách không lấy:" in full_prompt
```

- [x] **Step 2: Run the new test and the full test suite**

Run: `pytest tests/test_confirm_flow.py -k "test_working_memory_preserves_day" -v && pytest`
Expected: PASS (all 725+ tests green, 0 failures).

- [x] **Step 3: Commit Phase 6**

```bash
git add tests/test_confirm_flow.py
git commit -m "test(confirm_flow): verify cross-turn and same-turn working memory persistence"
```
