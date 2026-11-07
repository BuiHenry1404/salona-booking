# M6b — Sửa lỗi phát hiện khi chạy 10 lượt chat thật (07/11), trước Task 6.3

Đọc `README.md` trước. Bối cảnh: sau Task 6.2 (Socket.IO đã trỏ gói mới), chủ dự án cho chạy `scripts/test_live_10_turns.py` với Azure thật (07/11 13:51–13:53, DB dev). Kết quả: 9/10 lượt `complete`, 1 lượt `error`; DB cuối đúng (1 lịch 2026-11-08 16:00 `booked`, đề xuất 15:00 `superseded`, đề xuất 2026-11-11 09:00 `pending`). Log đầy đủ: scratchpad `live10.log`; tóm tắt trong ledger SDD (dòng "Live run (07/11 …)").

Bốn phát hiện (L1–L4). Milestone này sửa **L1, L2, L4**; **L3** ghi lại cho sau. Mỗi task kết thúc với `pytest -q` toàn suite xanh. Sau Task 6.2b.3: chạy lại 10 lượt thật (chủ dự án đã duyệt) để xác nhận, rồi mới sang Task 6.3.

**Ràng buộc toàn cục** (như README): không `import re` trong `app/agents/booking/` (ngoài 2 regex đã ở `draft_checks.py`); write fence không đổi; không đoán ý khách bằng từ khoá; chữ khách chỉ dưới nhãn untrusted; log có `user_id/turn_id/thread_id`, không chữ khách; test theo hành vi, không ghim chữ prompt; không gọi Azure trong test (`-m llm` vẫn bị loại).

---

### Task 6.2b.1: L1 — giờ mở cửa dạng `HH:MM` làm guard vỡ cả lượt

**Hiện tượng (lượt 5):** `shop_info_task(get_hours)` → `ShopHoursOut(open.iso="08:00", close.iso="19:00")` (prompt Shop Info bảo model chép `open.hhmm` vào `iso`; `shop_info._hhmm` đã chấp nhận cả hai dạng). `reply_brief._facts_for_shop_hours` đặt chuỗi đó vào `Fact.iso`; `guard.facts_from_brief` (guard.py:78-79) gọi `datetime.fromisoformat(fact.iso)` → `ValueError: Invalid isoformat string: '08:00'` → node `respond` ném → cả lượt thành `error` ("Máy đang bận chút xíu…"). Test đơn vị (`tests/test_reply_brief.py:150`, `tests/test_scenarios.py:559/713/751`) đều dùng ISO đầy đủ nên không bắt được.

**Files:**
- Modify: `app/agents/booking/reply_brief.py` (`_facts_for_shop_hours`): giờ mở/đóng cửa KHÔNG phải mốc thời gian → `Fact(key="open", spoken=…, text=data.open.iso)` / `close` tương tự; **không** đặt vào `iso`. (`_facts_for_shop_open`/`ShopOpenOut.at` là mốc thật, giữ `iso`.)
- Modify: `app/agents/booking/guard.py` (`facts_from_brief`): phòng thủ — `iso` không parse được → bỏ qua fact đó và log `guard_fact_iso_invalid` (extra: key, không chữ khách); guard là lưới kiểm, **không được** làm hỏng lượt.
- Modify: `tests/test_reply_brief.py`, `tests/test_reply_guard.py`, `tests/test_scenarios.py`.

- [ ] **Step 1: Test đỏ**
  - `tests/test_reply_guard.py::test_facts_from_brief_skips_a_non_iso_clock_and_logs` — Fact(key="open", iso="08:00") → không ném, `clocks` không chứa gì từ nó, có log `guard_fact_iso_invalid`.
  - `tests/test_reply_brief.py::test_shop_hours_facts_carry_hhmm_as_text_not_iso` — `ShopHoursOut(open.iso="08:00", …)` → facts open/close có `text="08:00"`, `iso is None`; `must_say == ["8 giờ sáng","7 giờ tối"]` giữ nguyên.
  - `tests/test_scenarios.py::test_shop_hours_turn_with_hhmm_iso_completes` — tái hiện lượt 5: specialist (thật hoặc fake) trả `open.iso="08:00"`, Conversation nói hai giờ → `complete`, không `error`, không log `respond_failed`/`agent_turn_failed`.
- [ ] **Step 2–3:** FAIL → sửa → PASS; full suite xanh. **Step 4: Commit** `fix(reply): keep shop hours out of the clock facts`

---

### Task 6.2b.2: L2 — Orchestrator trả lời bằng chữ thay vì `finish` → lượt thành `limit`

