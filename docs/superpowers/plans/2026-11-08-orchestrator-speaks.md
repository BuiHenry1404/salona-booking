# Orchestrator tự nói chuyện với khách — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Orchestrator vừa quyết vừa viết câu trả lời cho khách (bỏ ReplyBrief + Conversation Agent), câu được guard kiểm trước khi hiện, khách đặt được nhiều lịch và mọi lịch sau đều được giữ chỗ.

**Architecture:** Làm tiếp trên gói `app/agents/booking/`. Graph còn `resolve → (button_path) → orchestrate → record`. Orchestrator kết thúc mọi lượt bằng tool `reply(text, asks, service, stated)`; `OrchestratorGuard` kiểm câu trong `reply` bằng code, từ chối thì Orchestrator viết lại một lần, rồi câu mẫu code. Giữ chỗ là collection `slot_holds` (unique theo `slot_key`) mà mọi đường đặt lịch đều phải đi qua.

**Tech Stack:** Python 3.12, FastAPI, python-socketio, LangGraph + LangChain `create_agent`, Motor/MongoDB replica set `rs0`, pytest (asyncio auto), React frontend (Vitest).

**Spec:** `docs/superpowers/specs/2026-11-08-orchestrator-speaks-design.md` (đọc cả §4.5 user story). Spec cũ `2026-10-29-llm-orchestrator-design.md` vẫn đúng ở mọi chỗ spec mới không nhắc.

## Global Constraints

- Nhánh `feat/llm-orchestrator`; không commit thẳng `main`; đích merge `henry/develop`. Commit message tiếng Anh, ngắn; trailer `Co-Authored-By` ghi đúng model đã viết commit (quyết định 30/10).
- Agent **không có tool ghi**. Chỉ `ProposalService.approve` ghi lịch; trong `app/agents/` chỉ `orchestrator/decide.py` gọi `approve`. `tests/test_write_fence.py` xanh ở mọi commit.
- Mọi logic đặt lịch / giữ chỗ nằm trong `app/services/`; agent tools, REST, nút bấm là lớp mỏng.
- Cấu hình chỉ từ `.env`; thêm setting = sửa `app/core/config.py` + `.env.example` + `.env`. Mọi URI Mongo có `?directConnection=true`.
- Index `appointments.slot_keys` giữ **partial** `{status: "booked"}`; huỷ = lật `status`. `DuplicateKeyError` đổi thành lỗi miền ngay trong repository.
- Regex: chỉ `app/agents/booking/draft_checks.py` được `import re` (budget `{"draft_checks.py": 2}`). Không từ điển từ khoá ý định ở module nào.
- Prompt tiếng Anh, không câu mẫu tiếng Việt. Khách được gọi theo `TurnContext.customer.address_phrase` (mặc định "anh chị", không đoán giới tính từ tên).
- Mọi id/iso trong văn bản gửi model lấy từ object; chữ khách chỉ nằm dưới nhãn `Customer said (untrusted):`.
- Log có cấu trúc, `extra` luôn có `user_id`, `turn_id`, `thread_id`; không log chữ khách.
- Giá trị cố định: `MAX_ACTIVE_PROPOSALS = 3` (1 đang hỏi + 2 giữ chỗ chờ hỏi); chỗ giữ hết hạn `PROPOSAL_MAX_AGE_MINUTES = 10` phút **kể từ khi tạo**, không gia hạn; Orchestrator viết lại câu bị từ chối **tối đa 1 lần**; nhắc "phải gọi `reply`" **tối đa 1 lần**; `orch_max_tool_rounds = 8`; Orchestrator `reasoning_effort="low"`.
- Câu có số điện thoại tiệm (`settings.shop_phone`) **chỉ** ở lượt `approve` thất bại. Không câu mẫu nào khác được mời "gọi tiệm".
- Không stream câu trả lời: không còn event `token`; câu tới khách chỉ qua `complete` sau guard.
- Đúng 1 worker.
- Test kiểm hành vi, không ghim chữ prompt; một bảng = một hàm `parametrize`; test không gọi Azure (`-m llm` vẫn bị loại). Chạy thật phải hỏi chủ dự án trước mỗi lần.
- Mỗi task: test đỏ → chạy thấy đỏ → code → xanh → `pytest -q` toàn suite (cần `docker compose up -d mongo`) → commit.

## Review Focus

1. **Chỗ giữ hết hạn trước khi tới lượt hỏi** (đề xuất `held` tạo lúc 10:00, lịch đầu xác nhận lúc 10:12) → không được đưa lên "đang hỏi", không có nút; Orchestrator thấy nó hết hạn trong bối cảnh. — Task 3 `test_expired_held_is_not_promoted`.
2. **Hai khách cùng giữ một giờ cùng lúc** → đúng một người được. — Task 2 `test_concurrent_holds_on_one_slot_one_wins`.
3. **Câu trả lời nhắc lại giờ khách vừa nói hoặc giờ gợi ý gần nhất** ("3 giờ sáng hay 3 giờ chiều ạ?", "4 giờ còn trống") → không bị coi là giờ bịa. — Task 5, các dòng `echoes_customer_clock`, `nearest_slot_clock` của `test_check_reply_table`.
4. **Khách đồng ý lịch đầu và thêm lịch trong cùng tin** ("ừ được em, đặt thêm cho cháu 5 giờ nha") → lịch đầu được ghi (veto không coi giờ của lịch thêm là "giờ khác"), lịch thêm được giữ chỗ. — Task 4 `test_approve_and_add_booking_in_one_message`.
5. **Bấm nút cho đề xuất đang giữ chỗ (chưa tới lượt hỏi) hoặc đề xuất cũ** → `stale`, DB không đổi. — Task 8 `test_button_for_a_held_proposal_is_stale`.

