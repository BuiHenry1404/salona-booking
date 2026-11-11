# Level C Grounded Reply Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** The Orchestrator's final reply becomes strict-JSON segments. Every time, service and price in it cites a turn fact by id and role, and code checks the id, the role and the words.

**Architecture:**
- A code-built **fact catalog** (`guard/catalog.py`) gives every fact the reply may mention an id (`t*`, `s*`, `g*`, `c*`) and the roles it allows.
- `ContextMiddleware` shows the catalog in the context block. The same function rebuilds it in the guard, so ids match.
- `create_agent` gets one fixed `ProviderStrategy(REPLY_SCHEMA, strict=True)`.
- The guard checks the segments:
  1. parse JSON;
  2. check id, role and words per segment;
  3. join the texts and strip tool text;
  4. run the text rules (`claim`, `pronoun`, …, `foreign_script`, `english`);
  5. store the joined text and pass the cited decision ids on to `_mark_announced`.

**Tech Stack:** Python 3.12, LangChain 1.4.6 `create_agent` + middleware, LangGraph 1.2, langchain-openai 1.7.2 (Azure), pytest with the Mongo `rs0` test DB.

**Spec:** `docs/superpowers/specs/2026-11-11-level-c-grounded-reply-design.md`

## Global Constraints

- No regex anywhere in `app/agents/salon/` (`tests/test_regex_budget.py`).
- Prompts stay English, with no Vietnamese characters and no Vietnamese sample sentences (`tests/salon/test_prompts.py`, CONTEXT.md traps 17–19). Prompts are hard-wrapped at about 78 columns, and prompt tests compare against `" ".join(PROMPT.split())`.
- Graph state values are plain JSON, never pydantic objects (`orchestrator/state.py`).
- The reply schema is identical on every call:
  - `ref` is a free string or null;
  - `role` is the fixed enum `SEGMENT_ROLES`;
  - the format is passed explicitly as `ProviderStrategy(REPLY_SCHEMA, strict=True)`. Never use `ToolStrategy` or a raw schema;
  - never add a separate model call to format the reply.
- Azure validity (trap 13): never a tool call without its ToolMessage. Never send an empty `tools` list together with a `response_format`; verified offline, langchain-openai sends `"tools": []` in that case.
- Guard-rejected replies are only logged (`rejected_reply`), never stored in the DB. Logs never carry customer text.
- No Azure call (live run, probe) without the owner's explicit approval. `scripts/test_live_10_turns.py` without `--keep-db` wipes the dev DB.
- Unchanged: subagents, the button API, the `guard/fallback.py` templates, REST, Telegram, frontend.
- Run pytest once at a time (shared test DB): `.venv/bin/python -m pytest -q <paths>`.
- Commit messages in English, ending with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`. Never `git add` `docs/superpowers/review.md`, `image.png` or `scripts/terminal_chat.py`. Work on `feat/llm-orchestrator`; never commit to `main`.

## Review Focus

1. **A guard rewrite turn with no allowed tool.** Today the rewrite call hides every tool. With a `response_format` bound, langchain-openai then sends `tools: []`, which OpenAI-compatible APIs reject. The rewrite must keep the offered tools and set `tool_choice="none"`. Test: `test_rewrite_turn_forbids_tool_calls` (Task 7).
2. **Two facts at the same instant.** Example: the customer asked for 3 pm and 3 pm is taken. That gives one id with roles `[asked, taken]`; citing it as `offer` must still be `wrong_role`. Test: `test_same_instant_union_still_rejects_offer` (Task 2).
3. **Segment text arriving in NFD** (combining accents, which some keyboards and paste paths produce). Neither `foreign_script` nor `english` may fire. Test: `test_nfd_vietnamese_is_not_foreign_or_english` (Task 4).
4. **`{"segments": []}`, or segments whose texts are only whitespace.** The result is `empty`, not a crash and not `bad_structure`. Test: `test_empty_segment_list_is_empty` (Task 3).
5. **Price spelled "150k", "khoảng 150 nghìn", "120-180 nghìn" or "150.000đ"** inside the 120.000–180.000 range. All pass; 200 nghìn does not. Test rows `price_*` in `test_check_segments_table` (Task 2).

---

### Task 1: Clock helpers and the fact catalog

**Files:**
- Create: `app/agents/salon/guard/clocks.py`
- Create: `app/agents/salon/guard/catalog.py`
- Modify: `app/agents/salon/guard/facts.py`. Move code out and import it back:
  - the clock helpers `_PERIOD_HOURS`, `clock_of_24h`, `readings`, `clocks_in_text`, `is_known`, `_clocks_of_spoken`, `_clocks_of_moment`, `_clocks_of_hhmm`, `_customer_clocks`;
  - `CLAIM_FOR_KIND`, `_Decision`, `_decisions`, `_services_by_id`.
- Modify: `tests/salon/guard/__init__.py` (add the `catalog_for` helper)
- Test: `tests/salon/guard/test_catalog.py`

**Interfaces:**
- Produces (`guard/clocks.py`, moved unchanged apart from public names):
  ```python
  ClockKey = tuple[int, int, Optional[str]]
  Reading = frozenset[tuple[int, int]]
  def clock_of_24h(hour: int, minute: int) -> ClockKey
  def readings(hour: int, period: Optional[str], minute: Optional[int]) -> Reading
  def clocks_in_text(text: str) -> list[Reading]
  def is_known(said: Reading, known: Iterable[ClockKey]) -> bool
  def clocks_of_spoken(spoken: Optional[str]) -> set[ClockKey]
  def clocks_of_moment(moment: Optional[Moment]) -> set[ClockKey]
  def clocks_of_hhmm(hhmm: str) -> set[ClockKey]
  def customer_clocks(question: str) -> set[ClockKey]
  ```
  `facts.py` re-exports `ClockKey, Reading, clock_of_24h, readings, clocks_in_text, is_known`, because `checks.py` and tests import them from there.
- Produces (`guard/catalog.py`):
  ```python
  SegmentRole = Literal["offer", "taken", "held", "booked", "moved", "cancelled", "appointment",
                        "asked", "hours", "service", "price", "failed", "released", "busy"]
  SEGMENT_ROLES: tuple[str, ...]            # typing.get_args(SegmentRole), in that order
  ANNOUNCE_ROLES = frozenset({"booked", "moved", "cancelled", "failed"})
  CLAIM_FOR_KIND = {"book": "booked", "move": "moved", "cancel": "cancelled"}
  CATALOG_HEADER = "Reply facts (cite by id):"
  class Decision(BaseModel): outcome: str; kind: str; at: Moment
  def turn_decisions(turn_facts, pending_outcome) -> list[Decision]       # was facts._decisions
  async def services_by_id(catalog: ServiceCatalogService, ids: list[str]) -> dict[str, Service]
  def format_price(low: int, high: int) -> str                            # "120.000đ-180.000đ"
  class CatalogEntry(BaseModel):
      id: str
      kind: Literal["time", "service", "group", "customer_clock"]
      roles: list[str]                      # SegmentRole values, first-added order, unique
      label: str
      instant: Optional[str] = None         # time: UTC ISO of the moment; hours: "hours:08:00"
      clocks: list[tuple[int, int]] = []    # time / customer_clock: 24h values the words may say
      names: list[str] = []                 # service: [name]; group: member names
      price: Optional[tuple[int, int]] = None
      service_ids: list[str] = []           # service: [id]; group: ids; time cited as held: its proposal's ids
      announce_turn_ids: list[str] = []     # ledger turn ids to mark announced (see roles below)
  class FactCatalog(BaseModel):
      entries: list[CatalogEntry]
      def get(self, ref: str) -> Optional[CatalogEntry]
      def id_of(self, moment: Moment) -> Optional[str]   # by instant
      def render(self) -> str
  async def build_catalog(db, *, snapshot: TurnSnapshot, turn_facts: list[TurnFact],
                          pending_outcome: Optional[PendingOutcome], question: str,
                          turn_id: str) -> FactCatalog
  ```
- Produces (`tests/salon/guard/__init__.py`): `async def catalog_for(db, *, snapshot=None, turn_facts=(), pending_outcome=None, question="") -> FactCatalog`, which calls `build_catalog` with `TURN_ID`.

**How the catalog is built** (no signature or test can fix this; implement it exactly):

1. **Times.** Walk the sources in the order below. Merge every source with the same instant (the UTC ISO of `datetime.fromisoformat(moment.iso)`; skip a moment whose iso does not parse) into one entry. The first appearance fixes the number and the label (that moment's `spoken`). Roles add up. `clocks` = the `[:2]` of `clocks_of_moment`.
   1. `turn_facts`, in order:
      - `TimeFact`, by role: `free→offer`, `taken→taken`, `held→held`, `appointment→appointment`, `asked→asked`, `at→asked`, `shop_open→hours`, `shop_close→hours`, `busy_until→busy`.
      - `ProposalFact`: `held`, and its `service_ids` go into the entry's `service_ids`.
      - `DecisionFact`, by outcome:
        - `approved` → `CLAIM_FOR_KIND[kind]`, plus `announce_turn_ids=[turn_id]`;
        - `failed` → `failed`, plus `announce_turn_ids=[turn_id]`;
        - `not_approvable` → `failed`;
        - `rejected` and `superseded` → `released`;
        - `kept` and `clarify` → `held`.
   2. `pending_outcome`, only when `turn_facts` has no `DecisionFact`: its `at` uses the same outcome rules; its `nearest` → `offer`.
   3. `snapshot.pending` → `held` (plus its `service_ids`), only when every decision from `turn_decisions` is `kept` or `clarify`, or there is none.
   4. `snapshot.unannounced`: `approved` → `CLAIM_FOR_KIND[proposal_kind]`; `failed` → `failed`. Both get `announce_turn_ids=[unannounced.turn_id]`.
   5. `snapshot.upcoming` → `appointment`.
   6. Every waiting session's `question.options` → `offer`.
2. **Shop hours.** `open_time` and `close_time` become two more time entries, numbered after the times above:
   - roles `["hours"]`;
   - labels `f"opens {format_vi_hhmm(open_time)}"` and `f"closes {format_vi_hhmm(close_time)}"`;
   - `instant` `"hours:" + hhmm`;
   - clocks from `clocks_of_hhmm`.
3. **Customer clocks.** Take each distinct reading of `clocks_in_text(question)`, in order:
   - kind `customer_clock`, roles `["asked"]`;
   - `clocks=sorted(reading)`;
   - label `"customer said " + " or ".join(f"{h:02d}:{m:02d}")`. Never put customer words in the label.
   - A button turn has `question == ""`, so it has none.
4. **Services.** Collect distinct service ids in this order: `ServiceFact`s in `turn_facts` → `snapshot.known_services` → `pending.service_ids` → each `ProposalFact.service_ids` → each `upcoming[].service_ids` → each session's `memory["service_ids"]` (list entries that are strings only).
   - Name and price come from the `ServiceFact` when there is one, else from `services_by_id`. Skip an id found in neither.
   - roles `["service", "price"]`, label `f"{name}, {format_price(low, high)}"`.
5. **Groups.** For each multi-service list (pending, proposals, upcoming, sessions) with at least 2 distinct ids, all resolved:
   - roles `["price"]`;
   - `price = await ServiceCatalogService(db).price_range(ids)`;
   - label `f"{' + '.join(names)}, {format_price(low, high)}"`.
   - Skip duplicate id sets.
6. **Numbering:** `t1…` (times, then hours), `s1…`, `g1…`, `c1…`.
7. **`render()`:** `CATALOG_HEADER`, then one line per entry: `f"- {id} [{', '.join(roles)}]: {label}"`. With no entries, the header is followed by `- none`.

- [ ] **Step 1: Write the failing tests** in `tests/salon/guard/test_catalog.py`. Use the `test_db` fixture and `seed_catalog`; use `at`, `pending_at`, `appointment_at`, `r0`, `a_session` and `a_snapshot` from `tests.salon.guard`.

```python
async def test_same_instant_merges_roles(test_db):
    cat = await catalog_for(test_db, turn_facts=[TimeFact(role="asked", moment=at(15)),
                                                 TimeFact(role="taken", moment=at(15))])
    [t] = [e for e in cat.entries if e.kind == "time" and e.instant and not e.instant.startswith("hours")]
    assert (t.id, t.roles, t.label, t.clocks) == ("t1", ["asked", "taken"], "3 giờ chiều", [(15, 0)])

