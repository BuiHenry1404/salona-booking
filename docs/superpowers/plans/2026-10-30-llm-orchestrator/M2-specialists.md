# M2 — Hai specialist: Appointment Agent, Shop Info Agent

Đọc `README.md` trước. Cả hai là `create_agent` **isolated** (§8.1, §8.2, §12.15): chỉ nhận `TaskSpec` + `TurnContext full` + câu khách; không đọc thread. Hợp đồng chung: `run_task(db, user, spec: TaskSpec, ctx: TurnContext, question: str) -> TaskResult`.

**Giao:** `specialists/base.py`, `specialists/guard.py`, `specialists/appointment.py`, `specialists/shop_info.py`, `prompts/appointment.py`, `prompts/shop_info.py`, helper test `tool_call`.

---

### Task 2.1: Helper test `tool_call` + `tests/factories.py`

**Files:**
- Modify: `tests/fakes.py`
- Create/Modify: `tests/factories.py` (đã tạo ở M1 Task 1.2 nếu chưa: `make_ctx`, thêm `make_spec`)

**Interfaces — Produces:**

```python
def tool_call(name: str, args: dict, id: str | None = None) -> AIMessage        # AIMessage(content="", tool_calls=[{"name","args","id","type":"tool_call"}])
def tool_calls(*pairs: tuple[str, dict]) -> AIMessage                             # nhiều call trong MỘT AIMessage
def make_spec(agent="appointment", action="resolve_time", task_id="t1.0", **args) -> TaskSpec   # tests/factories.py
```

- [ ] **Step 1:** Viết hai hàm + test tự kiểm `test_tool_calls_builds_one_message_with_n_calls()` trong `tests/test_fakes.py` (mới, 2 test). **Step 2:** PASS. **Step 3: Commit** `test: add tool-call helpers for scripted models`

---

### Task 2.2: `specialists/guard.py` — allowlist + một đề xuất mỗi task

**Files:**
- Create: `app/agents/booking/specialists/__init__.py`, `app/agents/booking/specialists/guard.py`
- Test: `tests/test_specialist_guard.py`

**Interfaces — Produces:**

```python
PROPOSAL_TOOLS = ("propose_appointment", "cancel_appointment")
def allowlist(tools: Sequence[BaseTool], names: Sequence[str]) -> list[BaseTool]   # giữ thứ tự `names`; tên thiếu → ValueError (lỗi lập trình)
class OneProposalPerTaskMiddleware(AgentMiddleware):
    async def awrap_tool_call(self, request, handler):
        # đếm ToolMessage name ∈ PROPOSAL_TOOLS kể từ HumanMessage cuối trong request.state["messages"] (cách _rounds_this_turn);
        # ≥1 và call này ∈ PROPOSAL_TOOLS → ToolMessage(status="error", content='{"error_code":"one_proposal_per_task"}', tool_call_id, name), KHÔNG gọi handler
```

- [ ] **Step 1: Test đỏ**

```python
def test_allowlist_keeps_only_named_tools_in_order(): ...
def test_allowlist_raises_on_unknown_name(): ...
async def test_second_proposal_tool_call_in_a_task_is_rejected(): ...   # dựng request giả: state.messages = [Human, AI(tool_call), ToolMessage(name="propose_appointment", content='{"proposal_id":"x"}'), AI(tool_call)] → handler không được gọi, content chứa one_proposal_per_task
async def test_first_proposal_tool_call_passes_through(): ...
async def test_read_tools_are_never_blocked(): ...   # parse_time lần 2 vẫn chạy
```

- [ ] **Step 2–4:** FAIL → viết → PASS. **Step 5: Commit** `feat(specialists): add allowlist and one-proposal-per-task guard`

---

### Task 2.3: `specialists/base.py` — dựng agent, chạy task, đọc `TaskResult`

**Files:**
- Create: `app/agents/booking/specialists/base.py`
- Test: `tests/test_specialist_base.py`

**Interfaces — Consumes:** `ToolRoundLimitMiddleware`, `ToolFailureMiddleware`, `NoParallelToolsMiddleware` (`booking_graph.middleware.tools`), `model_retry_middleware`, `is_transient` (`booking_graph.agents`; M6 dời sang `booking/middleware.py`), `build_chat_model`, `render_context`.
**Produces:**