---

### Task 1: Xoá gói cũ `booking_graph`

Làm đúng **Task 6.3** trong `docs/superpowers/plans/2026-10-30-llm-orchestrator/M6-cutover.md` (danh sách file xoá / sửa, Step 0–3), với hai chỉnh:

- `tests/test_regex_budget.py`: `EXPECTED_BUDGET = {"draft_checks.py": 2}` (không phải `guard.py`).
- `tests/test_write_fence.py` `ALLOWED` theo bản trong M6-cutover.md; dòng `events.py … cancel` chỉ giữ nếu `grep -n "\.cancel(" app/agents/booking/events.py` còn thấy.

- [ ] **Step 1:** Làm Step 0–1 của Task 6.3. `grep -rn "booking_graph" app tests scripts main.py` → rỗng.
- [ ] **Step 2:** `scripts/terminal_chat.py` (untracked, của chủ dự án) **không đụng**; ghi vào ledger rằng nó sẽ hỏng.
- [ ] **Step 3:** `.venv/bin/pytest -q` → xanh. Commit `refactor(agents): remove the supervisor graph and its tests` (body ghi số test).

---

### Task 2: Giữ chỗ — `SlotHoldService` chặn mọi đường đặt lịch

**Files:**
- Create: `app/models/slot_hold.py`, `app/repositories/slot_hold.py`, `app/services/slot_hold.py`
- Modify: `app/core/errors.py` (thêm `SlotHeldError`), `app/infrastructure/database.py` (`ensure_indexes`), `app/services/appointment.py` (`create`, `reschedule`, `find_free_slots`)
- Test: `tests/test_slot_holds.py` (mới), `tests/test_api_flow.py` (thêm 1 test)

**Interfaces:**
- Produces:
  - `SlotHold(BaseDocument)`: `slot_key: str`, `user_id: str`, `proposal_id: str | None`, `expires_at: datetime`.
  - `class SlotHeldError(AppError)`: `status_code = 409`; `__init__(self, held_until: datetime)`; message `"Giờ này đang được giữ cho một khách khác tới {HH:MM}"` (giờ VN).
  - `SlotHoldService(db)`:
    - `async def hold(self, user_id: str, start_at: datetime, duration_minutes: int, *, proposal_id: str | None, expires_at: datetime, session=None) -> None` — raise `SlotHeldError` (khách khác giữ) hoặc `SlotTakenError` (đã có lịch `booked`).
    - `async def release(self, *, proposal_id: str, session=None) -> int`
    - `def booking_guard(self, user_id: str, start_at: datetime, duration_minutes: int, *, session=None) -> AsyncContextManager[None]`
    - `async def held_starts(self, start: datetime, end: datetime) -> set[datetime]` — mốc slot đang bị giữ (mọi khách, còn hạn).
- Index: `slot_holds` unique trên `slot_key` (tên `uniq_slot_hold_key`) + TTL `expires_at` (`expireAfterSeconds=0`, chỉ để dọn rác; logic luôn tự lọc theo `expires_at`).

**Thuật toán** (không suy ra được từ chữ ký):
- `hold`: xoá các doc `slot_key ∈ keys` đã hết hạn; chèn một doc mỗi `slot_key`; `DuplicateKeyError` → đọc doc trùng: cùng `user_id` và còn hạn → coi như đã giữ (không lỗi); khác → xoá các doc vừa chèn của lần này, raise `SlotHeldError(doc.expires_at)`. Chèn xong thì kiểm `AppointmentRepository.find_conflicting(keys)`: có lịch `booked` → xoá các doc vừa chèn, raise `SlotTakenError`. Trong `session`: đọc trước, chỉ chèn key chưa có (tránh `DuplicateKeyError` làm abort transaction ngoài ý muốn); lỗi trùng trong transaction đổi thành `SlotHeldError` sau khi abort, theo đúng khuôn `SlotTakenError` hiện có ở `AppointmentService.create`.
- `booking_guard`: giữ tạm các key với `proposal_id=None`, hạn 60 giây, bỏ qua key mà chính `user_id` đang giữ; `yield`; luôn xoá các doc tạm của lần này. Hai người cùng đặt một giờ thì `slot_holds` là điểm tuần tự hoá: ai chèn trước thắng.
- `AppointmentService.create` và `reschedule` bọc bước ghi trong `booking_guard` (mọi `created_via`, kể cả admin `for_user_id`; chủ tiệm **không** ghi đè). `find_free_slots` loại thêm `held_starts(...)` của ngày đó (giữ của mọi khách, kể cả chính khách — tránh đề xuất trùng giờ mình đang giữ).

- [ ] **Step 1: Test đỏ** — `tests/test_slot_holds.py`:

