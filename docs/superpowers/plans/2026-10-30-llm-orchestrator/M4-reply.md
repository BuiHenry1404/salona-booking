# M4 — Nói với khách: `ReplyBrief`, Conversation Agent, guard sự thật, `classify_only`, nút bấm

Đọc `README.md` trước. Conversation Agent là agent **duy nhất** nói với khách và stream (`respond`); nó chỉ nhận `ReplyBrief` + cửa sổ lịch sử (forked, §8.3). Guard chỉ còn kiểm **sự thật** (§9.8).

**Giao:** `reply_brief.py`, `conversation.py` + `prompts/conversation.py`, `guard.py`, `classify.py` + `prompts/classify.py`, `button_path.py`.

---

### Task 4.1: `reply_brief.py` — bảng ánh xạ §5.4

**Files:**
- Create: `app/agents/booking/reply_brief.py`
- Test: `tests/test_reply_brief.py`

**Interfaces — Consumes:** `OrchestrateOutcome` (M3), `code_phrase` (gói cũ `booking_graph.phrase`; M6 dời), `FALLBACK_ANSWER` (`booking_graph.middleware.tools`), `settings.shop_phone`.
**Produces:**

```python
class ButtonOutcome(BaseModel): kind: Literal["approved","rejected","failed","not_approvable","stale"]; outcome: PendingOutcome | None
class ClassifyOutcome(BaseModel): route: Literal["social","unsupported"]
TurnOutcome = OrchestrateOutcome | ButtonOutcome | ClassifyOutcome

FAILED_FALLBACK = "Dạ {address}, {when} em chưa ghi được, {address} nhắn lại giờ khác giúp em nhé."
BUTTON_STALE_REPLY = "Đề xuất lúc nãy hết hạn rồi, anh chị nhắn lại giờ giúp em nhé."   # sao nguyên văn từ confirm.py cũ; "anh chị" → address lúc dùng
PROPOSAL_FALLBACK = "Dạ {address}, em giữ chỗ {when} cho {address} nhé, {address} đồng ý không ạ?"
UNANNOUNCED_LINE = {"approved": "đã {verb} xong {when}", "failed": "{when} em chưa ghi được"}   # verb theo kind: đặt/dời/huỷ

def build_reply_brief(ctx: TurnContext, outcome: TurnOutcome, *, previous_reply: str | None, now: datetime) -> ReplyBrief
def facts_for(record: CallRecord) -> tuple[list[Fact], list[str], list[str]]     # (facts, must_say, must_not_claim) cho MỘT dòng bảng §5.4
def reply_kind_for(outcome: TurnOutcome, ctx: TurnContext) -> ReplyKind
```

Luật chọn `kind` (code, theo thứ tự): `ClassifyOutcome` → route; `ButtonOutcome.stale` → `stale_button`; `PendingOutcome.approved` → `confirmed`; `failed`/`not_approvable` → `failed`; `rejected`/`superseded` **không** kèm `ProposalCreated` mới → `declined`; có `ProposalCreated` trong `calls` → `proposal_made`; `clarify` hoặc `ask` → `clarify`; `ended_by ∈ {limit, forced, error}` → `limit_reached` nếu `rounds_executed ≥ max` hoặc `error`, còn `forced` không clarify → `answer`; `finish.kind_hint` → `answer`/`unsupported`/`social`. `must_say`/`must_not_claim`/`facts` = hợp của `facts_for` trên mọi `CallRecord.allowed` theo bảng §5.4 + dòng `unannounced` (R0, đứng đầu `must_say`) + `recently_declined` khi có call `appointment_task`. `tone_context`: `after_decline` nếu `ctx.recently_declined` hoặc outcome rejected/superseded; `changed_time` nếu superseded + proposal mới; `returning` nếu `ctx.recent_visits`; còn lại `first_time`. `fallback`: `confirmed` → `code_phrase({"kind": booked|moved|cancelled, "address", "when": spoken, "context": tone_context}, [previous_reply] )`; `failed` → `FAILED_FALLBACK`; `proposal_made` → `PROPOSAL_FALLBACK`; `stale_button` → `BUTTON_STALE_REPLY` với "anh chị" thay bằng `address`; `limit_reached` khi `ended_by == "error"` → `FALLBACK_ANSWER` (câu cứng §10); khác → `FALLBACK_ANSWER`. `then` = "Sau giờ này em sẽ hỏi tiếp {queued[0].spoken}" khi `ProposalCreated.queued` khác rỗng. `unsupported` thêm `Fact(key="shop_phone", text=settings.shop_phone)`.

- [ ] **Step 1: Test đỏ** — **một test mỗi dòng bảng §5.4** (parametrize theo `CallRecord` giả → `(facts keys, must_say, must_not_claim)` mong đợi), cộng:

