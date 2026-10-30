# M3 — Orchestrator: 5 tool, `decide_pending`, `OrchestratorGuard`, agent

Đọc `README.md` trước. Cơ chế thư viện đã kiểm trên nguồn (§1.2, §12.16): từ chối = trả `ToolMessage(status="error")` không gọi `handler`; ghi state = trả `Command(update=…)` từ `awrap_tool_call`; ép dừng = `abefore_model` + `@hook_config(can_jump_to=["end"])` trả `{"jump_to": "end"}`; kết thúc = `return_direct=True` (cạnh tools→model thoát END chỉ khi **mọi** tool vừa chạy là `return_direct`, `factory.py:2086`); các call **cùng vòng** chia một snapshot state (`tool_node.py:841-858`) nên luật "anh em" đọc `tool_calls` của AIMessage cuối; `model_settings` đi thẳng vào `bind_tools(**model_settings)` (`factory.py:1448`) nên `strict=True` đặt qua middleware.

**Giao:** `orchestrator/state.py`, `tools.py`, `decide.py`, `guard.py`, `agent.py`, `prompts/orchestrator.py`; `FakeSpecialists`.

---

### Task 3.1: `state.py` + `tools.py` — 5 tool và `OrchestratorDeps`

**Files:**
- Create: `app/agents/booking/orchestrator/__init__.py`, `state.py`, `tools.py`
- Test: `tests/test_orchestrator_tools.py`

**Interfaces — Produces:**

```python
class OrchestratorState(AgentState):            # §5.2
    turn_context: NotRequired[dict]
    calls: NotRequired[list[dict]]              # CallRecord.model_dump()
    proposals_created: NotRequired[int]
    pending_decided: NotRequired[bool]
    pending_outcome: NotRequired[dict | None]
    progress_markers: NotRequired[list[str]]
    no_progress_streak: NotRequired[int]
    rounds_executed: NotRequired[int]           # vòng có ≥1 call được chạy (§7.1)
    must_decide_rejections: NotRequired[int]    # (h) 2 lần liên tiếp → kept
    force_end: NotRequired[bool]
    ended_by: NotRequired[EndedBy | None]
    finish_args: NotRequired[dict | None]
    ask_args: NotRequired[dict | None]

@dataclass
class OrchestratorDeps:
    db; user: User; ctx: TurnContext; ledger: TurnLedger; turn_id: str
    run_appointment: Callable[[TaskSpec, TurnContext, str], Awaitable[TaskResult]]   # mặc định specialists.appointment.run_task đóng db/user
    run_shop: Callable[[TaskSpec, TurnContext, str], Awaitable[TaskResult]]
    question: str
    max_rounds: int                                # settings.orch_max_tool_rounds, hoặc 2 khi quá trần chi phí (M5)
    task_counter: itertools.count

SEQUENTIAL = frozenset({"appointment_task:propose", "decide_pending", "ask_customer", "finish"})   # nhóm (g); propose_* nhận ra từ action

def make_orchestrator_tools(deps: OrchestratorDeps) -> list[BaseTool]
    # appointment_task(action: AppointmentAction, args: <appointment_args_model(upcoming ids, queued ids)>) -> str  (TaskResult JSON)
    # shop_info_task(action: ShopAction, args: ShopArgs) -> str
    # decide_pending(label: DecideLabel, aside: Aside | None, reason: str) -> str   (PendingOutcome JSON; thân gọi decide.apply_decision — Task 3.2)
    # ask_customer(field: QuestionField, hint: str | None) -> str    return_direct=True, trả '{"ok":true}'
    # finish(kind_hint, service: str | None, stated: list[str]) -> str  return_direct=True, trả '{"ok":true}'
```

Thân `appointment_task`: `spec = TaskSpec(task_id=f"t{next(deps.task_counter)}", agent="appointment", action, args)` → `await deps.run_appointment(spec, deps.ctx, deps.question)` → nếu `data` là `ProposalCreated` → `deps.ledger.append_call` **ngay** (ghi trong vòng, §5.3) → trả JSON. Docstring 5 tool (tiếng Anh) là phần prompt quan trọng nhất: nêu khi nào gọi, `anchor` thắng neo ngầm, "finish/ask_customer alone, never alongside another call", "decide_pending first when a proposal is pending".

