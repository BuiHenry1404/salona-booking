# Guard Rebuild Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the stacked guard layer with Results → Planner (JSON) → one LLM call → fact-only Validator → template on failure, plus combined questions, multi-request turns and model-recorded forms of address.

**Architecture:** Code gathers the turn's facts (`results.py`), turns them into a JSON reply plan (`planner.py`) that the Orchestrator reads in its context block, and checks the model's plain-text reply only against extracted facts (`validator.py`: times, prices, catalogue service names, phone digits, `?`, characters, tool calls). Taken times are told by a code sentence prepended after validation. Every failure gets one rewrite (JSON hint), then the template (`templates.py`).

**Tech Stack:** Python 3.12, LangChain `create_agent` + middleware, LangGraph, Pydantic v2, Motor/MongoDB, pytest.

**Spec:** `docs/superpowers/specs/2026-11-14-guard-rebuild-design.md` (supersedes spec and plan `2026-11-13-plan-guided-reply*`).

## Global Constraints

- No regex anywhere in `app/agents/salon/` (`tests/test_regex_budget.py`).
- Prompts stay English with no Vietnamese characters (`tests/salon/test_prompts.py`). Vietnamese sentences live only in `guard/templates.py`; Vietnamese in the plan JSON is turn data (spoken labels, service names, spoken prices).
- No grammar word list in `app/agents/salon/guard/`. Allowed vocabulary: the number/time/money words already in `text_scan.py` / `clocks.py`.
- Every time in the plan JSON is a spoken label (`short_label`: "3 giờ chiều mai"; other days keep `Moment.spoken`); never "15:00" or ISO. Prices are spoken: under 1,000,000 → `"120 đến 150 nghìn"`; from 1,000,000 → `"1 triệu 2 đến 1 triệu 5"`.
- One model call per reply, plus at most one rewrite. No `response_format` / `ProviderStrategy` on the Orchestrator.
- Graph state values stay plain JSON. Guard-rejected replies are only logged (`rejected_reply`), never stored. Logs never carry customer text.
- Unchanged: subagent behaviour except combined questions, button API semantics, `decide_pending` and `veto`, `ToolRulesMiddleware` rules, REST, Telegram, frontend.
- No Azure / LLM call. Never run `scripts/test_live_10_turns.py` main.
- Run pytest one command at a time (shared test DB): `.venv/bin/python -m pytest -q <paths>`. Known pre-existing failure: `tests/salon/subagents/test_shop_info.py::test_parse_then_is_open_at_answers_a_relative_question`.
- "Break once" (owner's choice), adjusted because `tests/conftest.py` imports `main`, so deleting a module that `run.py` imports stops pytest from collecting anything: Tasks 4–7 write the new modules **beside** the old ones (new names, never importing the old modules, no bridge code, no edits to the old modules); Task 8 switches every caller and deletes the whole old guard and level C at once. Every task needs the **whole** backend suite green (`.venv/bin/python -m pytest -q`) before committing.
- Clock types: `ClockKey` (`guard/clocks.py`, `(hour, minute, period)`) for valid times checked with `is_known`; `Clock = tuple[int, int]` (defined in `results.py`) for times a plan requires or forbids. A reading said in the text (`clocks_in_text` → set of `(h, m)`) says `Clock` c when `c in reading`.
- Commit messages in English ending with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`. Branch `feat/llm-orchestrator`; run `git branch --show-current` before each commit. Never stash, checkout or reset (shared working tree). Never `git add` `docs/superpowers/review.md`, `image.png`, `scripts/terminal_chat.py`.
- "Unused means delete" (CONTEXT decision 21): code, settings, helpers and tests that only served the old guard or level C are removed.

## Review Focus

1. A taken time with the same clock as an offered/required time on another day ("3 giờ chiều mai" taken, "3 giờ chiều thứ Bảy" offered) — the model saying the offered one must pass. Test in Task 7 (`test_taken_clock_equal_to_an_act_clock_is_allowed`).
2. The bot repeating a time the customer typed this turn ("3h") or reading shop hours from the context block without any tool call — must not be `unknown_time`. Tests in Task 4 (`test_valid_clocks_include_context_and_customer_times`) and Task 7.
3. Phone digits and dates inside a reply ("0912 345 678", "14/11") read as money or time → false block. Test in Task 7 (`test_phone_and_date_are_not_prices`).
4. Button turn with a next request (`_continue_next`): code reply + model reply; a crash there must still produce a template that asks about the new proposal. Test in Task 8 (`test_continue_next_failure_template_asks_new_proposal`).
5. Empty plan (greeting) where the model invents a time: rewrite once, then `GENERIC_FALLBACK`, never a crash. Test in Task 8 (`test_no_plan_meaning_error_gets_a_rewrite_then_generic`).

---

### Task 1: Read million-range prices in `scan_money`

**Files:**
- Modify: `app/agents/salon/shared/text_scan.py` (`scan_money`, line ~406)
- Test: `tests/salon/shared/test_text_scan.py`

**Interfaces:**
- Produces: `scan_money(text: str) -> List[int]` (unchanged signature) reads the spoken million forms the templates and model use.

- [ ] **Step 1: Write the failing tests**

```python
@pytest.mark.parametrize("text, expected", [
    ("1 triệu 2 đến 1 triệu 5", [1_200_000, 1_500_000]),
    ("1 triệu 200 nghìn", [1_200_000]),
    ("1,2 triệu", [1_200_000]),
    ("2 triệu", [2_000_000]),
    ("120 đến 150 nghìn", [120_000, 150_000]),
    ("120k đến 150k", [120_000, 150_000]),
])
def test_scan_money_reads_million_forms(text, expected):
    assert scan_money(text) == expected
```

- [ ] **Step 2: Run** `.venv/bin/python -m pytest -q tests/salon/shared/test_text_scan.py -k million_forms` — expected FAIL (today `"1,2 triệu"` → `[12000000]`, `"1 triệu 200 nghìn"` → `[21000000]`).
- [ ] **Step 3: Implement.** In `scan_money`: a decimal comma before `triệu` is a fraction; `"X triệu Y"` adds `Y` hundred-thousands when `Y` is one digit and no unit follows it, or `Y` thousands when `nghìn/ngàn/k` follows. No regex.
- [ ] **Step 4: Run** `tests/salon/shared/test_text_scan.py`, then the whole suite. Expected: pass (known failure only).
- [ ] **Step 5: Commit** `fix: read million-range prices in scan_money`.

---

### Task 2: Combined questions from subagents

**Files:**
- Modify: `app/agents/salon/subagents/dispatch.py` (`SubagentResult`, `_session`, ToolMessage payload ~line 351, rendering ~line 178)
- Modify: `app/models/waiting_session.py` (`SessionQuestion`)
- Modify: `app/agents/salon/shared/context.py` (~line 371, session line)
- Modify: `app/agents/salon/subagents/appointment/agent.py` (~line 237, code-made `need_info`)
- Modify: `app/agents/salon/subagents/appointment/prompt.py`, `app/agents/salon/subagents/service/prompt.py`, `app/agents/salon/subagents/shop_info/prompt.py` (~line 33, "Set question_field")
- Modify: `dispatch.py` `_session(field="confirm")` (~line 303) → `fields=["confirm"]`; `tests/salon/subagents/__init__.py` (~line 65); `scripts/test_live_10_turns.py` (~line 159, `question.field`)
- Modify (keep the old guard compiling until Task 8 deletes it): `app/agents/salon/guard/plan.py`, `app/agents/salon/guard/fallback.py` read `question.fields[0]`
- Test: `tests/salon/subagents/test_dispatch.py`, `tests/salon/subagents/test_appointment.py`, `tests/salon/shared/test_context.py`

**Interfaces:**
- Produces:
  - `SubagentResult.question_fields: list[QuestionField]` (replaces `question_field`; `need_info` requires a non-empty list and `question_text`).
  - `SessionQuestion.fields: list[str]` (replaces `field: str`); a stored document that still has `field` is read as `fields=[field]` via a `model_validator(mode="before")`.
  - ToolMessage payload key `question_fields` (list) replaces `question_field`.
  - Context line: `- {agent}: fields=day,time, options=[...]` (fields joined with a comma, no spaces).

- [ ] **Step 1: Write the failing tests**
  - `test_need_info_accepts_several_fields`: `SubagentResult(status="need_info", question_fields=["day", "time"], question_text="...")` is valid; `question_fields=[]` raises `ValueError`.
  - `test_session_keeps_every_asked_field`: a scripted appointment agent returning `need_info` with `["day", "time"]` opens a session with `question.fields == ["day", "time"]` and the ToolMessage JSON has `"question_fields": ["day", "time"]`.
  - `test_old_session_document_reads_as_one_field`: `SessionQuestion.model_validate({"field": "time", "text": "x"}).fields == ["time"]`.
  - `test_context_lists_session_fields`: context block for a session with `["day", "time"]` contains `fields=day,time`.
  - `test_taken_without_proposal_asks_time`: the code-made `need_info` (agent.py ~237) has `question_fields == ["time"]`.
- [ ] **Step 2: Run** them (`-k "several_fields or every_asked_field or one_field or session_fields or asks_time"`). Expected FAIL.
- [ ] **Step 3: Implement.** Appointment prompt: "need_info asks every missing piece of the request at once in question_fields (for example day and time when the customer said nothing about when; only time when they gave the day). which_appointment and confirm are always asked alone." Service prompt keeps one field `service`. Update every scripted subagent result in tests from `question_field` to `question_fields`.
- [ ] **Step 4: Run** `tests/salon`, then the whole suite. Expected: pass.
- [ ] **Step 5: Commit** `feat: let subagents ask several missing pieces at once`.

---

### Task 3: `remember_address` — the Orchestrator records the form of address

**Files:**
- Modify: `app/models/user.py` (`SelfAddress`)
- Modify: `app/repositories/user.py` (`set_self_address`: overwrite allowed)
- Modify: `app/agents/salon/orchestrator/tools.py` (new tool), `app/agents/salon/shared/tool_names.py` (`CONTROL_TOOLS`)
- Modify: `app/agents/salon/orchestrator/middleware/tool_rules.py` (only `bad_args` applies to it)
- Modify: `app/agents/salon/turn/run.py` (delete `_seed_self_address` and its call ~line 698)
- Modify: `app/core/text.py` (delete `detect_self_address`), `tests/test_text.py` (delete its tests)
- Test: `tests/salon/orchestrator/test_tool_rules.py`, `tests/salon/turn/test_run.py`, `tests/test_user_model.py`

**Interfaces:**
- Produces:
  - `SelfAddress = Literal["anh", "chị", "cô", "chú", "bác", "ông", "bà"]`.
  - Tool `remember_address(form: Annotated[SelfAddress, Field(description="How the customer said they want to be addressed.")], runtime) -> Command` — writes `users.self_address`, also sets `self_address` on the `User` object the `SnapshotHolder` holds (`load_snapshot` never re-reads the user), calls `holder.mark_stale()`, returns a ToolMessage `{"ok": true}`. Not a subagent: no step shown to the customer, no turn fact. Being in `CONTROL_TOOLS` it also falls under `same_agent_twice_in_round`; harmless (one call per round).
  - `UserRepository.set_self_address(user_id: str, self_address: str) -> bool` overwrites. `seed_self_address_from_names` keeps its `user.self_address is not None` skip; update its docstring (idempotence now comes from that skip, not from the write filter).

- [ ] **Step 1: Write the failing tests**
  - `test_remember_address_updates_the_address_line`: scripted Orchestrator calls `remember_address(form="cô")`, then replies; the second model call's context block has the Address line starting with `cô`; DB user has `self_address == "cô"`.
  - `test_remember_address_rejects_unknown_form`: `form="em"` → ToolMessage error `bad_args`; DB unchanged.
  - `test_set_self_address_overwrites`: set `"chị"` then `"cô"` → stored `"cô"`.
  - `test_typed_turn_no_longer_guesses_address`: a typed turn "chị muốn làm móng" with no tool call leaves `self_address` None.
- [ ] **Step 2: Run** them. Expected FAIL.
- [ ] **Step 3: Implement.** Add `remember_address` to `CONTROL_TOOLS` (so `strip_tool_text` removes the name). Delete `detect_self_address`, `_seed_self_address`, their tests, and the comment in `app/models/user.py:43` that names the regex.
- [ ] **Step 4: Run** `tests/salon tests/test_text.py`, then the whole suite. Expected: pass.
- [ ] **Step 5: Commit** `feat: let the orchestrator record how the customer is addressed`.

---

### Task 4: `results.py` — the turn's facts in one model

**Files:**
- Keep untouched: the old guard modules (Task 8 deletes them); `app/agents/salon/guard/clocks.py`
- Create: `app/agents/salon/guard/results.py`
- Test: `tests/salon/guard/test_results.py`

**Interfaces:**
- Consumes: `TurnSnapshot`, `TurnFact` types, `PendingOutcome`, `ServiceCatalogService`, `clocks.py`.
- Produces (in `results.py`):
  - `Clock = tuple[int, int]`.
  - `short_label(moment: Moment, today: date) -> str` (same behaviour as today's `catalog.short_label`; a copy, not an import).
  - `spoken_price(low: int, high: int) -> str` (formats per Global Constraints; `low == high` → one amount).
  - `class ServiceItem(BaseModel)`: `service_id: str, name: str, price_min: int, price_max: int, identified: bool = False, asked_price: bool = False`.
  - `class TurnResults(BaseModel)`: `snapshot: TurnSnapshot, turn_facts: list[TurnFact], pending_outcome: Optional[PendingOutcome], turn_id: str, turn_kind: Literal["typed", "button"], services: dict[str, ServiceItem]` (every service id the turn can name, resolved from the DB), `turn_service_ids: list[str]`, `shop_service_names: list[str]`, `valid_clocks: set[ClockKey]`, `shop_phone: Optional[str]`, `previous_replies: list[str]`, `pending_undecided: bool`. Properties: `today`, `address`.
  - `async def build_turn_results(db, *, snapshot, turn_facts, pending_outcome, turn_id, turn_kind, question: str, previous_replies: list[str], pending_decided: bool) -> TurnResults`.

- [ ] **Step 1: Write the failing tests** in `test_results.py`:
  - `test_turn_services_are_this_turn_only`: `ServiceFact` "Cắt tóc nữ" in `turn_facts`, `snapshot.known_services` has "Sơn gel", upcoming appointment with "Sơn gel" not listed this turn → `turn_service_ids` has only Cắt tóc nữ's id.
  - `test_turn_services_include_proposal_pending_outcome_and_listed_appointments`: ids from a `ProposalFact`, `snapshot.pending`, `pending_outcome.service_ids`, and an upcoming appointment whose moment equals an `appointment` `TimeFact` of the turn are all in `turn_service_ids`.
  - `test_valid_clocks_include_context_and_customer_times`: `valid_clocks` contains the turn's `TimeFact` clocks, shop open/close, upcoming appointments, the pending proposal, session options, `pending_outcome.nearest`, R0, and the clocks typed in `question` ("3h chiều").
  - `test_pending_undecided`: pending proposal + typed turn + `pending_decided=False` → True; button turn → False.
  - `test_spoken_price`: `(120_000, 150_000)` → `"120 đến 150 nghìn"`; `(1_200_000, 1_500_000)` → `"1 triệu 2 đến 1 triệu 5"`; `(150_000, 150_000)` → `"150 nghìn"`; and `scan_money(spoken_price(a, b))` returns `[a, b]` for each pair.
  - `test_short_label`: today → `"3 giờ chiều hôm nay"`, tomorrow → `"3 giờ chiều mai"`, other day → `moment.spoken`.
- [ ] **Step 2: Run** `.venv/bin/python -m pytest -q tests/salon/guard/test_results.py`. Expected FAIL (module missing).
- [ ] **Step 3: Implement** `results.py`. It imports nothing from `guard/` except `clocks.py`; it defines each status constant once (failed / released / still-pending outcome sets) for `planner.py` to import. Fixture helpers for these tests go in a new `tests/salon/guard/builders.py` (not `__init__.py`, which imports the old guard until Task 8).
- [ ] **Step 4: Run** `tests/salon/guard/test_results.py`, then the whole suite. Expected PASS.
- [ ] **Step 5: Commit** `feat: gather the turn's facts in one results model`.

---

### Task 5: `planner.py` — acts and the plan JSON

**Files:**
- Create: `app/agents/salon/guard/planner.py`
- Test: `tests/salon/guard/test_planner.py`

**Interfaces:**
- Consumes: `TurnResults`, `short_label`, `spoken_price`, outcome sets from `results.py`.
- Produces:
  - `ActKind = Literal["failed", "done", "released", "proposal", "offer", "price", "appointments", "hours", "busy"]`.
  - `class PlanAct(BaseModel)`: `act: ActKind, fields: dict[str, Any]` (exactly the JSON fields of the spec table), `clocks: list[Clock]`, `prices: list[tuple[int, int]]`, `service_names: list[str]`, `announce_turn_ids: list[str]`.
  - `class PlanAsk(BaseModel)`: `fields: list[str], options: list[str], agent: str`.
  - `class ReplyPlan(BaseModel)`: `taken: list[str]` (spoken labels), `taken_clocks: list[Clock]`, `acts: list[PlanAct]`, `ask: Optional[PlanAsk]`, `phone: Optional[str]`, `asked_session: Optional[str]` (R12: the agent whose session opened this turn and whose question the plan asks, either through `ask` or through an `offer` built from that session's options). Methods: `to_json() -> dict` (`{"taken_told_by_code": [...], "acts": [{"act": ..., **fields}], "ask": {"fields": [...], "options": [...]} | None}`), `is_empty() -> bool` (no acts, no ask, no taken), `act_clocks() -> set[Clock]`, `announce_turn_ids() -> list[str]`.
  - `build_plan(results: TurnResults) -> ReplyPlan`.
  - `tests/salon/guard/plan_cases.py`: `PLAN_CASES: list[tuple[str, TurnResults, ReplyPlan]]` (name, results, built plan) covering every plan built in `test_planner.py`; Task 7 imports it.

- [ ] **Step 1: Write the failing tests** (one per rule; each asserts `to_json()` exactly):
  - `test_taken_goes_to_code_not_acts`: taken 3 giờ chiều mai + session options 4h, 5h → `{"taken_told_by_code": ["3 giờ chiều mai"], "acts": [{"act": "offer", "times": ["4 giờ chiều mai", "5 giờ chiều mai"]}], "ask": None}`.
  - `test_proposal_drops_offer`: new proposal + free times → acts `[proposal]` only, `{"act": "proposal", "kind": "book", "at": "4 giờ chiều mai", "services": ["Sơn gel"]}`.
  - `test_cancel_proposal_has_no_services`: a cancel proposal → `{"act": "proposal", "kind": "cancel", "at": ..., "services": []}` (its template names no service).
  - `test_offer_at_most_three`: five `free` times → `times` has 3.
  - `test_asked_price_on_a_booking_turn`: proposal + `ServiceItem(asked_price=True)` → acts `[proposal, {"act": "price", "service": "Sơn gel", "price": "120 đến 150 nghìn"}]`.
  - `test_identified_priced_only_without_booking_act`: identified service, no booking act → price act; same with a proposal and `asked_price=False` → no price act.
  - `test_done_with_services_and_announce`: typed turn, `pending_outcome` approved with `service_ids` → `{"act": "done", "kind": "book", "at": ..., "services": ["Sơn gel"]}` and `announce_turn_ids() == [turn_id]`.
  - `test_r0_same_as_turn_decision_not_twice`: R0 with the current `turn_id` → no second done act.
  - `test_failed_phone_rule`: failed, no nearest, phone set → `{"act": "failed", "at": ..., "nearest": [], "phone": "0912345678"}`; with nearest → `"phone": None`; second failure in the turn → `"phone": None`.
  - `test_kept_pending_not_reasked_clarify_reasked`.
  - `test_ask_combines_fields`: session opened this turn with `fields=["day", "time"]` → `"ask": {"fields": ["day", "time"], "options": []}`; `plan.asked_session == "appointment"`.
  - `test_offer_from_opened_session_replaces_ask`: `ask is None` and `plan.asked_session == "appointment"`.
  - `test_hours_busy_appointments`: shop_open/close → `{"act": "hours", "open": "8 giờ sáng", "close": "8 giờ tối"}`; busy_until → `{"act": "busy", "until": ...}`; up to 5 `appointment` times → `{"act": "appointments", "times": [...]}`.
  - `test_button_turn_has_no_decision_act`.
  - `test_greeting_plan_is_empty`.
  - `test_json_has_no_clock_digits`: for every plan above, `json.dumps(plan.to_json(), ensure_ascii=False)` contains no `":"` between digits and no ISO string.
- [ ] **Step 2: Run** `tests/salon/guard/test_planner.py`. Expected FAIL.
- [ ] **Step 3: Implement** `build_plan` and `plan_cases.py`. Act order: failed → done → released → proposal → offer → price → appointments → hours → busy. Labels for `hours` (no day) use the clock phrase only (`format_vi_hhmm`).
- [ ] **Step 4: Run** `tests/salon/guard/test_planner.py tests/salon/guard/test_results.py`. Expected PASS.
- [ ] **Step 5: Commit** `feat: build the reply plan as JSON acts`.

---

### Task 6: `templates.py` — sentences for failures and buttons

**Files:**
- Create: `app/agents/salon/guard/templates.py`
- Test: `tests/salon/guard/test_templates.py`

**Interfaces:**
- Consumes: `ReplyPlan`, `PlanAct`, `short_label`, `spoken_price`.
- Produces:
  - Constants with today's wording from `fallback.py`: `GENERIC_FALLBACK`, `BUTTON_STALE_REPLY`, `TAKEN_REPLY`, `PROPOSAL_REPLY`, `CANCEL_PROPOSAL_REPLY`, `DONE_REPLY`, `DONE_MOVE_SERVICES_REPLY`, `FAILED_REPLY`, `FAILED_NO_PHONE_REPLY`, `FAILED_NEAREST_REPLY`, `ALSO_FAILED_REPLY`, `REJECTED_REPLY`, `REJECTED_CANCEL_REPLY`, `TIME_OPTIONS_QUESTION`, `FIELD_QUESTION`, `PRICE_REPLY`, `APPOINTMENTS_REPLY`, `HOURS_REPLY`, `BUSY_REPLY`.
  - `taken_sentence(plan: ReplyPlan, address: str) -> str` ("" when no taken; several taken times joined into one sentence).
  - `body(plan: ReplyPlan, address: str) -> str` (acts + ask; `FIELD_QUESTION` over several fields gives one question, e.g. `"Dạ {address} muốn làm ngày nào, mấy giờ ạ?"`).
  - `compose(plan: ReplyPlan, address: str) -> str` = `taken_sentence` + `body`, or `GENERIC_FALLBACK` when `plan.is_empty()`.
  - `button_reply(outcome: PendingOutcome, *, address: str, shop_phone: Optional[str], today: date) -> str` (today's signature and behaviour).
  - Wording source: read today's `app/agents/salon/guard/fallback.py` (still present until Task 8); copy the constants, do not import them. Service names are lowercased inside sentences as today (`_lower_first`).

- [ ] **Step 1: Write the failing tests**
  - `test_compose_prefixes_taken`: plan of Task 5 `test_taken_goes_to_code_not_acts` → `"Dạ 3 giờ chiều mai đã có khách rồi ạ. Dạ còn trống 4 giờ chiều mai hoặc 5 giờ chiều mai, {address} chọn giờ nào ạ?"` (exact wording per the constants).
  - `test_field_question_combines_fields`: ask `["day", "time"]` → one sentence ending with `?`.
  - `test_price_sentence_is_spoken`: `"Dạ sơn gel giá 120 đến 150 nghìn ạ."`.
  - `test_taken_only_plan`: a plan with only `taken` → `compose` returns the taken sentence alone (intended: such a plan has no question to ask).
  - `test_failed_with_phone` / `test_failed_with_nearest_has_no_phone`.
  - `test_empty_plan_is_generic`.
  - `test_button_reply_unchanged_cases`: approved, rejected, failed with nearest, failed without phone — the same strings today's `fallback.button_reply` returns for the same inputs (assert equality against it while it still exists).
- [ ] **Step 2: Run** `tests/salon/guard/test_templates.py`. Expected FAIL.
- [ ] **Step 3: Implement.** `templates.py` imports `results.py` and `planner.py` only.
- [ ] **Step 4: Run** `tests/salon/guard`, then the whole suite. Expected PASS.
- [ ] **Step 5: Commit** `feat: build template replies from the reply plan`.

---

### Task 7: `validator.py` — fact-only checks

**Files:**
- Create: `app/agents/salon/guard/validator.py`
- Test: `tests/salon/guard/test_validator.py`

**Interfaces:**
- Consumes: `TurnResults`, `ReplyPlan`, `clocks.py` (`clocks_in_text`, `is_known`), `text_scan` (`scan_money`, `words`, `digits_in`), `normalize_for_match`, `CONTROL_TOOLS`, `SUBAGENT_TOOLS`.
- Produces:
  - `CODES = ("bad_tool_call", "pending_undecided", "plan_missing", "unknown_time", "unknown_price", "wrong_service", "taken_mentioned", "missing_question", "missing_phone", "phone_not_allowed", "empty", "foreign_script", "repeat")`.
  - `class Verdict(BaseModel)`: `text: str, codes: list[str]` (in `CODES` order), `missing: list[dict]` (the missing acts as plan-JSON dicts; a missing `ask` as `{"ask": {...}}`).
  - `strip_tool_text(text: str) -> str` (same behaviour as today's `checks.strip_tool_text`; a copy, not an import).
  - `validate(text: str, plan: ReplyPlan, results: TurnResults, *, invalid_tool_call: bool = False) -> Verdict`. Checks only the given text (the model part, or `templates.body`); the taken sentence is never validated.

- [ ] **Step 1: Write the failing tests** (`validate(...).codes`):
  - `test_offer_reply_passes`: "Còn trống 4 giờ hoặc 5 giờ chiều mai, chị chọn giờ nào ạ?" → `[]`.
  - `test_taken_mentioned`: "Chị chọn 3 giờ hay 4 giờ chiều mai ạ?" on the taken plan → contains `"taken_mentioned"` and `"plan_missing"`; `missing == [{"act": "offer", "times": [...]}]`.
  - `test_taken_clock_equal_to_an_act_clock_is_allowed`.
  - `test_plan_missing_service_name`: done act with "Sơn gel", reply names the time only → `["plan_missing"]`.
  - `test_unknown_time` / `test_customer_typed_time_is_valid` / `test_context_shop_hours_valid_without_act`.
  - `test_unknown_price`: "khoảng 200 nghìn" against 120–150k → `["unknown_price"]`; "khoảng 150 nghìn" → `[]`.
  - `test_wrong_service`: proposal "Cắt tóc nữ", reply "em giữ chỗ sơn gel 2 giờ chiều mai" → contains `"wrong_service"` and `"plan_missing"`; a name contained in a longer turn name ("cắt tóc" inside "cắt tóc nữ") is not counted.
  - `test_missing_question` (plan with proposal/offer/ask, no `?`).
  - `test_phone_rules`: `phone` set and absent from text → `missing_phone`; phone in text with `phone=None` → `phone_not_allowed`.
  - `test_phone_and_date_are_not_prices`: "số tiệm 0912 345 678, lịch ngày 14/11" with a phone act → no `unknown_price`, no `unknown_time`.
  - `test_pending_undecided`, `test_empty_after_strip` (reply is only a tool name), `test_foreign_script` (Armenian letters), `test_repeat`, `test_bad_tool_call_first`.
  - `test_no_word_lists`: `validator.py` source defines no module-level `frozenset`/`tuple`/`set` of Vietnamese words (scan the module's globals: no collection containing a str with a Vietnamese letter).
  - `test_template_body_passes_validator`: for every `(name, results, plan)` in `tests/salon/guard/plan_cases.py::PLAN_CASES`, `validate(templates.body(plan, results.address), plan, results).codes == []`.
- [ ] **Step 2: Run** `tests/salon/guard/test_validator.py`. Expected FAIL.
- [ ] **Step 3: Implement.** `taken_mentioned`: a clock of `plan.taken_clocks` said in the text and not in `plan.act_clocks()`. `missing_question`: plan has a proposal, an offer or an ask, and `"?"` not in text. `repeat`: ratio ≥ 0.85, ≥ 6 words, last 2 replies.
- [ ] **Step 4: Run** `tests/salon/guard`, then the whole suite. Expected PASS.
- [ ] **Step 5: Commit** `feat: validate replies against extracted facts only`.

---

### Task 8: Wire the Orchestrator and the turn to the new guard; remove level C

**Files:**
- Delete: `app/agents/salon/guard/{catalog,facts,plan,checks,fallback,segments}.py`, `tests/salon/guard/test_{catalog,checks,plan,fallback,segments}.py`
- Rewrite: `tests/salon/guard/__init__.py` (drop the old-guard helpers; keep only what the new tests use)
- Move: `test_tool_name_set_covers_every_subagent_tool` (from `test_checks.py`) into `tests/salon/guard/test_validator.py`; update the `shared/tool_names.py` docstring to name `validator.strip_tool_text`
- Modify: `app/agents/salon/orchestrator/agent.py` (drop `reply_format` / `response_format` / `REPLY_SCHEMA`)
- Modify: `app/agents/salon/orchestrator/middleware/context.py` (render the plan JSON; drop the `StructuredOutputValidationError` catch)
- Modify: `app/agents/salon/orchestrator/middleware/reply_guard.py` (new policy)
- Modify: `app/agents/salon/turn/run.py` (`_reply_facts` → results/plan, `_fallback`, `_continue_next`, `_write_code_reply`, `_mark_announced`, `button_reply` call)
- Modify: `tests/fakes.py` (delete `Cite`, `cite`, `reply`, `plain` and catalog helpers once unused), `tests/test_fakes.py`
- Modify tests: `tests/salon/orchestrator/test_middleware.py`, `tests/salon/turn/test_run.py`, `tests/test_socketio_chat.py`, `tests/test_telegram_handlers.py`

**Interfaces:**
- Consumes: Tasks 4–7.
- Produces:
  - `async def reply_inputs_for(db, holder: SnapshotHolder, state, context) -> tuple[TurnResults, ReplyPlan]` in `reply_guard.py`, used by `ContextMiddleware` and `ReplyGuardMiddleware`.
  - Context block section: `PLAN_HEADER = "Reply plan (JSON):"` followed by `json.dumps(plan.to_json(), ensure_ascii=False)`; omitted when `plan.is_empty()`.
  - Rewrite hint: a `HumanMessage` (marked `GUARD_HINT_MARK`) whose content is `json.dumps({"rejected": codes, "missing": verdict.missing, "hint": " ".join(HINTS[c] for c in codes)})`.
  - `HINTS` (English, one per code; `plan_missing`: "Tell every act and the ask in the Reply plan, with every time, price and service name in it."; `taken_mentioned`: "Do not mention the times in taken_told_by_code; the customer was already told."; `unknown_time`: "Mention only times from the context block."; `unknown_price`: "Mention only prices inside this turn's price ranges."; `wrong_service`: "Name only the services of this turn."; `missing_phone`: "Give the shop phone number from the failed act."; `missing_question`: "End with the question of the Reply plan: whether they agree to the proposal, which offered time they choose, or the ask fields."; the rest reuse today's `_CODE_HINTS` text for that code).

- [ ] **Step 1: Write the failing tests**
  - `test_context_block_shows_the_plan_json` (middleware): taken + options → the context SystemMessage contains `"Reply plan (JSON):"` and `"taken_told_by_code"`; no `"Reply facts"`.
  - `test_pass_prepends_taken_sentence`: model reply "Còn trống 4 giờ hoặc 5 giờ chiều mai, chị chọn giờ nào ạ?" → final message `"Dạ 3 giờ chiều mai đã có khách rồi ạ. Còn trống …"`, `salon_source` not `"code"`.
  - `test_every_error_rewrites_once_then_template`: first reply has `plan_missing` → 2 model calls; the hint message is valid JSON with `"rejected": ["plan_missing"]`; second reply fails again → final is `templates.compose(plan, address)` with `salon_source == "code"`.
  - `test_rewrite_hides_tools_except_decide_pending_for_pending_undecided`.
  - `test_no_plan_meaning_error_gets_a_rewrite_then_generic` (Review Focus 5).
  - `test_orchestrator_has_no_response_format`.
  - `test_accepted_reply_marks_plan_announce_ids` / `test_template_reply_marks_plan_announce_ids` (run).
  - `test_continue_next_failure_template_asks_new_proposal` (Review Focus 4).
  - `test_button_approve_is_announced`: a button approve, then a typed turn → the typed turn's plan has no `done` act (the ledger row is marked announced by the button turn).
  - `test_code_answer_keeps_announced_ids`: `_final_answer` for a guard template reply returns `announced == plan.announce_turn_ids()`.
- [ ] **Step 2: Run** them. Expected FAIL.
- [ ] **Step 3: Implement.** `aafter_model`: (1) `verdict = validate(...)`; (2) no codes → content = `taken_sentence + " " + verdict.text` (stripped), `announced_turn_ids = plan.announce_turn_ids()`; (3) codes and `guard_rewrites == 0` → remove the reply, add the JSON hint, `guard_rewrites: 1`, `jump_to: "model"`; (4) otherwise → `templates.compose`, `announced_turn_ids = plan.announce_turn_ids()`, close sessions opened this turn except `plan.asked_session()`, `jump_to: "end"`. Keep today's behaviour: remove earlier guard-hint messages on PASS and on the template branch; log `guard_rejected` (first failure) and `guard_violation` (template) with `codes` and `rejected_reply` (bot text only); drop the `structured_output_invalid` log with its catch. `run.py`:
  - `_mark_announced` marks `answer.announced` for both sources; delete the `spoken` / short-label matching and the `structured_response` clearing;
  - `_final_answer` stops dropping `announced_turn_ids` for code replies (remove the `not code` filter);
  - `_fallback` and the `_continue_next` failure path build results + plan and pass `plan.announce_turn_ids()` as `announced`;
  - `_button_turn` sets `announced=[turn.turn_id]` when the outcome is `approved` or `failed` (its `button_reply` told it).
- [ ] **Step 4: Delete the old guard and convert tests.** Delete the six old modules and five old test files, rewrite `tests/salon/guard/__init__.py`, move the tool-name canary test. Every `reply(...)`/`cite(...)`/`plain(...)` scripted reply becomes `AIMessage("<joined text>")` with the same sentence. Where a test asserted a removed code (`claim`, `pronoun`, `english`, `taken_offered`, level C codes), assert the new outcome for that sentence and add a ledger `Ruling:` line in the task report naming the test.
- [ ] **Step 5: Run** `.venv/bin/python -m pytest -q tests/salon tests/test_fakes.py tests/test_socketio_chat.py tests/test_telegram_handlers.py tests/test_regex_budget.py`, then the whole suite. Expected: all pass (known failure only).
- [ ] **Step 6: Commit** `feat: answer from the JSON reply plan and remove the segment format`.

---

### Task 9: Orchestrator prompt

**Files:**
- Modify: `app/agents/salon/orchestrator/prompt.py`
- Test: `tests/salon/test_prompts.py`

**Interfaces:**
- Consumes: `PLAN_HEADER` wording and the JSON keys of `ReplyPlan.to_json()`; tool `remember_address`.

- [ ] **Step 1: Write the failing tests** in `test_prompts.py`:
  - Delete `test_orchestrator_prompt_describes_segments`, `test_orchestrator_prompt_keeps_verbs_in_plain_segments`, `test_orchestrator_prompt_says_taken_times_are_not_offered`.
  - `test_orchestrator_prompt_has_no_segment_rules`: no `"segment"`, `"Reply facts"`, `"ref and role"`.
  - `test_orchestrator_prompt_names_plan_keys`: contains `"Reply plan"`, `"taken_told_by_code"`, `"acts"`, `"ask"`, `"remember_address"`.
  - Existing: no Vietnamese characters.
- [ ] **Step 2: Run** `tests/salon/test_prompts.py`. Expected FAIL.
- [ ] **Step 3: Rewrite** the "What you receive" and "Writing the reply" sections; keep Tools / Services / Pending proposal / Waiting sessions. Required rules (English, own wording):
  - One message with several requests (booking, a price, opening hours…): call every agent it needs, independent agents in the same round, before replying; the reply covers every request. Facts already in the context block (opening hours, upcoming appointments) may be used directly.
  - With a Reply plan: tell every act and the ask, with every time, price and service name in them, written the way the JSON writes them; add no other time, price or service.
  - Never mention a time listed in `taken_told_by_code`: the code tells the customer just before your reply; continue from there.
  - The `ask` lists fields: ask for all of them in one question.
  - `proposal` is not booked yet: ask whether they agree; say booked/moved/cancelled only for a `done` act.
  - Address the customer exactly as the Address line says; call `remember_address` only when the customer says how they want to be addressed, including when they refer to themselves ("I, your aunt…"); never guess from a name.
  - Keep: plain text, no Markdown, short sentences, phone only from a `failed` act, never write tools, agents, ids or JSON.
- [ ] **Step 4: Run** `tests/salon`, then the whole suite. Expected: pass.
- [ ] **Step 5: Commit** `feat: prompt the orchestrator to answer from the JSON reply plan`.

---

### Task 10: Guard accuracy set

**Files:**
- Create: `tests/salon/guard/accuracy/cases.json`, `tests/salon/guard/test_accuracy.py`

**Interfaces:**
- Consumes: `build_plan`, `validate`, `TurnResults` built from minimal fixtures.

- [ ] **Step 1: Build the data.** Bot sentences only (no customer text) from `.superpowers/sdd/2026-11-08-subagents-rebuild/live-{2,3,4}.log` and `.superpowers/sdd/2026-11-11-level-c/live-5-{A,B}.log`. Each case: `{"id", "reply", "turn": {minimal facts to build TurnResults}, "label": "ok" | "bad" | "accepted_leak", "codes": [...]}`. Required `bad` cases: live 5 B turn 7 → `taken_mentioned`; live 2 turn 2 → `taken_mentioned`; live 4 turn 8 → `wrong_service` and `plan_missing`; an Armenian-letter reply → `foreign_script`. Add `accepted_leak` cases for a "booked" claim on a proposal time and a wrong form of address.
- [ ] **Step 2: Write** `test_accuracy_set`: `bad` must be blocked with at least the listed codes; `ok` blocked count ≤ `cases.json["max_false_blocks"]` (starts at `0`); `accepted_leak` must pass the validator; print the three counts.
- [ ] **Step 3: Run** `tests/salon/guard/test_accuracy.py`. A failing `ok` case means a rule bug: fix the validator in this task (with a unit test in `test_validator.py`) or relabel with a reason field.
- [ ] **Step 4: Run** the whole suite. Expected: pass.
- [ ] **Step 5: Commit** `test: measure guard false blocks and leaks on real replies`.

---

### Task 11: Documents and final check

**Files:**
- Modify: `CONTEXT.md` (status section: the rebuild, owner decisions of 14/11: planner JSON, fact-only validator, taken told by code, rewrite once for every error, combined questions, multi-request turns, `remember_address`; remove the stale "ĐANG LÀM" content of the 13/11 plan)
- Modify: `docs/architech-agent.md` (guard section)
- Modify: `docs/superpowers/plans/2026-11-13-plan-guided-reply.md` (first line: superseded by this plan, Tasks 1–2b kept in history)

- [ ] **Step 1: Verify the done criteria:**
  - `grep -rnE "guard\.(catalog|segments|facts|checks|plan|fallback)\b|FactCatalog|build_catalog|check_structured|check_reply|reply_format|REPLY_SCHEMA|TAKEN_MARKERS|OFFER_WORDS|ENGLISH_FUNCTION_WORDS|_BOOKING_VERBS|detect_self_address" app tests` → no output.
  - `.venv/bin/python -m pytest -q` → all pass except the known failure.
- [ ] **Step 2: Update the three documents.**
- [ ] **Step 3: Commit** `docs: record the guard rebuild in CONTEXT and the architecture doc`.

After Task 11 (controller, not an implementer task): whole-branch review on the most capable model, one fix wave; then **ask the owner** before the live runs A and B (`scripts/test_live_10_turns.py`, and `--keep-db`), which need two passes; new real replies go into `cases.json`.