```python
def test_kind_precedence_confirmed_beats_everything()
def test_declined_without_new_proposal_vs_proposal_made_after_change()
def test_brief_not_approvable_has_expired_fact_and_no_claims()       # Review Focus #1: Fact key "expired", must_not_claim == ["booked","cancelled","moved"], kind failed
def test_unannounced_outcome_leads_must_say()
def test_fallback_for_confirmed_comes_from_code_phrase_with_the_right_time()
def test_fallback_for_failed_and_proposal_made_use_the_pinned_copy()
def test_limit_reached_still_lists_facts_gathered_so_far()
def test_unsupported_carries_the_shop_phone()
def test_queued_adds_then_and_blocks_booked_claim_for_queued_time()
```

- [ ] **Step 2–4:** FAIL → viết → PASS. **Step 5: Commit** `feat(reply): build ReplyBrief from the turn outcome`

---

### Task 4.2: `conversation.py` — Conversation Agent

**Files:**
- Create: `app/agents/booking/conversation.py`, `app/agents/booking/prompts/conversation.py`
- Test: `tests/test_conversation_agent.py`

**Interfaces — Consumes:** `history.model_history` (gói cũ), `build_chat_model`, `model_retry_middleware` không dùng (gọi model thẳng), `is_transient`.
**Produces:**

```python
RESPOND_TAG = "respond"
RESPOND_TIMEOUT_SECONDS = 20
def render_brief(brief: ReplyBrief) -> str      # khối nhãn tiếng Anh: Kind, Address, Say exactly (must_say), Facts, Ask, Never claim, Then, Previous reply, Tone, Staff note, Digest, Now
async def respond(brief: ReplyBrief, history: list[BaseMessage], question: str) -> tuple[str, str]   # (draft, source "llm" | "code")
    # messages = [SystemMessage(CONVERSATION_PROMPT), *history, HumanMessage(render_brief(brief) + UNTRUSTED + question)]
    # model = build_chat_model(tags=[RESPOND_TAG], streaming=True, max_retries=0); asyncio.wait_for(ainvoke, RESPOND_TIMEOUT_SECONDS)
    # lỗi/timeout/rỗng → (brief.fallback, "code") ; thử lại 1 lần nếu is_transient
```

`CONVERSATION_PROMPT` (tiếng Anh, §8.3): dùng `must_say` nguyên văn; không nói đã đặt/huỷ/dời nếu nằm trong `Never claim`; hỏi đúng **một** mảnh `Ask`; gọi khách bằng `Address`; không lặp `Previous reply`; ngắn, nói với người lớn tuổi; không nhắc tool/JSON; không câu mẫu tiếng Việt trong prompt.

- [ ] **Step 1: Test đỏ** (scripted qua `monkeypatch.setattr("app.agents.booking.conversation.build_chat_model", ...)`):

```python
async def test_respond_uses_a_streaming_model_tagged_respond()      # build_chat_model nhận tags=["respond"], streaming=True
async def test_customer_text_sits_only_under_untrusted_label()       # Review Focus #4: question xuất hiện đúng 1 lần, sau UNTRUSTED, không ở system
async def test_history_window_is_passed_before_the_brief()
async def test_model_error_returns_the_fallback_as_code()
async def test_timeout_returns_the_fallback()
def test_render_brief_prints_must_say_verbatim_and_every_fact_spoken()
def test_conversation_prompt_is_english()
```

- [ ] **Step 2–4:** FAIL → viết → PASS. **Step 5: Commit** `feat(reply): add the Conversation Agent`

---

### Task 4.3: `guard.py` — guard sự thật

**Files:**
- Create: `app/agents/booking/guard.py`
- Test: `tests/test_reply_guard.py`

**Interfaces — Consumes:** `strip_tool_names`, `unclaim`, `has_false_claim`, `repeats`, `clock_keys` (gói cũ `booking_graph.guard`; M6 dời vào file này), `rewrite.rewrite_once(draft, codes, previous_replies, context_block)` (gói cũ; truyền `context_block=f"Gọi khách là: {brief.address}"` vì `_address_from_block` đọc đúng dòng đó).
**Produces:**

```python
class GuardResult(BaseModel): answer: str; source: Literal["llm","code"]; violations: list[str]
def facts_from_brief(brief: ReplyBrief) -> dict        # {"clocks": {(h,m)...} từ facts.iso, "held_iso", "allowed_claims": {"booked",...} - must_not_claim}
def normalize(text: str) -> str                         # casefold + gộp khoảng trắng (không regex: split/join)
def check_reply(draft: str, brief: ReplyBrief, previous_replies: Sequence[str]) -> list[str]
    # "claim": has_false_claim(draft, facts) HOẶC câu nhận "đã đặt/huỷ/dời" khi verb đó ∈ must_not_claim (dùng _CLAIM_VERBS của gói cũ qua has_false_claim với facts không có held)
    # "missing_fact": tồn tại s ∈ must_say mà normalize(s) ∉ normalize(draft)
    # "repeat": repeats(draft, previous_replies)
async def apply_guard(draft: str, brief: ReplyBrief, previous_replies: Sequence[str], *, source: str) -> GuardResult
    # source "code" → trả nguyên (fallback đã đúng)
    # draft = strip_tool_names(draft); "claim" → draft = unclaim(draft); codes = check_reply(...)
    # codes rỗng → llm ; có → rewritten = await rewrite.rewrite_once(...); recheck
    # vẫn "missing_fact"/"claim" và brief.kind ∈ {confirmed, failed, proposal_made} → (brief.fallback, "code")
    # chỉ "repeat" → rewritten (hoặc draft nếu None) ; còn lại → draft gốc, violations ghi lại
```

