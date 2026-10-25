# Kiến trúc đích trên nền Harness B — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Thêm nút Đồng ý/Không, sửa lỗi "ok" không có đề xuất, chống tin trùng qua Redis, mở rộng trí nhớ trong phiên và hồ sơ khách, làm câu chốt bớt rập khuôn — trên Harness B đang chạy ở `main`, đo bằng benchmark sau mỗi mốc.

**Architecture:** Graph ngoài giữ nguyên (`supervisor → agent → record → confirm`); node `confirm` nhận thêm resume dạng dict từ nút bấm và đi cùng một đường `ProposalService.approve`. Socket.IO thêm event `confirm_proposal` và trường `proposal` trong `complete`. Trí nhớ là dữ liệu do code dựng vào khối bối cảnh (slots mở rộng, `users.self_address`, `conversation_sessions`); Redis chỉ làm dedupe/khóa/cache và luôn có fallback Mongo.

**Tech Stack:** Python 3.12, FastAPI, python-socketio, langchain 1.4.6, langgraph 1.2.16, motor 3.7.1 / pymongo 4.19, redis-py (async) mới, React 18 + Vite + vitest, pytest. Benchmark: `bench/` trong repo `salona-booking` nhánh `bench/harness-benchmark`.

**Spec:** `docs/superpowers/specs/2026-10-20-target-architecture-design.md` (đọc kèm `docs/research/Architech.md`).

## Global Constraints

- Đúng **1 worker** uvicorn; không chạy hai `pytest` song song (index build failed).
- Config **chỉ từ `.env`**: thêm setting = `app/core/config.py` + `.env.example` + `.env`. Khóa tùy chọn (`REDIS_URL`) comment = tắt; chuỗi rỗng ≠ tắt.
- **Mọi logic đặt lịch ở `app/services/`**; mọi đường ghi lịch đi qua `ProposalService.approve(user, proposal_id, turn_at=...)`.
- **Agent không có tool ghi**; fence `tests/test_write_fence.py` phải còn xanh.
- **Chỉ stream token tag `respond`**; bộ phân loại/supervisor/timeparse/state `streaming=False`.
- Thứ tự message model: System → [summary + slots] → lịch sử → khối bối cảnh (dòng đầu là giờ hiện tại) → câu khách.
- Cancel = lật `status`; Mongo đơn lẻ, không transaction.
- Gọi khách "anh chị" khi `self_address` trống; **không** đoán giới từ tên đầy đủ.
- Chuỗi khách thấy giữ nguyên chữ: `FALLBACK_ANSWER`, `TOOL_ERROR_TEXT`, `TURN_ERROR_MESSAGE`, các fallback của `confirm`.
- Prompt tiếng Anh, không ví dụ tiếng Việt cho luật phân loại; docs/comment/chuỗi UI tiếng Việt; commit message tiếng Anh ngắn, kết bằng `Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>`.
- Lệnh test backend (từ gốc worktree `salona-booking-harness-b`): `PYTHONPATH=. .venv/bin/python -m pytest -q <path>` — Mongo ở :27017. Frontend: `cd frontend && npx vitest run <path>`. Bench: từ gốc `salona-booking`: `bench/.venv/bin/python -m pytest -q bench/tests`.
- Mỗi mốc M1–M4 xong: tag `target/m<k>`, chạy benchmark theo mục "Benchmark" của spec, ghi kết quả vào CONTEXT.md.
- Nhánh `feat/target-architecture`; không merge, không push trong plan.

## Review Focus

1. Khách bấm **Đồng ý hai lần** (double-tap) hoặc bấm nút rồi gõ "ừ" ngay sau: chỉ một lịch, câu trả lời thứ hai nói lịch đã có — Task 2 (`test_second_button_press_is_idempotent`).
2. Nút Đồng ý tới **sau khi đề xuất hết hạn** hoặc proposal_id của **user khác**: không ghi, câu code báo hết hạn — Task 2 (`test_button_on_foreign_or_expired_proposal_writes_nothing`).
3. Tin gửi lại cùng `message_id` **khi lượt đang chạy** không được hủy lượt đó; tin khác id vẫn hủy như cũ — Task 10 (`test_same_message_id_while_running_is_ignored`, `test_different_message_id_still_supersedes`).
4. Redis **sập giữa chừng** (connection error lúc `begin`/`finish`): lượt vẫn chạy, fallback Mongo — Task 10 (`test_redis_error_falls_back_to_mongo`).
5. Khách tự xưng **"em"** hoặc câu có "chị" không phải ngôi thứ nhất ("chị Hoa bạn em làm được không"): `self_address` không được đặt — Task 13 (`test_em_and_third_person_do_not_set_self_address`).

---

## Mốc M1 — Nút xác nhận (an toàn + tốc độ)

### Task 1: `reasoning_effort` trong `build_chat_model`

**Files:**
- Modify: `app/agents/llm.py:26-90` (`build_chat_model`)
- Modify: `app/agents/booking_graph/supervisor.py:55-60`, `app/agents/booking_graph/reply.py:352`, `app/agents/booking_graph/timeparse.py:248`
- Test: `tests/test_llm_limits.py`

**Interfaces:**
- Produces: `build_chat_model(tags=None, temperature=0.0, streaming=True, timeout=LLM_TIMEOUT_SECONDS, max_retries=None, reasoning_effort: Optional[Literal["none","low","medium","high"]] = None)`. Khi `reasoning_effort` khác `None`: kwargs model có `reasoning_effort=<giá trị>` và **không** có `temperature` (Azure trả 400 với `temperature=0.0` khi bật reasoning). Helper thuần `model_kwargs(temperature, streaming, timeout, max_retries, reasoning_effort) -> dict` để test không cần key.

- [ ] **Step 1: Viết test đỏ** trong `tests/test_llm_limits.py`:

```python
def test_reasoning_effort_drops_temperature():
    from app.agents.llm import model_kwargs
    kw = model_kwargs(temperature=0.0, streaming=False, timeout=8, max_retries=1,
                      reasoning_effort="low")
    assert kw["reasoning_effort"] == "low" and "temperature" not in kw

def test_without_reasoning_temperature_is_kept():
    from app.agents.llm import model_kwargs
    kw = model_kwargs(temperature=0.2, streaming=True, timeout=20, max_retries=0,
                      reasoning_effort=None)
    assert kw["temperature"] == 0.2 and "reasoning_effort" not in kw
```