```python
async def test_hold_blocks_another_customer(): ...      # B.hold cùng giờ → SlotHeldError; e.held_until == A.expires_at
async def test_same_customer_can_hold_own_slot_again(): ...  # A.hold hai lần → không lỗi, 1 doc/slot_key
async def test_expired_hold_blocks_nobody(): ...        # doc expires_at < now → B.hold thành công
async def test_concurrent_holds_on_one_slot_one_wins(): ...  # asyncio.gather(A.hold, B.hold) → đúng 1 thành công, 1 SlotHeldError
async def test_hold_on_a_booked_slot_is_slot_taken(): ...    # lịch booked rồi → SlotTakenError, không còn doc hold
async def test_booking_on_slot_held_by_other_is_refused(): ...  # AppointmentService.create(B, giờ A giữ, created_via="web") → SlotHeldError, "đang được giữ" in str
async def test_booking_own_held_slot_is_allowed(): ...  # A.create giờ A giữ → booked
async def test_reschedule_into_held_slot_is_refused(): ...
async def test_find_free_slots_skips_held_slots(): ...  # giờ đang giữ không có trong find_free_slots(day)
```

`tests/test_api_flow.py::test_post_appointment_on_held_slot_returns_409` — admin `POST /api/v1/appointments` với `for_user_id` vào giờ khách khác đang giữ → 409, `"đang được giữ"` trong `detail`/`message` (theo khuôn lỗi 409 hiện có).

- [ ] **Step 2:** `.venv/bin/pytest -q tests/test_slot_holds.py tests/test_api_flow.py -k "hold"` → FAIL (`ModuleNotFoundError: app.services.slot_hold`).
- [ ] **Step 3:** Cài model/repo/service/lỗi/index như Interfaces + Thuật toán; sửa `AppointmentService`.
- [ ] **Step 4:** Chạy lại → PASS; `.venv/bin/pytest -q` toàn suite → xanh (test cũ của `AppointmentService`/`ProposalService.approve` phải còn xanh: duyệt đề xuất chưa có hold vẫn qua `booking_guard`).
- [ ] **Step 5:** Commit `feat(booking): hold slots so no other path can take them`

---

### Task 3: `ProposalService` nhiều đề xuất + giữ chỗ

**Files:**
- Modify: `app/models/proposal.py` (`ProposalStatus` thêm `"held"`), `app/repositories/proposal.py`, `app/services/proposal.py`, `app/core/errors.py` (`TooManyProposalsError`)
- Test: `tests/test_proposal_service.py`

**Interfaces:**
- Consumes: `SlotHoldService.hold / release` (Task 2).
- Produces:
  - `MAX_ACTIVE_PROPOSALS = 3` trong `app/services/proposal.py`.
  - `class TooManyProposalsError(AppError)` (`status_code = 409`).
  - `@dataclass class ActiveProposals: asking: Proposal | None; held: list[Proposal]; expired_now: list[Proposal]` (held theo `created_at` tăng dần; `expired_now` = đề xuất `held`/`pending` vừa bị lật `expired` trong lần đọc này).
  - `ProposalService.create(user, kind, start_at, note, *, target=None) -> Proposal` — **không còn supersede**; trạng thái `"pending"` (đang hỏi) nếu khách chưa có đề xuất đang hỏi còn hạn, ngược lại `"held"`; `kind in ("book","move")` → `SlotHoldService.hold(..., proposal_id=str(p.id), expires_at=p.expires_at)`; hold lỗi → đề xuất chuyển `failed` rồi raise lại lỗi đó. Đã có ≥ `MAX_ACTIVE_PROPOSALS` đề xuất `pending`+`held` còn hạn → `TooManyProposalsError` (không tạo gì).
  - `ProposalService.active(user) -> ActiveProposals` — đọc mọi `pending`/`held` của khách; cái nào quá `expires_at` → `expired` + `release`; không còn `pending` thì đưa `held` **còn hạn** cũ nhất lên `pending`.
  - Mọi đường đóng (`approve` thành công, `reject`, `supersede`, `mark_failed`, `mark_unclear` → rejected, hết hạn) gọi `SlotHoldService.release(proposal_id=...)` (cùng `session` nếu có). `approve` vẫn chỉ claim từ `pending` — `held` không bao giờ được duyệt trực tiếp.
  - Giữ `latest()` cho chỗ đọc "vừa bị từ chối" (`recently_declined`); mọi chỗ cần "đề xuất đang chờ" chuyển sang `active().asking` ở Task 4/7.

- [ ] **Step 1: Test đỏ** — thêm vào `tests/test_proposal_service.py`:

```python
async def test_second_proposal_is_held_and_first_stays_pending(): ...
    # p1 = create(15:00), p2 = create(16:00) → p1.status == "pending", p2.status == "held"; cả hai giờ bị giữ (SlotHoldService chặn khách khác)
async def test_fourth_active_proposal_is_refused(): ...   # 3 cái rồi → TooManyProposalsError, proposals count không đổi
async def test_approving_the_asking_one_promotes_the_oldest_held(): ...
    # approve(p1) → active().asking.id == p2.id; giờ 15:00 là lịch booked, hold của p1 đã xoá
async def test_rejecting_releases_its_hold(): ...         # reject(p1) → khách khác hold 15:00 được
async def test_expired_held_is_not_promoted(): ...        # p2 quá hạn → active(): asking None, held [], expired_now == [p2], p2.status == "expired", hold xoá
async def test_create_on_slot_held_by_other_customer_fails_cleanly(): ...
    # → SlotHeldError; đề xuất vừa tạo ở trạng thái "failed", không có hold nào của nó
```

Sửa test cũ còn kỳ vọng `create` supersede đề xuất trước (đổi assert sang hành vi mới, ghi trong commit body).

- [ ] **Step 2:** Chạy → FAIL. **Step 3:** Cài. **Step 4:** File test → PASS; toàn suite → xanh. **Step 5:** Commit `feat(booking): let a customer hold up to three proposals`

---

