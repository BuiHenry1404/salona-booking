# Live Run 2 Fixes (A, C, D, E, F) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stop the bot from offering a taken time as available (live run 2, turn 2). Make sure the customer is always told a time is taken. Keep Markdown from ever reaching customers.

**Architecture:**
- A code `reconcile` step on `SubagentSpec` turns the booking agent's `ok` into `need_info` when an asked time is taken and no proposal exists.
- The guard adds every taken time of the turn to `must_mention`, and the fallback reply announces the taken time.
- Prompt rules lower how often the models get this wrong.
- `strip_tool_text` removes `**` and `__` before the guard check.

**Tech Stack:** Python 3.12, LangChain v1 `create_agent`, LangGraph, pytest (Mongo `rs0` test DB), structlog.

**Spec:** `docs/superpowers/specs/2026-11-11-live-run-2-fixes-design.md`

## Global Constraints

- Prompts stay English with no Vietnamese sample sentences (`tests/salon/test_prompts.py`). Fallback replies in `guard/fallback.py` are fixed Vietnamese code strings, not prompts.
- No regex anywhere in `app/agents/salon/` (`tests/test_regex_budget.py`).
- Never run Azure (live run, probe, benchmark) without asking the owner first.
- Run pytest once at a time (shared `chatbot_test_db`): `.venv/bin/python -m pytest -q`.
- Commit messages in English, ending with the `Co-Authored-By` line from the session's attribution reminder. Never `git add` `docs/superpowers/review.md`, `image.png` or `scripts/terminal_chat.py`. Work on `feat/llm-orchestrator`; never commit to `main`.
- Prompts are hard-wrapped at about 78 columns. Every prompt fence test compares against the whitespace-flattened prompt, `" ".join(PROMPT.split())`.

## Review Focus

1. Two times in one message: the first is taken, the second is proposed.
   - The result must stay `ok` (no time session). Test: `test_ok_with_a_proposal_stays_ok` (Task 2).
   - The reply must still mention the taken time. Test: row `taken_with_proposal_needs_mention` (Task 3).
2. The customer only asks whether a taken time is free (no booking). Owner decision 11/11, option 1: this becomes `need_info` and opens a `time` session. Test: `test_availability_question_on_a_taken_time_opens_a_time_session` (Task 2).
3. The asked time is taken and the day has no free time left. The result must still become `need_info`, with the "which other time" note. Test: `test_taken_time_without_free_options_asks_for_another_time` (Task 2).
4. The booking agent already said `failed` or `need_info`. Reconcile must leave it untouched. Test: `test_reconcile_leaves_need_info_and_failed_alone` (Task 2).
5. An unpaired `**`, or `__` around a word. Every marker is removed and the rest of the text is unchanged. Test: `test_strip_tool_text_removes_an_unpaired_bold_marker` (Task 1).

---

### Task 1: No Markdown reaches the customer (C)

**Files:**
- Modify: `app/agents/salon/guard/checks.py` (`strip_tool_text`, around line 440)
- Modify: `app/agents/salon/orchestrator/prompt.py` (section "Writing the reply")
- Test: `tests/salon/guard/test_checks.py`, `tests/salon/turn/test_run.py`, `tests/salon/test_prompts.py`

**Interfaces:**
- Produces: `strip_tool_text(text: str) -> str` also drops every `**` and `__`. Its signature does not change. Single `*`, `#` headings and list bullets are deliberately **not** stripped; the prompt rule is the only defence for those.

- [ ] **Step 1: Write the failing tests**

In `tests/salon/guard/test_checks.py`, next to the other `strip_tool_text` tests:

```python
def test_strip_tool_text_removes_markdown_emphasis():
    # Live run 2: 8/10 replies had **...**; the chat shows raw characters.
    text = ("Dạ chị Lan, em đã giữ giờ **Thứ Năm 12/11, 4 giờ chiều** cho __sơn gel__ rồi ạ.  \n"
            "Chị đồng ý giờ này nhé?")
    assert strip_tool_text(text) == ("Dạ chị Lan, em đã giữ giờ Thứ Năm 12/11, 4 giờ chiều cho "
                                     "sơn gel rồi ạ.\nChị đồng ý giờ này nhé?")


def test_strip_tool_text_removes_an_unpaired_bold_marker():
    assert strip_tool_text("Dạ tiệm mở từ **8 giờ sáng ạ.") == "Dạ tiệm mở từ 8 giờ sáng ạ."
```