- [ ] **Step 2: Chạy, xác nhận FAIL** `ImportError: model_kwargs`.
- [ ] **Step 3: Implement** `model_kwargs` trong `app/agents/llm.py`; `build_chat_model` nhận `reasoning_effort` và dùng `model_kwargs` cho cả ba nhánh provider (Azure/OpenAI/compatible truyền `**kw`).
- [ ] **Step 4: Dùng `reasoning_effort="low"`** ở supervisor (`supervisor.py:55`), `classify_reply` (`reply.py:352`), `_ask_model` timeparse (`timeparse.py:248`); bỏ `temperature=0.0` ở ba chỗ đó. Cập nhật test supervisor `test_supervisor_model_does_not_stream` thêm `assert seen[0]["reasoning_effort"] == "low"`.
- [ ] **Step 5: Chạy** `tests/test_llm_limits.py tests/test_supervisor.py tests/test_reply_classify.py tests/test_timeparse.py` → PASS.
- [ ] **Step 6: Commit** `feat: add reasoning_effort to build_chat_model; low for classifiers`.

### Task 2: Node `confirm` nhận resume từ nút bấm

**Files:**
- Modify: `app/agents/booking_graph/confirm.py:125-234` (`make_confirm_node`), thêm hằng `BUTTON_NO_REPLY`, `BUTTON_STALE_REPLY`
- Modify: `app/agents/booking_graph/state.py` (thêm `confirm_source: NotRequired[Literal["text","button"]]`)
- Test: `tests/test_confirm_flow.py`

**Interfaces:**
- Consumes: `ProposalService.approve(user, proposal_id, *, turn_at) -> ApprovalResult`; `confirm_fact_for(result, address)`; `declined_line(proposal)`.
- Produces: resume value là `str` (gõ chữ, như cũ) **hoặc** `dict {"button": "yes"|"no", "proposal_id": str}`. Dict với `button="yes"` và `proposal_id` trùng `state["proposal_id"]` → `approve` thẳng, bỏ qua `reply.decide`, trả `{**confirm_fact_for(...), "proposal_id": None, "gate_next": "phrase", "confirm_source": "button"}`. `button="no"` → `ProposalService.reject`, trả `{"draft": BUTTON_NO_REPLY, "fallback": BUTTON_NO_REPLY, "answer_source": "code", "gate_next": "phrase", "confirm_fact": {"kind": "clarify", ...}, "proposal_id": None, "context_block": with_line(declined_line(...))}`. `proposal_id` không khớp hoặc `approve` trả `not_approvable` → `draft=BUTTON_STALE_REPLY`, không ghi.
- Chuỗi: `BUTTON_NO_REPLY = "Dạ, anh chị muốn đổi giờ khác hay đổi dịch vụ ạ?"`; `BUTTON_STALE_REPLY = "Đề xuất lúc nãy hết hạn rồi, anh chị nhắn lại giờ giúp em nhé."` (thay "anh chị" bằng `address`).

- [ ] **Step 1: Viết test đỏ** trong `tests/test_confirm_flow.py` (dùng fixture `models`, helper `turn`; thêm helper `button(test_db, user, decision, proposal_id)` gọi `run_turn` với `ButtonReply` của Task 5 — ở task này gọi thẳng graph: `graph.ainvoke(Command(resume={"button": "yes", "proposal_id": pid}), config)`):

```python
async def test_button_yes_books_without_classifier(test_db, models, lan):
    # lượt 1: "mai 3 giờ chiều làm tóc" → proposal pending (lấy pid từ ProposalService.latest)
    # resume dict yes → appointments có đúng 1 lịch 15:00 mai; models["classify"].calls == 0
async def test_button_no_rejects_and_asks_what_to_change(test_db, models, lan):
    # resume dict no → proposal.status == "rejected"; answer == BUTTON_NO_REPLY; 0 lịch
async def test_second_button_press_is_idempotent(test_db, models, lan):
    # yes hai lần (lượt mới với cùng pid qua ProposalService.approve) → vẫn 1 lịch, lần 2 là approved idempotent
async def test_button_on_foreign_or_expired_proposal_writes_nothing(test_db, models, lan, hung):
    # pid của hung gửi bởi lan → 0 lịch, answer == BUTTON_STALE_REPLY; pid hết hạn (sửa expires_at) → như trên
```

- [ ] **Step 2: Chạy, xác nhận FAIL** (resume dict bị `reply.decide(text=dict…)` nổ hoặc ghi sai).
- [ ] **Step 3: Implement** trong `make_confirm_node`: ngay sau `interrupt(...)`, `if isinstance(text, dict): return await _handle_button(...)`; `_handle_button(db, user, state, text, address, now)` theo Interfaces. Đường chữ giữ nguyên.
- [ ] **Step 4: Chạy** `tests/test_confirm_flow.py tests/test_confirm.py` → PASS.
- [ ] **Step 5: Commit** `feat: confirm node accepts yes/no button resume`.

### Task 3: Câu chốt bằng code cho đường nút

**Files:**
- Modify: `app/agents/booking_graph/phrase.py` (thêm `code_phrase`), `app/agents/booking_graph/middleware/phrase.py:40-50`
- Test: `tests/test_phrase.py`, `tests/test_mw_phrase.py`

**Interfaces:**
- Produces: `code_phrase(fact: dict, previous_replies: Sequence[str]) -> str`. `fact["kind"] ∈ {booked, moved, cancelled, failed}`, `fact["context"] ∈ {first_time, returning, changed_time, after_decline}` (Task 15 điền; thiếu → `first_time`). Mỗi (kind, context) có ≥3 mẫu chứa `{address}` và `{when}`; chọn mẫu đầu tiên không trùng ≥85% (`guard.repeats`) với `previous_replies`; hết mẫu → mẫu đầu. `kind="failed"` → `fact["fallback"]`.
- `PhraseMiddleware.abefore_agent`: `state.get("confirm_source") == "button"` → `text = code_phrase(fact, state.get("previous_replies") or [])`, không gọi LLM.

- [ ] **Step 1: Test đỏ** (`tests/test_phrase.py`):

