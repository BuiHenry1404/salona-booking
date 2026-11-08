# Đợt A — Orchestrator tự nói chuyện với khách: Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Orchestrator vừa quyết vừa viết câu trả lời cho khách (bỏ Conversation Agent), câu được guard kiểm bằng code trước khi hiện, không stream.

**Architecture:** Làm tiếp trên gói `app/agents/booking/`. Graph còn `resolve → (button_path) → orchestrate → record`. Orchestrator kết thúc mọi lượt bằng tool `reply(text, asks, service, stated)`; `OrchestratorGuard` kiểm câu trong `reply` bằng `reply_check.py` (phần trích sự thật + câu mẫu chuyển từ `reply_brief.py`); bị từ chối thì Orchestrator viết lại một lần, rồi câu mẫu code. Đợt A giữ nguyên một đề xuất chờ + hàng chờ hiện có (nhiều lịch + giữ chỗ là đợt B, plan riêng).

**Tech Stack:** Python 3.12, FastAPI, python-socketio, LangGraph + LangChain `create_agent`, Motor/MongoDB replica set `rs0`, pytest (asyncio auto), React frontend (Vitest).

**Spec:** `docs/superpowers/specs/2026-11-08-orchestrator-speaks-design.md` (đợt A). Spec cũ `2026-10-29-llm-orchestrator-design.md` vẫn đúng ở mọi chỗ spec mới không nhắc.

## Global Constraints

- Nhánh `feat/llm-orchestrator`; không commit thẳng `main`; đích merge `henry/develop`. Commit message tiếng Anh, ngắn; trailer `Co-Authored-By` ghi đúng model đã viết commit (quyết định 30/10).
- Agent **không có tool ghi**. Chỉ `ProposalService.approve` ghi lịch; trong `app/agents/` chỉ `orchestrator/decide.py` gọi `approve`. `tests/test_write_fence.py` xanh ở mọi commit.
- Logic đặt lịch nằm trong `app/services/`. Cấu hình chỉ từ `.env` (thêm setting = `config.py` + `.env.example` + `.env`). Mọi URI Mongo có `?directConnection=true`.
- Regex: chỉ `app/agents/booking/draft_checks.py` được `import re` (budget `{"draft_checks.py": 2}`). Không từ điển từ khoá ý định ở module mới.
- Prompt tiếng Anh, không câu mẫu tiếng Việt. Khách được gọi theo `TurnContext.customer.address_phrase` (mặc định "anh chị", không đoán giới tính từ tên).
- Mọi id/iso trong văn bản gửi model lấy từ object; chữ khách chỉ dưới nhãn `Customer said (untrusted):`; lời dặn `note` của Orchestrator không bao giờ là nguồn id/iso.
- Log có cấu trúc, `extra` luôn có `user_id`, `turn_id`, `thread_id`; không log chữ khách.
- Giá trị cố định: viết lại câu bị từ chối **tối đa 1 lần**, vòng đó **chỉ** được gọi `reply`; nhắc "phải gọi `reply`" **tối đa 1 lần** và không nhắc khi đã chạm trần vòng; `orch_max_tool_rounds = 8`; `orch_orchestrate_timeout_seconds = 110`; `orch_turn_timeout_seconds = 150`; Orchestrator `reasoning_effort="low"`; `note` ≤ 200 ký tự.
- Số điện thoại tiệm (`settings.shop_phone`) **chỉ** có trong câu ở lượt `approve` thất bại; mọi lượt khác có số điện thoại là vi phạm. Không câu mẫu nào khác mời "gọi tiệm".
- Không stream câu trả lời: không còn event `token`; câu tới khách chỉ qua `complete` sau guard.
- Đúng 1 worker.
- Test kiểm hành vi, không ghim chữ prompt; một bảng = một hàm `parametrize`; test không gọi Azure. Chạy thật phải hỏi chủ dự án trước mỗi lần.
- Mỗi task: test đỏ → chạy thấy đỏ → code → xanh → `.venv/bin/pytest -q` toàn suite (cần `docker compose up -d mongo`) → commit.

## Review Focus

