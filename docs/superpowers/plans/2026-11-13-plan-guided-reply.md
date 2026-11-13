# Plan-Guided Reply Guard Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the level C JSON-segment reply with a code-made reply plan. The model rewrites the plan's template in plain text, a plain-text guard checks it, and a meaning error sends the template.

**Architecture:**
- After the tools run, `guard/plan.py` builds a `ReplyPlan` (acts with Vietnamese template sentences) from the turn's facts and the existing `FactCatalog`.
- `ContextMiddleware` shows the plan as a "Reply plan" block.
- The guard checks the model's plain-text reply against the plan: times, prices and service names present, nothing invented, no taken time offered.
- On a meaning error the template is the reply. Style errors get one rewrite.
- Still one model call per reply, and no `response_format`.

**Tech Stack:** Python 3.12, LangChain 1.4.6 `create_agent` + middleware, pytest with the Mongo `rs0` test DB.

**Spec:** `docs/superpowers/specs/2026-11-13-plan-guided-reply-design.md`, including the sections "Quyết định sau builder check" and "Bộ đo độ chính xác của guard".

## Global Constraints

- No regex anywhere in `app/agents/salon/` (`tests/test_regex_budget.py`).
- Prompts stay English with no Vietnamese characters (`tests/salon/test_prompts.py`). Vietnamese template sentences live in code (`guard/fallback.py`, `guard/plan.py`), never in prompts.
- Word lists in the guard are grammar words of the **bot's** sentence; never guess the customer's intent (CONTEXT.md trap 21).
- One model call per reply. No `response_format` / `ProviderStrategy` on the Orchestrator after Task 3.
- Graph state values stay plain JSON.
- Unchanged: subagents, button API semantics, `decide_pending` and veto, `ToolRulesMiddleware`, REST, Telegram, frontend.
- Guard-rejected replies are only logged (`rejected_reply`), never stored. Logs never carry customer text.
- No Azure call without the owner's explicit approval. Never run `scripts/test_live_10_turns.py` main in tests.
- Run pytest once at a time (shared test DB): `.venv/bin/python -m pytest -q <paths>`. Known pre-existing failure, not this plan's: `tests/salon/subagents/test_shop_info.py::test_parse_then_is_open_at_answers_a_relative_question`. Before each commit run the **whole** backend suite (`.venv/bin/python -m pytest -q`), not only `tests/salon`. A test outside `tests/salon` broke last time.
- Commit messages in English. Never `git add` `docs/superpowers/review.md`, `image.png` or `scripts/terminal_chat.py`. Work on `feat/llm-orchestrator`, and check `git branch --show-current` before each commit. Another session shares this working tree, so never stash, checkout or reset.

## Review Focus

1. **Every template must pass the checker.** For every plan the builder can make, `check_reply(plan.text, facts)` has no meaning code. Otherwise a fallback is itself blocked. Test: `test_every_plan_template_passes_the_checks` (Task 2).
2. **A customer asks two things in one turn** (books a taken time and asks the price). The plan holds taken + offer + price. A reply missing the price is `plan_missing`; a reply with all of them passes. Test rows `two_requests_*` (Task 2).
3. **The same clock is taken one day and offered another.** No `taken_offered` (spec decision 5). Test row `same_clock_other_day` (Task 2).
4. **`pending_undecided` together with a meaning error.** The reply is rewritten once with `decide_pending` offered, not sent as the template (spec decision 6). Test: `test_pending_undecided_rewrites_before_template` (Task 3).
5. **A plan with only a question and no clock** (e.g. "muốn làm dịch vụ gì"). `plan_missing` must not fire on a reply that asks it in other words. Test row `question_only_plan` (Task 2).

---

### Task 1: `ReplyPlan` and template sentences

**Files:**
- Create: `app/agents/salon/guard/plan.py`
- Modify: `app/agents/salon/guard/fallback.py` (short labels, service names, new templates; `button_reply` takes `today`; `compose_fallback_asking` is removed, and its logic moves into `build_plan`)
- Modify: callers of `button_reply` in `app/agents/salon/turn/run.py`; pass `today = (await turn.holder.current()).today`
- Test: `tests/salon/guard/test_plan.py` (new). Port every case of `tests/salon/guard/test_fallback.py` that exercises `compose_fallback_asking` to `test_plan.py`; keep the `button_reply` tests in `test_fallback.py`.

