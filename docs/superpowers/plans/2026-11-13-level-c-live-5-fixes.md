# Level C Live Run 5 Fixes Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close the failures of live run 5:
- the bot offering a taken time (scenario B, turn 7);
- the booking agent re-asking the part of day (B, turn 4);
- the copied "08:00" label;
- stiff full dates;
- replies that drop verbs.

**Architecture:** One model call per reply stays as it is (owner, 13/11). Each failure is fixed where it starts:
- a code rule on the bot's own sentence (`taken_not_told`);
- role-specific rewrite hints;
- shorter code-made catalog labels;
- one prompt line;
- a `parse_time` fallback that reads the part of day the customer typed.

**Tech Stack:** Python 3.12, LangChain 1.4.6 `create_agent` + middleware, pytest with the Mongo `rs0` test DB.

**Spec:** `docs/superpowers/specs/2026-11-11-level-c-grounded-reply-design.md`, section "Bổ sung sau lần chạy thật 5 (13/11)" (items 1–6).

## Global Constraints

- No regex anywhere in `app/agents/salon/` (`tests/test_regex_budget.py`).
- Prompts stay English, with no Vietnamese characters (`tests/salon/test_prompts.py`). Rewrite hints are English too.
- Word lists in the guard are grammar words of the **bot's** sentence; never guess the customer's intent (CONTEXT.md trap 21).
- The reply schema and `ProviderStrategy(REPLY_SCHEMA, strict=True)` are unchanged. No extra model call.
- Code templates in `guard/fallback.py` stay unchanged and keep using `Moment.spoken`.
- No Azure call (live run, probe) without the owner's explicit approval. Never run `scripts/test_live_10_turns.py` main in tests.
- Run pytest once at a time (shared test DB): `.venv/bin/python -m pytest -q <paths>`. Known pre-existing failure, not this plan's: `tests/salon/subagents/test_shop_info.py::test_parse_then_is_open_at_answers_a_relative_question`.
- Commit messages in English. Never `git add` `docs/superpowers/review.md`, `image.png` or `scripts/terminal_chat.py`. Work on `feat/llm-orchestrator`; check `git branch --show-current` before each commit (another session shares this working tree).

## Review Focus

1. **A taken time and an offer in one sentence, split by a comma**, e.g. "[3 giờ chiều] đã kín, chị chọn [1 giờ chiều] nhé?". This must pass: the taken clause has no offer word. Test row `taken_then_offer_same_sentence` (Task 1).
2. **"em chưa đặt được lúc [t1] vì đã có khách rồi"** (live run 4, turn 2). "đặt" is not an offer word, and "có khách" is in the same sentence. It must pass. Test row `not_booked_because_taken` (Task 1).
3. **A label for a moment after midnight or on a later day** (e.g. Thứ Hai). It keeps the full `spoken`; only today and tomorrow are shortened. Test `test_label_keeps_spoken_for_other_days` (Task 2).
4. **The customer typed two different periods for the same hour** ("8 giờ sáng hay 8 giờ tối"). `parse_time` must not pick one and still reports `part_of_day` missing. Test `test_parse_time_keeps_asking_when_periods_conflict` (Task 4).
5. **A button turn** (`question == ""`). `parse_time` behaves exactly as today. Test `test_parse_time_without_customer_text_is_unchanged` (Task 4).

---

### Task 1: `taken_not_told` and role-specific rewrite hints

**Files:**
- Modify: `app/agents/salon/guard/segments.py` (new function and word lists)
- Modify: `app/agents/salon/guard/checks.py` (`CHECK_CODES`, `check_structured`)
- Modify: `app/agents/salon/guard/facts.py` (`RequiredRef`: rename `why` to `todo`, with per-role text)
- Modify: `app/agents/salon/orchestrator/middleware/reply_guard.py` (`_CODE_HINTS`)
- Test: `tests/salon/guard/test_segments.py`, `tests/salon/guard/test_checks.py`, `tests/salon/orchestrator/test_middleware.py` (only where hint text is asserted)