1. **Timeout sau khi đã duyệt lịch** → khách nhận câu báo đặt xong (dựng từ ledger), không phải "máy bận". — Task 3 `test_timeout_after_approve_answers_with_the_booked_phrase`.
2. **Câu nhắc lại giờ khách vừa nói hoặc giờ gợi ý gần nhất** ("3 giờ sáng hay 3 giờ chiều ạ?", "4 giờ chiều còn trống") → không bị coi là giờ bịa. — Task 2 dòng `echoes_customer_clock`, `nearest_slot_clock`.
3. **Câu "em vẫn đang giữ 3 giờ chiều cho chị"** khi đang có đề xuất 15:00 → hợp lệ; cùng câu khi không có đề xuất nào → nhận vơ. — Task 2 dòng `hold_wording_on_pending_slot`, `hold_wording_without_any_proposal`.
4. **Câu nhờ khách lịch sự không có dấu "?"** ở lượt không có đề xuất mới ("Dạ chị cho em biết chị muốn đặt mấy giờ nhé ạ.") → được nhận nguyên văn, không thay bằng câu mẫu. — Task 2 dòng `polite_ask_without_question_mark`; Task 3 `test_polite_ask_without_question_mark_is_accepted`.
5. **Model trả chữ thường đúng lúc đã chạm trần 8 vòng** → câu mẫu ngay, không vòng thêm, không `error`. — Task 3 `test_plain_text_at_the_round_cap_falls_back_at_once`.

---

### Task 1: Xoá gói cũ `booking_graph`

Làm đúng **Task 6.3** trong `docs/superpowers/plans/2026-10-30-llm-orchestrator/M6-cutover.md` (danh sách file xoá / sửa, Step 0–3), với hai chỉnh:

- `tests/test_regex_budget.py`: `EXPECTED_BUDGET = {"draft_checks.py": 2}` (không phải `guard.py`).
- `tests/test_write_fence.py` `ALLOWED` theo M6-cutover.md; dòng `events.py … cancel` chỉ giữ nếu `grep -n "\.cancel(" app/agents/booking/events.py` còn thấy.

- [ ] **Step 1:** Làm Step 0–1 của Task 6.3. `grep -rn "booking_graph" app tests scripts main.py` → rỗng.
- [ ] **Step 2:** `scripts/terminal_chat.py` (untracked, của chủ dự án) **không đụng**; ghi vào ledger rằng nó sẽ hỏng.
- [ ] **Step 3:** `.venv/bin/pytest -q` → xanh. Commit `refactor(agents): remove the supervisor graph and its tests` (body ghi số test).

---

### Task 2: `reply_check.py` — trích sự thật, kiểm câu, câu mẫu

**Files:**
- Create: `app/agents/booking/reply_check.py`
- Modify: `app/agents/booking/draft_checks.py` (tách claim "giữ" khỏi claim "đặt/ghi/chốt" — xem Thuật toán), `app/agents/booking/reply_brief.py` (chỉ chuyển: các hàm trích sự thật dùng chung — `facts_for` và các `_facts_for_*`, `unannounced_line`, hằng câu mẫu — sang `reply_check.py`; `reply_brief.py` import lại từ đó để graph cũ còn chạy tới Task 4)
- Test: `tests/test_reply_check.py` (mới)

**Interfaces:**
- Consumes: `TurnContext`, `CallRecord`, `PendingOutcome`, `ProposalCreated`, `TaskResult` (hiện có); `phrase.code_phrase`; `draft_checks`.
- Produces:

```python
class ReplyFacts(BaseModel):
    clocks: set[tuple[int, int, str | None]]       # mọi mốc giờ có thật của lượt
    held_clocks: set[tuple[int, int, str | None]]  # giờ đang có đề xuất chờ thật (ctx.pending + đề xuất tạo lượt này)
    allowed_claims: set[Literal["booked", "moved", "cancelled"]]
    must_mention: list[str]                         # spoken bắt buộc: đề xuất mới, R0, kết quả duyệt
    question_required: bool                         # lượt có ProposalCreated mới
    needs_phone: bool                               # approve thất bại lượt này
    announce_turn_id: str | None
    announce_spoken: str | None
    fallback: str                                   # câu mẫu code của lượt

def build_reply_facts(ctx: TurnContext, calls: list[CallRecord], pending_outcome: PendingOutcome | None, question: str) -> ReplyFacts
def check_reply_text(text: str, facts: ReplyFacts) -> list[str]
    # mã theo thứ tự: "claim", "unknown_time", "missing_fact", "missing_question", "missing_phone", "phone_not_allowed"
def is_announced(text: str, facts: ReplyFacts) -> bool   # announce_spoken có trong câu (so `guard.normalize`)
GENERIC_FALLBACK = "Dạ {address} nói lại giúp em được không ạ?"
```