### Task 4: Nhiều lịch trong chat — bỏ hàng chờ cũ, nới luật (e)(h)(j), bối cảnh có `held`

**Files:**
- Modify: `app/agents/booking/tools.py` (`propose_appointment`: bỏ `queued`; `SlotHeldError` → `slot_unavailable` + `asked` + `nearest`; `TooManyProposalsError` → `{"error": "too_many_proposals"}`), `app/agents/booking/specialists/appointment.py` (bỏ `_persist_queued`), `app/agents/booking/schemas.py` (`ProposalCreated` bỏ `queued`; `TimeHint.anchor` bỏ `"queued"`, bỏ `queue_id`; `appointment_args_model(appointment_ids)` bỏ tham số `queue_ids`), `app/agents/booking/orchestrator/tools.py`, `app/agents/booking/orchestrator/guard.py` (luật (c)(d) bỏ nhánh queue; (e)(h)(j) như dưới), `app/agents/booking/orchestrator/decide.py` (`veto(..., held=...)` thay `queued`), `app/agents/booking/context.py` (`TurnContext.held`, bỏ `queued_requests`/`live_queue`/`QUEUE_MAX_AGE_MINUTES`, nạp từ `ProposalService.active`), `app/models/conversation.py` + `app/services/conversation_state.py` (bỏ `QueuedRequest`, `queued`, `set_queued`), các prompt specialist/Orchestrator nhắc `queued`.
- Test: `tests/test_tools.py`, `tests/test_orchestrator_guard.py`, `tests/test_orchestrator_decide.py`, `tests/test_turn_context.py`, `tests/test_scenarios.py`; xoá test chỉ kiểm hàng chờ (ghi tên test thay thế trong commit body — chính sách test §4).

**Interfaces:**
- Consumes: `ProposalService.active`, `MAX_ACTIVE_PROPOSALS`, `TooManyProposalsError` (Task 3).
- Produces:
  - `TurnContext.pending: PendingProposal | None` = `active().asking`; `TurnContext.held: list[PendingProposal]` = `active().held`; `TurnContext.expired_held: list[PendingProposal]` = `active().expired_now`. `render_context` in `held` như `pending` (mỗi cái một dòng có id + spoken + hạn) và mỗi `expired_held` một dòng `Expired hold`.
  - `veto(label, aside, question, pending, now, held: Sequence[datetime] = ())` — `held` = giờ của `ctx.held` + các `ProposalCreated` của lượt này; giờ thuộc `held` không bao giờ là "giờ khác" (thay đúng chỗ `queued` cũ).
  - Luật (e): mã mới `too_many_proposals` khi `len(ctx.held) + (ctx.pending is not None) + proposals_created >= MAX_ACTIVE_PROPOSALS`; giữ mệnh đề "một `propose_*` mỗi vòng" (mã `one_proposal_per_round`). Bỏ mã `one_proposal_per_turn`.
  - Luật (h): `appointment_task(action="propose_book")` **được** chạy khi đề xuất đang hỏi chưa quyết; `propose_move`/`propose_cancel`/`reply` vẫn `must_decide_pending`.
  - Luật (j): sau `approved` trong lượt, `propose_book` **được** (lịch thêm); `propose_move`/`propose_cancel` vẫn `already_approved`. *(quyết định người lập plan: tool đã chặn trùng bằng `already_yours`)*
  - `OneProposalPerTaskMiddleware` giữ nguyên (mỗi task một đề xuất; nhiều lịch = nhiều task).

- [ ] **Step 1: Test đỏ**
  - `test_orchestrator_guard.py::test_rule_table` thêm các dòng (parametrize sẵn có): `propose_book_while_pending_undecided → allowed`, `propose_move_while_pending_undecided → must_decide_pending`, `fourth_active_proposal → too_many_proposals`, `two_proposes_same_round → one_proposal_per_round`, `propose_book_after_approved → allowed`, `propose_cancel_after_approved → already_approved`.
  - `test_orchestrator_decide.py::test_approve_naming_a_held_time_stays_approve` — pending 15:00, held 17:00, câu "ừ được em, cháu chị 5 giờ chiều nha" → `veto(...).label == "approve"`.
  - `test_tools.py::test_propose_on_slot_held_by_other_is_slot_unavailable` — JSON có `error == "slot_unavailable"`, `asked`, `nearest` không chứa giờ đó.
  - `test_tools.py::test_propose_beyond_cap_is_too_many_proposals`.
  - `test_scenarios.py::test_approve_and_add_booking_in_one_message` — pending p1 15:00; Orchestrator giả: `decide_pending(approve)`, `appointment_task(propose_book 17:00)`, kết thúc lượt (dùng `finish` cho tới Task 6) → p1 `approved` + lịch booked 15:00; đề xuất mới 17:00 là `pending` (đang hỏi, vì p1 đã đóng); không có `superseded`.
  - `test_scenarios.py::test_two_bookings_in_one_message_hold_both` — hai `propose_book` ở hai vòng → một `pending` + một `held`, khách khác không giữ được cả hai giờ.
  - Bối cảnh: `test_turn_context.py::test_context_lists_held_proposals_with_ids` — `render_context(ctx, "full")` chứa id của mỗi `held` (lấy từ object).
  - `test_turn_context.py::test_context_shows_a_hold_that_just_expired` — `active().expired_now` có p2 → `ctx.expired_held` có p2 và `render_context` có dòng `Expired hold` mang id/spoken của p2 (Orchestrator đề xuất lại, spec §6).