**Interfaces:**
- Produces:
  ```python
  TAKEN_MARKERS: tuple[tuple[str, ...], ...] = (("kín",), ("có", "khách"), ("có", "người"), ("được", "đặt"),
      ("hết", "chỗ"), ("không", "còn", "trống"), ("không", "trống"), ("bận",))
  OFFER_WORDS: tuple[tuple[str, ...], ...] = (("chọn",), ("lấy",), ("đồng", "ý"))
  def taken_not_told(segments: Sequence[Segment], catalog: FactCatalog) -> list[str]
      # refs cited as taken whose sentence breaks the rule; [] when fine
  class RequiredRef(BaseModel): ref: str; roles: list[str]; todo: str
  CHECK_CODES = ("bad_structure", "unknown_ref", "wrong_role", "ref_mismatch", "unreferenced",
                 "taken_not_told", "empty", "claim", "missing_fact", "missing_question", "missing_phone",
                 "phone_not_allowed", "pending_undecided", "pronoun", "foreign_script", "english", "repeat")
  ```

**Rules for `taken_not_told`:**
1. Walk the segments in order. Track sentences: a sentence ends after a plain segment's text whose last non-space character is `.`, `?`, `!` or `…`. Time and price segments never hold those characters.
2. For each sentence that contains a segment citing a known ref with role `taken`:
   - **(a) Taken marker.** The sentence's plain text must contain one of `TAKEN_MARKERS` as a contiguous word sequence of `words(normalize_for_match(...))`. Reuse `_contains_word_sequence`.
   - **(b) No offer word.** The clause holding the taken segment must not contain any of `OFFER_WORDS`. The clause is the plain text from the last `,` / `;` before the segment to the first `,` / `;` after it, within the sentence.
3. Every taken ref that breaks (a) or (b) is returned.
4. In `check_structured`, a non-empty result adds `taken_not_told`, with one problem per ref: `f"{ref} is cited as taken but the sentence does not tell the customer it is already booked, or invites them to choose it"`.

**`RequiredRef.todo`**, built in `facts.py` `_required_refs`. It replaces `why`, and the problem line becomes `f"{item.ref}: {item.todo}"`:

| roles | todo |
|---|---|
| `["taken"]` | `"already booked by another customer: say so in its own sentence and never ask the customer to choose it"` |
| `["held"]` | `"the time being held: state it and ask whether the customer agrees"` |
| `["booked"]` / `["moved"]` / `["cancelled"]` | `"booked in this turn: tell the customer"` / `"moved in this turn: tell the customer"` / `"cancelled in this turn: tell the customer"` |
| `["failed"]` | `"could not be written: tell the customer"` |

**`_CODE_HINTS`:**
- `missing_fact` → `"Do what each line below says for the listed facts, citing each by its id."`
- add `taken_not_told` → `"Tell the customer in its own sentence that each taken time is already booked, and never invite them to choose it."`

- [ ] **Step 1: Write the failing tests.** In `test_segments.py`, use the existing catalog fixture: `t1` taken 15:00, `t2` offer 13:00.

```python
TAKEN_TABLE = [  # (row id, segments as (text, ref, role) tuples, expected taken_not_told refs)
    ("live5_b_turn7", [("Dạ chị chọn giúp em ", None, None), ("3 giờ chiều", "t1", "taken"),
                       (" nhé. Chị chọn giúp em ", None, None), ("1 giờ chiều", "t2", "offer"),
                       (" ạ.", None, None)], ["t1"]),
    ("live5_a_turn2", [("Dạ chị Lan, giờ ", None, None), ("3 giờ chiều", "t1", "taken"),
                       (" đã kín rồi ạ. Chị chọn giúp em ", None, None), ("1 giờ chiều", "t2", "offer"),
                       (" nhé?", None, None)], []),
    ("co_khach_dat", [("3 giờ chiều mai", "t1", "taken"), (" đã có khách đặt rồi chị ạ.", None, None)], []),
    ("co_nguoi_dat", [("3 giờ chiều mai", "t1", "taken"), (" đã có người đặt rồi ạ.", None, None)], []),
    ("taken_then_offer_same_sentence", [("3 giờ chiều", "t1", "taken"), (" đã kín, chị chọn ", None, None),
                                        ("1 giờ chiều", "t2", "offer"), (" nhé?", None, None)], []),
    ("not_booked_because_taken", [("Dạ em chưa đặt được lúc ", None, None), ("3 giờ chiều", "t1", "taken"),
                                  (" vì đã có khách rồi ạ.", None, None)], []),
    ("taken_no_marker", [("Dạ ", None, None), ("3 giờ chiều", "t1", "taken"), (" nha chị.", None, None)], ["t1"]),
    ("taken_in_offer_clause", [("Chị lấy ", None, None), ("3 giờ chiều", "t1", "taken"),
                               (" đi, giờ đó kín rồi ạ.", None, None)], ["t1"]),
]
@pytest.mark.parametrize("segments, expected", [r[1:] for r in TAKEN_TABLE], ids=[r[0] for r in TAKEN_TABLE])
def test_taken_not_told_table(catalog, segments, expected): ...
```

