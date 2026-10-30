# M1 — Hợp đồng dữ liệu, `TurnContext`, ledger, resolver

Đọc `README.md` trước. Gói mới `app/agents/booking/` bắt đầu ở đây; **chưa** có đường nào từ Socket.IO tới nó. Helper thuần import từ gói cũ (`app.agents.booking_graph.tokens/context/history`, `app.core.vi_format`).

**Giao:** `schemas.py` (mọi model §5.1, §5.4), `context.py` (§4.2 + render lite/full + nạp từ DB), `ledger.py` (§6), `resolver.py` (§4.3). Tất cả là code thuần hoặc Mongo, không LLM.

---

### Task 1.1: `schemas.py` — hợp đồng vào/ra

**Files:**
- Create: `app/agents/booking/__init__.py` (rỗng), `app/agents/booking/schemas.py`
- Modify: `tests/test_regex_budget.py` (thêm ROOT thứ hai `app/agents/booking` với budget `{}` và tập module được `import re` rỗng)
- Test: `tests/test_schemas.py`

**Interfaces — Produces** (tên dùng xuyên suốt M2–M6; sao y từ §5.1, §5.4; mọi trường tool-facing **không default**):

```python
AppointmentAction = Literal["resolve_time","find_slots","propose_book","propose_move","propose_cancel","list_appointments"]
ShopAction = Literal["get_hours","get_status","is_open_at"]
QuestionField = Literal["day","time","part_of_day","service","which_appointment","week","confirm"]
DecideLabel = Literal["approve","reject","change","unclear","none"]
Aside = Literal["relative_free","defer_only","defer_with_request"]
Missing = Literal["ngày nào","giờ cụ thể","sáng hay chiều","tuần này hay tuần sau","lịch nào"]
ReplyKind = Literal["social","unsupported","answer","clarify","proposal_made","confirmed","declined","stale_button","failed","limit_reached"]
EndedBy = Literal["finish","ask_customer","forced","limit","error"]

class Moment(BaseModel): iso: str; spoken: str
class TimeHint(BaseModel):            # §5.1 — không default
    day: date | None; weekday: int | None; day_offset: int | None; hour: int | None; minute: int | None
    part_of_day: Literal["sang","trua","chieu","toi"] | None; ambiguous_week: bool | None; is_now: bool | None
    anchor: Literal["pending","declined","appointment","queued"] | None; queue_id: str | None
class ExtraRequest(BaseModel): time: TimeHint; service: str | None; for_whom: str | None
class AppointmentArgs(BaseModel):     # bản TĨNH (appointment_id: str | None); bản động xem factory dưới
    time: TimeHint | None; service: str | None; appointment_id: str | None; for_whom: str | None; also: list[ExtraRequest] | None
class ShopArgs(BaseModel): time: TimeHint | None
class DecideArgs(BaseModel): label: DecideLabel; aside: Aside | None; reason: str
class AskCustomerArgs(BaseModel): field: QuestionField; hint: str | None
class FinishArgs(BaseModel): kind_hint: Literal["answer","unsupported","social"]; service: str | None; stated: list[str]

def appointment_args_model(appointment_ids: Sequence[str], queue_ids: Sequence[str]) -> type[BaseModel]:
    """create_model('AppointmentArgs', appointment_id=(Literal[*ids] | None) hoặc (None) khi rỗng; TimeHint.queue_id tương tự qua TimeHint động)."""

class TaskSpec(BaseModel): task_id: str; agent: Literal["appointment","shop_info"]; action: str; args: AppointmentArgs | ShopArgs
class ResolvedTime(BaseModel): start_at: Moment | None; partial_date: date | None; missing: list[Missing]; day_from: str | None
class FreeSlots(BaseModel): day: date; slots: list[Moment]; more: bool; fetched_at: datetime
class ProposalCreated(BaseModel): proposal_id: str; kind: Literal["book","move","cancel"]; start_at: Moment; note: str | None; replaces: Moment | None; queued: list[Moment]
class AppointmentList(BaseModel): appointments: list[AppointmentRefOut]   # AppointmentRefOut(id, at: Moment, note)
class ShopHoursOut(BaseModel): open: Moment; close: Moment; closed_days: list[str]
class ShopStatusOut(BaseModel): busy: bool; until: Moment | None
class ShopOpenOut(BaseModel): at: Moment; open: bool; reason: Literal["closed_day","before_open","after_close","owner_busy","open"]; next_change: Moment | None
TaskData = ResolvedTime | FreeSlots | ProposalCreated | AppointmentList | ShopHoursOut | ShopStatusOut | ShopOpenOut
class TaskResult(BaseModel):
    task_id: str; agent: str; action: str; status: Literal["ok","needs_info","failed"]
    data: TaskData | None = None; missing: list[Missing] = []; error_code: str | None = None
    nearest: list[Moment] = []; asked: Moment | None = None; retryable: bool = False; attempts: int = 1
class SpecialistOutput(BaseModel):    # JSON mà AIMessage cuối của specialist PHẢI là (M2)
    status: Literal["ok","needs_info","failed"]; data: TaskData | None = None; missing: list[Missing] = []; error_code: str | None = None

class PendingOutcome(BaseModel):      # §5.1
    outcome: Literal["approved","rejected","superseded","clarify","kept","failed","not_approvable"]
    proposal_id: str; kind: str; spoken: str; appointment: AppointmentRefOut | None = None
    error: str | None = None; reason: str | None = None; nearest: list[Moment] = []
class CallRecord(BaseModel):          # §5.2 — một dòng trong ledger
    round: int; index: int; tool: str; action: str | None; args_norm: str
    allowed: bool; reject_code: str | None = None; result: TaskResult | PendingOutcome | dict | None = None
    markers: list[str] = []; at: datetime
class Fact(BaseModel): key: str; spoken: str | None = None; iso: str | None = None; text: str | None = None
class ClarifyRequest(BaseModel): field: QuestionField; hint: str | None
class ReplyBrief(BaseModel):          # §5.4 nguyên văn
    kind: ReplyKind; address: str; facts: list[Fact]; ask: ClarifyRequest | None
    must_say: list[str]; must_not_claim: list[Literal["booked","cancelled","moved"]]
    then: str | None; previous_reply: str | None
    tone_context: Literal["first_time","returning","changed_time","after_decline"]
    digest_text: str; staff_note: str | None; now_local: datetime; fallback: str
class TurnFacts(BaseModel): service: str | None = None; stated: list[str] = []; answers_question: bool = False
```