**Thuật toán** (phần chữ ký không quyết):
- `clocks` lấy từ: `ctx.pending`, `ctx.upcoming`, giờ mở/đóng cửa, hàng chờ trong `ctx`, mọi `Moment` trong kết quả các call **được phép** của lượt (`start_at`, `asked`, `nearest`, `slots`, `appointments`, `until`, `at`, `next_change`, `replaces`), `pending_outcome.spoken`, R0, **và** giờ trong `question` (`tokens.scan_spoken_clocks` / `scan_times`).
- `allowed_claims`: claim theo `kind` khi `pending_outcome.outcome == "approved"` hoặc R0 `approved`; còn lại rỗng.
- Claim "giữ": mệnh đề nhận vơ mà động từ cam kết **chỉ** là "giữ" (`đã/đang/vừa giữ`, `giữ … rồi`) là hợp lệ khi mọi giờ trong mệnh đề thuộc `held_clocks` (mệnh đề không có giờ: hợp lệ khi `held_clocks` khác rỗng). "đặt / ghi / chốt" vẫn chỉ hợp lệ theo `allowed_claims`. Thêm vào `draft_checks` một hàm `claim_verbs(clause) -> set[str]` (không regex mới) để `reply_check` phân biệt.
- `unknown_time`: có giờ trong câu (`draft_checks.clock_keys`) không khớp `_same_clock` với `clocks`.
- `missing_fact`: một cụm trong `must_mention` không có trong câu (so `guard.normalize`).
- `missing_question`: `question_required` và `"?" not in text`.
- `fallback` (thứ tự): approve thất bại → câu nêu giờ + "chưa ghi được" + `settings.shop_phone`; approved → `code_phrase(...)`; có `ProposalCreated` mới hoặc `ctx.pending` → `PROPOSAL_FALLBACK`; còn lại → `GENERIC_FALLBACK`. `FALLBACK_ANSWER` ("gọi trực tiếp cho tiệm") không được dùng làm câu mẫu nữa.

- [ ] **Step 1: Test đỏ** — `tests/test_reply_check.py::test_check_reply_table` (một hàm parametrize; mỗi dòng dựng ctx/calls/outcome giả bằng `tests.factories.make_ctx` + `CallRecord`):

| id | tình huống | câu | mã |
|---|---|---|---|
| `claim_before_approve` | đề xuất mới 15:00 lượt này | "Dạ em đặt xong 3 giờ chiều mai cho chị rồi ạ." | `["claim"]` |
| `claim_after_approve` | approve 15:00 lượt này | "Dạ em đặt xong 3 giờ chiều mai cho chị rồi ạ." | `[]` |
| `hold_wording_on_pending_slot` | `ctx.pending` 15:00, khách hỏi giờ mở cửa | "Dạ tiệm mở 8 giờ sáng tới 7 giờ tối ạ. Lịch 3 giờ chiều mai em vẫn đang giữ cho chị, chị đồng ý không ạ?" | `[]` |
| `hold_wording_without_any_proposal` | không đề xuất nào | "Dạ em đang giữ 3 giờ chiều mai cho chị ạ." | `["claim"]` |
| `invented_time` | đề xuất mới 15:00 | "Em giữ chỗ 4 giờ chiều mai cho chị nhé, chị đồng ý không ạ?" | `["unknown_time", "missing_fact"]` |
| `echoes_customer_clock` | khách "mai 3 giờ", `needs_info` | "Dạ chị Lan, 3 giờ sáng hay 3 giờ chiều ạ?" | `[]` |
| `nearest_slot_clock` | `slot_unavailable` 15:00, nearest 16:00 | "Dạ 3 giờ có người đặt rồi, 4 giờ chiều còn trống ạ." | `[]` |
| `proposal_without_question` | đề xuất mới 15:00 | "Em giữ chỗ Thứ Hai 9/11, 3 giờ chiều cho chị nhé." | `["missing_question"]` |
| `polite_ask_without_question_mark` | `needs_info`, không đề xuất | "Dạ chị cho em biết chị muốn đặt mấy giờ nhé ạ." | `[]` |
| `failed_without_phone` | approve failed | "Dạ lịch 3 giờ chiều mai em chưa ghi được ạ." | `["missing_phone"]` |
| `phone_when_nothing_failed` | đề xuất mới 15:00 | câu hỏi đồng ý có `settings.shop_phone` | `["phone_not_allowed"]` |
| `r0_missing` | R0 approved 15:00, lượt chào | "Dạ chào chị ạ." | `["missing_fact"]` |