async def test_free_time_and_session_option_are_offer(test_db): ...
    # TimeFact free 13:00 -> t1 ["offer"]; session option 14:00 -> t2 ["offer"]
async def test_new_proposal_is_held_with_its_services(test_db): ...
    # ProposalFact 16:00 service_ids ["nail-gel"] -> roles ["held"], service_ids ["nail-gel"]
async def test_pending_is_held_only_while_undecided(test_db): ...
    # pending 15:00 + DecisionFact approved book 15:00 -> roles ["booked"], announce_turn_ids [TURN_ID]
    # pending 15:00 + DecisionFact kept 15:00 -> roles ["held"], announce_turn_ids []
async def test_r0_is_announced_with_its_own_turn(test_db): ...
    # r0(at(9)) -> ["booked"], ["turn-0"];  r0(at(9), outcome="failed") -> ["failed"], ["turn-0"]
async def test_rejected_and_superseded_decisions_are_released(test_db): ...
async def test_upcoming_appointment_role(test_db): ...
async def test_shop_hours_entries(test_db): ...
    # labels "opens 8 giờ sáng" / "closes 7 giờ tối", roles ["hours"], numbered after the times
async def test_customer_clock_has_both_readings(test_db): ...
    # question "3 giờ nha em" -> c1 clocks [(3, 0), (15, 0)], label "customer said 03:00 or 15:00"
    # question "" -> no customer_clock entry
async def test_services_come_from_facts_then_catalog(test_db): ...
    # ServiceFact(GEL) + pending service_ids ["toc-goi-say"] ->
    #   s1 label "Sơn gel, 120.000đ-180.000đ", s2 "Gội sấy, 50.000đ-80.000đ", roles ["service", "price"]
async def test_group_price_for_a_multi_service_pending(test_db): ...
    # pending ["nail-gel", "toc-goi-say"] -> g1 roles ["price"],
    #   price == await ServiceCatalogService(test_db).price_range(["nail-gel", "toc-goi-say"])