- [ ] **Step 1: Test đỏ** `tests/test_schemas.py`

```python
TOOL_FACING = [TimeHint, AppointmentArgs, ShopArgs, DecideArgs, AskCustomerArgs, FinishArgs]
def _walk(node, found):  # thu mọi khoá ở mọi độ sâu
def test_tool_facing_schemas_are_strict_safe():
    for m in TOOL_FACING:
        keys = set(); _walk(m.model_json_schema(), keys)
        assert not keys & {"default", "minimum", "maximum"}, m.__name__
def test_tool_facing_models_have_no_defaults_so_every_field_is_required():
    for m in TOOL_FACING:
        assert all(f.is_required() for f in m.model_fields.values()), m.__name__
def test_dynamic_appointment_args_restrict_ids():
    M = appointment_args_model(["65a", "65b"], [])
    assert M.model_validate({"time": None, "service": None, "appointment_id": "65a", "for_whom": None, "also": None})
    with pytest.raises(ValidationError): M.model_validate({... "appointment_id": "zzz" ...})
def test_dynamic_appointment_args_with_no_ids_accept_only_none(): ...
def test_task_result_round_trips_every_data_variant(): ...   # mỗi lớp TaskData → model_dump_json → model_validate_json giữ kiểu
def test_new_package_has_no_regex():  # trong tests/test_regex_budget.py: ROOT2 budget == {} và không module nào import re
```