```python
def test_code_phrase_avoids_a_template_already_used():
    fact = {"kind": "booked", "when": "Thứ Tư 21/10, 3 giờ chiều", "address": "chị Lan", "context": "first_time"}
    first = code_phrase(fact, [])
    second = code_phrase(fact, [first])
    assert first != second and "3 giờ chiều" in second and "chị Lan" in second
def test_code_phrase_failed_uses_fallback():
    assert code_phrase({"kind": "failed", "fallback": "X"}, []) == "X"
```
và (`tests/test_mw_phrase.py`) `test_button_turn_does_not_call_the_phrase_model`: state có `confirm_source="button"` → model phrase `calls == []`.

- [ ] **Step 2: FAIL** `ImportError: code_phrase`.
- [ ] **Step 3: Implement** `code_phrase` + bảng mẫu `_TEMPLATES` trong `phrase.py`; rẽ nhánh trong middleware.
- [ ] **Step 4: PASS** `tests/test_phrase.py tests/test_mw_phrase.py tests/test_mw_guard.py`.
- [ ] **Step 5: Commit** `feat: code-written confirmation phrase for button path`.

### Task 4: `complete` mang `proposal` đang chờ

**Files:**
- Modify: `app/agents/booking_graph/events.py:285-300` (nơi yield `complete`)
- Test: `tests/test_graph_events.py`

**Interfaces:**
- Produces: `complete` data = `{"answer": str, "proposal": {"id": str, "kind": "book"|"move"|"cancel", "expires_at": iso, "when_spoken": str} | None}`. `proposal` khác `None` khi snapshot sau lượt có `interrupts` và `values["proposal_id"]`; lấy từ `ProposalService.get(user, pid)`; `when_spoken = format_vi_datetime(p.start_at)`.
- Helper: `pending_proposal_payload(db, user, snapshot) -> Optional[dict]` trong `events.py`.

- [ ] **Step 1: Test đỏ** (`tests/test_confirm_flow.py`, có `run_turn` thật): `test_complete_carries_the_pending_proposal` — lượt "mai 3 giờ chiều làm tóc" → event `complete` có `proposal["kind"]=="book"`, `proposal["id"]` trùng `ProposalService.latest(lan).id`; lượt không đề xuất ("chào em") → `proposal is None`.
- [ ] **Step 2: FAIL** `KeyError: 'proposal'`.
- [ ] **Step 3: Implement** helper và thêm vào payload `complete` (trước `persist`).
- [ ] **Step 4: PASS** `tests/test_confirm_flow.py tests/test_graph_events.py tests/test_socketio_chat.py`.
- [ ] **Step 5: Commit** `feat: complete event carries the pending proposal`.

### Task 5: Event Socket.IO `confirm_proposal`

**Files:**
- Modify: `app/services/socketio_service.py:53-125`, `app/agents/booking_graph/events.py:163-271` (`run_turn` nhận `ButtonReply`)
- Create: `app/agents/booking_graph/button.py` (`ButtonReply` dataclass)
- Test: `tests/test_socketio_chat.py`, `tests/test_run_turn_thread.py`

**Interfaces:**
- Produces: `@dataclass(frozen=True) ButtonReply: decision: Literal["yes","no"]; proposal_id: str; label: str` với `label = "Đồng ý" | "Không"`. `run_turn(db, user, question: str | ButtonReply)`: với `ButtonReply` → `question_text = reply.label` được ghi vào `conversations` và thread như câu khách (`fields["customer_text"] = label`), `turn_input = Command(resume={"button": decision, "proposal_id": proposal_id})`; nếu thread **không** đang chờ (`waiting=False`) → không gọi graph, yield `complete {"answer": BUTTON_STALE_REPLY, "proposal": None}`.
- Handler `confirm_proposal(sid, data)`: `data = {"proposal_id": str, "decision": "yes"|"no", "message_id": str?}`; sai kiểu → `error {"message": TURN_ERROR_MESSAGE}`; **không** `check_and_hit` rate limit chat; gọi `handle_button(sid, user, ButtonReply(...))` → `run_turn`.

- [ ] **Step 1: Test đỏ** (`tests/test_socketio_chat.py`, fixture `service` + monkeypatch `run_turn` ghi lại tham số):

```python
async def test_confirm_proposal_runs_a_button_turn_without_rate_limit(service, ...):
    # emit confirm_proposal {"proposal_id": "p1", "decision": "yes"} → run_turn nhận ButtonReply("yes","p1","Đồng ý"); RateLimitService.check_and_hit không được gọi
async def test_confirm_proposal_rejects_bad_payload(service, ...):
    # decision "maybe" → emit error, run_turn không gọi
```
và (`tests/test_run_turn_thread.py`) `test_button_turn_when_nothing_is_waiting_replies_stale`.

- [ ] **Step 2: FAIL** (event không đăng ký).
- [ ] **Step 3: Implement** `button.py`, nhánh `ButtonReply` trong `_run_turn`, handler + `handle_button` trong `socketio_service.py`.
- [ ] **Step 4: PASS** `tests/test_socketio_chat.py tests/test_run_turn_thread.py tests/test_confirm_flow.py`.
- [ ] **Step 5: Cập nhật AGENTS.md** mục giao thức Socket.IO (event mới + trường `proposal`). **Commit** `feat: confirm_proposal socket event drives the button path`.

### Task 6: Frontend — nút Đồng ý / Không

**Files:**
- Modify: `frontend/src/hooks/useAgentStream.ts` (state `proposal`, hàm `confirm`, `message_id` cho `send_message`)
- Create: `frontend/src/components/ConfirmButtons.tsx`
- Modify: `frontend/src/screens/ChatScreen.tsx:109-150`
- Test: `frontend/src/hooks/useAgentStream.test.ts`, `frontend/src/components/ConfirmButtons.test.tsx`

**Interfaces:**
- Consumes: `complete {answer, proposal}` (Task 4); event `confirm_proposal {proposal_id, decision, message_id}` (Task 5).
- Produces: hook trả thêm `proposal: {id, kind, expiresAt: string, whenSpoken: string} | null` và `confirm(decision: "yes"|"no"): void` (emit, thêm bong bóng user `"Đồng ý"|"Không"`, đặt `proposal=null`, phase `thinking`). `send(text)` emit `{message, message_id: crypto.randomUUID()}`. `<ConfirmButtons proposal onConfirm expired>`: hai nút, chữ ≥18px, cao ≥48px, Đồng ý là nút chính; `expired` (Date.now() > expiresAt) → disabled + chữ "Đề xuất đã hết hạn, anh chị nhắn lại giúp em".