def test_render_lists_ids_roles_and_labels():
    cat = FactCatalog(entries=[
        CatalogEntry(id="t1", kind="time", roles=["taken"], label="Thứ Năm 12/11, 3 giờ chiều"),
        CatalogEntry(id="s1", kind="service", roles=["service", "price"], label="Sơn gel, 120.000đ-180.000đ")])
    assert cat.render() == ("Reply facts (cite by id):\n- t1 [taken]: Thứ Năm 12/11, 3 giờ chiều\n"
                            "- s1 [service, price]: Sơn gel, 120.000đ-180.000đ")
async def test_catalog_is_deterministic(test_db): ...   # two builds from the same inputs are equal
async def test_id_of_finds_a_moment_by_instant(test_db): ...
    # id_of(Moment(iso=same instant, spoken="khác")) == "t1"
```

- [ ] **Step 2: Run them to see them fail**

Run: `.venv/bin/python -m pytest -q tests/salon/guard/test_catalog.py`
Expected: collection error, `ModuleNotFoundError: app.agents.salon.guard.catalog`.

- [ ] **Step 3: Create `guard/clocks.py`** by moving the helpers listed above from `facts.py`, under the public names in Interfaces. Make `facts.py` import them, keep its re-exports, and replace its internal calls.

- [ ] **Step 4: Create `guard/catalog.py`** as specified above. Move `CLAIM_FOR_KIND`, `_Decision` (as `Decision`), `_decisions` (as `turn_decisions`) and `_services_by_id` (as `services_by_id`) out of `facts.py`, and import them back into `facts.py`.

- [ ] **Step 5: Run the catalog tests and the existing guard tests**

Run: `.venv/bin/python -m pytest -q tests/salon/guard`
Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add app/agents/salon/guard/clocks.py app/agents/salon/guard/catalog.py app/agents/salon/guard/facts.py tests/salon/guard/__init__.py tests/salon/guard/test_catalog.py
git commit -m "feat: build a fact catalog with ids and roles for the reply"
```

---

### Task 2: Reply schema, parsing, joining and per-segment checks

**Files:**
- Create: `app/agents/salon/guard/segments.py`
- Test: `tests/salon/guard/test_segments.py`

**Interfaces:**
- Consumes: `FactCatalog`, `CatalogEntry`, `SEGMENT_ROLES` (Task 1), `clocks_in_text` (Task 1).
- Produces:
  ```python
  REPLY_SCHEMA: dict   # exact shape below
  class Segment(BaseModel): text: str; ref: Optional[str]; role: Optional[str]
  def parse_reply(raw: str) -> Optional[list[Segment]]   # None: not the schema
  def join_segments(segments: Sequence[Segment]) -> str
  TIME_WORDS: frozenset[str]
  @dataclass
  class SegmentReport:
      codes: set[str]                      # subset of bad_structure, unknown_ref, wrong_role, ref_mismatch, unreferenced
      problems: list[str]                  # English, one per finding, for the rewrite hint
      cited: list[tuple[str, str]]         # (ref, role) of segments whose id and role passed
  def check_segments(segments: Sequence[Segment], catalog: FactCatalog) -> SegmentReport
  ```

`REPLY_SCHEMA` is fixed copy (it is part of the cached prompt prefix):

```python
REPLY_SCHEMA = {
    "title": "Reply", "type": "object", "additionalProperties": False, "required": ["segments"],
    "properties": {"segments": {"type": "array", "items": {
        "type": "object", "additionalProperties": False, "required": ["text", "ref", "role"],
        "properties": {
            "text": {"type": "string"},
            "ref": {"type": ["string", "null"]},
            "role": {"anyOf": [{"type": "string", "enum": list(SEGMENT_ROLES)}, {"type": "null"}]},
        }}}},
}
```

**Rules:**

1. **`parse_reply`:** `json.loads`, then validate the shape: a dict with a `segments` list; every item a dict with a str `text`, a str-or-None `ref`, and a `role` that is None or in `SEGMENT_ROLES`. Any failure returns None.
2. **`join_segments`:** concatenate the texts. Insert one space where the previous text ends with an alphanumeric character and the next one starts with one. Collapse runs of spaces inside a line, and strip the ends.
3. **`check_segments`:** for each segment, in order:
   1. **Half reference.** `(ref is None) != (role is None)` → `bad_structure`. Skip the segment.
   2. **Unknown id.** `ref` not in the catalog → `unknown_ref`, problem `f"{ref} is not in Reply facts (valid ids: {', '.join(ids)})"`. Skip the segment.
   3. **Role.** `role not in entry.roles` → `wrong_role`, problem `f"{ref} allows {', '.join(entry.roles)}, not {role}"`. Skip the text check.
   4. **Held-service rule.** `held_services` is the union of `entry.service_ids` over the segments cited `held`. When it is non-empty, a `service` segment whose entry's id is not in it → `wrong_role`, problem `f"{ref} is not a service of the held proposal"`.
   5. **Text, by entry kind** (any failure → `ref_mismatch`, problem `f"{ref} is {entry.label}; the segment text {text!r} does not match it"`):
      - **`time` and `customer_clock`:**
        - `clocks_in_text(text)` is non-empty, and every reading shares a value with `entry.clocks`;
        - every token of `words(normalize_for_match(text))` is a digit token, a digit-`h` token (`"9h"`, `"9h30"`: strip one `h` and the rest is digits), or in `TIME_WORDS`;
        - the text has none of `. ? !`.

        `TIME_WORDS = {"giờ", "h", "rưỡi", "kém", "phút", "sáng", "trưa", "chiều", "tối", "đêm", "mai", "hôm", "nay", "ngày", "kia", "mốt", "thứ", "hai", "ba", "tư", "năm", "sáu", "bảy", "chủ", "nhật", "cn", "tuần", "này", "sau", "tới", "lúc", "vào"}`.
      - **`service`:** the words share at least one word with `entry.names[0]`, and the text contains no name of another catalog `service` entry (compared after `normalize_for_match`).
      - **`price`:**
        - `scan_money(text)` is non-empty, and every amount is inside `entry.price`;
        - the text has no `?` or `!`, and does not end with `.`.
   6. **Plain segment** (`ref` and `role` both null). The text containing any of these → `unreferenced`, problem `"write each time, service name and price as its own segment with ref and role"`:
      - a clock (`clocks_in_text`);
      - an amount (`scan_money`);
      - the name of any catalog `service` entry (`normalize_for_match` substring).
   7. **`cited`** gets `(ref, role)` for every segment that passed steps 1–3.

- [ ] **Step 1: Write the failing tests** in `tests/salon/guard/test_segments.py`. Use a hand-built `FactCatalog` fixture, no DB:
  - `t1` time `["taken"]`, label "Thứ Năm 12/11, 3 giờ chiều", clocks `[(15, 0)]`;
  - `t2` `["offer"]` 13:00;
  - `t3` `["held"]` 16:00, `service_ids ["toc-cat-nu"]`;
  - `s1` "Sơn gel" `nail-gel` 120000–180000;
  - `s2` "Cắt tóc nữ" `toc-cat-nu` 100000–150000;
  - `c1` customer_clock `["asked"]` `[(3, 0), (15, 0)]`.