Thêm: `test_fallback_has_the_phone_only_when_approve_failed` (parametrize các nhánh `fallback`), `test_is_announced_detects_r0_spoken`, `test_build_reply_facts_ignores_rejected_calls`.

- [ ] **Step 2:** `.venv/bin/pytest -q tests/test_reply_check.py` → FAIL (`ModuleNotFoundError`).
- [ ] **Step 3:** Cài. **Step 4:** PASS; toàn suite xanh (graph cũ vẫn dùng `reply_brief` + `apply_guard`). **Step 5:** Commit `feat(agents): check a reply against the turn's facts in code`

---

### Task 3: Tool `reply` — Orchestrator tự nói, guard kiểm trước khi nhận

**Files:**
- Modify: `app/agents/booking/orchestrator/tools.py` (thay `finish` + `ask_customer` bằng `reply`; thêm `note` cho `appointment_task` / `shop_info_task`), `orchestrator/guard.py`, `orchestrator/agent.py` (`OrchestrateOutcome`, `_outcome_from_state`, `_outcome_from_ledger`), `orchestrator/state.py`, `app/agents/booking/schemas.py` (`ReplyArgs`, `EndedBy`, `TaskSpec.note`), `specialists/base.py::task_message`, `prompts/orchestrator.py` (nhận các luật viết văn từ `prompts/conversation.py`, spec §6)
- Test: `tests/test_orchestrator_guard.py`, `tests/test_orchestrator_agent.py`, `tests/test_specialist_base.py`, `tests/test_prompts.py`

**Interfaces:**
- Consumes: `build_reply_facts`, `check_reply_text`, `is_announced`, `ReplyFacts` (Task 2).
- Produces:

```python
class ReplyArgs(BaseModel):          # strict-safe: không default, tuỳ chọn = | None
    text: str
    asks: QuestionField | None
    service: str | None
    stated: list[str]                # ≤ 2, như `finish.stated` cũ
EndedBy = Literal["reply", "forced", "limit", "error"]
# OrchestrateOutcome: bỏ `finish`, `ask`; thêm
#   reply: ReplyArgs | None          # câu đã qua guard (None khi dùng câu mẫu)
#   answer: str                      # câu gửi khách cuối cùng
#   answer_source: Literal["llm", "code"]
#   violations: list[str]
#   announced_turn_id: str | None    # facts.announce_turn_id khi is_announced(answer, facts)
# TaskSpec thêm: note: str | None    # lời dặn của Orchestrator
```

- **`reply`** chạy một mình trong vòng (luật (g) như `finish` cũ; (h) `must_decide_pending` như cũ). Trong `awrap_tool_call`: `strip_tool_names(text)` → `check_reply_text(text, build_reply_facts(ctx, calls_so_far, pending_outcome, question))`. Rỗng → `ended_by="reply"`, `answer=text`, `answer_source="llm"`, nhảy END ở `abefore_model` kế (như `finish` cũ). Có mã, lần đầu → `ToolMessage(status="error")`, `error_code="reply_rejected"`, kèm mã + gợi ý tiếng Anh (cụm cần nhắc lấy từ `facts.must_mention`, không chữ khách); state `reply_rejections = 1`. Có mã lần hai → kết thúc với `answer=facts.fallback`, `answer_source="code"`, `violations=mã`, log `guard_violation` (`fixed_by="fallback"`).
- Sau một lần từ chối, mọi tool khác `reply` bị từ chối mã `reply_only`.
- **Chữ thường** (`aafter_model`, AIMessage không tool call): lần đầu **và** `rounds_executed < max_rounds` → thêm `HumanMessage("You must end the turn by calling reply.")`, `jump_to="model"`, log `orch_text_end` (`thread_id` từ `langgraph.config.get_config()`); ngược lại → `ended_by="reply"`, `answer=facts.fallback`, `answer_source="code"`. Bỏ đường "chữ thường = `finish(answer)`" của M6b.
- **`forced` / `limit`**: `answer = facts.fallback`. **Timeout / lỗi model** (`_outcome_from_ledger`): có call được phép → `answer = build_reply_facts(ctx, ledger_calls, ledger_pending_outcome, question).fallback`, `answer_source="code"`; không có → `ended_by="error"` (graph dùng `TURN_ERROR_MESSAGE` như hiện tại).
- **`note`**: `appointment_task(action, args, note)`, `shop_info_task(action, args, note)` thêm `note: str | None` (cắt `single_line(note, 200)`) → `TaskSpec.note`; `task_message` in dòng `Orchestrator note: …` sau khối task, trước nhãn untrusted; `None` thì không có dòng.