- [ ] **Step 1: Test đỏ** (vitest): `useAgentStream.test.ts`: `complete` với `proposal` → `result.current.proposal.id === "p1"`; `confirm("yes")` → socket.emit gọi `("confirm_proposal", {proposal_id: "p1", decision: "yes", message_id: <string>})` và messages có bong bóng `{role: "user", text: "Đồng ý"}`; `complete` không `proposal` → `null`. `ConfirmButtons.test.tsx`: render 2 nút, click gọi `onConfirm("yes")`; `expired` → cả hai `disabled`.
- [ ] **Step 2: FAIL** (`proposal`/`confirm` undefined).
- [ ] **Step 3: Implement** hook + component + gắn vào `ChatScreen` ngay dưới danh sách tin nhắn khi `proposal && phase === "idle"`.
- [ ] **Step 4: PASS** `cd frontend && npx vitest run`; `npm run build` không lỗi TypeScript.
- [ ] **Step 5: Commit** `feat(frontend): approve/decline buttons under the confirmation question`.

### Task 7: Benchmark — lượt bấm nút và kịch bản nút

**Files (repo `salona-booking`, nhánh `bench/harness-benchmark`):**
- Modify: `bench/scenario.py` (`Turn` thêm `button: Optional[Literal["yes","no"]]`), `bench/adapters/a.py` (`send` với `turn.button` → emit `confirm_proposal` dùng `proposal.id` từ `complete` trước; lưu `message_id` uuid cho mọi emit)
- Create: `bench/scenarios/edge/nut-dong-y.yaml`, `bench/scenarios/edge/nut-khong.yaml`
- Test: `bench/tests/test_adapter_a.py` (hoặc file test adapter hiện có), `bench/tests/test_scenario.py`

- [ ] **Step 1: Test đỏ**: `test_turn_accepts_button` (YAML `- button: yes` parse ra `Turn(button="yes")`); `test_adapter_emits_confirm_proposal_with_last_proposal_id` (fake socket client ghi emit).
- [ ] **Step 2: FAIL**. **Step 3: Implement**. **Step 4: PASS** `bench/.venv/bin/python -m pytest -q bench/tests`.
- [ ] **Step 5: Viết kịch bản**: `nut-dong-y`: turns `mai 3 giờ chiều chị làm tóc` → `button: yes`; expect `booked: [{day: tomorrow, time: "15:00"}]`. `nut-khong`: turns … → `button: no` → `say: thôi 4 giờ đi em` → `intent: agree`; expect booked 16:00, `answer_must_not_include: ["đã đặt"]` ở lượt nút không.
- [ ] **Step 6: Commit** (repo bench) `feat(bench): button turns and two button scenarios`.

### Mốc M1 — đo

- [ ] Tag `target/m1` trên `feat/target-architecture`. Chạy `bench run --ref harness-b/align:a --ref target/m1:a --tags edge,llm --k 4` (từ `salona-booking`). Cổng: ghi sai 0; `first_token_ms` lượt nút ≤ 1500. Ghi kết quả vào CONTEXT.md, commit `docs: record M1 benchmark`.

## Mốc M2 — Lỗi LLM-02 và tin trùng

### Task 8: Khẳng định ngắn sau lượt booking đi vào booking

**Files:**
- Modify: `app/agents/booking_graph/supervisor.py:37-50`, `app/agents/booking_graph/state.py` (thêm `last_route: NotRequired[str]`), `app/agents/booking_graph/graph.py` (node `record` ghi `last_route = state["route"]`)
- Test: `tests/test_supervisor.py`, `tests/test_graph_wiring.py`

**Interfaces:**
- Produces: `is_short_affirmation(text: str) -> bool` trong `reply.py` (≤ 4 từ sau khi bỏ dấu câu, toàn bộ nằm trong tập `_YES_BARE` hiện có của `reply.py`, không chứa từ phủ định/giờ). Trong `supervise`: `if state.get("last_route") == "booking" and is_short_affirmation(text): return {"route": "booking"}` (trước khi gọi LLM, sau các nhánh queue/relative).

- [ ] **Step 1: Test đỏ**: `test_short_ok_after_a_booking_turn_routes_to_booking_without_llm` (state `last_route="booking"`, text "Ok em", model fake không được gọi → `calls == 0`); `test_short_ok_after_social_turn_still_asks_the_model`.
- [ ] **Step 2: FAIL**. **Step 3: Implement** (+ `record` ghi `last_route`). **Step 4: PASS** `tests/test_supervisor.py tests/test_graph_wiring.py tests/test_reply_veto.py`.
- [ ] **Step 5: Commit** `fix: route short affirmations after a booking turn back to booking`.

### Task 9: Không hỏi "được không" khi chưa có đề xuất

**Files:**
- Modify: `app/agents/booking_graph/prompts.py` (BOOKING_PROMPT: đoạn "propose before asking yes/no"), `app/agents/booking_graph/guard.py:426-440` (`find_violations` thêm code `ask_without_proposal`), `app/agents/booking_graph/rewrite.py` (hướng dẫn rewrite cho code mới)
- Test: `tests/test_guard.py`, `tests/test_prompts.py`
- Bench: `bench/scenarios/edge/ok-khong-de-xuat.yaml`

**Interfaces:**
- Produces: `asks_yes_no_about_a_clock(draft: str) -> bool` trong `guard.py`: câu cuối kết thúc `?`, chứa `được không|nhé|nha` **và** `clock_keys(draft)` không rỗng. `find_violations(..., has_proposal: bool)` thêm `"ask_without_proposal"` khi `not has_proposal and asks_yes_no_about_a_clock(draft)`. Rewrite hint: "Offer the time as a choice and ask which time suits, do not ask for a yes/no confirmation."

- [ ] **Step 1: Test đỏ** (`tests/test_guard.py`): `test_yes_no_question_about_a_time_without_proposal_is_flagged` (draft "Em giữ cho anh chị 2 giờ chiều mai được không?" + `has_proposal=False` → `"ask_without_proposal" in codes`); `test_same_question_with_a_proposal_is_fine`. (`tests/test_prompts.py`) `assert "propose_appointment" in BOOKING_PROMPT and "before asking" in BOOKING_PROMPT`.
- [ ] **Step 2: FAIL**. **Step 3: Implement**; `GuardMiddleware` truyền `has_proposal=bool(state.get("proposal_id"))`. **Step 4: PASS** `tests/test_guard.py tests/test_mw_guard.py tests/test_prompts.py tests/test_rewrite.py`.
- [ ] **Step 5: Kịch bản bench** `ok-khong-de-xuat` (tags edge, LLM-02b): turns `Ngày mai 2 giờ chiều làm móng được không em` → `Ok em` → `intent: agree`; expect `booked: [{day: tomorrow, time: "14:00"}]`, `answer_must_not_include: ["chào anh chị", "tạm biệt"]`.
- [ ] **Step 6: Commit** `fix: never ask a yes/no time question without a proposal`.