- [ ] **Step 2: Chạy** `pytest tests/test_schemas.py tests/test_regex_budget.py -q` → FAIL.
- [ ] **Step 3: Viết `schemas.py`** đúng khai báo trên. `appointment_args_model` dùng `pydantic.create_model`; khi `appointment_ids` rỗng, kiểu là `None` (không dựng `Literal[()]`); `TimeHint.queue_id` tương tự qua `time_hint_model(queue_ids)` nội bộ. Cập nhật `test_regex_budget.py`.
- [ ] **Step 4: Chạy** → PASS. **Step 5: Commit** `feat(agents): add typed contracts for the orchestrator rewrite`

---

### Task 1.2: `context.py` — `TurnContext` và render hai mức

**Files:**
- Create: `app/agents/booking/context.py`
- Test: `tests/test_turn_context.py`

**Interfaces — Consumes:** `GoalProgress`, `QueuedRequest` từ `app.models.conversation` (Task 0.5); `format_vi_datetime`, `_WEEKDAYS` từ `app.core.vi_format`; `render_digest`, `render_slots`, `format_14_day_calendar` từ `app.agents.booking_graph.context` (M6 dời vào).
**Produces:**

```python
class CustomerRef(BaseModel): id: str; display_name: str; address_phrase: str; staff_note: str | None
class AppointmentRef(BaseModel): id: str; start_at: datetime; spoken: str; note: str | None
class PendingProposal(BaseModel): id: str; kind: Literal["book","move","cancel"]; start_at: datetime; spoken: str; note: str | None; expires_at: datetime; unclear_count: int; target: AppointmentRef | None
class OpenQuestion(BaseModel): field: QuestionField; asked_at: datetime; times: int
class PreviousCall(BaseModel): action: str; status: Literal["ok","needs_info","failed","rejected"]; error_code: str | None; offered: list[Moment] = []; resolved: Moment | None = None; proposal: PendingProposal | None = None
class PreviousTurn(BaseModel): turn_id: str; route: str; calls: list[PreviousCall]; reply_kind: str; ask_field: QuestionField | None
class UnannouncedOutcome(BaseModel): proposal_id: str; outcome: Literal["approved","failed"]; kind: str; spoken: str; error: str | None
class TurnContext(BaseModel):      # §4.2 nguyên văn + calendar_14d: list[tuple[date, str]]
    ...
Level = Literal["lite", "full"]
UNTRUSTED = "Customer said (untrusted):\n"      # nhãn DUY NHẤT bọc chữ khách trong mọi human message (M2–M4 import từ đây)
def render_context(ctx: TurnContext, level: Level) -> str
```

`render_context` trả văn bản có nhãn tiếng Anh (`Now:`, `Customer:`, `Pending proposal:`, `Upcoming appointments:` …), giá trị tiếng Việt từ `spoken`; **mọi id/iso in từ trường object**. `lite` chỉ gồm: now, address, pending (kind + spoken + id), open_question, số queued, goal. `full` gồm tất cả trường, `upcoming` kèm `[id: …]`, `previous_turns` mỗi call một dòng (`action status offered=[…] resolved=… proposal=…`), `unannounced` đứng **đầu** khi có.

- [ ] **Step 1: Test đỏ** (dựng `TurnContext` bằng factory `make_ctx(**overrides)` trong test — đặt ở `tests/factories.py` để M2–M5 dùng chung):

```python
def test_lite_render_has_pending_and_goal_but_no_upcoming_list(): ...   # "Pending proposal" in lite; appointment id của upcoming NOT in lite
def test_full_render_lists_every_upcoming_id_and_iso(): ...
def test_unannounced_comes_first_in_full(): ...
def test_render_never_invents_ids(): ...   # mọi chuỗi 24 hex trong output ⊂ {ids trong ctx}
def test_previous_turn_calls_show_offered_and_resolved(): ...
def test_customer_text_is_not_part_of_render(): ...   # render không có đối số question; ctx không có trường chữ khách
```

- [ ] **Step 2: Chạy** → FAIL. **Step 3: Viết** model + `render_context`. **Step 4:** PASS.
- [ ] **Step 5: Commit** `feat(agents): add TurnContext and its two render levels`

---

### Task 1.3: `ledger.py` — `turn_ledger`

