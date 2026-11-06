# M5 — Đồ thị, `run_turn`, tiến độ xuyên lượt, kịch bản §13

Đọc `README.md` trước. Sau M5, `app.agents.booking.run_turn` chạy trọn một lượt với model giả; Socket.IO **vẫn** trỏ gói cũ tới M6.

**Giao:** `progress.py`, `llm_budget.py`, `graph.py`, `events.py`, `tests/test_scenarios.py`.

---

### Task 5.1: `progress.py` — `GoalProgress` và `TurnFacts`

**Files:**
- Create: `app/agents/booking/progress.py`
- Test: `tests/test_progress.py`

**Interfaces — Consumes:** `GoalProgress` (`app.models.conversation`), `clean_free_text_item` (`app.services.conversation_state`).
**Produces:**

```python
def step_goal(goal: GoalProgress, outcome: OrchestrateOutcome | None, resolve: ResolveResult, ctx: TurnContext) -> tuple[GoalProgress, bool]
    # §7.2: mở goal_id (uuid4 hex) ở call appointment_task đầu tiên khi chưa có; đóng (goal_id None, count 0) khi approved / rejected / decide_pending aside defer_only / đổi mục tiêu
    #   (service hoặc appointment_id của call appointment_task khác hẳn goal.service/appointment_id → mở goal mới);
    # +1 chỉ khi: có call appointment_task allowed VÀ không marker mới VÀ không phải ask_customer hợp lệ lần đầu cho field mới (field ≠ ctx.open_question.field)
    # không đếm/không reset: không call appointment_task, hoặc pending_outcome kept với aside defer_*
    # trả (goal, limit_hit) với limit_hit = count ≥ settings.orch_goal_turns_limit → count reset 0 sau khi trả
def turn_facts(outcome: OrchestrateOutcome | None, resolve: ResolveResult) -> TurnFacts
    # service = clean_free_text_item(finish.service); stated = [clean_free_text_item(s) for s in finish.stated][:2] bỏ None
    # answers_question = resolve.rule == "R3" and (outcome.ask is None or outcome.ask.field != ctx.open_question.field)
```

- [ ] **Step 1: Test đỏ** (parametrize theo §7.2): `test_first_appointment_call_opens_a_goal`, `test_social_turn_neither_counts_nor_resets`, `test_shop_only_turn_is_neutral`, `test_defer_kept_is_neutral`, `test_no_marker_turn_counts`, `test_first_ask_for_a_new_field_does_not_count`, `test_repeat_ask_same_field_counts`, `test_marker_resets_count`, `test_approved_closes_goal`, `test_service_change_opens_a_new_goal`, `test_third_strike_sets_limit_and_resets`, `test_turn_facts_caps_stated_at_two_and_cleans`, `test_answers_question_only_on_r3_without_same_field_ask`.
- [ ] **Step 2–4:** FAIL → viết → PASS. **Step 5: Commit** `feat(agents): cross-turn goal progress and turn facts`

---

### Task 5.2: `llm_budget.py` — trần chi phí LLM theo khách

**Files:**
- Create: `app/agents/booking/llm_budget.py`
- Test: `tests/test_llm_budget.py` (Mongo)

**Interfaces — Consumes:** `RateLimitService.hit/_count` (`check` ném lỗi — không dùng; đọc số qua `retry_after_seconds`? Không: thêm **không** sửa service; dùng `hit` + `check` trong try/except để biết quá trần).
**Produces:**

```python
LLM_KEY = "llm:user:{user_id}"
class LlmBudget:
    def __init__(self, db, user_id: str): ...
    async def hit(self) -> None                       # RateLimitService.hit(LLM_KEY, 3600)
    async def over(self) -> bool                      # RateLimitService.check(LLM_KEY, settings.orch_llm_calls_per_user_per_hour, 3600) ném RateLimitError → True
class BudgetMiddleware(AgentMiddleware):              # awrap_model_call: await budget.hit() rồi handler — gắn vào Orchestrator, hai specialist
async def count_model_call(budget: LlmBudget) -> None  # Conversation gọi thẳng trước ainvoke (classify.py bỏ 06/11, Task 4.4b)
```

Quá trần → `OrchestratorDeps.max_rounds = 2` (graph node `orchestrate` quyết, Task 5.3). Lượt vẫn chạy.