**Hiện tượng (lượt 3, 4, 9; lượt 10 một phần):** sau `appointment_task(propose_book)` (allowed, marker `proposal_created`), model trả **AIMessage không có tool call**. `create_agent` kết thúc; guard chỉ ghi `ended_by` khi có `finish`/`ask_customer`, nên `ended_by` rỗng → `_outcome_from_state` (orchestrator/agent.py ~161) mặc định `"limit"` (ruling Task 5.3 cho đường `ToolRoundLimit`). `reply_brief.reply_kind_for` vẫn ra `proposal_made` (có `ProposalCreated`) nhưng `turn_ended` ghi `limit` với `rounds_counted` 1–3 — và kết quả quan sát: câu trả lời chỉ còn các dòng `must_say` trơ trọi (lượt 3: 4 dòng giờ; lượt 4/9: một dòng giờ), không có câu "chị đồng ý không ạ?". Log lượt 3 không có `guard_violation`, nghĩa là Conversation thật sự trả về chỉ các dòng đó và guard thấy đủ `must_say`.

**Nguyên nhân gốc:** (a) kết thúc bằng chữ thường không được code nhận diện là một cách kết thúc hợp lệ; (b) prompt Conversation chỉ bắt "Say exactly" mà không bắt phải có câu hỏi xác nhận cho `proposal_made`.

**Files:**
- Modify: `app/agents/booking/orchestrator/guard.py` (`abefore_model`): nếu AIMessage cuối **không có tool call** (model đã dừng bằng chữ) VÀ lượt đã có ≥1 CallRecord `allowed` → `{"jump_to":"end","ended_by":"finish","finish_args": FinishArgs(kind_hint="answer", service=None, stated=[]).model_dump(mode="json")}` + log `orch_text_end` (extra ids, không chữ model). Chưa có call hợp lệ nào → giữ như cũ (→ `error`, §10). Lưu ý: `abefore_model` chạy TRƯỚC lần gọi model kế; khi model dừng bằng chữ, LangGraph đi thẳng END, không qua `abefore_model` nữa — nên phải kiểm tra `create_agent` thực tế (đọc `factory.py` trong `.venv`) xem hook nào chạy sau AIMessage không tool call (`after_model`), và đặt logic ở đó (`aafter_model` với `can_jump_to=["end"]`) nếu cần. Ghi rõ trong report hook nào được dùng và vì sao.
- Modify: `app/agents/booking/orchestrator/agent.py` (`_outcome_from_state`): `ended_by` rỗng → vẫn `"limit"` (đường `ToolRoundLimit`), không đổi; chỉ guard mới tạo `finish`.
- Modify: `app/agents/booking/prompts/orchestrator.py`: một câu tiếng Anh: "Always end the turn by calling finish or ask_customer; never answer with plain text."
- Modify: `app/agents/booking/prompts/conversation.py`: một câu: when Kind is `proposal_made`, the reply must end by asking the customer to confirm (one short question); when Kind is `clarify`/`ask`, ask exactly the Ask line. (Không ghim câu mẫu tiếng Việt.)
- Modify: `app/agents/booking/reply_brief.py`: `proposal_made` → `must_say` thêm một mục **ngắn** do code dựng? — KHÔNG (không ghim câu mẫu vào must_say; `PROPOSAL_FALLBACK` đã có sẵn khi guard phải rơi về code). Thay vào đó `guard.check_reply` thêm vi phạm `missing_question` cho kind `proposal_made`/`clarify`: draft không kết thúc bằng dấu `?` (sau strip) → `rewrite` một lần → vẫn thiếu → `fallback` (`PROPOSAL_FALLBACK`/`brief.fallback`). Không regex: dùng `rstrip().endswith("?")`.
- Tests: `tests/test_orchestrator_guard.py`, `tests/test_orchestrator_agent.py`, `tests/test_reply_guard.py`, `tests/test_scenarios.py`.

- [ ] **Step 1: Test đỏ**
  - `test_orchestrator_agent.py::test_plain_text_after_an_allowed_call_ends_as_finish_answer` — scripted: `appointment_task(propose_book)` (FakeSpecialists → ProposalCreated) rồi `AIMessage("Dạ em giữ chỗ rồi")` không tool call → `OrchestrateOutcome.ended_by == "finish"`, `finish.kind_hint == "answer"`, `rounds_executed == 1`, log `orch_text_end`.
  - `test_orchestrator_agent.py::test_plain_text_with_no_allowed_call_is_still_error` — model trả chữ ngay vòng 1 → `ended_by == "error"`.
  - `test_reply_guard.py::test_proposal_made_reply_without_a_question_is_rewritten_then_falls_back` — draft "Chủ Nhật 8/11, 4 giờ chiều" (đủ must_say, không `?`) → `missing_question` → rewrite (giả) vẫn không `?` → answer == `PROPOSAL_FALLBACK` đã format; draft kết thúc "…chị đồng ý không ạ?" → không vi phạm.
  - `test_scenarios.py::test_propose_then_plain_text_reply_asks_for_confirmation` — tái hiện lượt 3: `find_slots` + `propose_book` rồi chữ thường; Conversation trả đúng 4 dòng giờ → `complete.answer` kết thúc bằng `?` và chứa giờ đang giữ; `turn_ended.ended_by == "finish"`, `reply_kind == "proposal_made"`; `complete.proposal` khác None.