- [ ] **Step 1: Test đỏ**
  - `test_orchestrator_agent.py::test_valid_reply_ends_the_turn_with_the_model_text` — `appointment_task(propose_book)` → `reply(text="Em giữ chỗ {spoken} cho chị, chị đồng ý không ạ?", asks=None, service=None, stated=[])` → `ended_by == "reply"`, `answer` == text, `answer_source == "llm"`.
  - `…::test_rejected_reply_is_rewritten_once` — `reply("Dạ em đặt xong … rồi ạ.")` rồi `reply(câu đúng)` → `answer` == câu đúng; đúng một `ToolMessage` lỗi chứa `"claim"`.
  - `…::test_only_reply_is_allowed_after_a_rejection` — sau một `reply` bị từ chối, `appointment_task(...)` → `reply_only`; specialist giả không được gọi.
  - `…::test_second_rejected_reply_falls_back_to_code` — `answer_source == "code"`, `answer` chứa `spoken` của đề xuất, `"claim" in violations`.
  - `…::test_polite_ask_without_question_mark_is_accepted` — `reply("Dạ chị cho em biết chị muốn đặt mấy giờ nhé ạ.", asks="time", …)` → `answer_source == "llm"`.
  - `…::test_plain_text_gets_one_reminder_then_reply` — `AIMessage("Da")` rồi `reply(câu đúng)` → `ended_by == "reply"`, model gọi 2 lần, lần 2 thấy lời nhắc; log `orch_text_end` có `thread_id` == thread của lượt.
  - `…::test_plain_text_twice_falls_back_to_code` — `answer_source == "code"`, không `error`.
  - `…::test_plain_text_at_the_round_cap_falls_back_at_once` — `max_rounds=1`, một vòng `appointment_task` rồi `AIMessage("Da")` → model chỉ được gọi 2 lần, `answer_source == "code"`.
  - `…::test_timeout_after_approve_answers_with_the_booked_phrase` — pending p1; `decide_pending(approve)` rồi `Hang` → `answer_source == "code"`, `answer` chứa `spoken` của p1, lịch đã booked.
  - `test_orchestrator_guard.py::test_rule_table` thêm: `reply_with_other_tool_same_round → serialize_required`, `reply_while_pending_undecided → must_decide_pending`.
  - `test_specialist_base.py::test_task_message_puts_the_orchestrator_note_on_its_own_line` — dòng `Orchestrator note:` sau khối task, trước nhãn untrusted; `note=None` → không có dòng.
  - `test_prompts.py`: prompt Orchestrator qua `test_every_prompt_is_english_without_vietnamese_samples`.
- [ ] **Step 2:** FAIL. **Step 3:** Cài. Graph tạm đọc `outcome.answer` / `answer_source` thay `brief → respond` (đủ cho toàn suite xanh; dọn hẳn ở Task 4).
- [ ] **Step 4:** PASS; toàn suite xanh. **Step 5:** Commit `feat(orchestrator): end every turn with a checked reply`

---

### Task 4: Graph mới, không stream; bỏ Conversation / ReplyBrief / GoalProgress