### Task 10: Dedupe tin nhắn — Redis với fallback Mongo

**Files:**
- Create: `app/infrastructure/redis.py` (`get_redis() -> Optional[redis.asyncio.Redis]`, khởi tạo lười từ `settings.redis_url`, ping lúc lifespan: lỗi → `warning`, trả None)
- Create: `app/services/dedupe.py`
- Modify: `app/core/config.py` (+`redis_url: Optional[str] = None`), `.env.example`, `.env`, `requirements.txt` (+`redis==5.x` pin), `app/infrastructure/database.py` (`message_inbox`: unique `(user_id, message_id)`, TTL `expires_at`), `app/services/socketio_service.py` (`handle_message`/`handle_button` dùng dedupe), `main.py` (lifespan ping Redis)
- Test: `tests/test_dedupe.py`, `tests/test_socketio_chat.py`

**Interfaces:**
- Produces: `class MessageDedupe: async begin(user_id, message_id) -> Literal["new","running"] | dict` (dict = payload `complete` đã lưu); `async finish(user_id, message_id, complete_payload: dict) -> None`; `async abandon(user_id, message_id) -> None` (lượt lỗi/hủy → xóa khóa running). Backend: `RedisBackend` (SET NX `turn:{u}:{m}` TTL 90 s; `msg:{u}:{m}` TTL 24 h) và `MongoBackend` (`message_inbox` doc `{user_id, message_id, status: running|done, complete, expires_at}`); `MessageDedupe(backend)` bắt mọi lỗi của Redis backend, log `dedupe_redis_failed`, chuyển sang Mongo backend cho lời gọi đó. `message_id` thiếu → `begin` trả `"new"` và không lưu gì.
- `handle_message`: `state = await dedupe.begin(...)`; `"running"` → bỏ qua, log `duplicate_in_flight`; dict → `emit("complete", dict)` rồi return; `"new"` → chạy như cũ, `finish` sau `complete`, `abandon` khi `error`/hủy.

- [ ] **Step 1: Test đỏ** (`tests/test_dedupe.py`, backend Mongo dùng `test_db`, backend Redis giả bằng class dict trong test thực hiện `set(nx, ex)`/`get`/`delete`):

```python
async def test_first_message_is_new_then_running_then_done(...)
async def test_same_message_id_while_running_is_ignored(...)      # begin lần 2 == "running"
async def test_finished_message_replays_the_complete_payload(...)
async def test_redis_error_falls_back_to_mongo(...)               # backend Redis ném ConnectionError → kết quả như Mongo
async def test_missing_message_id_never_dedupes(...)
```
(`tests/test_socketio_chat.py`) `test_duplicate_message_id_replays_complete_without_running_a_turn`, `test_different_message_id_still_supersedes` (run_turn giả treo; tin khác id → run_turn gọi lần 2).

- [ ] **Step 2: FAIL**. **Step 3: Implement** theo Interfaces; `docker-compose.yml` thêm service `redis: image: redis:7-alpine` không publish port; `.env.example` thêm `# REDIS_URL=redis://redis:6379/0`.
- [ ] **Step 4: PASS** `tests/test_dedupe.py tests/test_socketio_chat.py tests/test_requirements_pins.py`; toàn suite xanh.
- [ ] **Step 5: Bench** (repo bench): `bench/scenarios/edge/tin-gui-lai.yaml` + `Turn.resend: Literal["after_complete","while_running"]` trong adapter (gửi lại cùng `message_id`): expect đúng 1 proposal/1 lịch, `max_turns` không đổi. Test adapter cho `resend`.
- [ ] **Step 6: Commit** `feat: message dedupe via Redis with Mongo fallback` (+ commit bench).

### Task 11: Redis cache trạng thái tiệm

**Files:**
- Modify: `app/services/shop.py` (`get_status`, `get_hours` đọc qua `cache_get_or_load`), `app/infrastructure/redis.py` (+`cache_get_or_load(key, ttl_s, loader) -> Any`), ghi invalidate khi admin đổi trạng thái (`ShopService.set_busy/…` → `cache_delete("shop:status")`)
- Test: `tests/test_shop_cache.py`

- [ ] **Step 1: Test đỏ**: `test_status_is_served_from_cache_within_ttl` (loader gọi 1 lần cho 2 lần đọc); `test_changing_status_invalidates_cache`; `test_cache_backend_error_reads_mongo`.
- [ ] **Step 2: FAIL**. **Step 3: Implement** (TTL `shop:status` 30 s, `shop:hours` 600 s). **Step 4: PASS** `tests/test_shop_cache.py tests/test_shop*.py tests/test_tools.py`.
- [ ] **Step 5: Commit** `feat: cache shop status and hours in Redis`.

### Mốc M2 — đo

- [ ] Tag `target/m2`; chạy `--tags edge,llm --k 4` so `target/m1`. Cổng: LLM-02 và `ok-khong-de-xuat` k/k; `tin-gui-lai` 1 đề xuất. Ghi CONTEXT.md, commit.

## Mốc M3 — Trí nhớ

### Task 12: Bộ phân tích câu khách và slots mở rộng (module, chưa nối graph)

> Sửa 25/10/2026: thay "nén nền mỗi lượt" bằng bộ phân tích đầu lượt (spec mục 3, bản sửa cùng ngày).

**Files:**
- Create: `app/agents/booking_graph/analyzer.py`
- Modify: `app/models/conversation.py:32-48` (`ConversationSlots` + `stated: List[str]`, `asked: List[AskedItem]`, `told: List[str]`, `agreed_at: Optional[AgreedAt]`; `AskedItem{question: str, answered: bool, times: int}`, `AgreedAt{proposal_id: str, at: datetime}`), `app/services/conversation_state.py` (`sanitize_slots` lọc các trường mới; thêm `apply_turn_analysis`), `app/core/text.py` (+`detect_self_address`, xem Task 13 Interfaces — làm ở đây vì sanitize cần), `app/agents/booking_graph/context.py:92-124` (`render_slots` in thêm 4 nhóm dòng), `app/agents/booking_graph/prompts.py` (+`ANALYZE_PROMPT` tiếng Anh, không câu ví dụ tiếng Việt; BOOKING/SOCIAL: luật đọc `asked`/`stated`/`told`)
- Test: `tests/test_analyzer.py` (mới), `tests/test_conversation_slots.py`, `tests/test_context_block.py`, `tests/test_prompts.py`, `tests/test_text.py`

