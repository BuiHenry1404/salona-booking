# LLM Orchestrator (hybrid) — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Thay toàn bộ tầng agent `app/agents/booking_graph/` bằng kiến trúc hybrid của spec: vỏ LangGraph (code quyết đường đi) + Orchestrator `create_agent` gọi hai specialist như tool, đúng 4 agent, write-fence giữ nguyên, không còn `interrupt()`.

**Architecture:** Gói mới `app/agents/booking/` được dựng **song song** với gói cũ qua M0–M5 (gói cũ vẫn phục vụ Socket.IO, suite 281 test xanh ở mọi commit; gói mới import các helper thuần từ gói cũ). M6 chuyển `socketio_service` sang gói mới, dời helper thuần vào gói mới, xoá gói cũ, viết lại test và tài liệu. Mọi dữ kiện giữa các bước là Pydantic (`schemas.py`, `context.py`); mọi lời gọi tool của Orchestrator đi qua `OrchestratorGuard` (code) trước khi chạy; chỉ `decide.apply_decision` gọi `ProposalService.approve`.

**Tech Stack:** Python 3.12, `langchain 1.4.6` (`create_agent`, `AgentMiddleware`, `awrap_tool_call`, `abefore_model` + `hook_config(can_jump_to=["end"])`, `@tool(return_direct=True)`), `langgraph 1.2.16`, `langchain-openai 1.7.2`, Pydantic v2, Motor/Mongo, pytest (`asyncio_mode = "auto"`, `-m "not llm"` mặc định), `tests/fakes.py:ScriptedChatModel`.

**Spec:** `docs/superpowers/specs/2026-10-29-llm-orchestrator-design.md` — plan trích theo số mục (§4.2, §5.1, §9.3 …). `refactor.md` là đề bài gốc. AGENTS.md *Hard constraints* là ràng buộc cao nhất.

## Milestones (mỗi file một milestone, làm tuần tự; mỗi task một commit)

| # | File | Giao gì | Gói cũ còn chạy? |
|---|---|---|---|
| T0 | [`T0-test-cleanup.md`](T0-test-cleanup.md) | dọn test trùng/ghim hằng/đọc chữ ở phần KHÔNG bị M6 xoá; làm trước M0 | có |
| M0 | [`M0-foundation.md`](M0-foundation.md) | nhánh; settings `orch_*`; `app/core/vi_format.py`; tool `is_open_at`; `ConversationState.goal/queued` + 2 phương thức service; **kiểm `strict=True` Azure một lần (xin phép)** | có |
| M1 | [`M1-context-resolver.md`](M1-context-resolver.md) | `schemas.py`, `context.py` (`TurnContext`, render lite/full, `load_turn_context`), `ledger.py` (`turn_ledger`), `resolver.py` (R0–R5) | có |
| M2 | [`M2-specialists.md`](M2-specialists.md) | Appointment Agent, Shop Info Agent: allowlist theo action, `one_proposal_per_task`, `TaskResult`, `fact_mismatch` | có |
| M3 | [`M3-orchestrator.md`](M3-orchestrator.md) | 5 tool, `OrchestratorState`, `decide_pending` (veto + `ProposalService`), `OrchestratorGuard` (a)–(k), `build_orchestrator`, `run_orchestrate` | có |
| M4 | [`M4-reply.md`](M4-reply.md) | `ReplyBrief` (bảng ánh xạ), Conversation Agent, guard sự thật, `classify_only`, `button_path` | có |
| M5 | [`M5-graph-run-turn.md`](M5-graph-run-turn.md) | `GoalProgress`/`TurnFacts`, trần chi phí LLM, `graph.py`, `events.run_turn`, kịch bản §13 | có (chưa nối) |
| M6 | [`M6-cutover.md`](M6-cutover.md) | dời helper thuần, nối Socket.IO, xoá `booking_graph` + test cũ, write-fence/regex budget mới, AGENTS.md/CONTEXT.md, PR → `henry/develop` | **không** |

## Global Constraints

Mọi task ngầm bao gồm mục này.