- [ ] **Step 2:** Chạy → FAIL. **Step 3:** Cài; `grep -rn "queued\|queue_id\|QueuedRequest\|set_queued" app/agents/booking app/models app/services` → rỗng (trừ comment lịch sử nếu có, nên xoá luôn).
- [ ] **Step 4:** PASS; toàn suite xanh. **Step 5:** Commit `feat(agents): book several appointments with every later slot held`

---

### Task 5: Kiểm câu trả lời bằng code — `reply_check.py`

**Files:**
- Create: `app/agents/booking/reply_check.py`
- Modify: `app/agents/booking/guard.py` (giữ `normalize`, `_claim_violation`, `_forbidden_verb_claim`, `strip_tool_names` qua `draft_checks`; bỏ `facts_from_brief`, `check_reply`, `apply_guard`, `missing_question`, `bare_facts`, `_FALLBACK_KINDS`), `app/agents/booking/phrase.py` (giữ `code_phrase`)
- Test: `tests/test_reply_check.py` (mới); xoá phần `tests/test_reply_guard.py` kiểm hàm đã bỏ (giữ test `normalize`/claim còn dùng, chuyển sang `test_reply_check.py`).

**Interfaces:**
- Consumes: `TurnContext` (Task 4), `CallRecord`, `PendingOutcome`, `ProposalCreated`, `TaskResult` (schemas hiện có).
- Produces:

```python
class ReplyFacts(BaseModel):
    clocks: set[tuple[int, int, str | None]]   # mọi mốc giờ có thật của lượt
    allowed_claims: set[Literal["booked", "moved", "cancelled"]]
    must_mention: list[str]                     # spoken bắt buộc (đề xuất mới, R0, kết quả duyệt)
    needs_phone: bool                           # chỉ True khi approve thất bại lượt này
    announce_turn_id: str | None                # R0 cần báo
    announce_spoken: str | None
    fallback: str                               # câu mẫu code của lượt

def build_reply_facts(ctx: TurnContext, calls: list[CallRecord], pending_outcome: PendingOutcome | None, question: str) -> ReplyFacts
def check_reply_text(text: str, facts: ReplyFacts) -> list[str]   # mã: "claim", "unknown_time", "missing_fact", "missing_phone", "phone_not_allowed"
def is_announced(text: str, facts: ReplyFacts) -> bool             # R0 đã báo: announce_spoken có trong câu
```

Nguồn `clocks`: `ctx.pending`, `ctx.held`, `ctx.upcoming`, giờ mở/đóng cửa, mọi `Moment` trong kết quả call được phép của lượt (`start_at`, `asked`, `nearest`, `slots`, `appointments`, `until`, `at`, `next_change`), `pending_outcome.spoken`, **và** giờ trong `question` (`tokens.scan_spoken_clocks` / `scan_times`) — khách được nhắc lại giờ của chính mình. `allowed_claims`: claim tương ứng `pending_outcome.outcome == "approved"` (theo `kind`) và R0 `approved`; còn lại rỗng. `fallback` (thứ tự): approve thất bại → `FAILED_FALLBACK` (đổi câu: nêu giờ, "chưa ghi được", và `settings.shop_phone`); approved → `code_phrase(...)`; có `ProposalCreated` mới hoặc `ctx.pending` → `PROPOSAL_FALLBACK`; còn lại → `GENERIC_FALLBACK = "Dạ {address} nói lại giúp em được không ạ?"` (không mời gọi tiệm). Chuyển `PROPOSAL_FALLBACK`, `FAILED_FALLBACK`, `BUTTON_STALE_REPLY`, `unannounced_line` sang module này. `FALLBACK_ANSWER` ("gọi trực tiếp cho tiệm") **không** còn được dùng làm câu mẫu.

- [ ] **Step 1: Test đỏ** — `tests/test_reply_check.py::test_check_reply_table` (một hàm parametrize, mỗi dòng: facts dựng từ ctx/calls giả, câu, mã kỳ vọng):

| id | tình huống | câu | mã |
|---|---|---|---|
| `claim_before_approve` | có đề xuất mới 15:00, chưa duyệt | "Dạ em đặt xong 3 giờ chiều mai cho chị rồi ạ." | `["claim"]` |
| `claim_after_approve` | approve 15:00 lượt này | "Dạ em đặt xong 3 giờ chiều mai cho chị rồi ạ." | `[]` |
| `invented_time` | đề xuất 15:00 | "Em giữ chỗ 4 giờ chiều mai cho chị nhé, chị đồng ý không ạ?" | `["unknown_time", "missing_fact"]` |
| `echoes_customer_clock` | khách: "mai 3 giờ", needs_info | "Dạ chị Lan, 3 giờ sáng hay 3 giờ chiều ạ?" | `[]` |
| `nearest_slot_clock` | slot_unavailable 15:00, nearest 16:00 | "Dạ 3 giờ có người đặt rồi, 4 giờ chiều còn trống ạ." | `[]` |
| `proposal_missing_time` | đề xuất 15:00 | "Dạ chị đồng ý không ạ?" | `["missing_fact"]` |
| `failed_without_phone` | approve failed | "Dạ lịch 3 giờ chiều mai em chưa ghi được ạ." | `["missing_phone"]` |
| `phone_when_nothing_failed` | đề xuất 15:00 | câu có `settings.shop_phone` | `["phone_not_allowed"]` |
| `r0_missing` | R0 approved 15:00, lượt chào | "Dạ chào chị ạ." | `["missing_fact"]` |