**Interfaces:**
- Produces: `class TurnAnalysis(BaseModel)`: `route: Literal["booking","shop","social"]`, `reply_to_proposal: Optional[Literal["approve","reject","change","unclear"]]`, `service: Optional[str]`, `time_text: Optional[str]`, `self_address: Optional[Literal["chị","anh"]]`, `stated: List[str]` (≤2), `answers_question: bool`. `TurnAnalysis.fallback(has_pending: bool) -> TurnAnalysis` (`route="booking"`, `reply_to_proposal="unclear" if has_pending else None`, còn lại rỗng/False).
- `async analyze_turn(messages: Sequence[BaseMessage], customer_text: str, pending: Optional[Mapping[str, Any]]) -> TurnAnalysis`: một lượt `build_chat_model(tags=["analyze"], streaming=False, reasoning_effort="low", timeout=8, max_retries=1).with_structured_output(TurnAnalysis)`, bọc `asyncio.wait_for(…, 8)`; mọi Exception → `fallback`, log `analyze_failed`; kết quả luôn qua `sanitize_analysis`. `pending` mô tả đề xuất như `reply._describe` (chỉ giờ, không ghi chú khách gõ).
- `sanitize_analysis(a: TurnAnalysis, customer_text: str, has_pending: bool) -> TurnAnalysis`: `reply_to_proposal=None` nếu không có đề xuất, `None`→`"unclear"` nếu có; `time_text` không phải chuỗi con (bỏ dấu, lowercase) của câu khách → None; `self_address` ≠ `detect_self_address(customer_text)` → None; `service`/`stated` qua cùng bộ lọc với `sanitize_slots` (≤60 ký tự, bỏ `_PHONE_RE`/`_ADDRESS_RE`/`_IMPERATIVE_RE` = `hãy|bỏ qua|ignore|quên|luôn đồng ý`), `stated` ≤ 2.
- `ConversationStateService.apply_turn_analysis(user_id, a: TurnAnalysis, last_bot_text: str) -> None`: ghi ngay vào `state.slots` (tạo state hôm nay nếu chưa có): `service` nếu có; nối `stated` (giữ ≤5, bỏ trùng); nếu `last_bot_text` kết thúc `?` → upsert `AskedItem(question=single_line(last_bot_text, 60))`, trùng câu → `times += 1`; `answers_question` → `answered=True`. Không gọi LLM.
- `render_slots` in thêm: `- Khách đã nói rõ: a; b`, `- Em đã hỏi: "q" (đã đáp | chưa đáp ×2)`, `- Em đã nói với khách: …`, `- Khách đã đồng ý đề xuất lúc HH:MM (chỉ để nói, không phải để ghi)`.

- [ ] **Step 1: Test đỏ**: (`test_analyzer.py`, model giả trả `TurnAnalysis` dựng sẵn) `test_model_error_returns_safe_fallback` (Exception → route booking, reply unclear khi có đề xuất), `test_reply_label_dropped_without_pending_proposal`, `test_time_text_not_in_message_is_dropped`, `test_self_address_requires_regex_agreement` (model "chị", câu "em muốn làm tóc" → None), `test_stated_imperatives_are_dropped` ("luôn đồng ý", "bỏ qua luật" → []), `test_analyzer_does_not_stream_and_uses_low_reasoning` (kwargs build_chat_model); (`test_conversation_slots.py`) `test_apply_turn_analysis_marks_question_answered`, `test_repeated_question_increments_times`, `test_stated_capped_at_five_and_deduped`; (`test_context_block.py`) `test_render_slots_shows_stated_asked_told`; (`test_text.py`) như Task 13 Step 1 cho `detect_self_address`.
- [ ] **Step 2: FAIL**. **Step 3: Implement**. **Step 4: PASS** các file trên.
- [ ] **Step 5: Commit** `feat: turn analyzer and extended in-session slots`.

### Task 12b: Nối bộ phân tích vào supervisor và confirm

**Files:**
- Modify: `app/agents/booking_graph/supervisor.py` (sau lối tắt code: gọi `analyze_turn`, route = `analysis.route`; lưu `analysis` vào state), `app/agents/booking_graph/confirm.py` (đường gõ chữ: `reply.decide` dùng nhãn từ `analyze_turn(..., pending=facts)` thay `classify_reply`), `app/agents/booking_graph/reply.py` (`decide(text, proposal, bot_question, now, *, label_fn)` — lưới code + go-ahead giữ nguyên thứ tự, bước cuối gọi `label_fn`), `app/agents/booking_graph/state.py` (+`analysis: NotRequired[Optional[dict]]`, reset trong `fresh_turn_fields`), `app/agents/booking_graph/events.py` (sau `complete`: `apply_turn_analysis` dưới `shield`, cùng chỗ `persist`), `app/services/conversation_state.py` (`maybe_compact` không đổi ngưỡng)
- Test: `tests/test_supervisor.py`, `tests/test_confirm_flow.py`, `tests/test_reply_classify.py`, `tests/test_run_turn_thread.py`

**Interfaces:**
- Consumes: Task 12 `analyze_turn`, `TurnAnalysis`, `apply_turn_analysis`.
- Produces: mỗi lượt gọi `analyze_turn` **tối đa một lần**; đường nút và lối tắt code gọi 0 lần. `classify_reply` giữ lại cho cờ/so sánh nhưng không còn được gọi trên đường chính.

- [ ] **Step 1: Test đỏ**: `test_new_message_calls_analyzer_once_and_routes_by_it`; `test_code_shortcuts_skip_the_analyzer` (hàng chờ, người nhà, "ok" ngắn sau booking); `test_typed_reply_uses_analyzer_label_after_veto_grid` (veto vẫn thắng: "dạ ko em" + analyzer "approve" → không ghi); `test_analyzer_failure_on_typed_reply_never_books`; `test_button_turn_never_calls_analyzer`; `test_slots_updated_in_the_same_turn` (lượt 2 thấy `stated` của lượt 1 trong khối slots mà nén nền không chạy).
- [ ] **Step 2: FAIL**. **Step 3: Implement**. **Step 4: PASS** + toàn suite.
- [ ] **Step 5: Commit** `feat: route and label confirmations through the turn analyzer`.