In `tests/salon/turn/test_run.py`, after `test_typed_turn_emits_started_tools_and_complete_without_tokens`:

```python
async def test_markdown_never_reaches_the_customer(test_db, monkeypatch):
    await default_hours(test_db)
    user = await a_user(test_db)
    orchestrator(monkeypatch, [tool_call("shop_info_task", SHOP),
                               AIMessage("Dạ tiệm mở cửa từ **8 giờ sáng tới 7 giờ tối** ạ.")])
    subagents(monkeypatch, shop_hours())

    assert completed(await turn(test_db, user, "tiệm mở cửa mấy giờ vậy em"))["answer"] == HOURS
```

In `tests/salon/test_prompts.py`, add a helper and the test. Import `ORCHESTRATOR_PROMPT` from `app.agents.salon.orchestrator.prompt`:

```python
def _flat(prompt: str) -> str:
    """Prompts are hard-wrapped; compare phrases on one line."""
    return " ".join(prompt.split())


def test_orchestrator_prompt_forbids_markdown():
    assert "no Markdown" in _flat(ORCHESTRATOR_PROMPT)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest -q tests/salon/guard/test_checks.py tests/salon/turn/test_run.py tests/salon/test_prompts.py -k "markdown or unpaired_bold"`
Expected: 4 failed.

- [ ] **Step 3: Implement**

- In `checks.py`, add `_drop_markdown(text: str) -> str`, which removes every `**` and `__` with `str.replace` (no regex). Put it first in the chain: `_drop_empty_brackets(_drop_tool_names(_drop_json(_drop_markdown(text))))`. The existing "cleaned != text → tidy each line" path also removes the trailing double space. Mention Markdown in the docstring.
- In `prompt.py`, under "Writing the reply", after the "Keep it short and plain" bullet, add this bullet (wrap it like the neighbouring lines):
  `- Write plain text only, with no Markdown (no bold, headings or lists): the chat shows every character as typed.`

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest -q tests/salon/guard tests/salon/turn/test_run.py tests/salon/test_prompts.py tests/test_regex_budget.py`
Expected: all passed.

- [ ] **Step 5: Commit**

```bash
git add app/agents/salon/guard/checks.py app/agents/salon/orchestrator/prompt.py tests/salon/guard/test_checks.py tests/salon/turn/test_run.py tests/salon/test_prompts.py
git commit -m "fix: strip Markdown emphasis from replies and ask for plain text"
```

---

### Task 2: A taken time is never reported as ok (A + D + F1)

**Files:**
- Modify: `app/agents/salon/subagents/dispatch.py` (`SubagentSpec` at line 131, `run_subagent` right after `extracted = await spec.extract(...)` at line 376)
- Modify: `app/agents/salon/subagents/appointment/agent.py` (new function and constants, `APPOINTMENT` spec)
- Modify: `app/agents/salon/subagents/appointment/prompt.py` (two bullets: the tool-error bullet and the `ok` definition)
- Test: `tests/salon/subagents/test_appointment.py`, `tests/salon/subagents/test_dispatch.py`, `tests/salon/test_prompts.py`

**Interfaces:**
- Produces in `dispatch.py`: `SubagentSpec.reconcile: Optional[Callable[[SubagentResult, Extracted], SubagentResult]] = None`. It is the last dataclass field, with a default, so the other two specs need no change.
- Produces in `appointment/agent.py`: `reconcile_appointment(result: SubagentResult, extracted: Extracted) -> SubagentResult`, plus constants (F1):
  `TAKEN_ASK_OFFERED = "The time the customer asked for is taken. Tell the customer, then ask which of the offered times they want."`
  `TAKEN_ASK_OTHER = "The time the customer asked for is taken. Tell the customer, then ask which other time they want."`
- Log event: `subagent_status_corrected`, warning level, with `extra={**log_extra, "from": <old status>, "to": <new status>}`.

- [ ] **Step 1: Write the failing unit tests for `reconcile_appointment`**

In `tests/salon/subagents/test_appointment.py`:

```python
TAKEN = Moment(iso="2026-11-12T15:00:00+07:00", spoken="Thứ Năm 12/11, 3 giờ chiều")
FREE = Moment(iso="2026-11-12T16:00:00+07:00", spoken="Thứ Năm 12/11, 4 giờ chiều")