- [ ] **Step 1: Test đỏ**: `test_hit_counts_per_user`, `test_over_after_the_limit`, `test_budget_middleware_hits_before_each_model_call`, `test_two_users_have_separate_budgets`.
- [ ] **Step 2–4:** FAIL → viết → PASS. **Step 5: Commit** `feat(agents): per-customer LLM call budget`

---

### Task 5.3: `graph.py` — vỏ LangGraph

**Files:**
- Create: `app/agents/booking/graph.py`
- Test: `tests/test_graph_turn.py` (Mongo, model giả)

**Interfaces — Consumes:** mọi thứ M1–M4; `stamp`, `model_history` (gói cũ `history`), `get_checkpointer`.
**Produces:**

```python
class GraphState(TypedDict, total=False):
    messages: Annotated[List[AnyMessage], add_messages]   # thread: chỉ Human/AI của câu khách và câu đáp
    turn_id: str; asked_at: datetime; history_after: Optional[datetime]
    customer_text: str; button: Optional[dict]            # ButtonReply dump
    turn_context: dict; resolve: dict                     # TurnContext / ResolveResult dump
    outcome: Optional[dict]                               # OrchestrateOutcome | ButtonOutcome dump (+ "type"); classify_route/ClassifyOutcome bỏ 06/11 (Task 4.4b)
    brief: Optional[dict]; draft: str; answer: str; answer_source: str; violations: List[str]
    previous_replies: List[str]; reply_kind: Optional[str]; ended_by: Optional[str]
    goal_limit_hit: bool
def build_graph(db, user: User, checkpointer=None)
# nodes: resolve → (button_path | orchestrate) → brief → respond → record → END ; orchestrate finish(kind_hint=social/unsupported) → brief (classify_only node bỏ 06/11, Task 4.4b)
# resolve: load_turn_context + resolver.resolve + TurnLedger.start ; budget.over() → ghi vào state "max_rounds"
# orchestrate: deps = OrchestratorDeps(..., run_appointment=partial(appointment.run_task, db, user), run_shop=..., max_rounds=2 nếu quá trần) ; run_orchestrate(deps, recent=model_history(messages, after=history_after, now=asked_at)[-4:])
# brief: build_reply_brief ; limit_hit (step_goal) → kind "limit_reached" + Fact shop_phone
# respond: stale_button HOẶC outcome.ended_by == "error" (Orchestrator lỗi vòng đầu, §10: câu cứng, pending coi là kept) → (brief.fallback, "code") không gọi model ; khác → conversation.respond → guard.apply_guard
# log §11: resolve_done{rule, has_pending, has_open_question, queued_n, has_unannounced}, turn_ended{ended_by, rounds_counted, rounds_rejected_only, calls_n, rejects_n, markers, no_progress_streak}, unannounced_reported, guard_violation{codes, fixed_by}, goal_progress — extra luôn có user_id, turn_id, thread_id
# record: messages += [stamp(AIMessage(answer), now_ms())] ; ledger.finish(ended_by, reply_kind) ; ledger.set_ask khi outcome.ask ; record_goal_progress ; conversations.append KHÔNG ở đây (events.py, như cũ)
```

Cạnh có điều kiện đọc `state["resolve"]["route"]`. Agent con `checkpointer=False`; graph ngoài `checkpointer or get_checkpointer()` (không tham số mặc định — như gói cũ). (`state["classify_route"]` bỏ 06/11 — Task 4.4b.)

- [ ] **Step 1: Test đỏ** (fixture `models(monkeypatch)` cắm scripted vào 4 module: `orchestrator.agent`, `conversation`, `specialists.base`, `booking_graph.rewrite`; `FakeSpecialists` qua monkeypatch `graph.appointment.run_task`/`graph.shop_info.run_task`; `classify` bỏ 06/11, Task 4.4b):

```python
async def test_r4_social_goes_orchestrate_then_finish_social_then_respond(test_db, user, models)
    # "chào em" (lượt sạch) → R5/orchestrate (R4 gộp vào R5, Task 4.4b) → model gọi finish(kind_hint="social") → brief kind "social" → respond, không specialist nào chạy
async def test_r5_runs_orchestrator_then_brief_then_respond_once(test_db, user, models)      # conversation model gọi đúng 1 lần
async def test_r1_button_yes_writes_and_speaks_the_confirmation(test_db, user, models)
async def test_stale_button_speaks_the_template_without_a_model_call(test_db, user, models)
async def test_record_appends_only_question_and_answer_to_the_thread(test_db, user, models)
async def test_ledger_is_finished_with_ended_by_and_reply_kind(test_db, user, models)
async def test_over_budget_caps_rounds_at_two(test_db, user, models)
async def test_goal_limit_turns_kind_into_limit_reached_with_the_phone(test_db, user, models)
async def test_graph_has_exactly_the_planned_nodes()      # {"resolve","button_path","orchestrate","brief","respond","record"} ("classify_only" bỏ 06/11, Task 4.4b)
async def test_orchestrator_error_on_first_round_speaks_the_hard_fallback_without_the_conversation_model(test_db, user, models)
async def test_structured_logs_carry_user_turn_and_thread_ids(test_db, user, models, caplog)   # resolve_done và turn_ended có đủ 3 khoá extra
```