**Interfaces:**
- Consumes: `build_catalog`, `FactCatalog`, `CatalogEntry`, `short_label`, `format_price`, `turn_decisions`, `CLAIM_FOR_KIND` (`guard/catalog.py`); `TurnSnapshot`; the turn facts types in `shared/schemas.py`.
- Produces:
  ```python
  ActKind = Literal["failed", "done", "taken", "proposal", "offer", "ask_field", "released",
                    "price", "appointments", "hours", "busy"]
  class PlanAct(BaseModel):
      kind: ActKind
      sentence: str                         # Vietnamese template sentence for this act
      clocks: list[tuple[int, int]] = []    # 24h values the reply must say (all of them)
      prices: list[tuple[int, int]] = []    # each range needs one amount inside it in the reply
      service_names: list[str] = []         # services this act is about (proposal / decision)
      taken_clocks: list[tuple[int, int]] = []   # set only on "taken" acts
      announce_turn_ids: list[str] = []     # ledger turns this act announces
  class ReplyPlan(BaseModel):
      acts: list[PlanAct]
      asks_session: Optional[str] = None    # agent whose opened question the plan asks (Ruling R12)
      def text(self) -> str                 # sentences joined by " ", each sentence capitalized; "" if no acts
      def render(self) -> str               # PLAN_HEADER + "\n- " + sentence per act; "" if no acts
      def clocks(self) -> set[tuple[int, int]]
      def taken_clocks(self) -> set[tuple[int, int]]
      def offered_clocks(self) -> set[tuple[int, int]]   # clocks of "offer" and "proposal" acts
      def prices(self) -> list[tuple[int, int]]
      def announce_turn_ids(self) -> list[str]           # unique, act order
  PLAN_HEADER = "Reply plan (tell the customer all of this, in your own words):"
  def build_plan(catalog: FactCatalog, *, snapshot: TurnSnapshot, turn_facts: list[TurnFact],
                 pending_outcome: Optional[PendingOutcome], turn_id: str,
                 turn_kind: Literal["typed", "button"], shop_phone: Optional[str]) -> ReplyPlan
  # fallback.py
  def button_reply(outcome: PendingOutcome, *, address: str, shop_phone: Optional[str], today: date) -> str
  ```

**Template copy** (fixed Vietnamese copy, in `fallback.py`):
- **Existing constants:** they keep their wording, but `{at}` is now `short_label(moment, snapshot.today)` instead of `Moment.spoken`. Update the module docstring line "`{at}` luôn là `Moment.spoken`".
- **`PROPOSAL_REPLY` and `DONE_REPLY`** get a `{what}` slot in place of `{at}`:
  - `what = f"{services} lúc {at}"` when the proposal or decision has service names;
  - otherwise `what = at`.
  - `services` = names joined by `" và "`, each with its first letter lower-cased (`"Cắt tóc nữ"` → `"cắt tóc nữ"`).
  - Example: `"Dạ em giữ chỗ cắt tóc nữ lúc 2 giờ chiều mai cho chị Lan, chị Lan đồng ý không ạ?"`.
- **New constants:**
  - `PRICE_REPLY = "Dạ {service} giá {price} ạ."` with `price = format_price(low, high)`;
  - `APPOINTMENTS_REPLY = "Dạ {address} có lịch hẹn {times} ạ."`, where `times` uses `_spoken_list` over the short labels;
  - `HOURS_REPLY = "Dạ tiệm mở cửa từ {open} đến {close} ạ."` with `format_vi_hhmm`;
  - `BUSY_REPLY = "Dạ tiệm đang bận tới {at} ạ."`.