def _result(status="ok", **fields) -> SubagentResult:
    return SubagentResult(status=status, question_field=fields.get("question_field"),
                          question_text=fields.get("question_text"),
                          error_code=fields.get("error_code"))


def test_ok_with_a_taken_time_and_no_proposal_becomes_need_info():
    # Live run 2, turn 2: propose refused 15:00 (taken), the agent still said ok.
    extracted = Extracted(facts=[TimeFact(role="asked", moment=TAKEN),
                                 TimeFact(role="taken", moment=TAKEN),
                                 TimeFact(role="free", moment=FREE)], options=[FREE])
    fixed = reconcile_appointment(_result(), extracted)
    assert (fixed.status, fixed.question_field, fixed.question_text) == (
        "need_info", "time", TAKEN_ASK_OFFERED)


def test_taken_time_without_free_options_asks_for_another_time():
    extracted = Extracted(facts=[TimeFact(role="taken", moment=TAKEN)], options=[])
    assert reconcile_appointment(_result(), extracted).question_text == TAKEN_ASK_OTHER


def test_ok_with_a_proposal_stays_ok():
    # Two times in one message: 15:00 taken, 16:00 proposed.
    extracted = Extracted(facts=[TimeFact(role="taken", moment=TAKEN)], options=[FREE],
                          proposal_created=True)
    assert reconcile_appointment(_result(), extracted) == _result()


def test_ok_without_a_taken_time_stays_ok():
    extracted = Extracted(facts=[TimeFact(role="free", moment=FREE)], options=[FREE])
    assert reconcile_appointment(_result(), extracted) == _result()


@pytest.mark.parametrize("given", [
    _result("need_info", question_field="day", question_text="Ask which day."),
    _result("failed", error_code="service_required"),
])
def test_reconcile_leaves_need_info_and_failed_alone(given):
    extracted = Extracted(facts=[TimeFact(role="taken", moment=TAKEN)], options=[FREE])
    assert reconcile_appointment(given, extracted) == given
```

Imports: `pytest`; `Moment`, `TimeFact` from `app.agents.salon.shared.schemas`; `Extracted`, `SubagentResult` from `app.agents.salon.subagents.dispatch`; `reconcile_appointment`, `TAKEN_ASK_OFFERED`, `TAKEN_ASK_OTHER` from `app.agents.salon.subagents.appointment.agent`.

- [ ] **Step 2: Write the failing integration tests through `run_subagent`**

In `tests/salon/subagents/test_dispatch.py`, after `test_need_info_opens_a_session_with_code_extracted_options`. Add `import structlog.testing` to the imports.

```python
async def _taken_at_15(db):
    await default_hours(db)
    other = await a_user(db, phone="0987654321", name="Chú Ba")
    await AppointmentService(db).create(other, tomorrow_at(15), None)
    return await a_user(db)


async def test_ok_after_a_refused_proposal_opens_a_time_session(test_db, monkeypatch):
    # Live run 2, turn 2, replayed: 15:00 is taken, the model finishes with ok.
    user = await _taken_at_15(test_db)
    use_model(monkeypatch, scripted([
        tool_call("parse_time", {"day_offset": 1, "hour": 3, "part_of_day": "chieu"}),
        tool_call("propose_appointment", {"start_at": moment(tomorrow_at(15))["iso"]}),
        result(status="ok"),
    ]))

    with structlog.testing.capture_logs() as logs:
        command = await _run(test_db, user)

    summary = _summary(command)
    assert (summary["status"], summary["question_field"]) == ("need_info", "time")
    assert summary["options"]
    session = (await _active(test_db, user))["appointment"]
    assert session.question.field == "time"
    assert [o.spoken for o in session.question.options] == summary["options"]
    assert any(log["event"] == "subagent_status_corrected" for log in logs)