```python
def test_schema_is_strict_compatible():   # every object: additionalProperties False, required == all keys;
                                          # role enum == list(SEGMENT_ROLES)
def test_parse_reply_reads_segments(): ...
@pytest.mark.parametrize("raw", ["Dạ em chào chị", '{"segments": [{"text": "a"}]}',
                                 '{"segments": [{"text": "a", "ref": null, "role": "boss"}]}', ""])
def test_parse_reply_rejects_anything_else(raw): assert parse_reply(raw) is None
def test_join_inserts_a_space_only_between_letters(): ...
    # ["3 giờ chiều", "đã có khách"] -> "3 giờ chiều đã có khách"
    # ["Chị chọn ", "4 giờ chiều", " nhé."] -> "Chị chọn 4 giờ chiều nhé."; "a  b" -> "a b"
TABLE = [  # (row id, segments as (text, ref, role) tuples, expected codes)
    ("run2_turn2_offer_on_taken", [("Dạ em tìm được ", None, None), ("3 giờ chiều", "t1", "offer"),
                                   (" chị đồng ý nhé?", None, None)], {"wrong_role"}),
    ("taken_ok", [("3 giờ chiều mai", "t1", "taken"), (" có khách rồi ạ.", None, None)], set()),
    ("run4_turn8_wrong_service", [("Dạ em giữ chỗ ", None, None), ("sơn gel", "s1", "service"),
                                  (" lúc ", None, None), ("4 giờ chiều", "t3", "held"),
                                  (", chị đồng ý không ạ?", None, None)], {"wrong_role"}),
    ("held_right_service", [("Dạ em giữ chỗ ", None, None), ("cắt tóc", "s2", "service"),
                            (" lúc ", None, None), ("4 giờ chiều", "t3", "held"),
                            (", chị đồng ý không ạ?", None, None)], set()),
    ("unknown_id", [("4 giờ chiều", "t9", "offer")], {"unknown_ref"}),
    ("half_ref", [("1 giờ chiều", "t2", None)], {"bad_structure"}),
    ("wrong_clock", [("2 giờ chiều", "t2", "offer")], {"ref_mismatch"}),
    ("time_residue", [("hoặc 1 giờ chiều nhé.", "t2", "offer")], {"ref_mismatch"}),
    ("time_day_words", [("1 giờ chiều thứ Năm tuần sau", "t2", "offer")], set()),
    ("time_24h", [("13h", "t2", "offer")], set()),
    ("price_k", [("150k", "s1", "price")], set()),
    ("price_nghin", [("khoảng 150 nghìn", "s1", "price")], set()),
    ("price_range", [("120-180 nghìn", "s1", "price")], set()),
    ("price_vnd", [("từ 120.000đ đến 180.000đ", "s1", "price")], set()),
    ("price_out", [("200 nghìn", "s1", "price")], {"ref_mismatch"}),
    ("service_mismatch", [("cắt tóc", "s1", "service")], {"ref_mismatch"}),
    ("plain_time", [("Dạ còn 1 giờ chiều ạ.", None, None)], {"unreferenced"}),
    ("plain_price", [("giá 150 nghìn ạ", None, None)], {"unreferenced"}),
    ("plain_service", [("chị làm sơn gel nhé", None, None)], {"unreferenced"}),
    ("customer_clock_asked", [("3 giờ", "c1", "asked"), (" sáng hay chiều ạ?", None, None)], set()),
    ("customer_clock_offer", [("3 giờ", "c1", "offer")], {"wrong_role"}),
    ("blank_ref_text", [("  ", "t2", "offer")], {"ref_mismatch"}),
]
@pytest.mark.parametrize("segments, expected", [r[1:] for r in TABLE], ids=[r[0] for r in TABLE])
def test_check_segments_table(catalog, segments, expected): ...
def test_same_instant_union_still_rejects_offer(): ...   # entry roles ["asked", "taken"]; offer -> wrong_role
def test_problems_name_the_id_and_allowed_roles(catalog): ...  # "t1 allows taken, not offer" in problems
def test_cited_lists_only_valid_refs(catalog): ...
```

- [ ] **Step 2: Run them to see them fail**

Run: `.venv/bin/python -m pytest -q tests/salon/guard/test_segments.py`
Expected: collection error, `ModuleNotFoundError: app.agents.salon.guard.segments`.

- [ ] **Step 3: Implement `guard/segments.py`** with the interfaces and rules above.

- [ ] **Step 4: Run the tests**

Run: `.venv/bin/python -m pytest -q tests/salon/guard/test_segments.py tests/test_regex_budget.py`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add app/agents/salon/guard/segments.py tests/salon/guard/test_segments.py
git commit -m "feat: parse reply segments and check each cited fact"
```

---

### Task 3: Required references and `check_structured`

**Files:**
- Modify: `app/agents/salon/guard/facts.py`: add `RequiredRef`; give `ReplyFacts` the fields `catalog` and `required`; `build_reply_facts` builds both.
- Modify: `app/agents/salon/guard/checks.py`: new `CHECK_CODES`, `check_text`, `GuardResult`, `check_structured`. `check_reply` keeps its current behaviour until Task 7.
- Modify: `tests/salon/guard/test_checks.py`
- Modify: `CONTEXT.md` (the F2 note)

**Interfaces:**
- Consumes: `build_catalog`, `FactCatalog`, `CLAIM_FOR_KIND`, `ANNOUNCE_ROLES` (Task 1); `parse_reply`, `join_segments`, `check_segments` (Task 2).
- Produces:
  ```python
  class RequiredRef(BaseModel): ref: str; roles: list[str]; why: str
  # ReplyFacts gains:  catalog: FactCatalog;  required: list[RequiredRef]
  CHECK_CODES = ("bad_structure", "unknown_ref", "wrong_role", "ref_mismatch", "unreferenced",
                 "empty", "claim", "unknown_time", "unknown_price", "missing_fact", "missing_question",
                 "missing_phone", "phone_not_allowed", "pending_undecided", "pronoun",
                 "foreign_script", "english", "repeat")   # Task 7 removes unknown_time, unknown_price
  def check_text(text: str, facts: ReplyFacts) -> set[str]
      # empty, claim, missing_question, missing_phone, phone_not_allowed, pending_undecided, pronoun, repeat
  def check_reply(text: str, facts: ReplyFacts) -> list[str]
      # unchanged result: check_text plus the old unknown_time / unknown_price / missing_fact text rules
  @dataclass(frozen=True)
  class GuardResult:
      text: str                    # joined and stripped; "" on bad_structure
      codes: list[str]             # ordered by CHECK_CODES
      problems: list[str]
      announce_turn_ids: list[str]
  def check_structured(raw: str, facts: ReplyFacts) -> GuardResult
  ```

**How `required` is built** in `build_reply_facts`, from the same sources `must_mention` uses today. The ref is always `catalog.id_of(moment)`; skip an item whose id is None, and keep only the first item for each ref.

| Source | roles | why |
|---|---|---|
| `snapshot.unannounced` | `[CLAIM_FOR_KIND[proposal_kind]]` when `approved`, `["failed"]` when `failed` | `"the unannounced decision"` |
| each `ProposalFact` | `["held"]` | `"the new proposal"` |
| each `TimeFact` with role `taken` | `["taken"]` | `"the taken time"` |
| decisions, typed turns only: `approved` | `[CLAIM_FOR_KIND[kind]]` | `"the decision of this turn"` |
| decisions, typed turns only: `failed` / `not_approvable` | `["failed"]` | `"the decision of this turn"` |
| decisions, typed turns only: `clarify` | `["held"]` | `"the decision of this turn"` |

**`check_structured(raw, facts)`:**
1. `segments = parse_reply(raw)`. If it is None, return `GuardResult("", ["bad_structure"], ["the reply was not the JSON list of segments"], [])`.
2. `text = strip_tool_text(join_segments(segments))`.
3. `report = check_segments(segments, facts.catalog)`.
4. Every `required` item with no `(ref, role)` in `report.cited` (where `ref` matches and `role` is in `item.roles`) adds `missing_fact`, with problem `f"cite {item.ref} as {' or '.join(item.roles)} ({item.why})"`.
5. `codes = report.codes | {missing_fact if any} | check_text(text, facts)`, ordered by `CHECK_CODES`.
6. `announce_turn_ids` lists the unique `entry.announce_turn_ids` of cited `(ref, role)` pairs whose role is in `ANNOUNCE_ROLES`, in citation order.

- [ ] **Step 1: Write the failing tests** in `tests/salon/guard/test_checks.py`. Build the replies with a local helper `_raw(*parts)` that makes the JSON; a part is a str (plain) or a `(text, ref, role)` tuple.

```python
async def test_required_refs_cover_proposal_taken_r0_and_decision(test_db): ...
    # ProposalFact 16:00, TimeFact taken 15:00, r0 approved 9:00, typed DecisionFact failed 10:00
    # -> [(r.ref, r.roles) for r in facts.required] == [(id9, ["booked"]), (id16, ["held"]),
    #                                                    (id15, ["taken"]), (id10, ["failed"])]