**Files:**
- Modify: `app/agents/booking/graph.py` (`START → resolve → button_path | orchestrate → record → END`; nút xử lý ở Task 5), `events.py` (bỏ nhánh `on_chat_model_stream` / `token`; `CONTROL_TOOL_NAMES` = `appointment_task`, `shop_info_task`, `decide_pending`, `reply`), `ledger.py` (`finish(turn_id, *, ended_by)` bỏ `reply_kind`; giữ `set_ask` — gọi khi `reply.asks` khác `None`), `context.py` (`PreviousTurn` bỏ `reply_kind`; `_open_question` chỉ dựa `ask_field`; bỏ `goal`), `resolver.py` (giữ R0 + R1; R2/R3/R5 cùng tới `orchestrate`), `app/models/conversation.py` + `app/services/conversation_state.py` (bỏ `goal`, `GoalProgress`, `record_goal_progress`), `app/agents/booking/__init__.py`
- Delete: `reply_brief.py` (phần còn lại sau Task 2), `conversation.py`, `rewrite.py`, `progress.py`, `prompts/conversation.py`, `prompts/rewrite.py`; trong `guard.py` các hàm `facts_from_brief`, `check_reply`, `apply_guard` và luật `missing_question` / `bare_facts` cũ.
- Test: `tests/test_graph_turn.py`, `tests/test_run_turn.py`, `tests/test_scenarios.py`, `tests/test_resolver.py`; xoá `test_reply_brief.py`, `test_conversation_agent.py`, `test_rewrite.py`, `test_progress.py`, phần `test_reply_guard.py` kiểm hàm đã xoá (ghi test thay thế trong commit body — chính sách test §4).

**Interfaces:**
- Consumes: `OrchestrateOutcome.answer / answer_source / reply / announced_turn_id` (Task 3).
- Produces: `run_turn` phát `turn_started`, `tool_started` / `tool_finished`, rồi đúng một `complete {"answer", "proposal"}` hoặc `error` — **không** `token`.

- [ ] **Step 1: Test đỏ**
  - `test_run_turn.py::test_turn_emits_no_token_events` — model giả của Orchestrator có stream chunk → không event `token`; có `complete`.
  - `test_scenarios.py`: viết lại các kịch bản §13 trên `reply` (không còn `conversation_replies`); giữ các assert về DB / ledger / từ chối; câu khách nhận = `reply.text` hoặc câu mẫu.
  - `test_scenarios.py::test_turns_without_progress_never_offer_the_phone` — ba lượt chỉ `reply` hỏi lại → không câu nào chứa `settings.shop_phone`.
  - `test_graph_turn.py::test_unannounced_outcome_is_marked_only_when_said` (viết lại test R0 hiện có trên `reply`).
- [ ] **Step 2:** FAIL. **Step 3:** Cài + xoá; `grep -rn "ReplyBrief\|RESPOND_TAG\|GoalProgress\|step_goal\|reply_kind\|apply_guard" app` → rỗng.
- [ ] **Step 4:** PASS; toàn suite xanh; `test_write_fence.py`, `test_regex_budget.py` xanh.
- [ ] **Step 5:** Commit `refactor(agents): let the orchestrator's checked reply be the answer`

---

### Task 5: Nút bấm — code quyết, Orchestrator chạy tiếp với quyết định áp sẵn

**Files:**
- Modify: `app/agents/booking/button_path.py`, `orchestrator/agent.py` (`run_orchestrate`), `graph.py` (`button_path → orchestrate`), `prompts/orchestrator.py` (đoạn "khách vừa bấm nút")
- Test: `tests/test_button_path.py`, `tests/test_scenarios.py`

**Interfaces:**
- Consumes: `apply_decision` (hiện có), `ReplyFacts` (Task 2), `reply` (Task 3).
- Produces: `run_orchestrate(deps, recent, *, thread_id=None, preset: PendingOutcome | None = None) -> OrchestrateOutcome`. `preset` khác `None`: state ban đầu `pending_decided=True`, `pending_outcome=preset` (luật (h)/(k) coi như đã quyết); human message có khối `Button decision:` render từ object; **đủ tool** như lượt gõ chữ; `build_reply_facts` nhận `pending_outcome=preset`; lời gọi model **có** qua `BudgetMiddleware`. Nút có `proposal_id` khác `ctx.pending.id` → `stale`: câu `BUTTON_STALE_REPLY` (code), không chạm DB, không gọi model.