**Files:**
- Create: `app/agents/booking/ledger.py`
- Test: `tests/test_ledger.py` (Mongo)

**Interfaces — Produces:**

```python
LEDGER_COLLECTION = "turn_ledger"
class TurnLedger:
    def __init__(self, db: AsyncIOMotorDatabase): ...
    async def start(self, *, turn_id: str, user_id: str, route: str, rule: str, asked_at: datetime) -> None
        # upsert doc {turn_id, user_id, day (ngày VN), route, rule, asked_at, calls: [], pending_outcome: None,
        #             ended_by: None, reply_kind: None, ask_field: None, announced: False, created_at}
        # lần đầu mỗi tiến trình: create_index("created_at", expireAfterSeconds=settings.orch_ledger_ttl_days*86400) + index (user_id, day, asked_at)
    async def append_call(self, turn_id: str, call: CallRecord) -> None                 # $push calls
    async def set_pending_outcome(self, turn_id: str, outcome: PendingOutcome) -> None  # ghi NGAY trong vòng (§5.3)
    async def set_ask(self, turn_id: str, field: QuestionField) -> None
    async def finish(self, turn_id: str, *, ended_by: EndedBy | "persist_failed", reply_kind: ReplyKind | None) -> None
    async def mark_announced(self, turn_id: str) -> None
    async def previous_turns(self, user_id: str, *, today: date, limit: int = 2) -> list[PreviousTurn]
    async def unannounced(self, user_id: str, *, today: date) -> UnannouncedOutcome | None
    async def record_of(self, turn_id: str) -> dict | None                            # cho test/brief limit_reached
def previous_call_from(call: CallRecord) -> PreviousCall   # thuần: FreeSlots.slots/nearest → offered; ResolvedTime.start_at → resolved; ProposalCreated → proposal; reject → status "rejected"
```

Index tạo trong `ledger.py` (như `checkpoint._sync_ttl` tự lo TTL), **không** sửa `app/infrastructure/database.py` (ngoài danh sách §1.3).

- [ ] **Step 1: Test đỏ**

```python
async def test_start_append_finish_roundtrip(test_db): ...
async def test_previous_turns_returns_at_most_two_most_recent_today_typed(test_db): ...   # 3 lượt hôm nay + 1 hôm qua → 2, mới nhất trước, calls là PreviousCall
async def test_previous_call_maps_slots_resolved_and_proposal(): ...   # thuần
async def test_unannounced_only_for_approved_or_failed_not_announced(test_db): ...   # kept/rejected → None; approved+announced → None; approved+False → outcome
async def test_mark_announced_clears_unannounced(test_db): ...
async def test_ttl_index_exists_after_first_start(test_db): ...   # index_information có expireAfterSeconds == 7*86400
```

- [ ] **Step 2–4:** FAIL → viết → PASS. **Step 5: Commit** `feat(agents): add the turn ledger`

---

### Task 1.4: `load_turn_context` — nạp từ DB

**Files:**
- Modify: `app/agents/booking/context.py`
- Test: `tests/test_turn_context.py`

**Interfaces — Consumes:** `ShopService.get_status/get_hours`, `AppointmentService.upcoming_for/recent_visits`, `ProposalService.latest/closed_earlier_today`, `ConversationService.get_state/history`, `TurnLedger.previous_turns/unannounced`, `address_phrase`/`display_name` (từ `booking_graph.context`, M6 dời), `derive_address`.
**Produces:**

```python
async def load_turn_context(db, user: User, *, turn_id: str, asked_at: datetime) -> TurnContext
```