async def test_button_turn_does_not_require_the_decision(test_db): ...
async def test_check_structured_bad_json(test_db):
    result = check_structured("Dạ có ạ.", await facts_for(test_db))
    assert (result.text, result.codes) == ("", ["bad_structure"])
async def test_empty_segment_list_is_empty(test_db): ...   # '{"segments": []}' and [("  ", None, None)] -> ["empty"]
async def test_short_phrase_satisfies_a_required_ref(test_db): ...
    # ProposalFact at Moment(spoken="Thứ Năm 12/11, 4 giờ chiều"); reply cites ("4 giờ chiều mai", id, "held")
    # and ends with "?" -> codes == [] (the old verbatim rule would have said missing_fact)
async def test_missing_fact_when_the_proposal_is_not_cited(test_db): ...
    # ProposalFact 16:00; reply [("Dạ chị đợi em chút nhé?", None, None)] -> ["missing_fact"]
async def test_announce_ids_come_from_cited_decisions(test_db): ...
    # r0 approved 9:00 cited as booked -> announce_turn_ids == ["turn-0"]
    # the same reply citing only a free time (offer) -> announce_turn_ids == []
async def test_check_structured_orders_codes(test_db): ...   # wrong_role + claim -> ["wrong_role", "claim"]
async def test_check_text_has_no_old_text_rules(test_db): ...
    # check_text("Dạ còn 5 giờ chiều ạ.", facts) has no "unknown_time"
```

- [ ] **Step 2: Run them to see them fail**

Run: `.venv/bin/python -m pytest -q tests/salon/guard/test_checks.py`
Expected: FAIL with an `ImportError` on `check_structured` / `check_text`.

- [ ] **Step 3: Implement** `RequiredRef` and the `ReplyFacts` fields, then `check_text`, `check_reply` (now `check_text` plus the old three rules), `GuardResult` and `check_structured`. In `CONTEXT.md`, replace the F2 parenthetical "(F2, so nguyên văn như giờ đề xuất — cố ý, đừng nới)" with "(F2; từ mức C phải tham chiếu id với vai trò taken, không còn so nguyên văn — spec 2026-11-11-level-c)".

- [ ] **Step 4: Run the guard tests**

Run: `.venv/bin/python -m pytest -q tests/salon/guard tests/salon/orchestrator/test_middleware.py`
Expected: all pass. The middleware still uses `check_reply`.

- [ ] **Step 5: Commit**

```bash
git add app/agents/salon/guard/facts.py app/agents/salon/guard/checks.py tests/salon/guard/test_checks.py CONTEXT.md
git commit -m "feat: check structured replies against required references"
```

---

### Task 4: `foreign_script` and `english`

**Files:**
- Modify: `app/agents/salon/guard/checks.py` (`check_text` gains both codes)
- Test: `tests/salon/guard/test_checks.py`

**Interfaces:**
- Produces:
  ```python
  ENGLISH_FUNCTION_WORDS = frozenset({"the", "is", "are", "was", "be", "of", "and", "or", "for", "with",
      "this", "that", "these", "you", "your", "we", "it", "in", "on", "at", "by", "from", "as", "if",
      "not", "does", "please", "will", "should", "must", "never", "only", "when", "what", "which",
      "there", "here"})
  ENGLISH_RUN_MIN_WORDS = 4
  ENGLISH_RUN_MIN_FUNCTION_WORDS = 2
  ```

**Rules:**
- **`foreign_script`:** after `unicodedata.normalize("NFC", text)`, any character with `ch.isalpha()` whose `unicodedata.name(ch, "")` does not start with `"LATIN"`.
- **`english`:**
  1. Split the NFC text into tokens on non-alphanumeric characters.
  2. An *ASCII word* is a token that is `isascii()` and `isalpha()`.
  3. Find each run of consecutive ASCII words.
  4. A run with at least `ENGLISH_RUN_MIN_WORDS` words and at least `ENGLISH_RUN_MIN_FUNCTION_WORDS` words in `ENGLISH_FUNCTION_WORDS` (lower-cased) → `english`.
- Prompt leaks are covered by `english`. There is no `prompt_leak` code (spec §6).

- [ ] **Step 1: Write the failing tests**

```python
LIVE_REPLIES = [  # verbatim model replies of live runs 3 and 4 (.superpowers/sdd/2026-11-08-subagents-rebuild/live-{3,4}.log)
    "Dạ em chào chị Lan ạ, rất vui được đón chị lần đầu qua tiệm. Chị cần em hỗ trợ gì cứ nhắn em nhé.",
    "Dạ chị Lan ơi, lúc Thứ Năm 12/11, 3 giờ chiều đã có người đặt rồi ạ. Chị chọn giúp em 1 giờ chiều, 2 giờ chiều, hoặc 4 giờ chiều nhé.",
    "Dạ chị Lan, tiệm mình mở từ 8 giờ sáng đến 7 giờ tối ạ.",
    "Dạ chị Lan, nhuộm tóc giá từ 350.000đ đến 700.000đ ạ.",
    "Dạ em chào chị Mai ạ. Chị muốn làm tóc hay làm móng, em giúp chị ngay nhé.",
    "Dạ chị Mai, Chủ Nhật 15/11 tiệm mở cửa bình thường ạ. Nếu chị muốn, em xem giúp giờ phù hợp cho chị.",
    "Dạ chị Mai, chị có 2 lịch: Thứ Năm 12/11, 11 giờ sáng và Thứ Năm 12/11, 5 giờ chiều ạ.",
]
ARMENIAN = "Dạ chị Mai, sơn gel bên em giá từ 120.000đ đến 180.000đ ạ. Եթե chị muốn, em đặt giúp chị giờ khác nhé."