- [ ] **Step 1: Test đỏ**

```python
def test_five_tools_with_expected_names_and_return_direct_flags(): ...   # {appointment_task, shop_info_task, decide_pending, ask_customer, finish}; return_direct đúng 2 tool
def test_orchestrator_tool_schemas_are_strict_safe(): ...                # convert_to_openai_tool(t, strict=True) không có default/minimum/maximum; additionalProperties False
def test_appointment_id_enum_follows_turn_context(): ...                 # ctx.upcoming 2 id → enum đúng 2 id
async def test_appointment_task_builds_spec_and_calls_the_specialist(): ...   # run_appointment giả nhận TaskSpec(task_id "t0", action, args typed)
async def test_proposal_created_is_written_to_the_ledger_before_returning(): ...  # ledger giả ghi trước khi tool trả
def test_tool_docstrings_are_english(): ...
```

- [ ] **Step 2–4:** FAIL → viết → PASS. **Step 5: Commit** `feat(orchestrator): add state and the five control tools`

---

### Task 3.2: `decide.py` — veto có cấu trúc và cổng ghi duy nhất

**Files:**
- Create: `app/agents/booking/orchestrator/decide.py`
- Test: `tests/test_orchestrator_decide.py` (Mongo)

**Interfaces — Consumes:** `reply.other_time(text, proposal: Mapping, now, ...)`, `reply.restates_proposal_time` (gói cũ `booking_graph.reply`; M6 dời sang `booking/timecheck.py`), `ProposalService.approve/reject/supersede/mark_unclear`, `UNCLEAR_LIMIT`, `AppointmentService.find_free_slots` + `nearest_slots` (gói cũ `tools.py`).
**Produces:**

```python
class Veto(BaseModel): label: DecideLabel; reason: str | None; reject_code: str | None   # reject_code "same_time_as_pending" → guard từ chối call
def veto(label: DecideLabel, aside: Aside | None, question: str, pending: PendingProposal, now: datetime) -> Veto
    # approve & other_time(question, pending) → change ("other_time")
    # change  & not other_time → reject_code "same_time_as_pending"
    # approve & aside == relative_free → unclear ("relative_free")
    # approve & aside ∈ defer_* → none ("defer")
    # còn lại giữ nguyên
async def apply_decision(deps: OrchestratorDeps, label: DecideLabel) -> PendingOutcome
    # approve → ProposalService.approve(user, pending.id, turn_at=ctx.asked_at) → approved | failed(+nearest ≤3 quanh pending.start_at) | not_approvable(reason)
    # reject → reject → rejected ; change → supersede → superseded ; none → kept
    # unclear → mark_unclear → unclear_count < UNCLEAR_LIMIT ? clarify : (reject → rejected)
    # LUÔN: await deps.ledger.set_pending_outcome(turn_id, outcome) TRƯỚC khi return
```

`decide_pending` (Task 3.1) thân: `v = veto(...)`; `reject_code` → ném `DecideRejected(code)` để guard biến thành `ToolMessage(error)`; khác → `apply_decision(deps, v.label)`. Đây là **nơi duy nhất** trong `app/agents/` gọi `.approve(` — M6 ghi vào `test_write_fence.ALLOWED`.

- [ ] **Step 1: Test đỏ** (`pending` 15:00 mai):