```python
TASK_BLOCK = "Task {task_id}: action={action}\nArgs (JSON): {args}\n"
# UNTRUSTED import từ app.agents.booking.context (M1 Task 1.2)
def build_specialist(*, prompt: str, tools: Sequence[BaseTool], tag: str, extra_middleware: Sequence[AgentMiddleware] = ()):
    # create_agent(model=build_chat_model(tags=[tag], streaming=False, max_retries=0), tools, system_prompt=prompt,
    #   middleware=[ToolRoundLimitMiddleware(4), ToolFailureMiddleware(), NoParallelToolsMiddleware(), model_retry_middleware(), *extra_middleware], checkpointer=False)
def task_message(spec: TaskSpec, ctx: TurnContext, question: str) -> HumanMessage   # render_context(ctx,"full") + TASK_BLOCK + UNTRUSTED + question
def parse_task_result(text: str, spec: TaskSpec, *, attempts: int) -> TaskResult     # SpecialistOutput.model_validate_json; lỗi → failed/schema_invalid/retryable False
async def run_specialist(agent, spec: TaskSpec, ctx: TurnContext, question: str, *, timeout: float) -> tuple[TaskResult, list[BaseMessage]]
    # asyncio.wait_for(agent.ainvoke({"messages":[task_message]}), timeout); TimeoutError → failed/tool_timeout/retryable True;
    # lỗi is_transient → thử lại ĐÚNG 1 lần (attempts=2); lỗi khác → failed/<"model_error">; trả cả messages để caller đối chiếu ToolMessage
```

`timeout` = `settings.orch_task_timeout_seconds`. `data` của `TaskResult` sao từ `SpecialistOutput.data` (Pydantic union đã phân biệt theo trường).

- [ ] **Step 1: Test đỏ** (ScriptedChatModel cắm qua `monkeypatch.setattr("app.agents.booking.specialists.base.build_chat_model", ...)`):

```python
def test_task_message_has_context_then_task_then_untrusted_question(): ...   # thứ tự 3 khối; question nằm SAU nhãn untrusted
def test_parse_valid_output_becomes_ok_result_with_typed_data(): ...        # ShopHoursOut
def test_parse_garbage_is_schema_invalid_not_retryable(): ...
async def test_run_specialist_times_out_as_tool_timeout(): ...               # Hang(100) với timeout 0.1
async def test_transient_error_is_retried_once_then_reported(): ...          # [APIConnectionError, APIConnectionError] → failed, attempts == 2
async def test_tool_round_limit_is_four_per_task(): ...                      # 5 tool_call liên tiếp → FALLBACK_ANSWER → schema_invalid, và model được gọi đúng 4 lần
```

- [ ] **Step 2–4:** FAIL → viết → PASS. **Step 5: Commit** `feat(specialists): shared build/run/parse for specialist agents`

---

### Task 2.4: Appointment Agent

**Files:**
- Create: `app/agents/booking/specialists/appointment.py`, `app/agents/booking/prompts/__init__.py`, `app/agents/booking/prompts/appointment.py`
- Test: `tests/test_specialist_appointment.py` (Mongo)

**Interfaces — Consumes:** `make_booking_tools(db, user)` (gói cũ, 5 tool; M6 dời), `ProposalService.get`, `ConversationStateService.set_queued`.
**Produces:**

```python
ALLOWLIST: dict[str, tuple[str, ...]] = {        # §8.1 nguyên văn
    "resolve_time": ("parse_time",),
    "find_slots": ("parse_time", "find_free_slots"),
    "propose_book": ("parse_time", "find_free_slots", "propose_appointment"),
    "propose_move": ("parse_time", "find_free_slots", "propose_appointment"),
    "propose_cancel": ("cancel_appointment",),
    "list_appointments": ("list_my_appointments",),
}
async def run_task(db, user: User, spec: TaskSpec, ctx: TurnContext, question: str) -> TaskResult
```

Luật trong `run_task` (code): (1) `spec.action == "propose_move"` mà `args.appointment_id is None` → `failed/schema_invalid` không gọi model; (2) dựng agent **mỗi task** với `allowlist(make_booking_tools(db,user), ALLOWLIST[action])` + `OneProposalPerTaskMiddleware()`; (3) sau khi chạy, nếu có ToolMessage của `propose_appointment`/`cancel_appointment` chứa `proposal_id`: `data` phải là `ProposalCreated` cùng `proposal_id`, và `ProposalService.get(user, id).start_at` phải bằng `data.start_at.iso` → lệch → `failed/schema_invalid`; ngược lại `data` là `ProposalCreated` mà không có ToolMessage nào → `schema_invalid`; (4) ToolMessage `propose_appointment` có `queued` → `set_queued(user_id, [QueuedRequest(queue_id=f"q{n}", start_at, spoken, note, queued_at=now)])` và `data.queued` = các `Moment`; (5) lỗi tool `slot_unavailable/outside_hours/closed_day/time_passed/already_yours/appointment_not_found` trong `data` hoặc `error_code` → `TaskResult.error_code` + `asked` + `nearest` sao từ JSON tool (model chỉ **chép**; code đọc lại ToolMessage cuối của tool đề xuất để điền `asked/nearest`, không tin chữ model).