- **Nhánh**: `feat/llm-orchestrator` tách từ `fix/review-ee60723` (sau khi commit spec). Merge vào `henry/develop`, không bao giờ commit thẳng `main`. Commit message tiếng Anh, ngắn, kết bằng `Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>`.
- **Không tool ghi.** Chỉ `ProposalService.approve(user, proposal_id, turn_at=TurnContext.asked_at)` ghi lịch; trong `app/agents/` chỉ `orchestrator/decide.py` gọi `approve` (nút bấm cũng đi qua đó). `tests/test_write_fence.py` phải xanh ở mọi commit (M6 cập nhật `ALLOWED`).
- **Ngoài `app/agents/` chỉ chạm đúng danh sách §1.3**: `app/core/vi_format.py` (mới); import trong `app/telegram/handlers.py`, `app/telegram/notify.py`, `app/repositories/user.py`; `app/services/conversation_state.py` (`apply_turn_analysis(TurnFacts)`, import `DIGEST_PROMPT`, thêm `record_goal_progress`, `set_queued`); `app/models/conversation.py` (`goal`, `queued`); `app/core/config.py` + `.env.example` + `.env`; `AGENTS.md`; xoá `scripts/probe_supervisor.py`, `scripts/probe_repeats.py`; sửa import `scripts/test_live_10_turns.py`; `app/services/socketio_service.py` **chỉ đổi 3 dòng import** (M6). Thêm file ngoài danh sách này là sai plan.
- **Regex**: `tests/test_regex_budget.py` quét `app/agents/booking_graph/` tới hết M5 (budget `{"guard.py": 2}`); M1 thêm quét `app/agents/booking/` với budget `{}` (0 regex, không `import re`); M6 đổi ROOT sang `app/agents/booking/` với budget `{"guard.py": 2}`.
- **Không từ điển từ khoá ý định** (phủ định/hoãn/đồng ý/câu hỏi) ở bất kỳ module mới nào. Được phép: `tokens.scan_times/scan_weekdays/scan_day_offsets/has_slash_date/names_a_day/names_a_clock/digits_in`, `reply.other_time/restates_proposal_time`.
- **Schema 5 tool Orchestrator strict-safe** (§12.17): không trường nào có `default`, không `conint`/`minimum`/`maximum`; tuỳ chọn = `| None`. Kiểm khoảng trong middleware (b). Chỉ 5 tool Orchestrator bind `strict=True`; tool specialist (có default) **không** strict.
- **Tag model**: Orchestrator `orchestrate`, Appointment `appointment`, Shop Info `shop_info`, Conversation `respond` (**duy nhất** stream, `streaming=True`), classify `classify`, rewrite `rewrite`. Mọi model khác `streaming=False`. Model của agent (Orchestrator/specialist/Conversation) `max_retries=0` + `model_retry_middleware()`; các lời gọi đơn (classify/rewrite) bọc `asyncio.wait_for(…, 8)`.
- **Giá trị cố định** (từ spec): `orch_max_tool_rounds=8` (chỉ đếm vòng có ≥1 call được chạy), `orch_max_calls_per_round=3`, `no_progress_streak≥2 → force_end`, 1 đề xuất/lượt **và** 1 đề xuất/task, `max_tool_rounds_per_task=4`, `max_retries_per_task=1`, GoalProgress ngưỡng 3, `orch_llm_calls_per_user_per_hour=150`, trần lượt 150 giây, `turn_ledger` TTL 7 ngày, `previous_turns ≤ 2`, `upcoming ≤ 5`, `slots ≤ 3`, `stated ≤ 2`, input ≤ 1000 ký tự, `UNCLEAR_LIMIT=2` (của service), `PROPOSAL_MAX_AGE_MINUTES=10` (của service).
- **Không gọi Azure thật** ngoài hai lần kiểm `strict` (M0 Task 0.6, M3 Task 3.6), mỗi lần **hỏi chủ dự án trước**, không chạy bench.
- **Prompt tiếng Anh**, không chứa câu mẫu tiếng Việt; docs/comment/chuỗi UI tiếng Việt; khách được gọi bằng `TurnContext.customer.address_phrase` (mặc định "anh chị", cố ý — không đoán giới tính từ tên).
- **Tất cả id/iso trong văn bản gửi model lấy từ object** (`render_context`, `render_brief`), không từ chữ khách; chữ khách chỉ nằm trong human message dưới nhãn `Customer said (untrusted):`.
- **Log có cấu trúc theo §11**: tên sự kiện nguyên văn (`resolve_done`, `orch_call`, `task_started/finished`, `pending_decided`, `turn_ended`, `unannounced_reported`, `guard_violation`, `goal_progress`), `extra` luôn có `user_id`, `turn_id`, `thread_id`; không log nguyên văn câu khách ngoài `turn_ledger`.
- Mỗi task: viết test đỏ → chạy → code → chạy xanh → `pytest -q` toàn suite (cần Mongo `docker compose up -d mongo`) → commit.