- [ ] **Step 2–3:** FAIL → sửa → PASS; full suite xanh. **Step 4: Commit** `fix(orchestrator): treat a plain-text stop after real work as finish`

---

### Task 6.2b.3: L4 — lượt có đề xuất mới không bắt buộc đọc hết mọi giờ trống

**Hiện tượng (lượt 3):** cùng lượt có `find_slots` (3 slot) và `propose_book` (1 held) → `must_say` = 4 dòng giờ → câu trả lời là danh sách 4 mốc giờ, trong đó giờ đang giữ xuất hiện hai lần.

**Files:**
- Modify: `app/agents/booking/reply_brief.py` (`build_reply_brief`): khi `kind == "proposal_made"`, các `must_say` đến từ `FreeSlots` (slot) **không** còn bắt buộc — vẫn là `Fact` (model được phép nhắc), `must_say` chỉ giữ `held_at` (và R0 nếu có). Các kind khác (chỉ `find_slots`, không đề xuất) giữ nguyên bảng §5.4. Khử trùng lặp `must_say` (giữ thứ tự).
- Modify: `docs/superpowers/specs/2026-10-29-llm-orchestrator-design.md` §5.4: ghi chú ở hàng `FreeSlots`: "khi cùng lượt có `ProposalCreated` → slot chỉ là Fact, không must_say (M6b, 07/11)".
- Tests: `tests/test_reply_brief.py`.

- [ ] **Step 1: Test đỏ** — `test_proposal_made_does_not_force_every_free_slot_into_must_say` (find_slots 3 slot + propose → `must_say == [held.spoken]`, facts vẫn có 3 slot); `test_find_slots_alone_keeps_slots_in_must_say` (không đề xuất → 3 slot trong must_say như §5.4); `test_must_say_is_deduplicated_in_order`.
- [ ] **Step 2–3:** FAIL → sửa → PASS; full suite xanh. **Step 4: Commit** `fix(reply): don't force every free slot into must_say once a proposal exists`

---

### Task 6.2b.4: Chạy lại 10 lượt thật và ghi kết quả

- [ ] **Step 1:** Controller (không subagent) chạy `PYTHONPATH=. .venv/bin/python scripts/test_live_10_turns.py` (xoá DB dev — chủ dự án đã duyệt), lưu log vào scratchpad.
- [ ] **Step 2:** Đối chiếu 10 lượt: mong đợi 10/10 `complete`; lượt 3/4/9 có câu hỏi xác nhận và `ended_by=finish`; lượt 5 trả lời giờ mở cửa; không `guard_violation` kiểu `fallback` ở lượt 3/4/9. Ghi bảng kết quả vào ledger SDD và báo chủ dự án. Lượt 7 (L3) vẫn có thể lặp câu — chấp nhận, đã ghi.
- [ ] **Step 3:** Nếu còn lỗi mới → quay lại brainstorm với chủ dự án trước khi sang Task 6.3.

---

### Ghi lại, KHÔNG sửa trong M6b

- **L3 (lượt 7):** "ừ đặt cho chị đi em" sau khi đã duyệt → `finish(social/answer)` → Conversation lặp y câu lượt 6 ("em đặt lịch … rồi ạ"); guard bắt `claim` (kind không cho `booked`) nhưng `fixed_by: none` vì kind không thuộc `_FALLBACK_KINDS` → giữ draft. Câu đúng sự thật, chỉ lặp. Sửa đúng cần: cho phép nhắc lại một lịch có thật trong `ctx.upcoming` (claim hợp lệ) + `repeat` dẫn tới rewrite. Để sau M6 (cùng đợt thí nghiệm "phẳng").
- **L5 (lượt 5):** khách hỏi cả địa chỉ — không có tool/fact địa chỉ; bot chỉ trả lời giờ (sau L1) là chấp nhận được; địa chỉ tiệm có thể thêm vào `ShopHoursOut`/context sau nếu chủ dự án muốn.