async def test_foreign_script_blocks_the_live_run_4_reply(test_db): ...   # "foreign_script" in check_text(ARMENIAN, facts)
@pytest.mark.parametrize("reply", LIVE_REPLIES)
async def test_live_replies_are_not_foreign_or_english(test_db, reply): ...
async def test_nfd_vietnamese_is_not_foreign_or_english(test_db): ...   # NFD of each LIVE_REPLIES item
async def test_english_sentence_is_blocked(test_db): ...   # "Sorry, I will check the schedule for you."
@pytest.mark.parametrize("prompt", [ORCHESTRATOR_PROMPT, APPOINTMENT_PROMPT, SERVICE_PROMPT, SHOP_INFO_PROMPT])
async def test_a_copied_prompt_line_is_blocked(test_db, prompt): ...
    # first non-empty line of the prompt inside a Vietnamese sentence -> "english"
```

- [ ] **Step 2: Run them to see them fail**

Run: `.venv/bin/python -m pytest -q tests/salon/guard/test_checks.py -k "foreign or english or prompt_line"`
Expected: FAIL; the codes are missing.

- [ ] **Step 3: Implement both rules in `check_text`.**

- [ ] **Step 4: Run the guard tests**

Run: `.venv/bin/python -m pytest -q tests/salon/guard tests/test_regex_budget.py`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add app/agents/salon/guard/checks.py tests/salon/guard/test_checks.py
git commit -m "feat: block foreign scripts and English runs in replies"
```

---

### Task 5: The context block shows the catalog

**Files:**
- Modify: `app/agents/salon/orchestrator/middleware/context.py`: `ContextMiddleware(db, holder, store=None)`; append `catalog.render()` to the block.
- Modify: `app/agents/salon/orchestrator/middleware/__init__.py`: construct `ContextMiddleware(db, holder, store)`.
- Test: `tests/salon/orchestrator/test_middleware.py`

**Interfaces:**
- Consumes: `build_catalog`, `CATALOG_HEADER` (Task 1); `read_pending_outcome` (state); `current_question` (turns).
- Produces: every Orchestrator model call's context block ends with `"\n\n" + catalog.render()`. The catalog comes from `build_catalog(self.db, snapshot=snapshot, turn_facts=read_facts(state), pending_outcome=read_pending_outcome(state), question=current_question(messages), turn_id=...)`. `turn_id` is `state.get("turn_id")`; when that is missing, use `request.runtime.context.turn_id`, or `""` without a runtime. These are the same inputs the guard passes (Task 3), so ids match.

- [ ] **Step 1: Write the failing test**

```python
async def test_context_block_lists_reply_facts(test_db): ...
    # any one-turn run; the first model call's SystemMessage (the context block) contains
    # "Reply facts (cite by id):" and lines matching "[hours]: opens 8 giờ sáng" and "[hours]: closes"
```

- [ ] **Step 2: Run it to see it fail**

Run: `.venv/bin/python -m pytest -q tests/salon/orchestrator/test_middleware.py -k reply_facts`
Expected: FAIL; the block has no "Reply facts".

- [ ] **Step 3: Implement** the constructor change and the appended block.

- [ ] **Step 4: Run the middleware and run tests**

Run: `.venv/bin/python -m pytest -q tests/salon/orchestrator tests/salon/turn/test_run.py`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add app/agents/salon/orchestrator/middleware/context.py app/agents/salon/orchestrator/middleware/__init__.py tests/salon/orchestrator/test_middleware.py
git commit -m "feat: show the reply fact catalog in the orchestrator context"
```

---

### Task 6: Test fakes that write segment replies

**Files:**
- Modify: `tests/fakes.py`
- Test: `tests/test_fakes.py`

**Interfaces:**
- Consumes: `CATALOG_HEADER` (Task 1).
- Produces:
  ```python
  @dataclass(frozen=True)
  class Cite: role: str; text: str; find: Optional[str] = None; ref: Optional[str] = None
  def cite(role: str, text: str, *, find: Optional[str] = None, ref: Optional[str] = None) -> Cite
  def reply(*parts: "str | Cite") -> Callable[[list], AIMessage]
  def plain(text: str) -> AIMessage           # one plain segment; needs no context
  ```
  - `ScriptedChatModel._next` and `_anext` call a callable reply with the message batch, after `Hang` handling.
  - `reply(...)` resolves each `Cite` from the context block of the batch:
    1. In the SystemMessages, find the lines after `CATALOG_HEADER` that have the form `- {id} [{roles}]: {label}`.
    2. Take the first line whose roles contain `role` and whose label contains `find or text` (both `normalize_for_match`).
    3. A `Cite` with `ref` set skips the lookup (used to test unknown ids).
    4. No match → `AssertionError(f"no reply fact for {role} {find or text!r}")`.
  - Content is `json.dumps({"segments": [...]}, ensure_ascii=False)`.

- [ ] **Step 1: Write the failing tests**

```python
BLOCK = ("Today: ...\n\nReply facts (cite by id):\n- t1 [taken]: Thứ Năm 12/11, 3 giờ chiều\n"
         "- t2 [offer]: Thứ Năm 12/11, 1 giờ chiều")
def test_plain_builds_one_plain_segment(): ...
def test_reply_resolves_ids_from_the_context_block():
    msg = reply("Dạ ", cite("taken", "3 giờ chiều mai", find="3 giờ chiều"), " có khách rồi ạ.")(
        [SystemMessage("prompt"), SystemMessage(BLOCK)])
    assert json.loads(msg.content)["segments"][1] == {"text": "3 giờ chiều mai", "ref": "t1", "role": "taken"}
def test_cite_with_an_explicit_ref_skips_the_lookup(): ...
def test_cite_without_a_matching_fact_raises(): ...
async def test_scripted_model_calls_callable_replies(): ...
```

- [ ] **Step 2: Run them to see them fail**

Run: `.venv/bin/python -m pytest -q tests/test_fakes.py`
Expected: `ImportError` on `cite` / `reply` / `plain`.

- [ ] **Step 3: Implement** the helpers and the callable support.

- [ ] **Step 4: Run the tests**

Run: `.venv/bin/python -m pytest -q tests/test_fakes.py`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add tests/fakes.py tests/test_fakes.py
git commit -m "test: add fakes that write cited segment replies"
```

---

### Task 7: The Orchestrator answers in grounded segments

This task flips the switch. Every scripted Orchestrator final reply in the suite becomes a segment reply in the same task.

**Files:**
- Modify: `app/agents/salon/orchestrator/agent.py`: add `reply_format()` and pass `response_format=reply_format()`.
- Modify: `app/agents/salon/orchestrator/middleware/context.py`:
  - a rewrite with no allowed tool sets `tool_choice="none"`;
  - catch `StructuredOutputValidationError`.
- Modify: `app/agents/salon/orchestrator/middleware/reply_guard.py`: switch to `check_structured`; update `_CODE_HINTS` and `guard_hint`; write `announced_turn_ids`.
- Modify: `app/agents/salon/orchestrator/state.py`: add `announced_turn_ids: NotRequired[list[str]]`.
- Modify: `app/agents/salon/orchestrator/middleware/turn_start.py`: reset `"announced_turn_ids": []`.
- Modify: `app/agents/salon/turn/run.py`: add `_Answer.announced`; read it in `_final_answer`; carry it through the button follow-up (`run.py:414`); change `_mark_announced`.
- Modify: `app/agents/salon/guard/checks.py` and `facts.py`. Delete:
  - `check_reply`, `_has_unknown_price`, and `unknown_time` / `unknown_price` from `CHECK_CODES`;
  - `ReplyFacts.must_mention`, `ReplyFacts.price_ranges` and `_price_ranges`.

  Tests that called `check_reply` call `check_text` (or `check_structured` for the old `unknown_*` / `missing_fact` rows).