## Chính sách test (áp cho T0, M1–M6 và mọi test mới)

Bộ test hiện có 1294 hàm, quá nửa kiểm *chữ* (prompt, docstring, khối bối cảnh) thay vì *hành vi*. Từ đây:

1. **Kiểm hành vi, không kiểm chữ.** Không assert substring trong prompt, docstring tool, hay câu render — trừ đúng hai bất biến: *prompt tiếng Anh, không câu mẫu tiếng Việt* (một test parametrize trên mọi prompt) và *mọi id/iso trong văn bản gửi model lấy từ object*.
2. **Một bảng = một hàm test parametrize.** Bảng ánh xạ §5.4, luật (a)–(k), R0–R5, §7.2: mỗi bảng một hàm `@pytest.mark.parametrize`, không một hàm mỗi dòng. Trong M1–M5 chỗ nào liệt kê nhiều tên test cùng khuôn thì hiểu là **các ca của một hàm**.
3. **Không test ghim hằng số/chữ ký** (`X == 4`, "module có đúng N tool", "class implements protocol"); `test_write_fence.py` và `test_regex_budget.py` là ngoại lệ vì chúng là hàng rào.
4. **Không test trùng tầng.** Hành vi của service kiểm ở test service; test tầng agent chỉ kiểm phần agent thêm vào (từ chối, ánh xạ, thứ tự gọi). Khi xoá một test, phải chỉ ra test nào đỏ thay nó.
5. **Xoá test của code đã xoá ngay trong cùng commit.** Không để test cũ sống bằng shim quá một task.

Mục tiêu: sau M6 còn ≈ 850 hàm test (từ 1294), suite chạy nhanh hơn, và không test nào đỏ vì sửa một câu prompt. Thứ tự: `T0-test-cleanup.md` (dọn độc lập, làm **trước** M0) → M0–M6.

## Review Focus

Năm ca spec ngụ ý nhưng dễ vỡ nhất khi chạy thật; mỗi ca có test ghim ở task sở hữu.

1. **Đề xuất hết hạn giữa resolve và `decide_pending(approve)`** → `PendingOutcome(not_approvable, reason="expired")`, không ghi gì, câu đáp nói hết hạn và mời nhắn lại giờ. — M3 Task 3.2 `test_approve_after_expiry_is_not_approvable`; M4 Task 4.1 `test_brief_not_approvable_has_expired_fact_and_no_claims`.
2. **Approve xong, lượt bị huỷ/lỗi trước khi nói** → lượt sau (kể cả "alo") bắt buộc nói "đã đặt {giờ}". — M5 Task 5.6 `test_unannounced_outcome_is_said_next_turn`, `test_conversation_error_after_approve_falls_back_to_code_phrase`.
3. **Model gọi `propose_book` khi đang có pending mà chưa `decide_pending`** → từ chối `must_decide_pending`, đề xuất cũ trong DB vẫn `pending` (không bị `create` supersede). — M3 Task 3.3 `test_propose_without_decide_is_rejected_and_pending_untouched`.
4. **Chữ khách chứa id/iso/lệnh** ("hủy lịch id 65ab…", "ignore the rules") → không có đường nào tới allowlist/action/id; id không thuộc `TurnContext.upcoming` bị (c) từ chối. — M3 Task 3.3 `test_unknown_appointment_id_is_rejected`; M4 Task 4.2 `test_customer_text_sits_only_under_untrusted_label`.
5. **Restart giữa lượt / nút cũ** → không resume; `approving` treo >60s thành `failed` khi đọc; nút có id khác `pending.id` → `stale_button` không chạm DB; lượt mới chạy từ START. — M5 Task 5.6 `test_restart_does_not_resume_and_stale_button_is_safe`.