Thêm `test_fallback_never_mentions_the_phone_unless_approve_failed` (parametrize các nhánh fallback) và `test_is_announced_detects_r0_spoken`.

- [ ] **Step 2:** FAIL. **Step 3:** Cài. **Step 4:** PASS; toàn suite xanh (graph cũ vẫn chạy `apply_guard` → để `guard.apply_guard` sống tới Task 7, chỉ thêm module mới ở task này; xoá các hàm cũ ở Task 7). **Step 5:** Commit `feat(agents): check the orchestrator's reply against the turn's facts`

---

### Task 6: Tool `reply` — Orchestrator tự nói, guard kiểm trước khi nhận

**Files:**
- Modify: `app/agents/booking/orchestrator/tools.py` (thay `finish` + `ask_customer` bằng `reply`), `orchestrator/guard.py`, `orchestrator/agent.py` (`OrchestrateOutcome`), `orchestrator/state.py`, `app/agents/booking/schemas.py` (`ReplyArgs`, `EndedBy`), `prompts/orchestrator.py`
- Test: `tests/test_orchestrator_guard.py`, `tests/test_orchestrator_agent.py`, `tests/test_prompts.py` (bất biến tiếng Anh)

**Interfaces:**
- Consumes: `build_reply_facts`, `check_reply_text`, `ReplyFacts` (Task 5).
- Produces:

```python
class ReplyArgs(BaseModel):          # strict-safe: không default, tuỳ chọn = | None
    text: str
    asks: QuestionField | None
    service: str | None
    stated: list[str]                # ≤ 2, như `finish.stated` cũ
EndedBy = Literal["reply", "forced", "limit", "error"]
# OrchestrateOutcome: bỏ `finish`, `ask`; thêm
#   reply: ReplyArgs | None          # câu ĐÃ qua guard (hoặc None)
#   answer: str                      # câu gửi khách cuối cùng
#   answer_source: Literal["llm", "code"]
#   violations: list[str]
#   announced_turn_id: str | None    # = facts.announce_turn_id khi is_announced(answer, facts)
# TaskSpec thêm: note: str | None   # lời dặn của Orchestrator (spec §5)
```

- **Lời dặn trong gói chuyển giao (spec §5):** `appointment_task(action, args, note)` và `shop_info_task(action, args, note)` có thêm `note: str | None` (strict-safe, ≤ 200 ký tự, cắt bằng `single_line`); đi vào `TaskSpec.note`; `specialists/base.py::task_message` in nó trên một dòng `Orchestrator note:` riêng, **sau** khối task và **trước** nhãn untrusted. Không id/iso nào được lấy từ `note` (specialist vẫn lấy từ `args` + bối cảnh).
- `reply` chạy một mình trong vòng (luật (g) như `finish` cũ; (h) như Task 4). Trong `awrap_tool_call` của `reply`: `strip_tool_names(text)`, rồi `check_reply_text(text, facts)` với `facts = build_reply_facts(ctx, calls_so_far, pending_outcome, question)`. Rỗng → ghi `ended_by="reply"`, `reply_args`, `answer=text`, `answer_source="llm"`, nhảy END ở `abefore_model` kế (như `finish` cũ). Có mã → lần đầu: `ToolMessage(status="error")` với `error_code="reply_rejected"` + danh sách mã + gợi ý tiếng Anh (vd "missing_fact: the reply must contain these exact phrases: …"; lấy chuỗi từ `facts.must_mention`, không chữ khách), state `reply_rejections += 1`; lần hai → nhận kết thúc với `answer=facts.fallback`, `answer_source="code"`, `violations=mã`, log `guard_violation` (`fixed_by="fallback"`).
- `aafter_model`: AIMessage không tool call → lần đầu: thêm `HumanMessage("You must end the turn by calling reply.")` và `jump_to="model"` (tính vào trần vòng), log `orch_text_end` (`extra` có `thread_id` từ `get_config()`); lần hai → `ended_by="reply"`, `answer=facts.fallback`, `answer_source="code"`. Bỏ đường "chữ thường = `finish(answer)`" của M6b.
- `ended_by` `forced`/`limit`/`error`: `answer = facts.fallback` (`error` mà chưa có call được phép → `TURN_ERROR_MESSAGE` như hiện tại ở `events.py`).
- Prompt Orchestrator: thêm phần viết câu trả lời (tiếng Việt cho khách, ngắn, dễ hiểu với người lớn tuổi, xưng "em", gọi khách theo `Address`, nêu nguyên văn `spoken` của giờ, không nhận "đã đặt" khi chưa duyệt, chỉ đưa số điện thoại khi duyệt thất bại); không câu mẫu tiếng Việt.