In `test_checks.py`:
- `test_check_structured_flags_taken_not_told`: the `live5_b_turn7` reply through `check_structured` gives `"taken_not_told"` in its codes, plus the problem line.
- `test_required_taken_todo_says_own_sentence`: a facts with a taken TimeFact. The `required` item for it has `todo` starting with `"already booked by another customer"`. The missing-fact problem is `f"{ref}: already booked …"`.
- Update the existing tests that assert `why` or the old `"cite … as …"` problem text.

- [ ] **Step 2: Run them to see them fail**

Run: `.venv/bin/python -m pytest -q tests/salon/guard/test_segments.py tests/salon/guard/test_checks.py`
Expected: FAIL with `ImportError: taken_not_told`, and assertion failures on `todo`.

- [ ] **Step 3: Implement** the interfaces and rules above.

- [ ] **Step 4: Run the guard and middleware tests**

Run: `.venv/bin/python -m pytest -q tests/salon/guard tests/salon/orchestrator/test_middleware.py tests/test_regex_budget.py`
Expected: all pass.

- [ ] **Step 5: Commit** with message `fix: require taken times to be told as taken and give role-specific rewrite hints`.

---

### Task 2: Shorter time labels and readable customer-clock labels

**Files:**
- Modify: `app/agents/salon/guard/catalog.py` (time labels, `c*` labels)
- Test: `tests/salon/guard/test_catalog.py`. Also update other tests that cite by a full spoken label, such as `tests/fakes.py` users with `find=`, only if they break.

**Interfaces:**
- Produces: `def short_label(moment: Moment, today: date) -> str` in `catalog.py`.
  - The moment's local date equals `today` → `f"{format_vi_hhmm(HH:MM)} hôm nay"`.
  - It equals `today + 1 day` → `f"{format_vi_hhmm(HH:MM)} mai"`.
  - Otherwise → `moment.spoken`. An unparsable iso also gives `moment.spoken`.
- `build_catalog` uses `short_label(moment, snapshot.today)` as the label of every time entry (the first appearance still fixes it). Shop-hours entries and their labels are unchanged.
- A customer-clock label is `"customer said " + " or ".join(format_vi_hhmm(f"{h:02d}:{m:02d}") for (h, m) in sorted(reading))`, for example `"customer said 8 giờ sáng"` or `"customer said 3 giờ sáng or 3 giờ chiều"`.

- [ ] **Step 1: Write the failing tests**

```python
def test_label_is_short_for_tomorrow(): ...   # today 2026-11-09, moment 2026-11-10 15:00 -> "3 giờ chiều mai"
def test_label_is_short_for_today(): ...      # same day -> "3 giờ chiều hôm nay"
def test_label_keeps_spoken_for_other_days(): ...   # 2026-11-12 09:00 spoken "Thứ Năm 12/11, 9 giờ sáng" -> spoken
async def test_customer_clock_label_is_spoken_by_code(test_db): ...
    # question "8 giờ sáng đi em" -> "customer said 8 giờ sáng"; "3 giờ nha" -> "customer said 3 giờ sáng or 3 giờ chiều"
```

Then update the existing catalog tests whose expected labels change, e.g. `"3 giờ chiều"` → `"3 giờ chiều mai"` because the snapshot's today is 9/11 and `at()` is on 10/11.

- [ ] **Step 2: Run them to see them fail**

Run: `.venv/bin/python -m pytest -q tests/salon/guard/test_catalog.py`
Expected: FAIL; the labels still use the full spoken form, and `c*` shows "03:00 or 15:00".

- [ ] **Step 3: Implement** `short_label` and the two label changes.

- [ ] **Step 4: Run the affected suites**

Run: `.venv/bin/python -m pytest -q tests/salon/guard tests/salon/orchestrator tests/salon/turn/test_run.py tests/test_fakes.py`
Expected: all pass. Fix any `cite(..., find=...)` that relied on the old long label by using a `find` that is a substring of the new label.

- [ ] **Step 5: Commit** with message `fix: show short code-made labels for reply facts`.

---

### Task 3: Prompt keeps the rest of the sentence

**Files:**
- Modify: `app/agents/salon/orchestrator/prompt.py`
- Test: `tests/salon/test_prompts.py`