- [ ] **Step 2–4:** FAIL → viết → PASS. **Step 5: Commit** `feat(agents): wire the hybrid turn graph`

---

### Task 5.4: `events.py` — `run_turn` không interrupt

**Files:**
- Create: `app/agents/booking/events.py`; `app/agents/booking/__init__.py` re-export `run_turn`, `AgentEvent`, `TURN_ERROR_MESSAGE`, `ButtonReply`, `BUTTON_LABELS`
- Test: `tests/test_run_turn.py` (Mongo)

**Interfaces — Consumes:** `translate_langchain_event`, `thread_id_for`, `pick_thread`, `_supersede`, `_ACTIVE_TURNS`, `schedule_compaction` **sao chép** từ `booking_graph.events` (M6 xoá bản cũ), `MessageDedupe` không đổi (ở socketio).
**Produces:**

```python
TURN_ERROR_MESSAGE = "Máy đang bận chút xíu, anh chị nhắn lại giúp em nhé."     # giữ nguyên chữ
def turn_timeout_seconds() -> int                                  # settings.orch_turn_timeout_seconds (đọc lúc gọi để test monkeypatch được)
async def pending_proposal_payload(db, user: User) -> Optional[dict]   # ProposalService.latest(user) pending & chưa hết hạn → {"id","kind","expires_at","when_spoken"} ; lỗi → None + log
async def run_turn(db, user, question: str | ButtonReply) -> AsyncIterator[AgentEvent]
# _run_turn: turn_started → question_text = single_line(question_text, 1000) (app.core.text; §9.1) → asked_at=now_ms(); turn_id=uuid4 hex; graph=build_graph; thread_id=pick_thread; fields={"messages":[stamp(Human(question_text), asked_at)], "turn_id","asked_at","customer_text","button", "history_after", "previous_replies"} ; LUÔN ainvoke/astream_events từ START (không aupdate_state, không Command(resume))
# sau stream: answer → persist cặp vào conversations (shield) → complete {"answer", "proposal": pending_proposal_payload} → ledger.mark_announced(turn_id) → apply_turn_facts (ConversationStateService.apply_turn_analysis(user_id, TurnFacts, last_bot_text)) → schedule_compaction
# timeout trần lượt → error TURN_ERROR_MESSAGE (như cũ) ; orchestrate vượt orch_orchestrate_timeout_seconds KHÔNG tới đây (Task 3.4 trả limit → complete)
# persist cặp conversations thất bại SAU khi record đã ghi thread → log + ledger.finish(turn_id, ended_by="persist_failed", reply_kind) nếu còn ghi được; vẫn phát complete (lịch là sự thật, §10)
```

`apply_turn_analysis` nhận `TurnFacts` qua duck typing (3 trường trùng tên) cho tới M6 đổi chữ ký.

- [ ] **Step 1: Test đỏ** (mỗi test trong danh sách cũ `tests/test_run_turn_thread.py` được viết lại nếu còn nghĩa):

```python
async def test_second_turn_sees_the_first_turn_from_the_thread
async def test_thread_holds_only_question_and_answer_not_tool_messages
async def test_conversations_and_thread_share_timestamps
async def test_compacted_messages_are_not_resent_to_the_conversation_model
async def test_complete_proposal_is_set_whenever_a_pending_proposal_exists      # kể cả đề xuất tạo ở lượt trước và lượt này chỉ "ừ để hỏi con" (kept)
async def test_complete_proposal_is_none_after_approve_or_reject
async def test_button_turn_when_nothing_is_pending_replies_stale_without_graph_writes
async def test_a_new_turn_supersedes_a_hung_turn ; test_turns_of_two_users_do_not_cancel_each_other ; test_three_quick_turns_only_the_newest_runs
async def test_cancel_after_record_still_stores_the_whole_pair
async def test_langfuse_session_is_the_user_not_the_thread
async def test_turn_timeout_emits_the_same_error
async def test_announced_is_set_after_complete
async def test_apply_turn_facts_runs_after_complete
async def test_no_code_path_calls_aupdate_state_or_command_resume()    # AST trên events.py + graph.py
async def test_customer_text_is_capped_at_1000_chars_and_single_line
async def test_persist_failure_marks_the_ledger_persist_failed_and_still_completes
```

