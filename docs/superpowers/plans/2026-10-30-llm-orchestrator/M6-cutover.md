# M6 — Chuyển giao: dời helper, nối Socket.IO, xoá gói cũ, tài liệu

Đọc `README.md` trước. Đây là milestone **đổi hành vi**: sau Task 6.2 khách nói chuyện với kiến trúc mới. Mỗi task kết thúc với `pytest -q` xanh toàn suite.

---

### Task 6.1: Dời helper thuần vào `app/agents/booking/`

**Files (git mv rồi sửa import):**

| Từ `app/agents/booking_graph/` | Tới `app/agents/booking/` | Giữ gì |
|---|---|---|
| `tokens.py` | `tokens.py` | nguyên |
| `timeparse.py` | `timeparse.py` | nguyên |
| `history.py` | `history.py` | nguyên |
| `tools.py` | `tools.py` | 5 + 3 tool, `_moment`, `nearest_slots`, `spread_slots`, `QueuedRequest` (tool-side) |
| `middleware/tools.py` + `agents.is_transient/model_retry_middleware/MODEL_RETRIES` | `middleware.py` | `ToolRoundLimitMiddleware`, `ToolFailureMiddleware`, `NoParallelToolsMiddleware`, `FALLBACK_ANSWER`, `TOOL_ERROR_TEXT`, `MAX_TOOL_ROUNDS`, `is_transient`, `model_retry_middleware` |
| `reply.py` (chỉ `other_time`, `mentions_other_time`, `restates_proposal_time`, `spoken_times`, helper riêng của chúng) | `timecheck.py` | bỏ `decide`, `resolve_label`, `REPLY_LABELS` |
| `phrase.py` (chỉ `_TEMPLATES`, `code_phrase`) | `phrase.py` | bỏ `write_phrase` |
| `guard.py` (chỉ `strip_tool_names`, `unclaim`, `has_false_claim`, `false_claim`, `clock_keys`, `repeats`, hằng của chúng, 2 regex) | gộp vào `guard.py` (M4) | bỏ `apply_guard` cũ, `claim_facts`, `check_pronoun/check_clock`, `drop_false_claims` |
| `rewrite.py` | `rewrite.py` | nguyên; `_address_from_block` giữ |
| `button.py` | `button.py` | nguyên |
| `prompts.py:DIGEST_PROMPT` | `prompts/digest.py` | nguyên văn |
| `prompts.py:REWRITE_PROMPT` | `prompts/rewrite.py` | nguyên văn |
| `context.py` (`render_digest`, `render_slots`, `format_14_day_calendar`, `address_phrase`, `display_name`, `live_queue`, `QUEUE_MAX_AGE_MINUTES`) | gộp vào `context.py` (M1) | bỏ `build_context_block`, `declined_line`, `queued_line`, `queue_offer`, `with_line`, `load_context` |

- [ ] **Step 1:** `git mv` các file nguyên; với file gộp: tạo nội dung mới rồi xoá phần cũ ở bước 6.3. Sửa mọi import trong `app/agents/booking/**` từ `app.agents.booking_graph.*` → `app.agents.booking.*` (`grep -rn "booking_graph" app/agents/booking` phải **rỗng**). `tools.py` mới import `format_vi_datetime/format_vi_hhmm` từ `app.core.vi_format`.
- [ ] **Step 2:** Gói cũ còn tồn tại tới Task 6.3: thêm shim một dòng ở các module cũ bị dời (`from app.agents.booking.tokens import *  # noqa`) để test cũ còn chạy qua task này.
- [ ] **Step 3:** `pytest -q` → xanh. **Step 4: Commit** `refactor(agents): move pure helpers into the new package`

---

### Task 6.2: Nối Socket.IO, service, script

**Files:**
- Modify: `app/services/socketio_service.py:7-9` (3 import → `app.agents.booking`), `app/services/conversation_state.py` (`TurnFacts` thay `TurnAnalysis` trong TYPE_CHECKING + chữ ký `apply_turn_analysis(self, user_id, a: TurnFacts, last_bot_text)`; `DIGEST_PROMPT` từ `app.agents.booking.prompts.digest`), `scripts/test_live_10_turns.py` (import `run_turn`, `build_graph`, `pick_thread` từ gói mới), xoá `scripts/probe_supervisor.py`, `scripts/probe_repeats.py`
- Test: `tests/test_socketio_chat.py` (đổi import; thêm `test_complete_proposal_follows_the_pending_proposal_rule`), `tests/test_conversation_slots.py`, `tests/test_conversation_state_service.py` (`TurnAnalysis(...)` → `TurnFacts(...)`)

- [ ] **Step 1:** Sửa import, chữ ký, test. `python -m py_compile scripts/*.py` xanh.
- [ ] **Step 2:** `pytest -q` → xanh (test cũ của gói cũ vẫn chạy nhờ shim).
- [ ] **Step 3: Commit** `feat(chat): serve turns from the orchestrator graph`

---

### Task 6.3: Xoá gói cũ và test cũ; hàng rào mới