async def test_availability_question_on_a_taken_time_opens_a_time_session(test_db, monkeypatch):
    # Owner 11/11, option 1: a customer who only asks whether a taken time is free
    # also gets the free times and a session, so "4 giờ đi" books next turn.
    user = await _taken_at_15(test_db)
    use_model(monkeypatch, scripted([
        tool_call("find_free_slots", {"day": tomorrow_at(15).date().isoformat(), "around": "15:00"}),
        result(status="ok"),
    ]))

    command = await _run(test_db, user, request="Is 3pm tomorrow free?")

    assert _summary(command)["status"] == "need_info"
    assert (await _active(test_db, user))["appointment"].question.field == "time"
```

- [ ] **Step 3: Write the failing prompt fence test (D)**

In `tests/salon/test_prompts.py`, using `_flat` from Task 1. Import `APPOINTMENT_PROMPT` from `app.agents.salon.subagents.appointment.prompt`.

```python
def test_appointment_prompt_never_reports_a_taken_time_as_ok():
    flat = _flat(APPOINTMENT_PROMPT)
    assert "or the information asked for is in the tool results" not in flat
    assert "turned out taken and no proposal was created, the result is never ok" in flat
```

- [ ] **Step 4: Run the tests to verify they fail**

Run each file separately. A missing import is a collection error that stops pytest before it runs the other files.
- `.venv/bin/python -m pytest -q tests/salon/subagents/test_appointment.py`. Expected: `ImportError` on `reconcile_appointment` ("1 error during collection").
- `.venv/bin/python -m pytest -q tests/salon/subagents/test_dispatch.py -k "refused_proposal or availability_question"`. Expected: 2 failed (`status == "ok"`).
- `.venv/bin/python -m pytest -q tests/salon/test_prompts.py -k taken_time_as_ok`. Expected: 1 failed.

- [ ] **Step 5: Implement**

- `dispatch.py`: add the `reconcile` field. In `run_subagent`, right after `extracted = await spec.extract(messages, ctx)`, if `result is not None and spec.reconcile is not None`, replace `result` with `spec.reconcile(result, extracted)`. When the status changed, log `subagent_status_corrected`. Everything below (status, `_write_session`, `_summary`, `AgentStatusFact`) then reads the corrected result.
- `appointment/agent.py`: `reconcile_appointment` returns `result` unchanged unless all three hold:
  - `result.status == "ok"`;
  - `not extracted.proposal_created`;
  - some fact is a `TimeFact` with `role == "taken"`.

  In that case, return `result.model_copy(update={"status": "need_info", "question_field": "time", "question_text": TAKEN_ASK_OFFERED if extracted.options else TAKEN_ASK_OTHER})`. Pass `reconcile=reconcile_appointment` in `APPOINTMENT`.
- `appointment/prompt.py`: replace the two bullets with these (wrap them like the neighbouring lines):
  - `- A tool error carries `asked`, the time that was actually checked; report about THAT time. `slot_unavailable` carries `nearest`: offer those through need_info.`
  - `- ok: the task is done: a proposal was created, or the information asked for (free times, the customer's appointments) is in the tool results. If a time the customer asked about turned out taken and no proposal was created, the result is never ok: finish need_info with question_field time.`

- [ ] **Step 6: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest -q tests/salon/subagents tests/salon/turn tests/salon/test_prompts.py`
Expected: all passed. That includes `test_second_request_is_proposed_after_the_first_is_approved` (two requests stay `ok`).

- [ ] **Step 7: Commit**

```bash
git add app/agents/salon/subagents/dispatch.py app/agents/salon/subagents/appointment/agent.py app/agents/salon/subagents/appointment/prompt.py tests/salon/subagents/test_appointment.py tests/salon/subagents/test_dispatch.py tests/salon/test_prompts.py
git commit -m "fix: turn the booking agent's ok into need_info when the asked time is taken"
```

---

### Task 3: The customer is always told a time is taken (F2 + F3)

**Files:**
- Modify: `app/agents/salon/guard/facts.py` (`build_reply_facts`: the `must_mention` block around line 323, and the `compose_fallback_asking(...)` call around line 345)
- Modify: `app/agents/salon/guard/fallback.py` (new template, `compose_fallback_asking`)
- Test: `tests/salon/guard/test_checks.py`, `tests/salon/guard/test_fallback.py`, `tests/salon/turn/test_run.py` (one existing expectation moves)

**Interfaces:**
- Produces in `fallback.py`: `TAKEN_REPLY = "Dạ {at} đã có khách rồi ạ."`, and `compose_fallback_asking(..., taken_at: Optional[Moment] = None)` (new keyword, defaults to `None`).
- Consumes: `TimeFact(role="taken")` items in `turn_facts` (from Task 2's flow; also produced today by `extract_appointment`).
- F2's match is verbatim, the same rule proposals already follow (`test_must_mention_is_verbatim`). A reply that drops the weekday/date ("3 giờ chiều mai đã kín") costs one rewrite. This is accepted; do not relax the match.

- [ ] **Step 1: Write the failing guard tests (F2)**

Add these rows to `ROWS` in `tests/salon/guard/test_checks.py`. There, `M15` and `M16` have spoken "3 giờ chiều" and "4 giờ chiều", and `_proposal` already exists.

```python
    # F2 (owner 11/11): a taken time found this turn must be mentioned.
    pytest.param(
        dict(turn_facts=[TimeFact(role="asked", moment=M15), TimeFact(role="taken", moment=M15),
                         TimeFact(role="free", moment=M16)]),
        "Dạ chị chọn 4 giờ chiều được không ạ?", ["missing_fact"], id="taken_time_not_mentioned"),
    pytest.param(
        dict(turn_facts=[TimeFact(role="asked", moment=M15), TimeFact(role="taken", moment=M15),
                         TimeFact(role="free", moment=M16)]),
        "Dạ 3 giờ chiều đã có khách rồi ạ, chị chọn 4 giờ chiều được không ạ?", [],
        id="taken_time_mentioned"),
    pytest.param(
        dict(turn_facts=[TimeFact(role="taken", moment=M15), _proposal(M16)]),
        "Dạ em giữ chỗ 4 giờ chiều cho chị, chị đồng ý không ạ?", ["missing_fact"],
        id="taken_with_proposal_needs_mention"),
```

- [ ] **Step 2: Write the failing fallback tests (F3)**

Add these rows to `FALLBACK_ROWS` in `tests/salon/guard/test_fallback.py`. There, `M15` has spoken "Thứ Ba 10/11, 3 giờ chiều". Add `TimeFact` to the schemas import.

```python
    pytest.param(
        dict(snapshot=dict(sessions={"appointment": a_session(field="time", opened_turn_id=TURN_ID)}),
             turn_facts=[TimeFact(role="taken", moment=M15)]),
        "Dạ Thứ Ba 10/11, 3 giờ chiều đã có khách rồi ạ. Dạ chị Lan muốn mấy giờ ạ?",
        id="taken_then_session_question"),
    pytest.param(
        dict(turn_facts=[TimeFact(role="taken", moment=M15),
                         ProposalFact(proposal_id="p-2", proposal_kind="book",
                                      at=at(16, spoken="Thứ Ba 10/11, 4 giờ chiều"), service_ids=[])]),
        "Dạ Thứ Ba 10/11, 3 giờ chiều đã có khách rồi ạ. "
        "Dạ em giữ chỗ Thứ Ba 10/11, 4 giờ chiều cho chị Lan, chị Lan đồng ý không ạ?",
        id="taken_then_proposal"),
```

Then update the existing expectation in `tests/salon/turn/test_run.py::test_limit_error_gives_a_code_reply_not_english`. Its turn uses `need_info_15()` (15:00 taken) and ends in the opened-session fallback, which F3 now prefixes. Import `TAKEN_REPLY` from `app.agents.salon.guard.fallback`, and change the answer assertion to:

```python
    assert answer == f"{TAKEN_REPLY.format(at=S15)} {FIELD_QUESTION['time'].format(address='chị Lan')}"
```

The R12 assertions after it (the session stays open, `ended_by == "limit"`) stay as they are.

- [ ] **Step 3: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest -q tests/salon/guard -k "taken"`
Expected: 4 failed (`taken_time_not_mentioned`, `taken_with_proposal_needs_mention`, and both fallback rows). `taken_time_mentioned` already passes.

Run: `.venv/bin/python -m pytest -q tests/salon/turn/test_run.py -k limit_error_gives_a_code_reply`
Expected: 1 failed (the answer has no taken prefix yet).

- [ ] **Step 4: Implement**

- `facts.py`: in the `must_mention` block, after the proposals line, append `f.moment.spoken` for every `TimeFact` in `turn_facts` with `role == "taken"`. This applies on typed and button turns; the existing `dict.fromkeys` dedupes. Pass `taken_at=` the first such moment (or `None`) to `compose_fallback_asking`.
- `fallback.py`: add `TAKEN_REPLY`. In `compose_fallback_asking`:
  - The failure / approved branch (`if parts:`) is unchanged.
  - The four later branches (proposal, R0 announce, opened session, generic) are prefixed with `TAKEN_REPLY.format(at=taken_at.spoken)` when `taken_at` is set, joined by a space, still through `_capitalized`.
  - The returned "asks session" flag is unchanged.
  - Update the docstring order line.

- [ ] **Step 5: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest -q tests/salon/guard tests/salon/turn tests/salon/orchestrator`
Expected: all passed, including the updated `test_limit_error_gives_a_code_reply_not_english`. The other `test_run.py` turns that use `need_info_15()` reply with `ASK_OTHER_TIME`, which already names 3 giờ chiều in its exact spoken form.

- [ ] **Step 6: Commit**

```bash
git add app/agents/salon/guard/facts.py app/agents/salon/guard/fallback.py tests/salon/guard/test_checks.py tests/salon/guard/test_fallback.py tests/salon/turn/test_run.py
git commit -m "fix: require replies and fallbacks to tell the customer a time is taken"
```

---

### Task 4: Orchestrator reads taken facts correctly (E) + handoff

**Files:**
- Modify: `app/agents/salon/orchestrator/prompt.py` (section "Writing the reply")
- Modify: `CONTEXT.md` (section "Trạng thái — 11/11/2026")
- Test: `tests/salon/test_prompts.py`

**Interfaces:**
- Consumes: the fact line format from `shared/context.py::_render_fact`, i.e. `time taken <spoken> (<iso>)`. The ToolMessage JSON uses `"role": "taken"`.

- [ ] **Step 1: Write the failing prompt fence test**

```python
def test_orchestrator_prompt_says_taken_times_are_not_offered():
    flat = _flat(ORCHESTRATOR_PROMPT)
    assert "role taken is already booked by another customer" in flat
    assert "When suggesting other times" in flat
```

- [ ] **Step 2: Run it to verify it fails**

Run: `.venv/bin/python -m pytest -q tests/salon/test_prompts.py -k taken_times`
Expected: 1 failed.

- [ ] **Step 3: Implement**

In `prompt.py`, under "Writing the reply", right after the "Copy every time and date exactly…" bullet, add this bullet (wrap it like the neighbouring lines):
`- A time fact with role taken is already booked by another customer: say so, and never offer it or say it is free. When suggesting other times, use only facts with role free or the options of a need_info result.`

- [ ] **Step 4: Run the full suites**

Run: `.venv/bin/python -m pytest -q`, then `cd frontend && npx vitest run`
Expected: pytest all passed (874 + the new tests), vitest 279 passed.

- [ ] **Step 5: Update `CONTEXT.md` status**

In "Đang dở — Task 19", record:
- live run 2 (11/11) did not pass, with the turn 2 and Markdown findings;
- A/C/D/E/F are fixed, and B was dropped for level C;
- owner decisions 11/11: option 1 (a taken asked time with no proposal is always `need_info`), and a taken time is always told to the customer;
- level C is the next design step, together with prompt-leak checking and storing guard-rejected replies.

Update the pytest count.

- [ ] **Step 6: Commit**

```bash
git add app/agents/salon/orchestrator/prompt.py tests/salon/test_prompts.py CONTEXT.md
git commit -m "fix: tell the orchestrator taken times are never offered"
```

- [ ] **Step 7: Ask the owner before live run 3**

Do not run it. Ask for approval to run `PYTHONPATH=. .venv/bin/python scripts/test_live_10_turns.py`, which wipes the dev DB and calls Azure. If approved, read the transcript by hand against the spec's acceptance list:
- turn 2 says 3 giờ is taken, asks the customer to pick a free time, and opens a `time` session;
- no reply contains `**`.