- Modify tests:
  - `tests/salon/orchestrator/test_middleware.py`: `_agent` passes `response_format=reply_format()`; `Recorder.bind_tools` appends its kwargs to `bind_kwargs`;
  - `tests/salon/orchestrator/test_state.py`;
  - `tests/salon/turn/test_run.py`;
  - `tests/test_socketio_chat.py`;
  - `tests/salon/guard/test_checks.py`;
  - `tests/salon/guard/__init__.py`: docstring, no more verbatim rule.

**Interfaces:**
- Consumes: `check_structured`, `GuardResult` (Task 3); `REPLY_SCHEMA` (Task 2); `plain`, `reply`, `cite` (Task 6).
- Produces:
  ```python
  def reply_format() -> ProviderStrategy            # ProviderStrategy(schema=REPLY_SCHEMA, strict=True)
  def offered_tools(tools, *, has_pending, pending_decided, rewriting, codes) -> tuple[list, Optional[str]]
      # (tools, tool_choice); tool_choice is "none" when rewriting and no tool is allowed
      # (then the tools are the normal offered list), else None
  def guard_hint(codes: list[str], facts: ReplyFacts, problems: list[str]) -> str
  # _Answer gains: announced: list[str] = field(default_factory=list)
  async def _mark_announced(turn: _Turn, answer: _Answer, r0: Optional[UnannouncedDecision]) -> None
  ```

**Behaviour:**

1. **`ContextMiddleware`**
   - **Tool choice:** when `offered_tools` returns `"none"`, override with `model_settings={**request.model_settings, "tool_choice": "none"}`. Under `ProviderStrategy` the factory passes `model_settings`, not `request.tool_choice`, into `bind_tools`.
   - **Parse errors:** wrap `await handler(...)`:
     ```python
     except StructuredOutputValidationError as exc:
         return ModelResponse(result=[exc.ai_message])
     ```
     Log `structured_output_invalid` with the turn ids only.
2. **`ReplyGuardMiddleware.aafter_model`**
   - `result = check_structured(reply.text, facts)`.
   - With `reply.invalid_tool_calls`: `codes = ["bad_tool_call", *[c for c in result.codes if c != "bad_structure"]]`.
   - **Accept:** replace the reply's content with `result.text`, and return `"announced_turn_ids": result.announce_turn_ids` together with the messages.
   - **Reject:** log `rejected_reply = result.text or reply.text`, use the hint `guard_hint(codes, facts, result.problems)`, and leave `announced_turn_ids` unwritten.
3. **`_CODE_HINTS`**
   - Remove `unknown_time` and `unknown_price`. `missing_fact` becomes "Cite each required fact listed below with an allowed role."
   - Add:
     - `bad_structure`: "Your reply was not the JSON list of segments. Answer only with segments; ref and role are both null or both set."
     - `unknown_ref`: "Use only ids from the Reply facts list."
     - `wrong_role`: "Give each cited fact one of the roles listed for its id in Reply facts."
     - `ref_mismatch`: "A cited segment says only its fact: the same clock time with an optional day, a word of the service name, or an amount inside the price range."
     - `unreferenced`: "Write every time, service name and price as its own segment with ref and role."
     - `foreign_script`: "Write only Vietnamese letters."
     - `english`: "Write the whole reply in Vietnamese."
   - `guard_hint` appends `Details:` and then one `- {problem}` line per problem.
4. **`run.py`**
   - `_final_answer` sets `announced = list(state.get("announced_turn_ids", []))` when the turn is the same and the reply is not code.
   - **`_mark_announced`:**
     - `answer.source == "llm"`: call `turn.ledger.mark_announced(tid)` for each `tid` in `answer.announced`.
     - Otherwise (code templates): keep today's verbatim `spoken` matching on `answer.text`.
5. **Converting the tests.** Every scripted Orchestrator final reply (an `AIMessage` with text and no tool calls) changes:
   - It becomes `plain(text)` when it mentions no time, service or price.
   - Otherwise it becomes `reply(...)`, with each time, service and price as a `cite(role, text)` whose role is allowed in that turn's catalog. The joined text must equal the old string, so assertions on the answer text still hold; give the old strings their own `*_TEXT` constants.
   - Replies that a test means to be rejected must still be JSON, so the asserted code fires and not `bad_structure`.
   - Keep every test's asserted codes. Where the old expectation was `unknown_time`, `unknown_price` or verbatim `missing_fact`, assert the new code (`unreferenced`, `ref_mismatch`, `missing_fact` by reference).
   - Ledger a `Ruling:` for any test whose asserted codes must change for another reason.

- [ ] **Step 1: Write the failing tests**

```python
# test_middleware.py
async def test_plain_text_reply_is_bad_structure_and_rewritten(test_db): ...
    # replies [AIMessage("Dạ có ạ."), plain("Dạ có ạ.")]; the 2nd call's guard hint has codes ["bad_structure"];
    # the final AIMessage content in state is "Dạ có ạ." (plain text, not JSON)
async def test_accepted_reply_is_stored_as_joined_text(test_db): ...
    # reply("Dạ tiệm mở cửa từ ", cite("hours", "8 giờ sáng"), " tới ", cite("hours", "7 giờ tối"), " ạ.")
    # -> state's last AI content == "Dạ tiệm mở cửa từ 8 giờ sáng tới 7 giờ tối ạ."
async def test_rewrite_turn_forbids_tool_calls(test_db): ...        # replaces test_rewrite_turn_offers_no_tools
    # a rejected first reply -> the 2nd call's offered tools are non-empty and its bind kwargs have tool_choice "none";
    # with code pending_undecided -> offered == ["decide_pending"] and no tool_choice "none"
async def test_announced_ids_are_written_only_on_acceptance(test_db): ...
    # R0 approved in the ledger; the 1st reply cites R0 as booked plus cite(..., ref="t99") -> rejected,
    # state["announced_turn_ids"] == [] while rewriting; an accepted 2nd reply citing R0 -> == [r0 turn id]
async def test_reply_format_is_strict_provider_strategy(): ...
    # isinstance(reply_format(), ProviderStrategy) and reply_format().schema_spec.strict is True
# test_run.py
async def test_short_phrased_decision_is_marked_announced(test_db, monkeypatch): ...
    # a typed approval turn; the reply cites the booked time as "9 giờ sáng mai"; ledger record(turn_id).announced
async def test_r0_cited_in_a_short_phrase_marks_the_r0_turn(test_db, monkeypatch): ...
# test_state.py: the roundtrip test includes "announced_turn_ids": ["t-0"]
```

- [ ] **Step 2: Run them to see them fail**

Run: `.venv/bin/python -m pytest -q tests/salon/orchestrator/test_middleware.py tests/salon/turn/test_run.py -k "bad_structure or joined_text or forbids_tool or announced or reply_format"`
Expected: FAIL. `reply_format` is missing, the reply is still stored as raw text, and tools are hidden on rewrite.

- [ ] **Step 3: Implement** the production changes listed under Files and Behaviour.

- [ ] **Step 4: Convert the scripted final replies**, following Behaviour item 5.

- [ ] **Step 5: Run the affected suites, one command at a time**