**Files:**
- Delete: toàn bộ `app/agents/booking_graph/` (kể cả shim Task 6.1, `middleware/`, `__init__.py`)
- Delete tests: `test_analyzer.py`, `test_confirm.py`, `test_confirm_flow.py`, `test_context_block.py`, `test_graph_wiring.py`, `test_graph_events.py`, `test_mw_confirm.py`, `test_mw_guard.py`, `test_mw_layout.py`, `test_mw_phrase.py`, `test_queue.py`, `test_relatives.py`, `test_reply_classify.py`, `test_subagents.py`, `test_supervisor.py`, `test_run_turn_thread.py`
- Rewrite/trim tests (đổi import sang gói mới, bỏ phần của code đã xoá): `test_tokens.py`, `test_timeparse.py`, `test_timeparse_llm.py` (import `format_14_day_calendar` từ `booking.context`, `APPOINTMENT_PROMPT` thay `BOOKING_PROMPT`), `test_tools.py`, `test_history_window.py` (bỏ test `HistoryWindowMiddleware`/`MessageLayoutMiddleware`), `test_mw_tools.py` → `test_middleware.py`, `test_phrase.py` (chỉ `code_phrase`), `test_reply_veto.py` → `test_timecheck.py` (chỉ `other_time`/`mentions_other_time`/`restates_proposal_time`), `test_guard.py` (chỉ `strip_tool_names`/`unclaim`/`has_false_claim`/`repeats`), `test_rewrite.py` (bỏ `build_context_block`; dựng block bằng chuỗi `"Gọi khách là: cô Lan"`), `test_llm_limits.py` (giữ `is_transient`/retry; các test lượt viết lại trên `booking.events`), `test_prompts.py` (giữ 2 test digest của T0.4; **xoá** toàn bộ 70+ test đọc chữ prompt cũ; thêm đúng **một** hàm parametrize `test_every_prompt_is_english_without_vietnamese_samples` trên 7 prompt mới — chính sách test §1), `test_checkpoint.py`/`conftest.py` không đổi
- Modify: `tests/test_write_fence.py` `ALLOWED` → `{("app/agents/booking/tools.py","proposals","create"), ("app/agents/booking/orchestrator/decide.py","ProposalService(db)","approve"), ("app/agents/booking/events.py","previous","cancel")}` và test tự kiểm trỏ `decide.py`; `tests/test_regex_budget.py` ROOT → `app/agents/booking`, `EXPECTED_BUDGET = {"guard.py": 2}`, bỏ ROOT2 của M1

- [ ] **Step 0:** Trước khi xoá từng file test, chạy `pytest <file> -q --co` để biết số ca mất; test nào kiểm hành vi của code **còn sống** (service, tools) mà chưa có test tương đương thì chuyển sang file mới thay vì xoá (chính sách test §4).
- [ ] **Step 1:** Xoá, sửa; `grep -rn "booking_graph" app tests scripts main.py docs/*.md AGENTS.md CONTEXT.md RUNBOOK.md` → chỉ còn trong `docs/history/` và spec (lịch sử).
- [ ] **Step 2:** `pytest -q` → xanh; ghi số test vào commit message body.
- [ ] **Step 3: Commit** `refactor(agents): remove the supervisor graph and its tests`

---

### Task 6.4: Tài liệu

**Files:**
- Modify: `AGENTS.md` (mục *Layout*: `app/agents/booking/  LangGraph: resolve → orchestrate (create_agent, 5 tool) | button → brief → respond → record; 8 tool đọc/đề xuất, không tool ghi`; mục *Hard constraints* "Agent has NO create/write tool": thay đoạn mô tả `ConfirmMiddleware`/`confirm`/`interrupt()` bằng: đề xuất tạo bởi `propose_appointment`/`cancel_appointment` trong Appointment Agent (1/task, 1/lượt); quyết định ở lượt sau đi qua `decide_pending` với veto giờ bằng code (`timecheck.other_time`) rồi `ProposalService.approve(user, id, turn_at)`; nút bấm đi cùng cổng; `tests/test_write_fence.py` canh `orchestrator/decide.py`. Mục *Socket.IO protocol*: `proposal` khác `None` **khi cuối lượt còn một đề xuất `pending` chưa hết hạn** (không còn nhắc `interrupt()`); *Only stream tokens tagged respond*: liệt kê tag mới (`orchestrate`, `appointment`, `shop_info`, `rewrite` không stream — `classify` bỏ 06/11, Task 4.4b). Thêm vào *Read first* dòng spec mới.)
- Modify: `CONTEXT.md` (đầu file: mục "Kiến trúc hiện tại (30/10)" trỏ spec + plan; các "chốt cứng" bị §0 thay thế gạch ngang với chú "thay bởi spec 2026-10-29 §0"; bẫy liên quan interrupt/supervisor đánh dấu "lịch sử")
- Modify: `RUNBOOK.md` chỉ nếu có lệnh/biến mới cần người vận hành biết (`ORCH_*` — một đoạn ngắn)
- Delete: `docs/superpowers/plans/2026-10-29-review-fixes-ee60723.md` (plan cũ đã xong và kiến trúc nó vá đã bị thay) — **hỏi chủ dự án một câu** trước khi xoá; không đồng ý thì giữ

- [ ] **Step 1:** Sửa 3 file; đọc lại AGENTS.md một lượt với mắt người mới: không còn từ `interrupt`, `supervisor`, `ConfirmMiddleware`, `booking_graph`.
- [ ] **Step 2: Commit** `docs: describe the orchestrator architecture`

---

### Task 6.5: Khép nhánh

- [ ] **Step 1:** `pytest -q` toàn suite xanh; `python -m py_compile $(git ls-files '*.py')` xanh.
- [ ] **Step 2:** Review cả nhánh bằng `superpowers:requesting-code-review` (BASE = commit đầu nhánh, HEAD = hiện tại); sửa Critical/Important.
- [ ] **Step 3:** `superpowers:finishing-a-development-branch` → PR `feat/llm-orchestrator` → `henry/develop`. Mô tả PR: liên kết spec, plan, kết quả hai lần kiểm `strict`, số test, và dòng "Chưa đo Azure thật với khách — rủi ro §14 mục 1 còn nguyên".