- [ ] **Step 1: Test đỏ**
  - `test_orchestrator_agent.py::test_valid_reply_ends_the_turn_with_the_model_text` — scripted: `appointment_task(propose_book)` → `reply(text="Em giữ chỗ {spoken} cho chị, chị đồng ý không ạ?", asks=None, service=None, stated=[])` → `out.ended_by == "reply"`, `out.answer` == text, `out.answer_source == "llm"`.
  - `…::test_rejected_reply_is_rewritten_once` — `reply("Dạ em đặt xong … rồi ạ.")` rồi `reply(câu đúng)` → `answer` == câu đúng; model nhận đúng một `ToolMessage` lỗi chứa `"claim"`.
  - `…::test_second_rejected_reply_falls_back_to_code` — hai `reply` sai → `answer_source == "code"`, `answer` chứa `spoken` của đề xuất, `"claim" in out.violations`.
  - `…::test_plain_text_gets_one_reminder_then_reply` — `AIMessage("Da")` rồi `reply(câu đúng)` → `ended_by == "reply"`, model được gọi 2 lần, lần 2 thấy lời nhắc.
  - `…::test_plain_text_twice_falls_back_to_code` — `answer_source == "code"`, không `error`.
  - `test_orchestrator_guard.py::test_rule_table` thêm: `reply_with_other_tool_same_round → serialize_required`, `reply_while_pending_undecided → must_decide_pending`.
  - `test_prompts.py`: Orchestrator prompt vẫn qua `test_every_prompt_is_english_without_vietnamese_samples`.
  - `test_specialist_base.py::test_task_message_puts_the_orchestrator_note_on_its_own_line` — `TaskSpec(note="khách muốn làm cho con gái")` → dòng `Orchestrator note:` nằm sau khối task và trước nhãn untrusted; `note=None` → không có dòng đó.
- [ ] **Step 2:** FAIL. **Step 3:** Cài. Graph tạm đọc `outcome.answer`/`answer_source` thay brief → respond (đủ để toàn suite xanh; dọn hẳn ở Task 7).
- [ ] **Step 4:** PASS; toàn suite xanh. **Step 5:** Commit `feat(orchestrator): end every turn with a checked reply`

---

### Task 7: Graph mới, không stream, bỏ ReplyBrief / Conversation / GoalProgress

**Files:**
- Modify: `app/agents/booking/graph.py` (`START → resolve → button_path | orchestrate → record → END`), `events.py` (bỏ nhánh `on_chat_model_stream`/`token`; `CONTROL_TOOL_NAMES` = `appointment_task`, `shop_info_task`, `decide_pending`, `reply`; `complete.proposal` = `ProposalService.active(user).asking`), `ledger.py` (`finish(turn_id, *, ended_by)` — bỏ `reply_kind`; `set_ask` giữ), `context.py` (`PreviousTurn` bỏ `reply_kind`; `_open_question` chỉ dựa `ask_field`; bỏ `goal`), `resolver.py` (chỉ R0 + R1; R2/R3/R5 gộp `orchestrate`), `app/models/conversation.py` + `app/services/conversation_state.py` (bỏ `goal`/`GoalProgress`/`record_goal_progress`), `app/agents/booking/__init__.py`
- Delete: `reply_brief.py`, `conversation.py`, `rewrite.py`, `progress.py`, `prompts/conversation.py`, `prompts/rewrite.py`; trong `guard.py` các hàm cũ đã thay ở Task 5.
- Test: `tests/test_graph_turn.py`, `tests/test_run_turn.py`, `tests/test_scenarios.py`, `tests/test_resolver.py`; xoá `test_reply_brief.py`, `test_conversation_agent.py`, `test_rewrite.py`, `test_progress.py` (ghi test thay thế trong commit body).

**Interfaces:**
- Consumes: `OrchestrateOutcome.answer/answer_source/reply/announced_turn_id` (Task 6), `ProposalService.active` (Task 3).
- Produces: `run_turn` phát đúng chuỗi `turn_started`, `tool_started`/`tool_finished`, một `complete {"answer", "proposal"}` hoặc `error` — **không** `token`. Graph lấy `announced_turn_id` từ `OrchestrateOutcome` (không tự dựng lại facts).

- [ ] **Step 1: Test đỏ**
  - `test_run_turn.py::test_turn_emits_no_token_events` — model giả của Orchestrator có stream chunk → không event `token` nào; có `complete`.
  - `test_run_turn.py::test_complete_proposal_is_the_asking_one` — p1 pending + p2 held → `complete.proposal.id == p1`; sau khi duyệt p1 → lượt sau `complete.proposal.id == p2`.
  - `test_scenarios.py`: viết lại các kịch bản §13 trên `reply` (không còn `conversation_replies`): giữ các assert về DB/ledger/từ chối; câu khách nhận = `reply.text` hoặc fallback.
  - `test_scenarios.py::test_three_turns_without_progress_do_not_offer_the_phone` — ba lượt chỉ `reply` hỏi lại → không câu nào chứa `settings.shop_phone`.
  - `test_graph_turn.py::test_unannounced_outcome_is_marked_only_when_said` (viết lại test R0 hiện có trên câu `reply`).
- [ ] **Step 2:** FAIL. **Step 3:** Cài + xoá file; `grep -rn "ReplyBrief\|RESPOND_TAG\|GoalProgress\|step_goal\|reply_kind" app` → rỗng.
- [ ] **Step 4:** PASS; toàn suite xanh; `tests/test_write_fence.py` + `tests/test_regex_budget.py` xanh.
- [ ] **Step 5:** Commit `refactor(agents): let the orchestrator's checked reply be the answer`

---

### Task 8: Nút bấm — code quyết, Orchestrator chỉ báo kết quả

**Files:**
- Modify: `app/agents/booking/button_path.py`, `orchestrator/agent.py` (`run_orchestrate(..., mode)`), `graph.py` (`button_path → orchestrate`), `prompts/orchestrator.py` (đoạn chế độ báo kết quả), `app/services/socketio_service.py` (không đổi rate limit tin nhắn của `confirm_proposal`)
- Test: `tests/test_button_path.py`, `tests/test_scenarios.py`