```python
def test_veto_changes_approve_when_another_time_is_named(): ...          # "ừ mà 4 giờ nha" → change
def test_veto_rejects_change_that_restates_the_same_time(): ...          # "đổi qua 3 giờ chiều mai" (= pending) → reject_code same_time_as_pending
def test_veto_downgrades_approve_with_relative_free_to_unclear(): ...
def test_veto_downgrades_approve_with_defer_to_none(): ...
def test_veto_never_upgrades(): ...                                      # reject + aside None → reject; none + "4 giờ" → none
async def test_approve_writes_the_appointment_and_the_ledger(test_db, user): ...
async def test_approve_on_a_taken_slot_is_failed_with_nearest(test_db, user): ...   # khách khác đã đặt đúng slot → failed, len(nearest) ≤ 3
async def test_approve_after_expiry_is_not_approvable(test_db, user): ...           # Review Focus #1: expires_at quá → outcome not_approvable, reason "expired", appointments rỗng
async def test_unclear_first_time_is_clarify_second_time_rejects(test_db, user): ...
async def test_none_keeps_the_proposal_untouched(test_db, user): ...
async def test_ledger_has_the_outcome_before_apply_returns(test_db, user): ...      # ledger giả ghi mốc; so thứ tự
```

- [ ] **Step 2–4:** FAIL → viết → PASS. **Step 5: Commit** `feat(orchestrator): structured veto and the single approve gate`

---

### Task 3.3: `guard.py` — `OrchestratorGuard` (a)–(k), marker, trùng, ép dừng, trần vòng

**Files:**
- Create: `app/agents/booking/orchestrator/guard.py`
- Test: `tests/test_orchestrator_guard.py`

**Interfaces — Produces:**

```python
REJECT_HINTS: dict[str, str]     # error_code → hint tiếng Anh ngắn cho model (unknown_tool, bad_args, unknown_appointment_id, unknown_queue_id, missing_anchor_target,
                                 #   one_proposal_per_turn, duplicate_call, serialize_required, must_decide_pending, same_time_as_pending, already_approved, already_decided, too_many_calls)
def normalize_args(tool: str, args: dict) -> str                  # json.dumps(sort_keys) sau khi bỏ `reason`, `hint`; dùng cho trùng
def is_duplicate(calls: list[dict], tool: str, action: str | None, args_norm: str) -> bool   # có call cùng khoá và không marker mới kể từ call đó
def markers_for(tool: str, action: str | None, result: TaskResult | PendingOutcome | None) -> list[str]   # §5.6
def siblings(messages) -> list[dict]                               # tool_calls của AIMessage cuối
def is_propose(tool: str, args: dict) -> bool                      # appointment_task với action ∈ propose_*
def reject(call: dict, code: str) -> ToolMessage                   # status="error", content=json{"error_code", "hint"}

class OrchestratorGuard(AgentMiddleware):
    state_schema = OrchestratorState
    def __init__(self, deps: OrchestratorDeps, *, max_calls_per_round: int, no_progress_limit: int): ...
    async def awrap_tool_call(self, request, handler) -> Command
        # thứ tự kiểm: (a) tool ∈ 5 → (b) parse args vào model + khoảng: hour 0–23, minute 0–59, weekday 0–6, day_offset -1..90, stated ≤2
        #  → too_many_calls (index ≥ max_calls_per_round) → (c) appointment_id/queue_id ∈ ctx → (d) anchor có đối tượng
        #  → (g) nhóm tuần tự khi len(siblings)>1 (chỉ call index nhỏ nhất của nhóm chạy NẾU nó là call duy nhất; còn lại serialize_required)
        #  → (h) ctx.pending và not pending_decided: propose_*/ask_customer/finish → must_decide_pending (2 lần liên tiếp → ghi kept, pending_decided=True)
        #  → (k) decide_pending lần 2 → already_decided → (j) đã approved → propose_* already_approved
        #  → (e) propose_* với proposals_created ≥ 1 hoặc anh em propose_* index nhỏ hơn → one_proposal_per_turn → (f) trùng → duplicate_call
        # từ chối → Command(update={"messages":[reject], "calls":[CallRecord(allowed=False)], "no_progress_streak": +1 nếu duplicate/must_decide, "force_end": streak ≥ limit})
        # cho phép → result = await handler(request); DecideRejected → reject(code); markers → Command(update={messages, calls, progress_markers, proposals_created, pending_decided, pending_outcome, ended_by (finish/ask_customer), finish_args/ask_args, force_end (unclear lần 1), no_progress_streak reset nếu có marker})
        # mỗi CallRecord → deps.ledger.append_call (await) ; log orch_call
    @hook_config(can_jump_to=["end"])
    async def abefore_model(self, state, runtime)
        # force_end → {"jump_to":"end","ended_by":"forced"} ; rounds_executed ≥ deps.max_rounds → {"jump_to":"end","ended_by":"limit"}
        # còn lại: nếu vòng vừa rồi có ≥1 call allowed → rounds_executed += 1 (đếm bằng ToolMessage status != error sau AIMessage cuối)
```