**How `build_plan` picks acts** (spec "Quyết định sau builder check", item 2). Acts are emitted in this order:
1. **failed:** the typed turn's decision `failed`/`not_approvable` (`FAILED_REPLY` / `FAILED_NO_PHONE_REPLY`, `announce_turn_ids=[turn_id]` for `failed`), then R0 failed (`ALSO_FAILED_REPLY` when the turn also failed, otherwise `FAILED_REPLY`; `announce_turn_ids=[unannounced.turn_id]`). Button turns skip the turn's own decision (`button_reply` already told it).
2. **done:** the typed turn's decision `approved` (`DONE_REPLY` by kind, `announce_turn_ids=[turn_id]`), then R0 approved.
3. **released:** the typed turn's decision `rejected`/`superseded` (`REJECTED_REPLY`, or `REJECTED_CANCEL_REPLY` for a cancel).
4. **taken:** one act per distinct taken moment of the turn (`TimeFact` role `taken`), using `TAKEN_REPLY`, with `taken_clocks`.
5. **proposal:** the last new `ProposalFact` of the turn. Without one, the pending proposal when a turn decision is `clarify` (not `kept`; spec decision 4). Uses `PROPOSAL_REPLY` / `CANCEL_PROPOSAL_REPLY` with the service names.
6. **offer:** only when there is no proposal act. Options of the session opened this turn when its field is `time` (`TIME_OPTIONS_QUESTION`; then set `asks_session`). Otherwise `pending_outcome.nearest` (`FAILED_NEAREST_REPLY`'s second part becomes the offer sentence). Otherwise the turn's `free` TimeFacts, **capped at the first 3** *(ruling: plan cap; other free times stay allowed by `unknown_time`)*.
7. **ask_field:** the session opened this turn when no offer act asked it (`FIELD_QUESTION[field]`; set `asks_session`).
8. **price:** one act per identified service of the turn (`ServiceFact.identified`), and per service of the proposal act, using the catalog price (`PRICE_REPLY`).
9. **appointments:** the turn's `TimeFact` role `appointment` (all of them; `APPOINTMENTS_REPLY`).
10. **hours / busy:** `TimeFact` `shop_open` + `shop_close` (`HOURS_REPLY`), and `busy_until` (`BUSY_REPLY`).

`clocks` of an act = the clocks of the moments its sentence names. `prices` = the ranges its sentence names.

- [ ] **Step 1: Write the failing tests** in `tests/salon/guard/test_plan.py`. Use `test_db`, `seed_catalog`, `catalog_for` and the moment helpers from `tests/salon/guard`. Build plans through a helper `plan_for(db, **kwargs)`, which builds the catalog and then `build_plan`.

```python
async def test_taken_then_session_options(test_db): ...
    # TimeFact taken 15:00 + session opened this turn (field time, options 13:00, 14:00, 17:00)
    # -> kinds ["taken", "offer"]; text == "Dạ 3 giờ chiều mai đã có khách rồi ạ. Dạ còn trống 1 giờ chiều mai, "
    #    "2 giờ chiều mai hoặc 5 giờ chiều mai, chị Lan chọn giờ nào ạ?"; asks_session == "appointment"
async def test_new_proposal_names_its_service_and_suppresses_offers(test_db): ...
    # ProposalFact 14:00 service_ids ["toc-cat-nu"] + free 13:00 -> kinds ["proposal", "price"]
    # sentence "Dạ em giữ chỗ cắt tóc nữ lúc 2 giờ chiều mai cho chị Lan, chị Lan đồng ý không ạ?"
async def test_failed_then_done_order_and_announce_ids(test_db): ...
    # R0 failed + typed decision approved -> kinds ["failed", "done"]; announce_turn_ids == ["turn-0", TURN_ID]
async def test_kept_pending_is_not_re_asked(test_db): ...   # pending + DecisionFact kept -> no proposal act
async def test_clarify_pending_is_re_asked(test_db): ...
async def test_free_offers_capped_at_three(test_db): ...    # 5 free TimeFacts -> offer clocks == first 3
async def test_price_appointments_hours_busy_acts(test_db): ...
async def test_button_turn_skips_its_own_decision(test_db): ...
async def test_no_acts_means_empty_text_and_render(test_db): ...   # greeting turn -> acts == [], text() == ""
def test_render_lists_sentences_under_the_header(): ...
# ported compose_fallback_asking cases: same expectations, with {at} now the short label
```

- [ ] **Step 2: Run them to see them fail**

Run: `.venv/bin/python -m pytest -q tests/salon/guard/test_plan.py`
Expected: `ModuleNotFoundError: app.agents.salon.guard.plan`.

- [ ] **Step 3: Implement `plan.py` and the `fallback.py` changes.** `facts.py` still calls `compose_fallback_asking` today. In this task, switch `build_reply_facts` to `build_plan(...).text()`, using `GENERIC_FALLBACK` when it is empty, and take `asked_session` from the plan, so the suite stays green.

- [ ] **Step 4: Run the guard and turn tests, then the whole suite**

Run: `.venv/bin/python -m pytest -q tests/salon/guard tests/salon/turn` and then `.venv/bin/python -m pytest -q`
Expected: all pass, except the known pre-existing failure. Update the expected template strings in existing tests where `{at}` became the short label.

- [ ] **Step 5: Commit** with message `feat: build a reply plan with template sentences from the turn facts`.

---

### Task 2: Plain-text checks against the plan

**Files:**
- Modify: `app/agents/salon/guard/checks.py` (new codes and `check_reply`)
- Modify: `app/agents/salon/guard/facts.py` (`ReplyFacts` gains `plan: ReplyPlan` and `shop_service_names: list[str]`; `build_reply_facts` builds both)
- Move: `_contains_word_sequence`, `TAKEN_MARKERS`, `OFFER_WORDS` from `guard/segments.py` into `guard/checks.py`. Leave re-exports in `segments.py` until Task 3 deletes it.
- Test: `tests/salon/guard/test_checks.py`

**Interfaces:**
- Consumes: `ReplyPlan` (Task 1); `clocks_in_text`, `is_known` (`guard/clocks.py`); `scan_money`, `split_sentences`, `split_clauses`, `words` (`shared/text_scan.py`).
- Produces:
  ```python
  CHECK_CODES = ("plan_missing", "unknown_time", "unknown_price", "wrong_service", "taken_offered",
                 "claim", "missing_question", "missing_phone", "phone_not_allowed",
                 "pending_undecided", "empty", "pronoun", "foreign_script", "english", "repeat")
  MEANING_CODES = frozenset({"plan_missing", "unknown_time", "unknown_price", "wrong_service",
                             "taken_offered", "claim", "missing_question", "missing_phone",
                             "phone_not_allowed"})
  @dataclass(frozen=True)
  class GuardResult: text: str; codes: list[str]; problems: list[str]; announce_turn_ids: list[str]
  def check_reply(text: str, facts: ReplyFacts) -> GuardResult
      # text = strip_tool_text(text); announce_turn_ids = facts.plan.announce_turn_ids() when codes == []
  ```
  `check_structured` and the level C codes stay untouched until Task 3.

**Rules** (on the stripped text):
- **`plan_missing`:**
  - every clock in `facts.plan.clocks()` is said, i.e. some reading from `clocks_in_text(text)` contains it;
  - every range in `facts.plan.prices()` has an amount from `scan_money(text)` inside it.
  - Problem: `f"the reply must still tell: {act.sentence}"` for each act with a missing piece.
- **`unknown_time`:** every reading from `clocks_in_text(text)` is known in `facts.clocks` (the existing set).
- **`unknown_price`:** every amount from `scan_money(text)` lies inside a catalog `service`/`group` price range.
- **`wrong_service`:** both of these, using the word-sequence match on `words(normalize_for_match(...))`:
  - **(a)** the text names a shop service (`facts.shop_service_names`, all catalog names from `ServiceCatalogService`) that is not a catalog `service` entry of the turn;
  - **(b)** a clause containing `giữ`/`đặt`/`dời` names a service that is not in the `service_names` of the plan's proposal or done act, when such an act exists.
- **`taken_offered`:** for each taken clock not also in `facts.plan.offered_clocks()`, every sentence (`split_sentences`) that says it must contain a `TAKEN_MARKERS` sequence. The clause (`split_clauses`, commas) that says it must not contain an `OFFER_WORDS` sequence.
- `claim`, `missing_question`, `missing_phone`, `phone_not_allowed`, `pending_undecided`, `empty`, `pronoun`, `foreign_script`, `english` and `repeat` keep today's logic (from `check_text`).

- [ ] **Step 1: Write the failing tests**

```python
CASES = [  # (id, setup kwargs for facts_for, reply text, expected codes)
    ("taken_offer_ok", TAKEN15_OPTIONS, "Dạ chị Lan ơi, 3 giờ chiều mai bên em có khách mất rồi ạ. Chị lấy giúp em "
                                        "1 giờ, 2 giờ hay 5 giờ chiều mai được không ạ?", []),
    ("live5_b_turn7", TAKEN15_OPTIONS, "Dạ chị chọn giúp em 3 giờ chiều mai nhé. Chị chọn giúp em 1 giờ chiều, "
                                       "2 giờ chiều hoặc 5 giờ chiều ạ.", ["taken_offered"]),
    ("missing_option", TAKEN15_OPTIONS, "Dạ 3 giờ chiều mai có khách rồi ạ, chị lấy 1 giờ hay 2 giờ chiều nhé?",
     ["plan_missing"]),
    ("invented_time", TAKEN15_OPTIONS, "... 4 giờ chiều ...", ["plan_missing", "unknown_time"]),
    ("live4_turn8_wrong_service", PROPOSAL_CUT14, "Dạ em đã giữ chỗ sơn gel lúc 2 giờ chiều mai cho chị Mai, "
                                                  "chị đồng ý nhé?", ["wrong_service"]),
    ("proposal_ok", PROPOSAL_CUT14, "Dạ em giữ chỗ cắt tóc nữ 2 giờ chiều mai cho chị nha, chị đồng ý không ạ?", []),
    ("two_requests_ok", TAKEN15_OPTIONS_PLUS_GEL_PRICE, "... đủ giờ ... sơn gel khoảng 150 nghìn ...", []),
    ("two_requests_missing_price", TAKEN15_OPTIONS_PLUS_GEL_PRICE, "... đủ giờ, không giá ...", ["plan_missing"]),
    ("same_clock_other_day", TAKEN15_TODAY_OFFER15_TOMORROW, "Dạ chị chọn 3 giờ chiều mai nhé?", []),
    ("question_only_plan", SESSION_SERVICE_QUESTION, "Dạ chị muốn làm gì để em xếp lịch ạ?", []),
    ("greeting_no_plan", NO_FACTS, "Dạ em chào chị Lan ạ.", []),
]
@pytest.mark.parametrize("setup, reply, expected", [c[1:] for c in CASES], ids=[c[0] for c in CASES])
async def test_check_reply_cases(test_db, setup, reply, expected): ...
async def test_every_plan_template_passes_the_checks(test_db): ...
    # for every setup used in CASES and in test_plan's fixtures: no MEANING_CODES in check_reply(facts.plan.text(), facts).codes
async def test_accepted_reply_carries_the_plan_announce_ids(test_db): ...
```

(The `"..."` placeholders above are for the implementer to fill with full sentences that keep the named content. Every listed time and price must be stated explicitly.)

- [ ] **Step 2: Run them to see them fail**

Run: `.venv/bin/python -m pytest -q tests/salon/guard/test_checks.py -k "check_reply_cases or every_plan or plan_announce"`
Expected: FAIL; `check_reply` has the old signature and result.

- [ ] **Step 3: Implement** the codes, `MEANING_CODES`, `GuardResult` and `check_reply`, plus the `ReplyFacts` fields. `check_text` and `check_structured` keep working for the current middleware until Task 3.

- [ ] **Step 4: Run the guard tests, then the whole suite**

Run: `.venv/bin/python -m pytest -q tests/salon/guard tests/test_regex_budget.py` and then `.venv/bin/python -m pytest -q`
Expected: all pass, except the known pre-existing failure.

- [ ] **Step 5: Commit** with message `feat: check plain-text replies against the reply plan`.

---

### Task 3: Switch the Orchestrator to the plan and remove level C

**Files:**
- Modify: `app/agents/salon/orchestrator/agent.py` (drop `reply_format` and `response_format`)
- Modify: `app/agents/salon/orchestrator/middleware/context.py`:
  - render `facts.plan.render()` in place of `catalog.render()`;
  - drop the `StructuredOutputValidationError` catch.
- Modify: `app/agents/salon/orchestrator/middleware/reply_guard.py`:
  - use `check_reply`;
  - apply the new failure policy;
  - update `_CODE_HINTS`;
  - drop `_STRUCTURED`;
  - expose `async def reply_facts_for(db, holder, state, context) -> ReplyFacts`, used by both middlewares.
- Modify: `app/agents/salon/turn/run.py`:
  - `_mark_announced` marks `answer.announced` for every answer (model or code);
  - code answers take `facts.plan.announce_turn_ids()`;
  - drop the verbatim `spoken` matching and `structured_response` clearing in `_write_code_reply`.
- Modify: `app/agents/salon/guard/checks.py` and `facts.py`. Delete:
  - `check_structured`, `check_text` (its logic now lives in `check_reply`);
  - `RequiredRef`, `ReplyFacts.required`.
- Modify: `app/agents/salon/guard/catalog.py` (delete `render`, `CATALOG_HEADER`, `SEGMENT_ROLES`/`SegmentRole` if unused; keep the rest)
- Delete: `app/agents/salon/guard/segments.py`, `tests/salon/guard/test_segments.py`
- Modify: `tests/fakes.py` (delete `Cite`, `cite`, `reply`, `plain`, callable replies only if no test uses them after conversion), `tests/test_fakes.py`
- Modify tests:
  - `tests/salon/orchestrator/test_middleware.py`, `tests/salon/turn/test_run.py`, `tests/test_socketio_chat.py`, `tests/test_telegram_handlers.py`: convert every `reply(...)`/`cite(...)`/`plain(...)` scripted reply back to `AIMessage("<joined text>")`, keeping the same sentence. Drop `response_format=reply_format()` from `test_middleware._agent`.
  - `tests/salon/guard/test_checks.py`, `test_catalog.py`: drop level C assertions (`check_structured`, `render`).
  - `tests/salon/orchestrator/test_state.py`: keep `announced_turn_ids`.

**Failure policy in `ReplyGuardMiddleware.aafter_model`** (spec §3 and decision 6):
1. `result = check_reply(reply.text, facts)`. Invalid tool calls add `bad_tool_call` in front.
2. **No codes:** replace the content with `result.text` and write `announced_turn_ids = result.announce_turn_ids`.
3. **Rewrite once** (when `guard_rewrites == 0`, with the existing hint and tool rules) when any of these holds:
   - `pending_undecided` is among the codes;
   - **or** every code is outside `MEANING_CODES`;
   - **or** the plan has no acts.
4. **Otherwise** (a meaning code on a plan turn, or a second failure): send `fallback_reply(facts)` (the plan text, or `GENERIC_FALLBACK` with no plan) as the code reply, and write `announced_turn_ids = facts.plan.announce_turn_ids()`. Close opened sessions except `facts.asked_session`, as today.

**`_CODE_HINTS`:**
- remove the level C codes;
- add:
  - `plan_missing`: `"Tell the customer everything in the Reply plan, with every time, price and service name in it."`
  - `unknown_time`: `"Mention only times that are in the context block."`
  - `unknown_price`: `"Mention only prices inside the price ranges of this turn."`
  - `wrong_service`: `"Name only the services of this turn; a held or booked time goes with its own service."`
  - `taken_offered`: `"Tell the customer each taken time is already booked, in its own sentence, and never invite them to choose it."`

- [ ] **Step 1: Write the failing tests** in `test_middleware.py` / `test_run.py`.

```python
async def test_context_block_shows_the_reply_plan(test_db): ...
    # a turn with a taken time and session options -> the context SystemMessage contains
    # "Reply plan (tell the customer all of this, in your own words):" and "đã có khách rồi"; no "Reply facts"
async def test_meaning_error_sends_the_template_without_rewrite(test_db): ...
    # scripted reply "Dạ chị chọn giúp em 3 giờ chiều mai nhé." -> one model call only; final message is the
    # code template (salon_source "code") with "đã có khách rồi"
async def test_style_error_is_rewritten_once(test_db): ...   # wrong pronoun -> 2 model calls, second accepted
async def test_pending_undecided_rewrites_before_template(test_db): ...
    # pending proposal, typed "đồng ý"; first reply has no decide_pending call and an invented time ->
    # second call is offered decide_pending (rewrite), not the template
async def test_no_plan_meaning_error_gets_a_rewrite_then_generic(test_db): ...
async def test_orchestrator_has_no_response_format(): ...    # build_orchestrator's model bind has no response_format
# test_run.py
async def test_template_reply_marks_the_plan_announce_ids(test_db, monkeypatch): ...
async def test_accepted_model_reply_marks_the_plan_announce_ids(test_db, monkeypatch): ...
```

- [ ] **Step 2: Run them to see them fail**

Run: `.venv/bin/python -m pytest -q tests/salon/orchestrator/test_middleware.py tests/salon/turn/test_run.py -k "reply_plan or meaning_error or style_error or pending_undecided_rewrites or no_plan or response_format or announce_ids"`
Expected: FAIL; the block still shows "Reply facts" and the guard still parses JSON.

- [ ] **Step 3: Implement** the production changes above.

- [ ] **Step 4: Convert the scripted replies, then delete the level C code.** Keep every test's sentence. Where a test asserted a level C code, assert the new code that the same sentence now gets. Ledger a `Ruling:` for any test whose expectation changes for another reason.

- [ ] **Step 5: Run the affected suites, then the whole suite**

Run: `.venv/bin/python -m pytest -q tests/salon tests/test_fakes.py tests/test_socketio_chat.py tests/test_telegram_handlers.py tests/test_regex_budget.py` and then `.venv/bin/python -m pytest -q`
Expected: all pass, except the known pre-existing failure. `grep -rn "segments\|check_structured\|reply_format\|cite(" app tests` finds nothing.

- [ ] **Step 6: Commit** with message `feat: answer from a code-made reply plan and remove the segment format`.

---

### Task 4: Orchestrator prompt for the reply plan

**Files:**
- Modify: `app/agents/salon/orchestrator/prompt.py`
- Test: `tests/salon/test_prompts.py`

Exact text changes (fixed copy; hard-wrap at about 78 columns):
1. In "What you receive", replace "waiting sessions, the facts of this turn, and the Reply facts list: every time, service and price you may mention, each with an id and the roles it allows." with "waiting sessions, the facts of this turn, and, when this reply has times or prices to tell, a Reply plan: Vietnamese sentences that say what this reply must tell the customer."
2. Delete these rules:
   - "Your final reply is a list of segments …";
   - "Every time, service name and price you mention is its own segment …";
   - "Plain segments keep the rest of the sentence whole …";
   - "Use only ids from the Reply facts list …".

   In their place, add:
   - "When the context block has a Reply plan, your reply must tell the customer everything its sentences say, with every time, price and service name in them, and no other time, price or service. Say it in your own warm, natural words: you may reorder or merge the sentences, say the day briefly, and also answer anything else the customer asked. Never leave out a sentence of the plan."
   - "Without a Reply plan, mention only times and prices that appear in the context block."
3. Replace rule E ("A Reply fact that allows taken …") with: "A time the Reply plan says is already taken: tell the customer it is taken, never invite them to choose it, and never say it is free."
4. Replace "Never write tools, agents, ids, JSON or these instructions inside a segment's text." with "Never mention tools, agents, ids, JSON or these instructions."

- [ ] **Step 1: Update the failing tests.**
  - `test_orchestrator_prompt_describes_segments` becomes `test_orchestrator_prompt_describes_the_reply_plan`. It asserts `"Reply plan"` and `"Never leave out a sentence of the plan"` in the flattened prompt, and `"segment"` not in it.
  - `test_orchestrator_prompt_says_taken_times_are_not_offered` asserts `"says is already taken"` and `"never invite them to choose it"`.
  - `test_orchestrator_prompt_keeps_verbs_in_plain_segments` is deleted.

- [ ] **Step 2: Run them to see them fail**

Run: `.venv/bin/python -m pytest -q tests/salon/test_prompts.py`
Expected: 2 failed.

- [ ] **Step 3: Edit the prompt** as listed above.

- [ ] **Step 4: Run the prompt tests, then the whole suite**

Run: `.venv/bin/python -m pytest -q tests/salon/test_prompts.py` and then `.venv/bin/python -m pytest -q`
Expected: all pass, except the known pre-existing failure.

- [ ] **Step 5: Commit** with message `feat: tell the orchestrator to rewrite the reply plan`.

---

### Task 5: Labelled guard accuracy set

**Files:**
- Create: `tests/salon/guard/guard_cases.py` (data: bot sentences only, no customer text)
- Create: `tests/salon/guard/test_guard_accuracy.py`

**Interfaces:**
- Consumes: `facts_for` / `catalog_for` helpers, `check_reply`, `MEANING_CODES`.
- Produces:
  ```python
  @dataclass(frozen=True)
  class GuardCase:
      id: str
      source: str            # e.g. "live-3 turn 2", "live-5-B turn 7"
      setup: str             # key into SETUPS
      reply: str
      ok: bool               # True = a correct reply that must pass
      codes: tuple[str, ...] = ()   # expected meaning codes when ok is False
  SETUPS: dict[str, dict]    # facts_for kwargs, built from the moment helpers
  CASES: list[GuardCase]
  ALLOWED_FALSE_BLOCKS: tuple[str, ...] = ()   # ids of correct replies accepted as blocked (none at start)
  ```

**Content:**
- **Correct replies:** every model reply in `.superpowers/sdd/2026-11-08-subagents-rebuild/live-{2,3,4}.log` and `.superpowers/sdd/2026-11-11-level-c/live-5-{A,B}.log` (lines `Bot đáp (…, nguồn=llm)`) that is a correct answer for its turn, each with a minimal setup reproducing that turn's facts and plan.
- **Wrong replies:**
  - live-2 turn 2 (offering the taken 3 pm);
  - live-4 turn 8 (wrong service);
  - live-5-B turn 7;
  - the live-4 Armenian reply.

  `ok=False`, each with its expected code.
- Any live reply whose right label is unclear is left out and listed in the module docstring.

- [ ] **Step 1: Write the test**

```python
async def test_no_wrong_reply_passes(test_db): ...        # every ok=False case gets all its expected codes
async def test_false_blocks_stay_within_the_allowed_list(test_db): ...
    # every ok=True case has no MEANING_CODES unless its id is in ALLOWED_FALSE_BLOCKS;
    # print a summary line "guard accuracy: N correct passed, M blocked, K wrong caught"
```

- [ ] **Step 2: Run it.** Fix the setups, not the guard, until any remaining failure is a real guard miss or a real false block.

Run: `.venv/bin/python -m pytest -q tests/salon/guard/test_guard_accuracy.py -s`
Expected: pass. Ledger a `Ruling:` for each real false block, with the choice made: fix the rule in `checks.py` with a covering row in `test_checks.py`, or add the case to `ALLOWED_FALSE_BLOCKS` with a reason.

- [ ] **Step 3: Run the whole suite**

Run: `.venv/bin/python -m pytest -q`
Expected: all pass, except the known pre-existing failure.

- [ ] **Step 4: Commit** with message `test: add a labelled guard accuracy set from live runs`.

---

### Task 6: Docs and full verification

**Files:**
- Modify: `CONTEXT.md` (status: the plan-guided reply replaces level C; live runs pending owner approval)
- Modify: `RUNBOOK.md` §8c (the guard: plan, `check_reply`, `CHECK_CODES` exactly as in `checks.py`, meaning vs style policy, the accuracy set)

- [ ] **Step 1: Update both docs** in Vietnamese, matching the surrounding text. Every code name and file named must exist at HEAD.

- [ ] **Step 2: Run the whole backend suite and the frontend tests**

Run: `.venv/bin/python -m pytest -q` and then `cd frontend && npx vitest run`
Expected: all pass, except the known pre-existing failure.

- [ ] **Step 3: Commit** with message `docs: record the plan-guided reply guard`.

**After the plan (not a task):** ask the owner, then run live scenarios A and B. Read the transcripts against the spec's anti-goals. Count the template replies and their codes. Add the new correct and wrong replies to `guard_cases.py`.

---

### Task 2b: `asks_price` on `service_task` (added 13/11, owner approved)

Runs after Task 2, before Task 3. Spec section "Bổ sung: `asks_price`".

**Files:**
- Modify: `app/agents/salon/shared/schemas.py` (`ServiceFact.asked_price: bool = False`)
- Modify: `app/agents/salon/orchestrator/tools.py` (`service_task` gains `asks_price`)
- Modify: `app/agents/salon/guard/plan.py` (price acts for asked services)
- Test: `tests/salon/orchestrator/test_tool_rules.py` or a tools test, `tests/salon/guard/test_plan.py`, `tests/salon/guard/test_checks.py`

**Interfaces:**
- `service_task(request, continue_session, asks_price: Annotated[bool, Field(description="true when the customer asks about a price in this message; false otherwise.")], runtime)`
  - Required, no default (the tool schema stays fixed and strict-compatible).
  - When `asks_price` is true, every `ServiceFact` in the Command's `turn_facts` update gets `asked_price=True`.
- `build_plan`: price acts are built for every service with `asked_price=True`, whatever the booking acts. Without booking acts, the existing rule still prices the identified services. A service gets at most one price act.

- [ ] **Step 1: Write the failing tests**
  - `test_service_task_marks_asked_price_facts` (true → facts carry `asked_price=True`; false → False). Use the existing scripted-subagent harness for `service_task`.
  - `test_service_task_schema_requires_asks_price`: `asks_price` is in `required` of the strict OpenAI tool schema.
  - `test_asked_price_adds_a_price_act_on_a_booking_turn`: taken + options + an asked-price Sơn gel → kinds include `price`.
  - CASES row `two_requests_asked_price_missing`: the same setup, with a reply that states every time but no price → `["plan_missing"]`.
  - CASES row `two_requests_asked_price_ok`: states "sơn gel khoảng 150 nghìn" → `[]`.
- [ ] **Step 2: Run them to see them fail** (`.venv/bin/python -m pytest -q tests/salon/guard tests/salon/orchestrator -k "asked_price or asks_price"`). Expected: FAIL.
- [ ] **Step 3: Implement.** Update every scripted `service_task` call in the tests to pass `asks_price`.
- [ ] **Step 4: Run** `tests/salon`, then the whole suite. Expected: all pass, except the known pre-existing failure.
- [ ] **Step 5: Commit** with message `feat: let the orchestrator mark price questions on service_task`.