### Task 12c: Nén theo mục cố định, cuốn chiếu, kèm câu gốc của khách

> Thêm 25/10/2026 (spec mục 3, đoạn "Nén giữ lại, đổi cách tóm").

**Files:**
- Modify: `app/models/conversation.py` (+`StateDigest`, `ConversationState.digest: Optional[StateDigest] = None`, `ConversationState.customer_quotes: List[str] = []`; giữ `summary` cho doc cũ), `app/services/conversation_state.py` (`StateOutput.summary` → `StateOutput.digest: StateDigest`; `_compact` đưa digest cũ vào prompt; merge an toàn; chọn câu gốc), `app/agents/booking_graph/prompts.py` (prompt nén tiếng Anh: năm mục, cuốn chiếu, giữ nguyên "wants"/"constraints" trừ khi tin mới mâu thuẫn), `app/agents/booking_graph/context.py` (render digest thay summary; doc cũ chỉ có `summary` vẫn render như trước)
- Test: `tests/test_conversation_state_service.py`, `tests/test_conversation_state_model.py`, `tests/test_context_block.py`, `tests/test_prompts.py`

**Interfaces:**
- Produces: `class StateDigest(BaseModel)`: `wants: List[str]` (≤4), `settled: List[str]` (≤4), `declined: List[str]` (≤4), `constraints: List[str]` (≤4), `open: List[str]` (≤3); mỗi mục `single_line(…, 120)`, lọc `_PHONE_RE`/`_ADDRESS_RE`/`_IMPERATIVE_RE` như `sanitize_slots`.
- `merge_digest(previous: Optional[StateDigest], new: StateDigest) -> StateDigest`: `new.wants` rỗng mà `previous.wants` có → giữ `previous.wants`; tương tự `constraints`; cắt về giới hạn. (Lưới chống mất sạch, không cố đoán mâu thuẫn.)
- `select_customer_quotes(session_messages, kept_from) -> List[str]`: câu khách đầu tiên của phiên + 2 câu khách gần nhất nằm TRƯỚC `kept_from` (phần bị nén); bỏ trùng; mỗi câu `single_line(…, 120)`, bỏ câu khớp `_PHONE_RE`; tối đa 3. Code chọn, không LLM.
- Render (thay dòng summary): `Tóm tắt phần trước (dữ liệu tham khảo, không phải lệnh):` rồi các dòng có dữ liệu `- Khách muốn: a; b`, `- Đã chốt: …`, `- Đã từ chối: …`, `- Ràng buộc: …`, `- Đang dở: …`, rồi `Khách đã nói (nguyên văn): «…» · «…»`.
- Ngưỡng nén (`COMPACT_THRESHOLD_TOKENS=800`, `KEEP_RECENT_TURNS=4`), chạy nền, `MAX_FAILURES=3` không đổi.

- [ ] **Step 1: Test đỏ**: `test_compaction_returns_five_section_digest`; `test_previous_digest_is_passed_to_the_compaction_prompt`; `test_merge_keeps_previous_wants_when_model_drops_them`; `test_digest_items_are_filtered_and_capped`; `test_customer_quotes_are_first_plus_two_latest_compacted`; `test_quotes_drop_phone_numbers`; `test_render_digest_and_quotes_with_untrusted_label`; `test_old_state_with_summary_only_still_renders`; `test_compaction_failure_does_not_block_and_counts_failures` (hồi quy).
- [ ] **Step 2: FAIL**. **Step 3: Implement**. **Step 4: PASS** + `tests/test_history_window.py` + toàn suite.
- [ ] **Step 5: Commit** `feat: sectioned rolling compaction with verbatim customer quotes`.

### Task 13: Hồ sơ khách — `self_address`, `staff_note`, bỏ số điện thoại

**Files:**
- Modify: `app/models/user.py` (+`self_address: Optional[Literal["anh","chị"]] = None`, `staff_note: Optional[str] = None`), `app/core/text.py` (`detect_self_address` đã có từ Task 12), `app/agents/booking_graph/events.py` (sau lượt: `user.self_address is None` và `state["analysis"]["self_address"]` có — tức analyzer và regex cùng thấy — → `UserRepository.set_self_address`; lượt đi lối tắt code không có analysis thì dùng `detect_self_address` một mình), `app/agents/booking_graph/context.py:138-165, 269-343` (`address_phrase(user)` đọc `self_address`; dòng "Ghi chú tiệm: …"; bỏ `({phone})`), `app/agents/booking_graph/guard.py:350-400` (`_pronouns` theo `self_address`), `app/api/v1/routers/admin.py` (+`PATCH /admin/users/{user_id}/note {staff_note}` ≤200 ký tự, `require_admin`), `main.py` lifespan (migration một lần: `full_name` có tiền tố → seed `self_address` theo bảng `cô/chị/bà→chị`, `chú/anh/ông→anh`)
- Test: `tests/test_text.py`, `tests/test_context_block.py`, `tests/test_guard.py`, `tests/test_admin_stats_api.py` (hoặc `tests/test_admin_users_api.py` mới), `tests/test_user_model.py`

**Interfaces:**
- Produces: `detect_self_address`: khớp `\b(chị|anh)\s+(làm|muốn|đặt|hủy|dời|đổi|qua|tới|đến|hỏi|cần|xem|rảnh)\b` hoặc `\b(cho|giúp|với)\s+(chị|anh)\b` ở **đầu mệnh đề**; trả None với "em", với "chị/anh + Tên viết hoa" (ngôi thứ ba), và khi cả hai từ cùng xuất hiện. `address_phrase(user: User) -> str`: `self_address` → `f"{self_address} {display_name}"`, không thì `"anh chị"` (đổi chữ ký từ `full_name` sang `user`; cập nhật mọi caller).