- [ ] **Step 1: Test đỏ**
  - `test_scenarios.py::test_button_approve_is_announced_by_the_orchestrator` — pending p1 15:00, nút Đồng ý → lịch booked; Orchestrator giả gọi `reply` nêu 15:00 → `complete.answer` là câu đó, `answer_source == "llm"`.
  - `test_scenarios.py::test_button_turn_can_continue_with_tools` — sau nút Đồng ý, Orchestrator giả gọi `appointment_task(find_slots)` → không bị `must_decide_pending` / `already_decided`, specialist giả được gọi.
  - `test_button_path.py::test_stale_button_does_not_call_the_model` — id cũ → `stale`, DB không đổi, model Orchestrator không được gọi.
  - `test_scenarios.py::test_button_turn_counts_toward_llm_budget` — budget đầy → không gọi model, `complete.answer` = câu mẫu báo kết quả (code).
- [ ] **Step 2:** FAIL. **Step 3:** Cài. **Step 4:** PASS; toàn suite xanh. **Step 5:** Commit `feat(chat): let the orchestrator announce button decisions`

---

### Task 6: Frontend bỏ token

**Files:**
- Modify: `frontend/src/hooks/useAgentStream.ts` (bỏ xử lý `token`; trạng thái "đang trả lời" từ `turn_started` tới `complete` / `error`), `frontend/src/hooks/useAgentStream.test.ts`

- [ ] **Step 1: Test đỏ** — thay các ca có `token` bằng `it("không còn token: turn_started → thinking, complete → một bubble cuối", …)` và `it("event token lạ bị bỏ qua, không tạo bubble", …)`.
- [ ] **Step 2:** `cd frontend && npx vitest run src/hooks/useAgentStream.test.ts` → FAIL. **Step 3:** Sửa hook. **Step 4:** `npx vitest run` → PASS. **Step 5:** Commit `feat(web): show the reply only once it has been checked`

---

### Task 7: Script chạy thật + tài liệu

**Files:**
- Modify: `scripts/test_live_10_turns.py` (đọc `answer_source`, `violations` từ state; đo thời gian mỗi lượt từ lúc gọi `run_turn` tới `complete` bằng `time.monotonic()`; cuối bài in trung vị và chậm nhất; thêm một lượt bấm nút), `AGENTS.md` (Hard constraints: thay luật stream `respond` bằng "không stream, chỉ `complete` sau guard"; mô tả `reply`; Socket.IO bỏ `token`; Layout), `CONTEXT.md` (mục trạng thái mới; sửa chỗ giả định có stream — dòng "Mất mạng giữa lúc stream"; ghi đợt B còn chờ), `RUNBOOK.md` (bỏ dòng `probe_repeats`), đầu hai spec 2026-10-29 thêm một dòng trỏ sang spec đợt A.

- [ ] **Step 1:** Sửa; `.venv/bin/python -m py_compile scripts/*.py` xanh; `grep -n "respond\|token\|interrupt\|supervisor\|ConfirmMiddleware\|booking_graph" AGENTS.md` chỉ còn chỗ nói "đã bỏ".
- [ ] **Step 2:** Toàn suite xanh. **Step 3:** Commit `docs: describe the orchestrator that speaks`

---

### Task 8: Chạy thật 2 lần + khép đợt A

- [ ] **Step 1:** **Hỏi chủ dự án** trước khi chạy. Rồi `PYTHONPATH=. .venv/bin/python scripts/test_live_10_turns.py > <scratchpad>/live-A1.log 2>&1`, lặp lại → `live-A2.log` (xoá DB dev — đã duyệt cho script này).
- [ ] **Step 2:** Đọc câu từng lượt từ checkpoint (như `scratchpad/drafts.py` ngày 08/11). Đạt khi cả hai lần: mọi lượt `complete`; không câu nhận vơ; mọi lượt có đề xuất mới đều hỏi xác nhận; không câu trơ; không hỏi lại điều khách đã nói; DB cuối đúng; độ trễ trung vị ≤ 10 giây, chậm nhất ≤ 25 giây. Ghi bảng kết quả vào ledger SDD, báo chủ dự án. Không đạt → dừng, quay lại chủ dự án.
- [ ] **Step 3:** `.venv/bin/pytest -q` + `python -m py_compile $(git ls-files '*.py')` xanh; review cả nhánh (`superpowers:requesting-code-review`, model mạnh nhất); sửa Critical/Important.
- [ ] **Step 4:** `superpowers:finishing-a-development-branch` → PR `feat/llm-orchestrator` → `henry/develop` (mô tả: link spec đợt A, plan này, kết quả hai lần chạy thật, số test). Đợt B lập plan riêng sau khi PR này được duyệt.