Insert this rule right after the rule "Every time, service name and price you mention is its own segment …". It is fixed copy; hard-wrap at about 78 columns:

"- Plain segments keep the rest of the sentence whole, including its verbs and linking words; only the time, the service name or the price itself moves into its own segment."

- [ ] **Step 1: Write the failing test**

```python
def test_orchestrator_prompt_keeps_verbs_in_plain_segments():
    assert "Plain segments keep the rest of the sentence whole" in _flat(ORCHESTRATOR_PROMPT)
```

- [ ] **Step 2: Run it to see it fail**

Run: `.venv/bin/python -m pytest -q tests/salon/test_prompts.py`
Expected: 1 failed.

- [ ] **Step 3: Add the rule.**

- [ ] **Step 4: Run the prompt tests**

Run: `.venv/bin/python -m pytest -q tests/salon/test_prompts.py`
Expected: all pass, including the no-Vietnamese test.

- [ ] **Step 5: Commit** with message `fix: tell the orchestrator to keep verbs in plain segments`.

---

### Task 4: `parse_time` takes the part of day the customer typed

**Files:**
- Modify: `app/agents/salon/subagents/time_tool.py`
- Test: `tests/salon/subagents/test_time_tool.py` (create). Build a `ToolRuntime` the way `tests/salon/subagents/__init__.py:52` does.

**Interfaces:**
- `parse_time` gains a last parameter `runtime: ToolRuntime[Any]`. It is injected, so the model-facing schema is unchanged; assert that in a test.
  - When `part_of_day is None` and `1 <= hour <= 12`, read `question = getattr(runtime.context, "question", "") or ""`.
  - Collect the periods of `scan_times(question)` entries whose hour equals `hour`. Map them through `strip_diacritics` to `"sang"`, `"trua"`, `"chieu"` or `"toi"`, and drop any other value.
  - Exactly one distinct period → use it as `part_of_day`.
  - Zero or several periods → behave exactly as today.
- Exported helper for the test: `def period_from_customer(question: str, hour: int) -> Optional[str]`.

- [ ] **Step 1: Write the failing tests**

```python
def test_period_from_customer_reads_the_typed_period():
    assert period_from_customer("8 giờ sáng đi em", 8) == "sang"
    assert period_from_customer("1 giờ chiều đi em", 1) == "chieu"
def test_parse_time_keeps_asking_when_periods_conflict():
    assert period_from_customer("8 giờ sáng hay 8 giờ tối", 8) is None
def test_parse_time_without_customer_text_is_unchanged(): ...   # question "" -> None
async def test_parse_time_uses_the_typed_period_when_the_model_omits_it(): ...
    # runtime context question "8 giờ sáng đi em"; parse_time(day_offset=1, hour=8) returns an iso at 08:00 local
async def test_parse_time_schema_hides_runtime(): ...   # "runtime" not in parse_time's model-facing args schema
```

- [ ] **Step 2: Run them to see them fail**

Run: `.venv/bin/python -m pytest -q tests/salon/subagents/test_time_tool.py`
Expected: `ImportError` on `period_from_customer`.

- [ ] **Step 3: Implement.** Check how `ToolRuntime` is injected into the appointment tools (`appointment/tools.py:287`) and follow the same pattern.

- [ ] **Step 4: Run the subagent tests**

Run: `.venv/bin/python -m pytest -q tests/salon/subagents tests/test_regex_budget.py`
Expected: all pass, except the known pre-existing `test_shop_info.py` failure.

- [ ] **Step 5: Commit** with message `fix: let parse_time use the part of day the customer typed`.

---

### Task 5: Status and full verification

**Files:**
- Modify: `CONTEXT.md` (status: live run 5 result and these fixes; live runs pending owner approval)
- Modify: `RUNBOOK.md` section 8c (add `taken_not_told` to the code list, in the `CHECK_CODES` order)

- [ ] **Step 1: Update both docs** in Vietnamese, matching the surrounding text.

- [ ] **Step 2: Run the whole backend suite**

Run: `.venv/bin/python -m pytest -q`
Expected: all pass, except the known pre-existing failure.

- [ ] **Step 3: Commit** with message `docs: record live run 5 and its fixes`.

**After the plan (not a task):** ask the owner, then run live scenarios A and B again. Read the transcripts for:
- B turn 7: the taken time is told as taken and never offered;
- B turn 4: no part-of-day question after "8 giờ sáng";
- no "08:00";
- shorter dates;
- no verbless replies.

Count the template fallbacks and the rewrites by code.