**Interfaces:**
- Consumes: `apply_decision` (hiện có), `TurnContext.held` (Task 4), `ReplyFacts` (Task 5), tool `reply` (Task 6).
- Produces: `run_orchestrate(deps, recent, *, thread_id=None, mode: Literal["full", "announce"] = "full", button_outcome: PendingOutcome | None = None) -> OrchestrateOutcome`. `mode="announce"`: chỉ bind tool `reply`; human message thêm khối `Button outcome:` (render từ object); `build_reply_facts` nhận `pending_outcome=button_outcome`; lời gọi model **có** qua `BudgetMiddleware` (tính vào `orch_llm_calls_per_user_per_hour`). Nút có `proposal_id` khác `ctx.pending.id` (kể cả id của một đề xuất `held`) → `stale`: không chạm DB, câu `BUTTON_STALE_REPLY` code, không gọi model.

- [ ] **Step 1: Test đỏ**
  - `test_button_path.py::test_button_for_a_held_proposal_is_stale` — p1 pending, p2 held, bấm Đồng ý cho p2 → `stale`, p1/p2 không đổi, không appointment mới, model không được gọi.
  - `test_scenarios.py::test_button_approve_is_announced_by_the_orchestrator_then_asks_next` — p1 pending 15:00, p2 held 16:00, nút Đồng ý p1 → lịch 15:00 booked; Orchestrator giả (chỉ có `reply`) trả câu nêu 15:00 và hỏi 16:00 → `complete.answer` là câu đó, `complete.proposal.id == p2`.
  - `test_scenarios.py::test_button_turn_counts_toward_llm_budget` — budget đầy → không gọi model, câu báo kết quả = `facts.fallback` (code).
- [ ] **Step 2:** FAIL. **Step 3:** Cài. **Step 4:** PASS; toàn suite xanh. **Step 5:** Commit `feat(chat): announce button decisions through the orchestrator`

---

### Task 9: Frontend và giao thức Socket.IO

**Files:**
- Modify: `frontend/src/hooks/useAgentStream.ts` (bỏ xử lý `token`; giữ trạng thái "đang trả lời" từ `turn_started` tới `complete`/`error`), `frontend/src/hooks/useAgentStream.test.ts`
- Test: Vitest trong `frontend/`

- [ ] **Step 1: Test đỏ** — `useAgentStream.test.ts`: thay các ca có `token` bằng `it("không còn token: turn_started → thinking, complete → một bubble cuối", …)` và `it("event token lạ bị bỏ qua, không tạo bubble", …)`.
- [ ] **Step 2:** `cd frontend && npx vitest run src/hooks/useAgentStream.test.ts` → FAIL. **Step 3:** Sửa hook. **Step 4:** `npx vitest run` → PASS. **Step 5:** Commit `feat(web): show the reply only once it has been checked`

---

### Task 10: Script chạy thật + tài liệu

**Files:**
- Modify: `scripts/test_live_10_turns.py` (đọc `answer_source`, `violations` từ state; thêm một lượt "đặt cho chị 3 giờ chiều mai, với cho con gái chị 4 giờ luôn nha" và một lượt bấm nút; in trạng thái `pending`/`held`), `AGENTS.md` (Hard constraints: thay luật stream `respond` bằng "không stream, chỉ `complete` sau guard"; mô tả `reply`, giữ chỗ, nhiều đề xuất; Socket.IO protocol bỏ `token`; Layout), `CONTEXT.md` (mục trạng thái mới + bẫy: chỗ giữ, `held`, không còn hàng chờ / GoalProgress), `RUNBOOK.md` (bỏ dòng về `probe_repeats`), đầu hai spec 2026-10-29 thêm một dòng trỏ sang spec mới.
- Test: `.venv/bin/python -m py_compile scripts/*.py`

- [ ] **Step 1:** Sửa; `py_compile` xanh; `grep -n "respond\|token\|interrupt\|supervisor\|ConfirmMiddleware\|booking_graph\|queued" AGENTS.md` chỉ còn chỗ nói "đã bỏ".
- [ ] **Step 2:** Toàn suite xanh. **Step 3:** Commit `docs: describe the orchestrator that speaks`

---

### Task 11: Chạy thật 2 lần + khép nhánh

- [ ] **Step 1:** **Hỏi chủ dự án** trước khi chạy. Rồi `PYTHONPATH=. .venv/bin/python scripts/test_live_10_turns.py > <scratchpad>/live-A.log 2>&1`, lặp lại → `live-B.log` (xoá DB dev — đã duyệt cho script này).
- [ ] **Step 2:** Đọc draft/answer từng lượt từ checkpoint (như `scratchpad/drafts.py` ngày 08/11). Đạt khi cả hai lần: mọi lượt `complete`; không câu nhận vơ; mọi lượt có đề xuất mới đều hỏi xác nhận; không câu trơ; không hỏi lại điều khách đã nói; DB cuối đúng; lịch thứ hai còn được giữ trong lúc hỏi lịch đầu. Ghi bảng kết quả vào ledger SDD, báo chủ dự án. Có lỗi → dừng, quay lại brainstorm với chủ dự án.
- [ ] **Step 3:** `.venv/bin/pytest -q` + `python -m py_compile $(git ls-files '*.py')` xanh; review cả nhánh (`superpowers:requesting-code-review`, model mạnh nhất); sửa Critical/Important.
- [ ] **Step 4:** `superpowers:finishing-a-development-branch` → PR `feat/llm-orchestrator` → `henry/develop` (mô tả: link hai spec, plan này, kết quả hai lần chạy thật, số test).