- [ ] **Step 2–4:** FAIL → viết → PASS. **Step 5: Commit** `feat(agents): run_turn on the new graph without interrupt`

---

### Task 5.5: Kịch bản §13 (model giả, Mongo)

**Files:**
- Create: `tests/test_scenarios.py` (helper `scenario(orchestrator_replies, specialists_plan, conversation_reply, *, seed)` chạy `run_turn` và trả `(events, db_state)`)

- [ ] **Step 1: Viết test đỏ** — 15 dòng bảng "thêm" của §13, tên theo dòng:

```python
test_skip_decide_pending_is_rejected_and_old_proposal_stays_pending
test_decide_pending_twice_is_already_decided
test_specialist_second_propose_in_task_blocked_db_has_one
test_two_proposes_in_one_round_leave_one_proposal
test_finish_in_parallel_is_rejected_then_finish_alone_ends
test_unclear_forces_end_no_call_after
test_approve_failed_slot_taken_reply_names_the_time_and_three_nearest
test_approve_not_approvable_reply_says_expired
test_defer_with_agreement_keeps_the_proposal             # "ừ để chú hỏi con đã" + approve/defer_only → kept, complete.proposal còn
test_unsupported_service_goes_orchestrator_finish_unsupported_no_tool_runs   # Orchestrator finish(kind_hint="unsupported"), "classify_only" bỏ 06/11 (Task 4.4b)
test_rejected_only_rounds_do_not_count_toward_the_limit
test_fourth_call_in_a_round_is_too_many_calls
test_schema_strict_safe_for_the_five_tools                # đã có ở M3 — tham chiếu, không lặp
test_restated_same_time_as_change_is_rejected_no_supersede
test_social_turn_does_not_touch_goal_progress
```

cộng các dòng ma trận cũ còn nghĩa: `test_pending_beats_wording_short_yes_approves`, `test_two_part_question_fans_out_two_specialists_in_one_round`, `test_shop_info_question_is_answered_from_tool_facts`, `test_day_named_last_turn_is_reused_via_time_hint_anchor`, `test_only_respond_tokens_reach_the_stream`, `test_prompt_injection_in_customer_text_cannot_add_an_id`.

- [ ] **Step 2–4:** chạy, sửa code gói mới cho tới PASS (không nới test). **Step 5: Commit** `test(agents): scenario suite for the orchestrator`

---

### Task 5.6: Review Focus #2, #5 và ngân sách thời gian

**Files:**
- Modify: `tests/test_scenarios.py`, `tests/test_run_turn.py`

- [ ] **Step 1: Test đỏ**

```python
async def test_unannounced_outcome_is_said_next_turn(test_db, user, models)
    # lượt 1: decide_pending(approve) → fake Conversation Hang → lượt 2 "alo" tới (supersede) → lượt 1 huỷ; ledger lượt 1 announced False
    # lượt 2 reply chứa spoken của lịch đã ghi; ledger lượt 1 vẫn False (chỉ lượt 2 announced), unannounced lượt 3 == None
async def test_conversation_error_after_approve_falls_back_to_code_phrase(test_db, user, models)
async def test_restart_does_not_resume_and_stale_button_is_safe(test_db, user, models)
    # đề xuất pending; checkpoint xoá (memory_checkpointer mới); "ừ" chạy từ START → R2; nút với id cũ sau khi đề xuất bị supersede → stale
def test_turn_time_budget_inequality()
    # orch_orchestrate_timeout_seconds + RESPOND_TIMEOUT_SECONDS + 5 <= orch_turn_timeout_seconds  (CLASSIFY_TIMEOUT_SECONDS bỏ 06/11, Task 4.4b)
    # và orch_task_timeout_seconds * 2 + LLM_TIMEOUT_SECONDS <= orch_orchestrate_timeout_seconds  (một vòng: 2 specialist song song + 1 lượt model)
```

- [ ] **Step 2–4:** FAIL → sửa → PASS. **Step 5: Commit** `test(agents): unannounced outcomes, restart safety, time budget`