Hai luật cần nhìn "anh em" đọc `siblings(request.state["messages"])` và `request.tool_call["id"]` để biết index — **không** đọc bộ đếm state (snapshot chung).

- [ ] **Step 1: Test đỏ** — dựng `request` giả (`SimpleNamespace(tool_call, tool, state, runtime)`) và `handler` giả ghi lại lời gọi; mỗi luật một test, tên theo mã:

```python
async def test_a_unknown_tool_is_rejected()
async def test_b_hour_out_of_range_is_rejected_without_running()
async def test_c_unknown_appointment_id_is_rejected()                    # Review Focus #4
async def test_d_anchor_pending_without_pending_is_rejected()
async def test_e_second_propose_in_the_turn_is_rejected()
async def test_e_sibling_propose_with_lower_index_blocks_this_one()
async def test_f_duplicate_call_without_new_marker_is_rejected_and_bumps_streak()
async def test_g_finish_next_to_another_call_is_serialize_required()
async def test_g_lone_lowest_index_sequential_call_runs()
async def test_h_propose_without_decide_is_rejected_and_pending_untouched(test_db, user)   # Review Focus #3: ProposalService.latest vẫn pending cùng id
async def test_h_two_rejections_in_a_row_record_kept()
async def test_i_veto_result_flows_through_decide_pending()              # same_time_as_pending → reject
async def test_j_propose_after_approved_is_rejected()
async def test_k_second_decide_pending_is_already_decided()
async def test_too_many_calls_fourth_call_is_rejected()
async def test_unclear_first_time_sets_force_end()
async def test_streak_of_two_sets_force_end()
async def test_before_model_jumps_to_end_on_force_end_with_ended_by_forced()
async def test_rounds_with_only_rejections_do_not_count_toward_the_limit()   # 3 vòng từ chối + 8 vòng chạy → limit ở vòng chạy thứ 8
def test_markers_for_each_result_kind()                                  # parametrize §5.6
def test_every_reject_code_has_an_english_hint()
async def test_every_call_logs_orch_call_with_round_index_tool_allowed_and_reject_code(caplog)   # §11; args_norm không chứa chữ khách
```

- [ ] **Step 2–4:** FAIL → viết → PASS. **Step 5: Commit** `feat(orchestrator): guard every tool call in code`

---

### Task 3.4: `agent.py` — dựng mỗi lượt, chạy, `OrchestrateOutcome`; `FakeSpecialists`

**Files:**
- Create: `app/agents/booking/orchestrator/agent.py`, `app/agents/booking/prompts/orchestrator.py`
- Modify: `tests/fakes.py` (thêm `FakeSpecialists`)
- Test: `tests/test_orchestrator_agent.py`

**Interfaces — Produces:**