- [ ] **Step 1: Test đỏ** (`rewrite_once` giả qua `monkeypatch.setattr("app.agents.booking_graph.rewrite.rewrite_once", ...)`):

```python
def test_missing_must_say_is_a_violation()
def test_must_say_match_ignores_case_and_spacing()
def test_claim_booked_when_forbidden_is_a_violation()
def test_facts_from_brief_collects_clocks_from_iso()
async def test_tool_name_in_draft_is_stripped_before_checks()
async def test_confirmed_with_wrong_time_falls_back_to_code_phrase()      # draft nói 4 giờ, must_say "3 giờ chiều" → fallback, source code
async def test_rewrite_runs_once_and_fixes_missing_fact()
async def test_only_repeat_keeps_the_rewrite()
async def test_answer_kind_with_unfixable_violation_keeps_the_draft_and_logs()
async def test_code_source_is_passed_through_untouched()
```

- [ ] **Step 2–4:** FAIL → viết → PASS. **Step 5: Commit** `feat(reply): fact-only guard over the final answer`

---

### Task 4.4: `classify.py` — `classify_only`

**Files:**
- Create: `app/agents/booking/classify.py`, `app/agents/booking/prompts/classify.py`
- Test: `tests/test_classify.py`

**Interfaces — Produces:**

```python
class ClassifyOut(BaseModel): route: Literal["social","unsupported","business"]
CLASSIFY_TIMEOUT_SECONDS = 8
async def classify_only(ctx: TurnContext, question: str) -> Literal["social","unsupported","business"]
    # build_chat_model(tags=["classify"], streaming=False, max_retries=1, reasoning_effort=None).with_structured_output(ClassifyOut)
    # messages: System(CLASSIFY_PROMPT) + Human(render_context(ctx,"lite") + UNTRUSTED + question); wait_for 8s; mọi lỗi → "business"
```

`CLASSIFY_PROMPT`: ba nhãn, định nghĩa `unsupported` = dịch vụ/việc tiệm không làm qua chat (hỏi giá chi tiết, dịch vụ lạ, việc ngoài đặt lịch/giờ mở cửa); nghi ngờ → `business`.

- [ ] **Step 1: Test đỏ**

```python
async def test_social_route_from_scripted_model()
async def test_errors_fall_back_to_business()
async def test_classify_uses_the_lite_render_without_upcoming_ids()
async def test_classify_model_is_not_tagged_respond()
def test_classify_prompt_is_english()
```

- [ ] **Step 2–4:** FAIL → viết → PASS. **Step 5: Commit** `feat(reply): add classify_only for chit-chat and unsupported asks`

---

### Task 4.5: `button_path.py`

**Files:**
- Create: `app/agents/booking/button_path.py`
- Test: `tests/test_button_path.py` (Mongo)

**Interfaces — Consumes:** `ButtonReply` (gói cũ `button.py`; M6 dời), `apply_decision` (M3).
**Produces:**

```python
# BUTTON_STALE_REPLY import từ reply_brief.py (Task 4.1)
def stale_reply(address: str) -> str                       # BUTTON_STALE_REPLY.replace("anh chị", address)
async def run_button(deps: OrchestratorDeps, button: ButtonReply) -> ButtonOutcome
    # ctx.pending None hoặc button.proposal_id != ctx.pending.id → ButtonOutcome(kind="stale", outcome=None), KHÔNG chạm DB
    # "yes" → apply_decision(deps, "approve") → kind theo outcome (approved|failed|not_approvable) ; "no" → apply_decision(deps, "reject") → "rejected"
```

- [ ] **Step 1: Test đỏ**

```python
async def test_yes_on_the_pending_proposal_approves_and_writes(test_db, user)
async def test_no_rejects_without_writing(test_db, user)
async def test_mismatched_id_is_stale_and_touches_nothing(test_db, user)       # Review Focus #5 (phần nút)
async def test_yes_on_expired_is_not_approvable(test_db, user)
def test_stale_reply_uses_the_address()
```

- [ ] **Step 2–4:** FAIL → viết → PASS. **Step 5: Commit** `feat(reply): button path through the single approve gate`