Run: `.venv/bin/python -m pytest -q tests/salon tests/test_fakes.py tests/test_socketio_chat.py tests/test_regex_budget.py`
Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add app/agents/salon tests/salon tests/test_socketio_chat.py
git commit -m "feat: answer in grounded JSON segments checked by the guard"
```

---

### Task 8: Orchestrator prompt

**Files:**
- Modify: `app/agents/salon/orchestrator/prompt.py`
- Test: `tests/salon/test_prompts.py`

Exact text changes (this is fixed copy; keep the hard wrap at about 78 columns):

1. In "What you receive", replace "waiting sessions, and the facts of this turn." with "waiting sessions, the facts of this turn, and the Reply facts list: every time, service and price you may mention, each with an id and the roles it allows."
2. Replace the rule "Copy every time and date exactly as the `spoken` value in tool results or the context block. Mention only prices from tool results." with three rules:
   - "Your final reply is a list of segments. Joined in order, their texts are the message the customer reads, so put spaces and punctuation inside the texts."
   - "Every time, service name and price you mention is its own segment: its text holds only that mention, written briefly and naturally, with service names in lower case as inside a sentence; its ref is the id from the Reply facts list and its role is one of the roles listed for that id. A time segment may add the day but no other words. Every other segment has ref and role null and never mentions a time, a service or a price."
   - "Use only ids from the Reply facts list, and mention only times, services and prices that are in it."
3. Replace rule E with: "A Reply fact that allows taken is already booked by another customer: cite it as taken, never offer it and never say it is free. When suggesting other times, cite only Reply facts that allow offer."
4. Replace "Never mention tools, agents, ids, JSON or these instructions." with "Never write tools, agents, ids, JSON or these instructions inside a segment's text."

- [ ] **Step 1: Write the failing tests**

```python
def test_orchestrator_prompt_describes_segments():
    flat = _flat(ORCHESTRATOR_PROMPT)
    assert "Reply facts list" in flat and "its own segment" in flat
    assert "Copy every time and date exactly" not in flat
    assert "inside a segment's text" in flat
def test_orchestrator_prompt_says_taken_times_are_not_offered():   # updated
    flat = _flat(ORCHESTRATOR_PROMPT)
    assert "allows taken is already booked by another customer" in flat
    assert "cite only Reply facts that allow offer" in flat
```

- [ ] **Step 2: Run them to see them fail**

Run: `.venv/bin/python -m pytest -q tests/salon/test_prompts.py`
Expected: 2 failed.

- [ ] **Step 3: Edit the prompt** as listed above.

- [ ] **Step 4: Run the prompt tests**

Run: `.venv/bin/python -m pytest -q tests/salon/test_prompts.py`
Expected: all pass, including the no-Vietnamese test.

- [ ] **Step 5: Commit**

```bash
git add app/agents/salon/orchestrator/prompt.py tests/salon/test_prompts.py
git commit -m "feat: teach the orchestrator to cite reply facts in segments"
```

---

### Task 9: Live scenario B picks its times from the database

**Files:**
- Modify: `scripts/test_live_10_turns.py`:
  - remove `KEEP_DB_TURNS` and `check_keep_db_scenario`;
  - add the helpers below;
  - in `main`, stop and report when a pick fails;
  - update the docstring.
- Test: `tests/test_live_script.py` (pure helpers with the test DB; no Azure, never calls `main`)

**Interfaces:**
- Produces (in `scripts/test_live_10_turns.py`):
  ```python
  PICK_OPTION = "<pick an option of the open booking session>"   # sentinel text for turns 4 and 8
  def customer_clock_phrase(start: datetime) -> str                 # format_vi_hhmm of the local HH:MM, e.g. "4 giờ chiều"
  async def pick_taken_starts(db, *, exclude_user_id: str) -> Optional[tuple[datetime, datetime]]
      # the two earliest distinct start times of tomorrow's booked appointments of OTHER customers; None if < 2
  async def pick_session_option(db, *, user_id: str) -> Optional[datetime]
      # first option of the user's open appointment waiting session; None if no session or no option
  def keep_db_turns(first_taken: datetime, second_taken: datetime) -> list[tuple]
      # 10 turns as today; turn 2 "Em ơi mai chị muốn sơn gel lúc {customer_clock_phrase(first_taken)}",
      # turn 7 "Chị muốn đặt thêm cắt tóc lúc {customer_clock_phrase(second_taken)} mai nữa",
      # turns 4 and 8 text PICK_OPTION, turn 5 None (button)
  ```
  In `main` (keep-db):
  - `starts = await pick_taken_starts(db, exclude_user_id=str(user.id))`. When it is None, print why and stop before any Azure call.
  - For a `PICK_OPTION` turn, call `pick_session_option`. When it is None, print "phiên chờ không có lựa chọn" and stop the loop. Otherwise send `f"{customer_clock_phrase(option)} đi em"`.

- [ ] **Step 1: Write the failing tests**

```python
def test_customer_clock_phrase(): assert customer_clock_phrase(<16:00 local>) == "4 giờ chiều"
async def test_pick_taken_starts_skips_the_customer_and_needs_two(test_db): ...
async def test_pick_session_option_reads_the_open_session(test_db): ...
def test_keep_db_turns_shape(): ...   # 10 turns; turn 5 text None; turns 4 and 8 PICK_OPTION; turns 2 and 7 hold the phrases
```

- [ ] **Step 2: Run them to see them fail**

Run: `.venv/bin/python -m pytest -q tests/test_live_script.py`
Expected: `ImportError` on `customer_clock_phrase`.

- [ ] **Step 3: Implement** the helpers and the `main` changes.

- [ ] **Step 4: Run the tests and compile the script**

Run: `.venv/bin/python -m pytest -q tests/test_live_script.py && .venv/bin/python -m py_compile scripts/test_live_10_turns.py`
Expected: all pass, no output from `py_compile`.

- [ ] **Step 5: Commit**

```bash
git add scripts/test_live_10_turns.py tests/test_live_script.py
git commit -m "test: pick scenario B times from the dev database"
```

---

### Task 10: Status and full verification

**Files:**
- Modify: `CONTEXT.md` (status section: level C implemented, live runs pending)

- [ ] **Step 1: Update `CONTEXT.md`.** Add one status paragraph under the live-run status:
  - the spec and plan paths;
  - final replies are JSON segments citing the Reply facts by id and role;
  - the new guard codes `bad_structure`, `unknown_ref`, `wrong_role`, `ref_mismatch`, `unreferenced`, `foreign_script`, `english`; removed `unknown_time` and `unknown_price`; `missing_fact` is now by reference;
  - live runs A and B are pending owner approval.

- [ ] **Step 2: Run the whole backend suite**

Run: `.venv/bin/python -m pytest -q`
Expected: all pass.

- [ ] **Step 3: Run the frontend tests**

Run: `cd frontend && npx vitest run`
Expected: all pass (the frontend is unchanged).

- [ ] **Step 4: Commit**

```bash
git add CONTEXT.md
git commit -m "docs: record level C in the project status"
```

**After the plan (not a task):** ask the owner before each live run. Run scenario A, then scenario B (`--keep-db`). Read each transcript against the spec's anti-goals, especially stiff wording: the full "Thứ Năm 12/11, …" and capitalised service names. From the `guard_rejected` / `guard_violation` logs, count:
- the turns that ended on a template;
- the rewrites caused by `wrong_role`.