`prompts/appointment.py:APPOINTMENT_PROMPT` (tiếng Anh): vai "execute exactly one task"; chỉ dùng tool có sẵn; `TimeHint.anchor` do Orchestrator điền **thắng** neo ngầm của `parse_time` (chép `anchor` sang tham số tool); kết thúc bằng **một** JSON `SpecialistOutput` (schema liệt kê) không chữ thừa; không nói với khách; mọi mốc giờ chép từ `iso`/`spoken` của tool.

- [ ] **Step 1: Test đỏ** (user qua `AuthService.create_user`; giờ mở cửa mặc định 08:00–19:00):

```python
async def test_resolve_time_runs_parse_time_and_returns_resolved(test_db, user, monkeypatch): ...
async def test_propose_book_creates_exactly_one_proposal_and_reports_its_id(test_db, user, monkeypatch): ...
async def test_second_propose_in_the_same_task_is_blocked_and_db_has_one(test_db, user, monkeypatch): ...
async def test_mismatched_proposal_id_is_schema_invalid(test_db, user, monkeypatch): ...   # model trả data.proposal_id khác ToolMessage
async def test_propose_move_without_appointment_id_fails_before_the_model(test_db, user, monkeypatch): ...  # model giả không được gọi (calls == [])
async def test_tool_outside_allowlist_does_not_exist_in_the_task(test_db, user, monkeypatch): ...   # resolve_time gọi find_free_slots → ToolMessage "Không có tool" → failed
async def test_slot_unavailable_fills_asked_and_nearest_from_the_tool_json(test_db, user, monkeypatch): ...
async def test_queued_requests_are_persisted_via_set_queued(test_db, user, monkeypatch): ...
def test_appointment_prompt_is_english_and_mentions_anchor_precedence(): ...   # không ký tự có dấu tiếng Việt; chứa "anchor"
```

- [ ] **Step 2–4:** FAIL → viết → PASS. **Step 5: Commit** `feat(specialists): add the Appointment Agent`

---

### Task 2.5: Shop Info Agent

**Files:**
- Create: `app/agents/booking/specialists/shop_info.py`, `app/agents/booking/prompts/shop_info.py`
- Test: `tests/test_specialist_shop_info.py` (Mongo)

**Interfaces — Consumes:** `make_shop_tools(db, user)` (3 tool sau M0 Task 0.4), `ShopService.get_hours/get_status/is_open_at`.
**Produces:**

```python
ALLOWLIST = {"get_hours": ("get_shop_hours",), "get_status": ("get_shop_status",),
             "is_open_at": ("get_shop_hours", "get_shop_status", "is_open_at")}
async def run_task(db, user, spec, ctx, question) -> TaskResult
def check_facts(data: TaskData, *, hours: ShopHours, status: ShopStatusView, open_at: bool | None) -> bool
    # ShopHoursOut: open.hhmm (lấy từ iso HH:MM) == hours.open_time, close tương tự, closed_days cùng tập;
    # ShopStatusOut: busy == status.is_busy và (until is None) == (status.busy_until is None);
    # ShopOpenOut: open == open_at (ShopService.is_open_at(at)) ; reason=="owner_busy" chỉ khi status.is_busy
```

Sau khi agent trả `ok`: code đọc lại `get_hours/get_status` (+ `is_open_at(at)` khi `ShopOpenOut`) và gọi `check_facts`; sai → `TaskResult(failed, error_code="fact_mismatch", retryable=False)`. `prompts/shop_info.py:SHOP_INFO_PROMPT`: chỉ ghép dữ kiện tool, không bịa, kết thúc bằng JSON `SpecialistOutput`.

- [ ] **Step 1: Test đỏ**

```python
async def test_get_hours_returns_hours_out_matching_the_shop(test_db, user, monkeypatch): ...
async def test_is_open_at_combines_hours_and_status(test_db, user, monkeypatch): ...
async def test_fabricated_closing_time_is_fact_mismatch_and_not_retried(test_db, user, monkeypatch): ...  # model chép "18:00" trong khi tiệm 19:00 → failed/fact_mismatch, model gọi đúng số lần của 1 lượt
async def test_status_tool_not_in_get_hours_allowlist(test_db, user, monkeypatch): ...
def test_check_facts_pure_cases(): ...   # parametrize 6 ca
```

- [ ] **Step 2–4:** FAIL → viết → PASS. **Step 5: Commit** `feat(specialists): add the Shop Info Agent with fact check`