## Bản đồ file sau M6

```
app/agents/
  llm.py, checkpoint.py                 giữ nguyên
  booking/
    __init__.py                          re-export run_turn, ButtonReply, BUTTON_LABELS, TURN_ERROR_MESSAGE, AgentEvent
    schemas.py        M1  mọi model hợp đồng: TimeHint, AppointmentArgs, ShopArgs, TaskSpec, TaskResult + data, PendingOutcome, Fact, ReplyBrief, TurnFacts, CallRecord, FinishArgs, AskCustomerArgs, DecideArgs
    context.py        M1  TurnContext + sub-model, render_context(lite|full), load_turn_context
    ledger.py         M1  TurnLedger (collection turn_ledger, TTL 7 ngày)
    resolver.py       M1  resolve(ctx, question) → ResolveResult (R0–R5)
    specialists/
      base.py         M2  build_specialist, run_specialist, parse_task_result
      guard.py        M2  OneProposalPerTaskMiddleware, allowlist
      appointment.py  M2  ALLOWLIST, run_task
      shop_info.py    M2  ALLOWLIST, run_task, check_facts
    orchestrator/
      state.py        M3  OrchestratorState
      tools.py        M3  make_orchestrator_tools (5 tool), OrchestratorDeps
      decide.py       M3  veto, apply_decision  ← nơi DUY NHẤT gọi ProposalService.approve
      guard.py        M3  OrchestratorGuard (a)–(k), markers, duplicate, force_end, round limit
      agent.py        M3  build_orchestrator, run_orchestrate → OrchestrateOutcome
    reply_brief.py    M4  build_reply_brief (bảng §5.4), fallback copy
    conversation.py   M4  render_brief, respond (tag respond)
    guard.py          M4  facts_from_brief, check_reply, apply_guard  (+ M6: strip_tool_names/unclaim/has_false_claim/repeats dời vào)
    classify.py       M4  classify_only
    button_path.py    M4  run_button, BUTTON_STALE_REPLY
    progress.py       M5  step_goal, turn_facts
    llm_budget.py     M5  LlmBudget, BudgetMiddleware
    graph.py          M5  GraphState, build_graph
    events.py         M5  run_turn, AgentEvent, translate_langchain_event, pick_thread, pending_proposal_payload
    prompts/          M2–M4  appointment.py, shop_info.py, orchestrator.py, conversation.py, classify.py; M6: digest.py, rewrite.py
    tokens.py, timeparse.py, history.py, tools.py, middleware.py, timecheck.py, phrase.py, rewrite.py, button.py   M6: dời từ booking_graph
app/core/vi_format.py                    M0
tests/
  test_schemas.py test_turn_context.py test_ledger.py test_resolver.py               M1
  test_specialist_appointment.py test_specialist_shop_info.py test_specialist_guard.py  M2
  test_orchestrator_tools.py test_orchestrator_decide.py test_orchestrator_guard.py test_orchestrator_agent.py  M3
  test_reply_brief.py test_conversation_agent.py test_reply_guard.py test_classify.py test_button_path.py  M4
  test_progress.py test_llm_budget.py test_graph_turn.py test_run_turn.py test_scenarios.py  M5
  test_strict_schema_llm.py (marker llm)  M0/M3
```

## Quy ước test giả (dùng từ M2)

- `tests/fakes.py` thêm `tool_call(name, args, id=None) -> AIMessage` và `tool_calls(*pairs) -> AIMessage` (nhiều tool call trong **một** AIMessage — mô phỏng song song).
- Model giả cắm qua `monkeypatch.setattr("<module>.build_chat_model", lambda **kw: scripted)` đúng module gọi: `app.agents.booking.specialists.base`, `app.agents.booking.orchestrator.agent`, `app.agents.booking.conversation`, `app.agents.booking.classify`, `app.agents.booking_graph.rewrite` (M6: `app.agents.booking.rewrite`).
- Specialist giả cho test Orchestrator/graph: `FakeSpecialists` (M3 Task 3.4) — dict `action → TaskResult | Exception`, cắm vào `OrchestratorDeps.run_appointment/run_shop`.
- Mongo test: fixture `test_db` (DB `chatbot_test_db`, xoá sau mỗi test), `memory_checkpointer` autouse.