```python
class StrictToolsMiddleware(AgentMiddleware):
    async def awrap_model_call(self, request, handler):   # request.override(model_settings={**request.model_settings, "strict": True}) khi request.tools
class OrchestrateOutcome(BaseModel):
    calls: list[CallRecord]; pending_outcome: PendingOutcome | None; ended_by: EndedBy
    finish: FinishArgs | None; ask: AskCustomerArgs | None; proposals_created: int; rounds_executed: int
RECENT_TURNS = 2
def build_orchestrator(deps: OrchestratorDeps):
    # create_agent(model=build_chat_model(tags=["orchestrate"], streaming=False, max_retries=0), tools=make_orchestrator_tools(deps),
    #   system_prompt=ORCHESTRATOR_PROMPT, state_schema=OrchestratorState,
    #   middleware=[OrchestratorGuard(deps, ...), StrictToolsMiddleware(), ToolRoundLimitMiddleware(deps.max_rounds * 2), model_retry_middleware()], checkpointer=False)
def orchestrator_message(ctx: TurnContext, recent: list[BaseMessage], question: str) -> HumanMessage
    # render_context(ctx,"full") + "Last turns:" (≤ RECENT_TURNS cặp, từ history.model_history) + UNTRUSTED + question
async def run_orchestrate(deps: OrchestratorDeps, recent: list[BaseMessage]) -> OrchestrateOutcome
    # asyncio.timeout(settings.orch_orchestrate_timeout_seconds) quanh agent.ainvoke; TimeoutError → ended_by="limit" (calls lấy từ ledger/state đã ghi)
    # lỗi model ở vòng đầu (không ToolMessage nào) → ended_by="error" ; ended_by None sau ainvoke → "limit" (backstop ToolRoundLimit)
# tests/fakes.py
class FakeSpecialists:   # plan: dict[(agent, action)] → TaskResult | Exception | list (lần lượt); ghi lại specs nhận được
    async def appointment(self, spec, ctx, question) -> TaskResult
    async def shop(self, spec, ctx, question) -> TaskResult
```

`ORCHESTRATOR_PROMPT` (tiếng Anh): vai điều phối, không nói với khách; "If a proposal is pending, call decide_pending first and alone"; "finish or ask_customer ends the turn and must be the only call"; "never compute dates: pass TimeHint"; "one proposal per turn; extra requests go in `also`"; mô tả `PendingOutcome` ảnh hưởng gì (failed → find_slots; not_approvable → finish).

- [ ] **Step 1: Test đỏ** (scripted model qua `monkeypatch.setattr("app.agents.booking.orchestrator.agent.build_chat_model", ...)`, `FakeSpecialists`, ledger thật trên `test_db`):

```python
async def test_finish_alone_ends_the_loop_with_ended_by_finish()
async def test_ask_customer_ends_and_records_ask_args()
async def test_finish_next_to_shop_task_is_rejected_then_finish_alone_ends()      # §13: vòng 1 tool_calls(finish, shop_info_task) → vòng 2 finish → END; ended_by finish; calls có 1 reject
async def test_two_proposes_in_one_message_leave_one_proposal()                  # fake specialist đếm: appointment gọi 1 lần
async def test_unclear_forces_end_and_no_call_runs_afterwards()
async def test_limit_ends_after_eight_executed_rounds_not_counting_rejected_ones()
async def test_model_error_on_first_round_is_ended_by_error()
async def test_strict_is_passed_to_bind_tools()                                  # scripted.bind_kwargs chứa {"strict": True}
async def test_orchestrator_message_has_at_most_two_recent_pairs_and_untrusted_question()
async def test_orchestrate_timeout_is_ended_by_limit()                           # Hang + monkeypatch timeout 0.1
def test_orchestrator_prompt_is_english_and_names_the_two_ending_tools()
```

- [ ] **Step 2–4:** FAIL → viết → PASS. **Step 5: Commit** `feat(orchestrator): build the orchestrator agent per turn`

---

### Task 3.5: Kiểm `strict` với 5 tool thật (Azure, có phép)

**Files:**
- Modify: `tests/test_strict_schema_llm.py` (thêm test dùng `make_orchestrator_tools(deps giả với ctx có 1 upcoming)`)

- [ ] **Step 1:** Thêm `test_real_orchestrator_tools_are_accepted_strict()` cùng mẫu Task 0.6 (yêu cầu model gọi `finish`).
- [ ] **Step 2: DỪNG — hỏi chủ dự án** cho một lời gọi Azure. Có đồng ý → chạy → ghi kết quả vào checkbox. 400 → theo Task 0.6 Step 4.
- [ ] **Step 3: Commit** `test(llm): strict probe with the real orchestrator tools`