- [ ] **Step 1: Test đỏ**: (`test_text.py`) `test_detects_first_person_chi_and_anh` ("mai 3 giờ chị làm móng"→"chị"; "cho anh 3 giờ chiều"→"anh"), `test_em_and_third_person_do_not_set_self_address` ("em muốn làm tóc"→None; "chị Hoa bạn em làm được không"→None; "anh chị ơi"→None); (`test_context_block.py`) `test_block_uses_self_address_and_omits_phone`, `test_staff_note_line_present_when_set`; (`test_guard.py`) `test_pronoun_check_accepts_chi_lan_when_self_address_is_chi`; (admin API) `test_admin_can_set_staff_note`, `test_note_longer_than_200_is_rejected`; (`test_user_model.py`) `test_prefixed_name_seeds_self_address_once`.
- [ ] **Step 2: FAIL**. **Step 3: Implement**. **Step 4: PASS** các file trên + `tests/test_confirm_flow.py` (confirm dùng `address_phrase(user)`).
- [ ] **Step 5: Commit** `feat: customer self-address and staff note in the context block`.

### Task 14: `conversation_sessions` và "Lần trước"

**Files:**
- Create: `app/repositories/conversation_session.py`, `app/models/conversation.py` (+`ConversationSession{user_id, day, summary, slots, closed_at, expires_at}`)
- Modify: `app/services/conversation_state.py` (khi `_compact` chạy → upsert session của `day`; khi `pick_thread` sang ngày mới → `closed_at`), `app/infrastructure/database.py` (unique `(user_id, day)`, TTL `expires_at` 30 ngày), `app/agents/booking_graph/context.py` (`load_context` thêm `recent_visits` (3 lịch đã qua, `start_at < now`, status `booked|done`) và `last_session`; `build_context_block(..., recent_visits=None, last_session=None)` in `Khách đã đến: …` và `Lần trước (hôm qua|dd/mm): {summary 1 dòng}` khi `slots.intent` có mà không có lịch mới sau `closed_at`)
- Test: `tests/test_conversation_sessions.py`, `tests/test_context_block.py`

- [ ] **Step 1: Test đỏ**: `test_compaction_upserts_todays_session`, `test_last_session_line_only_when_intent_left_open`, `test_recent_visits_lists_three_past_appointments_newest_first`.
- [ ] **Step 2: FAIL**. **Step 3: Implement**. **Step 4: PASS**. **Step 5: Commit** `feat: per-day conversation sessions feed the context block`.

### Mốc M3 — đo

- [ ] Tag `target/m3`; chạy `--tags edge,llm --k 4`. Cổng: ghi sai 0; `hoi_lai_da_biet` không giảm; LLM-09 (khách nhờ tiệm chọn) k/k. Ghi CONTEXT.md, commit.

## Mốc M4 — Tự nhiên và cờ đo

### Task 15: `phrase` theo ngữ cảnh; cờ `CONFIRM_VETO_MODE`, `GUARD_VOICE_RULES`

**Files:**
- Modify: `app/agents/booking_graph/confirm.py:61-92` (`confirm_fact_for(result, address, *, context)`; context tính trong node: `changed_time` nếu proposal có `superseded` cùng phiên trước đó, `after_decline` nếu khối bối cảnh có `[declined`, `returning` nếu `recent_visits` không rỗng, còn lại `first_time`), `app/agents/booking_graph/phrase.py` (prompt LLM đọc `fact["context"]` + `last_reply`), `app/agents/booking_graph/reply.py:372-383` (`decide(..., veto_mode)`: `time_only` → chỉ `other_time` + `is_go_ahead`), `app/agents/booking_graph/guard.py:426-440` (`find_violations(..., voice_rules: bool)`: `False` → bỏ `repeat/pronoun/clock`), `app/core/config.py` (+`confirm_veto_mode: Literal["full","time_only"]="full"`, `guard_voice_rules: bool=True`), `.env.example`, `.env`
- Test: `tests/test_confirm.py`, `tests/test_reply_veto.py`, `tests/test_guard.py`, `tests/test_phrase.py`

- [ ] **Step 1: Test đỏ**: `test_fact_context_is_changed_time_after_a_superseded_proposal`; `test_time_only_mode_lets_negation_reach_the_classifier` ("dạ hông" với `veto_mode="time_only"` → gọi classifier, không veto); `test_time_only_mode_still_vetoes_a_different_time`; `test_voice_rules_off_keeps_claim_and_tool_name_only`; `test_phrase_prompt_mentions_context_and_last_reply`.
- [ ] **Step 2: FAIL**. **Step 3: Implement**. **Step 4: PASS** + toàn suite.
- [ ] **Step 5: Commit** `feat: contextual confirmation phrasing; veto and voice-rule flags`.

### Task 16: Rubric judge, kịch bản tự xưng, đo ma trận cờ

**Files (repo bench):**
- Modify: `bench/judge.py:26-45` (rubric `xung_ho`: "anh chị" đúng khi khách chưa tự xưng; sai khi đã tự xưng mà vẫn "anh chị" hoặc bot đoán giới), `bench/judge.py:73` (`render_transcript(turns, self_address)` thêm dòng đầu `Khách tự xưng: chị|anh|chưa`), `bench/runner.py` (truyền `self_address` lấy từ `detect_self_address` trên các câu khách — import từ app của ref không được; chép regex sang `bench/vi_text.py`)
- Create: `bench/scenarios/llm/khach-tu-xung-chi.yaml` (persona tự xưng "chị"; expect `answer_must_include: ["chị"]` ở lượt ≥2, `answer_must_not_include: ["anh chị"]` từ lượt 2)
- Test: `bench/tests/test_judge.py`

- [ ] **Step 1: Test đỏ**: `test_rubric_accepts_anh_chi_before_self_address`, `test_transcript_header_states_self_address`.
- [ ] **Step 2: FAIL**. **Step 3: Implement**. **Step 4: PASS** `bench/tests`.
- [ ] **Step 5: Chấm lại baseline** `bench rescore <run harness-b/align> --rejudge`.
- [ ] **Step 6: Đo**: tag `target/m4`; (a) `--tags edge,llm --k 4` so `target/m3`; (b) `edge --k 4` với `.env.docker` của ref `CONFIRM_VETO_MODE=time_only`; (c) `edge,llm --k 4` với `GUARD_VOICE_RULES=false`; (d) nếu có deployment lớn hơn: `llm --k 2` đổi `AZURE_OPENAI_DEPLOYMENT`. Quyết định mặc định hai cờ theo kết quả; ghi CONTEXT.md; cập nhật `Architech.md` nếu lệch. Commit `docs: record M4 benchmark and flag decisions`.