Luật nạp (code, không LLM): `pending` = `latest` nếu `status == "pending"` và `expires_at > asked_at` (`ProposalService.latest` đã chuẩn hoá expired); `recently_declined` = `latest` nếu status ∈ {rejected, superseded, expired} và đóng ≤ 10 phút trước; `upcoming` ≤ 5; `recent_visits` ≤ 3 (ngày); `open_question` = từ `previous_turns[0]` nếu `ask_field` khác None và `reply_kind == "clarify"` (`times` = số lượt liên tiếp gần nhất cùng `ask_field`); `last_bot_text` = tin assistant cuối trong `ConversationService.history`; `goal`/`queued` từ `state` (`QueuedRequest` hết hạn 30 phút bị bỏ — hằng `QUEUE_MAX_AGE_MINUTES` của gói cũ); `digest_text` = `render_digest(state.digest, state.customer_quotes)` hay `""`; `calendar_14d` = 14 ngày từ `today` với tên thứ `_WEEKDAYS`; `answers_open_question_hint=False` (resolver đặt). Nạp song song bằng `asyncio.gather` như `load_context` cũ.

- [ ] **Step 1: Test đỏ** (Mongo, tạo user bằng `AuthService.create_user`):

```python
async def test_pending_is_set_only_when_latest_is_pending_and_unexpired(test_db): ...
async def test_recently_declined_within_ten_minutes(test_db): ...
async def test_open_question_comes_from_previous_clarify_turn(test_db): ...   # ledger: ask_field="time", reply_kind="clarify" → OpenQuestion(field="time", times=1); hai lượt liên tiếp → times=2
async def test_upcoming_capped_at_five(test_db): ...
async def test_unannounced_is_loaded_from_ledger(test_db): ...
async def test_calendar_has_fourteen_days_starting_today(test_db): ...
```

- [ ] **Step 2–4:** FAIL → viết → PASS. **Step 5: Commit** `feat(agents): load TurnContext from services and ledger`

---

### Task 1.5: `resolver.py` — R0–R5

**Files:**
- Create: `app/agents/booking/resolver.py`
- Test: `tests/test_resolver.py`

**Interfaces — Consumes:** `tokens.scan_times/scan_weekdays/scan_day_offsets/has_slash_date/names_a_day/names_a_clock/digits_in/words` (gói cũ), `ButtonReply` (`booking_graph.button`, M6 dời).
**Produces:**

```python
Route = Literal["button", "classify_only", "orchestrate"]
class ResolveResult(BaseModel):
    route: Route; rule: Literal["R0","R1","R2","R3","R4","R5"]
    must_announce: bool            # R0 kèm theo nhánh khác
    answers_open_question_hint: bool
def resolve(ctx: TurnContext, question: str | ButtonReply) -> ResolveResult
def is_short_time_answer(text: str) -> bool    # ≤ 6 từ (tokens.words) VÀ có ≥1 trong: scan_times, scan_weekdays, scan_day_offsets, has_slash_date, names_a_day, names_a_clock
def mentions_time_or_number(text: str) -> bool # digits_in or names_a_day or names_a_clock or scan_weekdays
```

Thứ tự: R1 nút → R2 `ctx.pending` → R3 `ctx.open_question and is_short_time_answer` → R4 (không pending/open_question/queued/unannounced **và** `not mentions_time_or_number`) → R5. `must_announce = ctx.unannounced is not None` gắn kèm bất kỳ kết quả nào (R0 không phải nhánh riêng). Không ghi DB, không từ khoá.

- [ ] **Step 1: Test đỏ** (parametrize):

```python
def test_button_goes_to_button_path_even_with_pending(): ...
def test_pending_text_goes_to_orchestrate_r2(): ...
def test_short_time_answer_to_open_question_sets_hint_r3(): ...        # open_question(time), "3 giờ chiều" → R3, hint True
def test_long_answer_to_open_question_is_r5_without_hint(): ...        # 8 từ → R5
def test_plain_chat_without_numbers_goes_to_classify_r4(): ...        # "chào em" → R4
def test_text_with_a_weekday_skips_classify(): ...                     # "thứ năm được không" → R5
def test_queued_or_unannounced_disables_r4(): ...
def test_unannounced_marks_must_announce_on_any_rule(): ...
def test_resolver_module_has_no_string_literals_that_look_like_intent_words(): ...  # AST: không hằng chuỗi tiếng Việt ngoài tên rule
```

- [ ] **Step 2–4:** FAIL → viết → PASS. **Step 5: Commit** `feat(agents): add the state resolver`
