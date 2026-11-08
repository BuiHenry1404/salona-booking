# Thiết kế: LLM Orchestrator và 4 agent cho Salona Booking (hybrid)

**Ngày**: 2026-10-29, sửa lớn 2026-10-30 (chốt hybrid; sửa theo review độc lập cùng ngày)
**Trạng thái**: Bản nháp chờ chủ dự án duyệt
**Nguồn ý định**: `refactor.md` (gốc repo). Tài liệu này là lời giải cho đề bài đó, đối chiếu với code thật ở commit `6d348b7` và thư viện đã cài: `langchain 1.4.6`, `langchain-core 1.6.12`, `langchain-openai 1.7.2`, `langgraph 1.2.16`.
**Phạm vi**: viết lại toàn bộ `app/agents/` (trừ hai module hạ tầng `llm.py`, `checkpoint.py`). Giữ nguyên `app/services/`, `app/api/`, `app/models/`, `app/repositories/`, giao thức Socket.IO và frontend, **trừ** danh sách đóng ở §1.3.
**Kiến trúc**: **hybrid** = vỏ LangGraph custom workflow (code quyết đường đi) + Orchestrator là `create_agent` gọi specialist như tool (mẫu *subagents* của LangChain), chặn bằng middleware. Quyết định chủ dự án 30/10, xem §12.16.
**Tài liệu kèm**: `docs/superpowers/specs/2026-10-29-llm-orchestrator-architecture.md` — bản vẽ kiến trúc của chủ dự án, chi tiết triển khai hơn trên cùng thiết kế hybrid này.

---

## 0. Những quyết định cũ bị thay thế

Chủ dự án (29/10) quyết: `refactor.md` là ý định hiện tại, không ràng buộc bởi các "chốt cứng" trong `CONTEXT.md` nữa.

| Quyết định cũ (CONTEXT.md) | Số phận |
|---|---|
| "Mỗi lượt tối đa một lượt LLM trước agent" | **Bỏ.** Orchestrator chạy vòng tool, mỗi vòng một lượt model. Độ trễ không còn là mục tiêu (refactor.md §16). |
| Supervisor = code + `TurnAnalysis`; 4 luật định tuyến cứng | **Bỏ.** Orchestrator (LLM) quyết gọi specialist nào; middleware (code) kiểm từng lời gọi. |
| Node `confirm` dùng `interrupt()`; resume bằng `Command(resume)` | **Bỏ.** Mọi lượt chạy từ START; đề xuất đang chờ đọc từ DB `proposals` (§6). |
| Lưới veto **consent** bằng từ điển từ khoá (`_NEG_*`, `is_go_ahead`, `relatives.py`) | **Bỏ.** Hiểu ý là việc của Orchestrator. **Giữ** bộ quét mốc giờ có cấu trúc (`tokens.scan_*`, `reply.other_time`) vì đó là dữ kiện, không phải từ điển ý định. |
| Khối bối cảnh text có tag `[id: …]`, `[declined: …]`, `[queued …]` | **Bỏ.** Dữ kiện đi bằng `TurnContext` có kiểu (§4.2). |
| `GuardMiddleware` với luật giọng (`repeat`, `pronoun`, `clock`) | **Thu hẹp.** Guard chỉ còn kiểm *sự thật* (§9.8). |
| Hướng "4 bước, `BOOKING_ENGINE=agent\|rules`" (ĐANG BÀN 26/10) | **Bỏ**, thay bằng tài liệu này. |
| AGENTS.md: 1 worker, cấu hình từ `.env`, logic ở `services/`, **không tool ghi** | **Giữ.** |

---

## 1. Kiến trúc hiện tại (đã kiểm trên code)

```
START → supervisor(code: analyzer LLM + 4 luật) → booking | shop | social (create_agent + 7 middleware)
      → record → confirm (interrupt khi có proposal_id) → END
resume → confirm (code veto → analyzer → approve) → phrase/booking/supervisor
```

- 3 agent ngang hàng, mỗi lượt chạy đúng một. Điều phối là code rải ở `supervisor.py`, `confirm.py`, `reply.py`, `relatives.py`, `guard.py`, `events.py`.
- Write-fence: tool `propose_appointment`/`cancel_appointment` tạo document `proposals`; chỉ `ProposalService.approve` ghi lịch (claim pending→approving lọc chủ, hạn, `created_at < turn_at`; kiểm snapshot đích; idempotent với `approved`; `approving` treo >60s → `failed` khi đọc).
- `ProposalService.create` **luôn** `supersede_pending(user_id)` trước khi tạo đề xuất mới (`proposal.py:81`). Hệ quả cho thiết kế: xem §9.3 (h) và (j).
- `parse_time` hiện **tự neo** vào `proposals.latest` (pending hoặc vừa đóng ≤10 phút) và `state.tool_day` khi không có `anchor` (`tools.py:245–270`).
- Services giữ nguyên: `ProposalService`, `AppointmentService`, `ConversationService`, `ConversationStateService`, `ShopService`, `RateLimitService`, `MessageDedupe`.

### 1.1 Vấn đề (phân loại ~28 lỗi đã ghi nhận)

| Nhóm | Số lỗi | Bản chất |
|---|---|---|
| A. Hiểu sai câu khách | ~10 | nhãn consent/route sai ở edge case |
| B. Hiểu đúng, agent tự lái sai | ~14 | agent booking tự quyết gọi tool nào, mấy lần, nói gì |
| C. Mất ngữ cảnh giữa lượt | ~4 | tool message không quay lại thread; code "nói lại" bằng text |

Kiến trúc mới nhắm vào B và C: tách hiểu/điều phối (Orchestrator) khỏi thực thi (specialist) khỏi nói (Conversation Agent); mọi dữ kiện giữa các bước là **cấu trúc có kiểu**.

### 1.2 Hook middleware đã có trong repo (mẫu cho §5, §9)

| Cần | Hook `create_agent` | Mẫu sẵn có |
|---|---|---|
| Từ chối một tool call, trả lỗi cho model thay vì chạy | `awrap_tool_call` trả `ToolMessage(status="error", content=JSON)` không gọi `handler` | `ToolFailureMiddleware` |
| Ghi state agent từ kết quả tool | `awrap_tool_call` trả `Command(update={...})` | `ConfirmMiddleware` |
| Ép kết thúc vòng từ code | `abefore_model` với `@hook_config(can_jump_to=["end"])` trả `{"jump_to": "end"}` | `ToolRoundLimitMiddleware` |
| Tool kết thúc vòng khi được gọi | `@tool(return_direct=True)`; cạnh tools→model thoát END khi **mọi** tool vừa chạy là `return_direct` (`factory.py:2086`) | chưa có |

**Không có**: `Command(goto=...)` từ `wrap_*` (`factory.py:265`), và state giữa các tool call **cùng vòng** là một snapshot chung (`tool_node.py:841–858`, `asyncio.gather`) — `Command(update)` của call này không hiện với call anh em.

### 1.3 Danh sách đóng các file **ngoài** `app/agents/` được chạm

| File | Thay đổi | Vì sao |
|---|---|---|
| `app/core/vi_format.py` (mới) | nhận `format_vi_datetime`, `format_vi_hhmm`, `_clock_phrase`, `_WEEKDAYS`, `derive_address` từ `agents/context.py` | `telegram/handlers.py`, `telegram/notify.py`, `repositories/user.py` đang import từ agents |
| `app/telegram/handlers.py`, `app/telegram/notify.py`, `app/repositories/user.py` | đổi dòng import | như trên |
| `app/services/conversation_state.py` | `apply_turn_analysis` nhận `TurnFacts` (3 trường cũ: `service`, `stated`, `answers_question`); import `DIGEST_PROMPT` từ `agents/prompts/digest.py`; thêm `record_goal_progress`, `set_queued` | service đọc kiểu của agent cũ; hai phương thức mới là ghi trường code-only |
| `app/models/conversation.py` | `ConversationState` thêm `goal: GoalProgress \| None`, `queued: list[QueuedRequest]` (code-only, `_compact` không chạm, như `tool_day`) | hàng chờ và tiến độ rời khỏi checkpoint |
| `app/core/config.py`, `.env.example`, `.env` | `orch_*` settings (§7) | quy ước repo |
| `AGENTS.md` | mô tả `complete.proposal` mới (§6) | giao thức giữ tên, đổi định nghĩa |
| `scripts/probe_supervisor.py`, `scripts/probe_repeats.py` | **xoá** | thuộc kiến trúc cũ |
| `scripts/test_live_10_turns.py` | sửa import | chủ dự án giữ script dev |
| `docker-compose.yml` | `mongo` chạy `--replSet rs0`, healthcheck tự `rs.initiate` (thành viên `mongo:27017`); `app`/`mongo-express` chờ `service_healthy` | Task 3.6 — transaction cần replica set |
| `.env.example`, `.env`, `.env.docker`, `tests/conftest.py` | `MONGO_URI`/`TEST_MONGO_URI` thêm `?directConnection=true` (chỉ query string) | máy host không phân giải `mongo` |
| `app/services/proposal.py` | `approve`/`reject`/`supersede`/`mark_unclear`/`get` nhận `session=None`; `approve` có session thì không shield, ném `ApprovalFailed` khi lỗi nghiệp vụ lúc ghi; thêm `mark_failed` | Task 3.6 |
| `app/services/appointment.py`, `app/services/rate_limit.py`, `app/repositories/{base,appointment,proposal}.py` | tham số tuỳ chọn `session=None` cho các lệnh đọc/ghi trong đường approve; có session thì báo tin chủ tiệm hoãn tới sau commit (`send_notices`) | Task 3.6 |
| `RUNBOOK.md` | đoạn replica set một node + chuỗi kết nối | Task 3.6 |

Không chạm: REST, Socket.IO handler, frontend, `ShopService`. **Quyết định chủ
dự án 05/11 (Task 3.6): transaction thật** — `ProposalService`/`AppointmentService`
(và repository/rate limit chúng gọi) được nới đúng như các dòng trên: chỉ thêm
`session=None`; không session thì hành vi cũ giữ nguyên cho REST/Telegram/nút
bấm cũ. `decide.apply_decision` chạy đổi đề xuất + `pending_outcome` trong
`with_transaction` (bỏ `asyncio.shield`).

---

## 2. Mục tiêu, phi mục tiêu, phản mục tiêu

**Mục tiêu**
1. Đúng 4 agent. Trách nhiệm không chồng.
2. Mọi quyết định "gọi ai, làm gì" ở Orchestrator; **mọi lời gọi qua middleware code kiểm trước khi chạy**.
3. Mọi dữ kiện giữa các bước là Pydantic model.
4. Write-fence giữ nguyên.
5. Không vòng lặp vô hạn; tách tiến độ khỏi rate limit.
6. Phục hồi an toàn sau restart; không lặp side effect; **mọi lịch đã ghi đều được nói với khách**, kể cả khi lượt ghi bị huỷ giữa chừng.
7. Test được không cần Azure, trừ **một** lần kiểm schema `strict` (§13).

**Phi mục tiêu**: tối ưu độ trễ; đổi stack/services/REST/Socket.IO/frontend ngoài §1.3; catalog dịch vụ; agent thứ năm; benchmark Azure.

**Phản mục tiêu**: ghi/huỷ/dời lịch khi khách chưa đồng ý ở lượt sau; nói "đã đặt" khi DB chưa ghi; **lịch đã ghi mà khách không được báo**; phạt khách chào hỏi/hoãn; JSON/tool call lọt ra màn hình.

---

## 3. Bốn agent và ranh giới

| # | Agent | Loại | Tag | Đầu vào | Đầu ra | Cấm |
|---|---|---|---|---|---|---|
| 1 | **Orchestrator** | LLM `create_agent`; tool = 2 specialist + 3 tool điều phối (§5.1); không tool nghiệp vụ | `orchestrate` | `TurnContext full` + 2 lượt cuối + câu khách | chuỗi tool call; kết thúc bằng `finish`/`ask_customer` hoặc bị code ép dừng | nói với khách; gọi tool nghiệp vụ; ghi DB; quyết consent ngoài `decide_pending` |
| 2 | **Appointment** | LLM `create_agent` + 5 tool | `appointment` | một `TaskSpec` + `TurnContext full` + câu khách | `TaskResult` | ghi lịch; action ngoài giao; >1 đề xuất/task; nói với khách |
| 3 | **Shop Info** | LLM `create_agent` + 3 tool đọc | `shop_info` | một `TaskSpec` + `TurnContext full` + câu khách | `TaskResult` | bịa; nói với khách |
| 4 | **Conversation** | LLM, không tool | `respond` (duy nhất stream) | `ReplyBrief` + cửa sổ lịch sử + câu khách | câu tiếng Việt | bịa số liệu; quyết consent; đặt/đổi lịch |

Control tool (`decide_pending`, `ask_customer`, `finish`), resolver, brief, guard, ledger là **code**, không phải agent.

---

## 4. State Resolver và định tuyến (code)

### 4.1 Vị trí
Node đầu tiên, mọi lượt, không LLM.

### 4.2 `TurnContext`

```python
class Moment(BaseModel): iso: str; spoken: str

class PendingProposal(BaseModel):
    id: str; kind: Literal["book","move","cancel"]; start_at: datetime; spoken: str
    note: str | None; expires_at: datetime; unclear_count: int
    target: AppointmentRef | None

class AppointmentRef(BaseModel): id: str; start_at: datetime; spoken: str; note: str | None
class OpenQuestion(BaseModel): field: QuestionField; asked_at: datetime; times: int
class GoalProgress(BaseModel): goal_id: str | None; turns_without_progress: int; last_marker: str | None
class QueuedRequest(BaseModel): queue_id: str; start_at: datetime; spoken: str; note: str | None; queued_at: datetime

class PreviousCall(BaseModel):            # CÓ KIỂU, rút từ ledger §6
    action: str; status: Literal["ok","needs_info","failed","rejected"]
    error_code: str | None
    offered: list[Moment] = []            # giờ đã chào (FreeSlots.slots / nearest)
    resolved: Moment | None = None        # ResolvedTime.start_at
    proposal: PendingProposal | None = None   # ProposalCreated
class PreviousTurn(BaseModel): route: str; calls: list[PreviousCall]; reply_kind: str

class UnannouncedOutcome(BaseModel):      # C1: lượt trước ghi/huỷ xong mà khách chưa được báo
    proposal_id: str; outcome: Literal["approved","failed"]; kind: str; spoken: str; error: str | None

class TurnContext(BaseModel):
    turn_id: str; asked_at: datetime        # mốc câu khách; = `turn_at` cho ProposalService.approve
    now_local: datetime; today: date; calendar_14d: list[tuple[date, str]]
    customer: CustomerRef                  # id, display_name, address_phrase, staff_note
    shop_status: ShopStatusView; shop_hours: ShopHours
    upcoming: list[AppointmentRef]          # ≤5
    recent_visits: list[date]
    pending: PendingProposal | None         # CHỐT lúc resolve; decide_pending luôn nhắm vào id này
    recently_declined: PendingProposal | None
    unannounced: UnannouncedOutcome | None  # §6
    queued_requests: list[QueuedRequest]
    open_question: OpenQuestion | None
    answers_open_question_hint: bool
    last_bot_text: str
    previous_turns: list[PreviousTurn]      # ≤2 lượt trước trong ngày
    goal: GoalProgress
    slots: ConversationSlots | None; digest_text: str
```

Render thành văn bản có nhãn; **mọi id/iso lấy từ object**. Hai mức: `lite` (now, address, pending kind+giờ, open_question, số queued, goal — dựng cho `classify_only`, Task 4.4b (06/11) bỏ `classify_only` nên mức này hiện không còn hàm nào gọi, giữ nguyên không xoá) và `full` (Orchestrator vòng tool, specialist). Conversation chỉ nhận `ReplyBrief`.

### 4.3 Luật định tuyến

| # | Điều kiện | Đi đâu | LLM? |
|---|---|---|---|
| R0 | `unannounced` khác None | đi tiếp theo R1–R5 **nhưng** `ReplyBrief.must_say` bắt buộc có câu chốt/câu lỗi của lượt trước (§5.4) | theo nhánh |
| R1 | `ButtonReply` | `button_path` (code): id khớp `pending.id` → `approve`/`reject`; kết quả `approved`/`rejected`/`failed` → Conversation; không khớp/không pending → `stale_button` câu mẫu | `approved`/`rejected`/`failed` có (một lần mỗi đề xuất); `stale` không |
| R2 | `pending` còn hạn, input chữ | `orchestrate`; middleware (h): **mọi** `propose_*`, `ask_customer`, `finish` bị từ chối cho tới khi `decide_pending` đã gọi | có |
| R3 | `open_question` và câu ngắn (≤6 từ) chỉ chứa giờ/ngày/buổi (`tokens.scan_*`) | `orchestrate` với `answers_open_question_hint=True` | có |
| R5 | còn lại (gộp cả "lượt sạch" không pending/open_question/queued/unannounced, câu không có số/giờ/ngày/thứ — Task 4.4b (06/11) bỏ `classify_only`/R4 riêng) | `orchestrate`; Orchestrator tự nhận biết xã giao/không hỗ trợ bằng `finish(kind_hint=social|unsupported|answer)` | có |

Resolver không gán consent, không đoán ý, không từ khoá, không ghi DB (trừ chuẩn hoá expired sẵn có của `ProposalService.get`).

---

## 5. Orchestrator: vòng tool, hợp đồng, vòng đời

### 5.1 Bộ tool (5, code áp)

| Tool | Tham số | Trả | Thực thi | Kết thúc vòng? |
|---|---|---|---|---|
| `appointment_task(action, args)` | `AppointmentAction`, `AppointmentArgs` | `TaskResult` JSON | code gọi Appointment Agent | không |
| `shop_info_task(action, args)` | `ShopAction`, `ShopArgs` | `TaskResult` | code gọi Shop Info Agent | không |
| `decide_pending(label, aside, reason)` | §5.5 | `PendingOutcome` | **code**: veto §9.3 (i) → `ProposalService` | `unclear` → có (code ép, §5.6) |
| `ask_customer(field, hint)` | `field: QuestionField` | — | code ghi `open_question` | **có** (`return_direct=True`) |
| `finish(kind_hint, service, stated)` | `kind_hint: Literal["answer","unsupported","social"]`, `service: str\|None`, `stated: list[str]` | — | code | **có** (`return_direct=True`) |

Schema tham số (luật strict, §12.17): **không trường nào có default**; tuỳ chọn dùng `| None`; không `conint`/`minimum`/`maximum` — kiểm khoảng trong middleware (b).

```python
AppointmentAction = Literal["resolve_time","find_slots","propose_book","propose_move","propose_cancel","list_appointments"]
ShopAction = Literal["get_hours","get_status","is_open_at"]
QuestionField = Literal["day","time","part_of_day","service","which_appointment","week","confirm"]

class TimeHint(BaseModel):
    day: date | None; weekday: int | None; day_offset: int | None
    hour: int | None; minute: int | None
    part_of_day: Literal["sang","trua","chieu","toi"] | None
    ambiguous_week: bool | None; is_now: bool | None
    anchor: Literal["pending","declined","appointment","queued"] | None
    queue_id: str | None                   # bắt buộc khi anchor="queued"

class ExtraRequest(BaseModel): time: TimeHint; service: str | None; for_whom: str | None

class AppointmentArgs(BaseModel):
    time: TimeHint | None; service: str | None
    appointment_id: str | None             # ∈ TurnContext.upcoming — Literal động (§12.17) + kiểm middleware
    for_whom: str | None
    also: list[ExtraRequest] | None        # chỉ propose_book

class ShopArgs(BaseModel): time: TimeHint | None

class TaskSpec(BaseModel):                 # code dựng từ (tool, args); task_id code sinh "t<round>.<idx>"
    task_id: str; agent: Literal["appointment","shop_info"]
    action: AppointmentAction | ShopAction; args: AppointmentArgs | ShopArgs

class TaskResult(BaseModel):
    task_id: str; agent: str; action: str
    status: Literal["ok","needs_info","failed"]
    data: ResolvedTime | FreeSlots | ProposalCreated | AppointmentList | ShopHoursOut | ShopStatusOut | ShopOpenOut | None
    missing: list[Literal["ngày nào","giờ cụ thể","sáng hay chiều","tuần này hay tuần sau","lịch nào"]]
    error_code: str | None   # slot_unavailable, outside_hours, closed_day, time_passed, already_yours, appointment_not_found,
                             # tool_timeout, schema_invalid, fact_mismatch, one_proposal_per_task, dependency_failed
    retryable: bool; attempts: int

class PendingOutcome(BaseModel):
    outcome: Literal["approved","rejected","superseded","clarify","kept","failed","not_approvable"]
    proposal: PendingProposal; appointment: AppointmentRef | None
    error: str | None; reason: str | None   # failed: lý do nghiệp vụ; not_approvable: expired/approving/...
    nearest: list[Moment]                   # failed vì SlotTaken: giờ gần nhất, code tra `find_free_slots`
```

Neo thời gian: `TimeHint.anchor` do Orchestrator điền **thắng** neo ngầm của `parse_time`; chỉ khi `anchor=None` tool mới tự neo như hiện nay. Ghi rõ trong docstring tool.

### 5.2 State Orchestrator

```python
class OrchestratorState(AgentState):
    turn_context: TurnContext
    calls: list[CallRecord]        # spec chuẩn hoá, kết quả, mốc, từ chối + mã
    proposals_created: int
    pending_decided: bool
    pending_outcome: PendingOutcome | None
    progress_markers: list[str]
    no_progress_streak: int
    force_end: bool                # §5.6: before_model đọc, nhảy END
    ended_by: Literal["finish","ask_customer","forced","limit","error"] | None
```

Middleware ghi state bằng `Command(update=...)` **sau** mỗi call; vì các call cùng vòng chia sẻ một snapshot, mọi luật cần "nhìn anh em" đọc `tool_calls` của AIMessage cuối trong `request.state["messages"]`, không đọc bộ đếm (§9.3 (e),(g)).

### 5.3 Vòng đời một lượt

```
START → resolve ──R1──→ button_path ─────────────────────────────┐
          │ R2/R3/R5                                               ↓
          └──→ orchestrate (create_agent + OrchestratorGuard) → brief (code) → respond (Conversation) → guard → record → END
```

(Task 4.4b, 06/11: bỏ nhánh `classify_only` riêng — lượt "sạch" trước đây đi
R4/`classify_only` nay đi R5/`orchestrate` như mọi lượt khác; Orchestrator tự
gọi `finish(kind_hint=social|unsupported|answer)`.)

- `orchestrate` dừng theo một trong bốn đường: (1) `finish`/`ask_customer` (`return_direct`); (2) `force_end` → `before_model` nhảy END; (3) `ToolRoundLimit`; (4) model lỗi vòng đầu → `ended_by=error`. Nếu model phát `finish` **song song** với tool khác, `all(return_direct)` sai → quay về model; luật (g) đã từ chối `finish` trong trường hợp đó nên vòng sau model gọi lại một mình.
- **Ghi ledger ngay trong vòng** (C1): `decide_pending` ghi `pending_outcome` vào `turn_ledger` **trước khi trả về** cho model; `appointment_task` ghi `ProposalCreated` ngay khi có. Không chờ `record`.
- `brief`: dựng `ReplyBrief` từ `calls` + `pending_outcome` + `unannounced`; không từ chữ của `finish`.
- `respond`: một lần mỗi lượt, **trừ** `stale_button` (câu mẫu).
- `record`: ghi câu đáp vào thread + `conversations`; cập nhật ledger `reply_kind`, `ended_by`; ghi `GoalProgress`, `queued`.

### 5.4 `ReplyBrief`

```python
class ReplyBrief(BaseModel):
    kind: Literal["social","unsupported","answer","clarify","proposal_made","confirmed","declined",
                  "stale_button","failed","limit_reached"]
    address: str; facts: list[Fact]; ask: ClarifyRequest | None
    must_say: list[str]; must_not_claim: list[Literal["booked","cancelled","moved"]]
    then: str | None; previous_reply: str | None
    tone_context: Literal["first_time","returning","changed_time","after_decline"]
    digest_text: str; staff_note: str | None; now_local: datetime
    fallback: str                 # confirmed/failed: câu chốt CODE viết (tái dùng `phrase.code_phrase`), không phải câu chung
```

**Ánh xạ bắt buộc đủ (code):**

| Nguồn | `Fact` | `must_say` | `must_not_claim` |
|---|---|---|---|
| `ProposalCreated{book}` | `held_at`, `note` | `held_at.spoken` | `booked` |
| `ProposalCreated{move}` | `held_at`, `replaces.was` | `held_at.spoken` | `moved` |
| `ProposalCreated{cancel}` | `will_cancel` | `will_cancel.spoken` | `cancelled` |
| `ProposalCreated.queued[]` | `queued_at[]` + `then` | — | `booked` cho giờ chờ |
| `FreeSlots` | `slots[≤3]`, `more`, `day` | mỗi `slots[].spoken` — khi cùng lượt có `ProposalCreated` → slot chỉ là Fact, không must_say (M6b, 07/11) | — |
| `slot_unavailable` | `asked`, `nearest[≤3]` | `asked.spoken` | — |
| `outside_hours`/`closed_day`/`time_passed` | `asked`, `open/close`, `reason` | `asked.spoken` | — |
| `already_yours` | `existing` | `existing.spoken` | — |
| `ResolvedTime{missing}` / `ask_customer` | `ask` | — | — |
| `AppointmentList` | `appointments[]` | mỗi `spoken` | — |
| `ShopHoursOut`/`ShopStatusOut`/`ShopOpenOut` | các mốc | các `spoken` có mặt | — |
| `PendingOutcome{approved}` | `booked_at` (hoặc `moved_to`/`cancelled_at`) | `spoken` | — |
| `PendingOutcome{rejected/superseded}` | `declined{kind, spoken}` | — | theo `kind` |
| `PendingOutcome{clarify}` | `ask(confirm)` + `pending` | `pending.spoken` | theo `kind` |
| `PendingOutcome{kept}` | `pending{kind, spoken, expires_in}` | — | theo `kind` |
| **`PendingOutcome{failed}`** (SlotTaken, TARGET_CHANGED) | `asked{spoken}`, `error_reason`, `nearest[]` | `asked.spoken` | mọi claim |
| **`PendingOutcome{not_approvable}`** (expired/approving/not_found) | `expired{spoken}` hoặc `error_reason` | — | mọi claim |
| **`TurnContext.unannounced`** (R0) | `prior_outcome{kind, spoken, outcome}` | `spoken` + "đã đặt/huỷ/dời xong" hoặc lý do lỗi | — |
| `TurnContext.recently_declined` + route booking | `declined` "chưa ghi gì" | — | theo `kind` |
| `failed` khác / `limit` | `error_reason` | — | mọi claim |
| `finish(kind_hint=unsupported)` | `shop_phone` | — | — |

### 5.5 `decide_pending(label, aside, reason)`

- `label: Literal["approve","reject","change","unclear","none"]`; `aside: Literal["relative_free","defer_only","defer_with_request"] | None`; `reason: str` (nội bộ, trích cụm chữ khách làm căn cứ, để log soi).
- **Dùng một lần mỗi lượt**, luôn nhắm `TurnContext.pending.id` chốt lúc resolve. Lần hai → từ chối `already_decided`.
- `aside` chỉ dùng để **hạ cấp**: `approve` + `relative_free` → `unclear`; `approve` + `defer_*` → `none`. Không bao giờ nâng cấp.

| Nhãn (sau veto) | Code làm gì | `unclear_count` | Vòng tiếp? |
|---|---|---|---|
| `approve` | `ProposalService.approve(user, pending.id, turn_at=asked_at)` → ghi ledger ngay → `approved`/`failed`/`not_approvable` | — | có, nhưng (j) từ chối mọi `propose_*` sau approve |
| `reject` | `reject` → `rejected` | — | có |
| `change` | `supersede` → `superseded`; model gọi `propose_*` | — | có |
| `unclear` | `mark_unclear`; lần 1 → `clarify` + **`force_end=True`**; lần 2 → `rejected` | +1 | không (code ép) |
| `none` | không đụng → `kept` | 0 | có |

Luật `none` thực hiện refactor.md §4 (hoãn giữ đề xuất) và §8 (chào hỏi không phá luồng). Hoãn không ép `reject`.

### 5.6 Tiến triển, trùng, dừng (code trong middleware)

| Khái niệm | Định nghĩa |
|---|---|
| Marker mới | `time_resolved`, `slots_found:<day>`, `proposal_created:<id>`, `appointments_listed`, `shop_hours_known`, `shop_status_known`, `shop_open_checked:<iso>`, `pending_decided:<label>` |
| Call trùng | cùng `(agent, action, args chuẩn hoá)` đã có trong `calls` và không marker mới từ lần đó → `duplicate_call`, `no_progress_streak += 1` |
| Vòng "toàn từ chối" | mọi call trong vòng bị từ chối → **không** tính vào `orch_max_tool_rounds`, nhưng tính vào `no_progress_streak` |
| Ép dừng | `no_progress_streak ≥ 2` hoặc `unclear` lần 1 → `Command(update={"force_end": True})`; `abefore_model` thấy `force_end` → `jump_to="end"`, `ended_by="forced"` |
| Dừng vì vòng | `ToolRoundLimit` = `orch_max_tool_rounds` → `ended_by=limit` |
| Dừng bình thường | `finish`/`ask_customer` |

---

## 6. Trạng thái và lưu trữ

| Loại | Ở đâu | Ai ghi |
|---|---|---|
| Nhật ký hiển thị | `conversations` | `record` |
| Lịch sử model | thread checkpointer `<user>:<ngày VN>`, `add_messages`, cắt khi đọc (`history.model_history`) | `record` |
| Checkpoint state lượt | checkpointer (TTL 7 ngày); không phải nguồn sự thật | LangGraph |
| **Ledger lượt** `turn_ledger` (mới, TTL 7 ngày) | một doc/lượt: `turn_id`, `route`, `calls[]`, `pending_outcome`, `ended_by`, `reply_kind`, `announced: bool` | middleware **trong vòng**, `record` |
| Lịch thật | `appointments` | chỉ qua `ProposalService.approve` |
| Đề xuất | `proposals` | `ProposalService` |
| `GoalProgress`, `queued` | `conversations.state` (code-only) | `record` |
| Slots/digest | `conversations.state` (tham khảo) | như hiện tại |

**`unannounced` (C1)**: resolver đọc ledger lượt trước của khách trong ngày; nếu `pending_outcome.outcome ∈ {approved, failed}` mà `announced=False` (lượt bị `_supersede`/timeout/lỗi Conversation trước `record`) → `TurnContext.unannounced`; R0 buộc nói. `record` đặt `announced=True` sau khi `complete` phát. Lượt bị huỷ **có** side effect là lịch đã ghi; cơ chế này bảo đảm khách luôn nghe.

**Vì sao bỏ `interrupt()`**: "đang chờ" = `proposals.status == pending` còn hạn; checkpoint có TTL; resume từng cần ba lớp vá.

**Phục hồi sau restart**: không resume; `approving` >60s → failed (sẵn có); dedupe trả `complete` cũ; ledger `announced=False` → lượt sau báo.

**Nút cũ**: id không khớp `pending.id` → `stale_button`, không đụng DB.

**`complete.proposal`**: khác `None` khi cuối lượt `ProposalService.latest(user)` còn `pending` chưa hết hạn. Frontend thay `proposal` mỗi `complete` (`useAgentStream.ts:166–168`), không sửa. Cập nhật AGENTS.md.

**Hai tin chồng nhau**: giữ `_ACTIVE_TURNS`/`_supersede`; side effect có thể có của lượt bị huỷ: đề xuất mới (vô hại) và lịch đã approve (được `unannounced` cứu).

**Giờ trống cũ**: `propose_*` tra lại ngay trước khi tạo; `approve` kiểm index.

---

## 7. Giới hạn tiến độ và rate limit

### 7.1 Trong một lượt

| Giới hạn | Mặc định | Lý do |
|---|---|---|
| `orch_max_tool_rounds` | 8 (chỉ đếm vòng có ≥1 call được chạy) | ca dài nhất không lỗi 5 vòng; dư 3 cho từ chối/sửa |
| `orch_max_calls_per_round` | 3; call thứ 4 trở đi bị từ chối `too_many_calls` | fan-out tối đa 2 specialist + 1 điều phối |
| `max_tool_rounds_per_task` | 4 | giữ `MAX_TOOL_ROUNDS` |
| `max_retries_per_task` | 1, lỗi tạm thời | giữ |
| `no_progress_streak` | 2 → `force_end` | |
| `proposals_created` | 1/lượt (Orchestrator) **và** 1/task (specialist, §9.5) | |
| Trần thời gian lượt | 150 giây; **kiểm timeout Socket.IO client, reverse proxy khi nâng từ 60** | 8 vòng xấu nhất không đạt được vì vòng từ chối không gọi specialist; test tính ngân sách |

Chạm trần: dừng, DB nguyên, `limit_reached` từ ledger (vẫn nói phần đã có). Không bao giờ tự approve.

### 7.2 Xuyên lượt (`GoalProgress`)

- `goal_id` mở khi lượt có call `appointment_task` lần đầu và chưa có goal; đóng khi `approved`, `rejected` dứt khoát, `decide_pending(aside=defer_only)`, hoặc khách đổi mục tiêu (Orchestrator gọi `appointment_task` với `service`/`appointment_id` khác hẳn → code mở goal mới).
- `turns_without_progress += 1` chỉ khi lượt có call `appointment_task` **và** không marker mới **và** không phải `ask_customer` hợp lệ lần đầu cho một field mới. Lượt `social`/`unsupported`, lượt chỉ có `shop_info_task`, lượt hoãn (`kept` với `aside=defer_*`) **không** đếm và **không** reset.
- Ngưỡng 3 → `ReplyBrief(kind="limit_reached")` mời gọi tiệm (`settings.shop_phone`), reset về 0 sau khi nói; không khoá khách.

### 7.3 Rate limit kỹ thuật (giữ nguyên)

`RateLimitService.check_and_hit("chat:user:<id>", chat_max_per_hour=30, 3600)` cho `send_message`; `confirm_proposal` không tính. Thêm trần chi phí `llm_calls_per_user_per_hour` (mặc định 150 ≈ 30 lượt × 5 lời gọi) ghi bằng `RateLimitService.hit` trong wrapper `build_chat_model`; vượt thì lượt vẫn chạy nhưng `orch_max_tool_rounds` bị ép về 2.

Cấu hình: `Settings` mới (`config.py` + `.env.example` + `.env`), prefix `orch_`.

---

## 8. Hợp đồng specialist

### 8.1 Appointment Agent
- Đầu vào: `TaskSpec` + `TurnContext full` (gồm `last_bot_text`, `open_question`, `previous_turns` có `offered`/`resolved`) + câu khách. **Isolated, task message phải đủ** (LangChain Deep Agents); dữ kiện lượt trước đến từ `TimeHint` (Orchestrator điền từ `previous_turns.offered`) và `TurnContext`. Chủ ý: một chỗ hiểu, một chỗ thực thi; tránh agent thực thi tự đọc lịch sử rồi lái khác Orchestrator (lỗi nhóm B).
- Allowlist tool theo action (**code** áp khi dựng agent cho task đó; tool không trong danh sách không tồn tại trong lượt đó):

| action | tool được phép | `data` |
|---|---|---|
| `resolve_time` | `parse_time` | `ResolvedTime{start_at \| None, partial_date, missing, day_from}` |
| `find_slots` | `parse_time`, `find_free_slots` | `FreeSlots{day, slots[≤3]{iso,spoken}, more, fetched_at}` |
| `propose_book` | `parse_time`, `find_free_slots`, `propose_appointment` | `ProposalCreated{proposal_id, kind, start_at, spoken, note, queued[]}` hoặc `error_code` + `nearest[]` |
| `propose_move` | như trên + `appointment_id` bắt buộc ∈ `upcoming` | `ProposalCreated{kind="move", replaces}` |
| `propose_cancel` | `cancel_appointment` | `ProposalCreated{kind="cancel"}` |
| `list_appointments` | `list_my_appointments` | `AppointmentList` |

- **Điều kiện đổi chế độ context**: nếu ca R3 ("trả lời câu bot vừa hỏi") sai đều trong vận hành thật, cho riêng R3 chuyển Appointment sang **forked nhẹ** (nhận thêm 2 lượt cuối nguyên văn). Chỉnh một tham số của tool `appointment_task`, không đổi hợp đồng.
- **Middleware trong agent con** (`wrap_tool_call`): đếm `propose_appointment`/`cancel_appointment` kể từ HumanMessage cuối (cách `_rounds_this_turn` làm); lần thứ hai → `ToolMessage(error_code="one_proposal_per_task")`, không chạy (C3).
- Kết thúc bằng `TaskResult`; code đối chiếu `proposal_id` JSON tool ↔ `TaskResult`; lệch → `schema_invalid`.
- Hàng chờ qua `also` → `propose_appointment(queued=[...])` → `set_queued`; lượt sau `anchor="queued", queue_id`; sau nút Đồng ý chỉ `then`, không tự tạo đề xuất.

### 8.2 Shop Info Agent

- **Vì sao là agent LLM** (chủ dự án, 29/10): câu kiểu "tối thứ Năm tiệm còn làm không", "chủ tiệm xong việc lúc mấy giờ, tới lúc đó đặt được không" cần ghép giờ mở cửa + ngày nghỉ + bận + mốc hiện tại rồi suy ra; Orchestrator không gọi tool nghiệp vụ nên phải có agent làm. Cùng khuôn với Appointment để Orchestrator fan-out hai task song song.
- Đầu vào: `TaskSpec(agent="shop_info")` + `TurnContext full` + câu khách. Isolated.
- 3 tool đọc, allowlist theo action, `ToolRoundLimit` 4, kết thúc bằng `TaskResult`:

| action | tool được phép | `data` |
|---|---|---|
| `get_hours` | `get_shop_hours` | `ShopHoursOut{open, close (hhmm+spoken), closed_days[]}` |
| `get_status` | `get_shop_status` | `ShopStatusOut{busy, until?{iso,spoken}}` |
| `is_open_at` | `get_shop_hours`, `get_shop_status`, `is_open_at(iso)` | `ShopOpenOut{at{iso,spoken}, open: bool, reason: closed_day\|before_open\|after_close\|owner_busy\|open, next_change?}` |

Tool `is_open_at(iso)` (mới) bọc `ShopService.is_open_at(dt)` đã có, trả JSON có `iso` lẫn `spoken`.
- **Đối chiếu sau khi agent trả**: code đọc lại `ShopService.get_hours/get_status` và so với `data`; lệch giờ/ngày → `failed, fact_mismatch`, không retry (model chép sai thì lặp lại không khác).

### 8.3 Conversation Agent

- Đầu vào: `ReplyBrief` + lịch sử cửa sổ (`history.model_history`, giữ) + câu khách. Chế độ **forked** (cần mạch chuyện). System prompt tiếng Anh: dùng `must_say` nguyên văn; không nói "đã đặt/huỷ/dời" nếu trong `must_not_claim`; hỏi đúng một mảnh `ask`; gọi khách bằng `address`; không lặp `previous_reply`; không câu mẫu tiếng Việt phân loại (bài học bẫy #16 cũ).
- Đầu ra: chuỗi (stream `respond`). Guard §9.8 kiểm.
- `TurnFacts` cho `ConversationStateService.apply_turn_analysis`: code dựng với đúng ba trường service đang đọc: `service` và `stated` từ tham số `finish(...)` (đã qua `clean_free_text_item`, ≤2 `stated`), `answers_question` = R3 đã xảy ra và Orchestrator không `ask_customer` cùng field. Chữ ký service đổi kiểu tham số, logic ghi giữ nguyên.

### 8.4 Orchestrator
- `create_agent(model, tools=[5 tool], middleware=[OrchestratorGuard, ToolRoundLimit(orch_max_tool_rounds), ModelRetry])`; `finish`/`ask_customer` khai `return_direct=True`.
- Orchestrator: reasoning_effort=low (chủ dự án 06/11); `build_chat_model` bỏ `temperature` khi có reasoning_effort.
- Dựng **mỗi lượt** (repo đã làm vậy với `build_graph`/`_LazyAgent`) vì schema `appointment_id` là Literal động.
- System prompt: vai điều phối; luật "có đề xuất chờ thì `decide_pending` trước mọi việc khác"; "kết thúc bằng `finish`/`ask_customer`"; không nói với khách. Human message: `TurnContext full` + **2 lượt cuối** + câu khách (untrusted).
- Lỗi vòng đầu → `ended_by=error`, pending coi là `kept`, câu cứng.

---

## 9. Guardrails

| # | Ranh giới | Kiểm (code) |
|---|---|---|
| 9.1 | Input | ≤1000 ký tự; `single_line`; dedupe; rate limit |
| 9.2 | Resolver | pending theo `user_id`; nút phải khớp id; `unannounced` từ ledger |
| 9.3 | **`OrchestratorGuard.awrap_tool_call`** | (a) tool ∈ 5; (b) args parse + kiểm khoảng (`hour 0–23`, `minute 0–59`, `weekday 0–6`, `day_offset -1..90`, `stated ≤2`); (c) `appointment_id`/`queue_id` ∈ `TurnContext`; (d) `anchor` có đối tượng thật; (e) **một đề xuất/lượt**: từ chối `propose_*` nếu `proposals_created ≥ 1` **hoặc** có `propose_*` anh em cùng AIMessage có index nhỏ hơn; (f) trùng (§5.6); (g) nhóm tuần tự (`propose_*`, `decide_pending`, `ask_customer`, `finish`): nếu AIMessage cuối có >1 tool call và call này thuộc nhóm → `serialize_required` (chỉ call index nhỏ nhất của nhóm được chạy nếu nó là call duy nhất; còn lại từ chối); (h) **có `pending` chưa `decide_pending`** → từ chối `propose_*`, `ask_customer`, `finish` với `must_decide_pending` (C2); sau 2 lần từ chối liên tiếp → code tự ghi `kept`, `pending_decided=True`; (i) veto có cấu trúc trên `decide_pending`: `approve` mà giờ/ngày khách nêu khác `pending.start_at` → `change`; `change` mà giờ trùng → từ chối `same_time_as_pending`; `approve`+`relative_free` → `unclear`; `approve`+`defer_*` → `none`; (j) sau `approved` trong lượt → từ chối mọi `propose_*` (`already_approved`); (k) `decide_pending` lần 2 → `already_decided`. Từ chối = `ToolMessage(status="error", content=JSON{error_code, hint})`, không exception |
| 9.4 | Authorization | tool nghiệp vụ đóng `user` trong closure |
| 9.5 | Specialist | allowlist theo action; `one_proposal_per_task`; `ToolRoundLimit`; `ToolFailure`; `NoParallelTools` |
| 9.6 | Task result | parse; đối chiếu `proposal_id`; `ProposalCreated.start_at` khớp DB; `fact_mismatch` |
| 9.7 | Proposal | `ProposalService.approve(..., turn_at=TurnContext.asked_at)` nguyên vẹn |
| 9.8 | Final response | `strip_tool_names`, `unclaim`, `has_false_claim`, `repeats` tái dùng; `facts_from_brief` viết mới; `must_say` đủ; rewrite 1 lần; `fallback` |

Prompt injection: chữ khách chỉ trong human message có nhãn untrusted; không đường nào tới allowlist/action/id.

---

## 10. Xử lý lỗi và phục hồi

| Tình huống | Hành vi |
|---|---|
| Model Orchestrator lỗi/timeout vòng đầu | `ended_by=error` → câu cứng; pending coi là `kept`; log `orchestrate_failed` |
| Tool call không được phép / args sai / id lạ | `ToolMessage{error_code, hint}`; model sửa ở vòng sau; lặp → `no_progress_streak` |
| Specialist lỗi / tool timeout | `TaskResult(failed, retryable)`; 1 retry trong specialist; vẫn lỗi → trả Orchestrator |
| Structured output specialist sai | `failed, schema_invalid`, không retry |
| Shop Info bịa | `failed, fact_mismatch`, không retry |
| Model không `finish`, hết vòng | `ended_by=limit` → `ReplyBrief(limit_reached)` từ ledger (vẫn nói phần đã có) |
| Hai `propose_*` một lượt (khác vòng) | call sau `one_proposal_per_turn`; model chuyển sang `also` hoặc bỏ |
| Lời gọi trùng | `duplicate_call`; 2 lần → `force_end` |
| Restart tiến trình | không resume; `approving` → failed sau 60s; dedupe trả `complete` cũ; ledger `announced=False` → lượt sau báo |
| Tin trùng / hai tin chồng | `MessageDedupe`, `_supersede` (giữ) |
| Giờ trống cũ | `propose_*` tra lại; `approve` kiểm index |
| Đề xuất hết hạn trước lượt | resolver `pending=None`, `recently_declined{expired}`; không `decide_pending` |
| Consent mơ hồ | `unclear` lần 1 → clarify + `force_end` (giữ đề xuất); lần 2 → rejected |
| Ghi lịch xong nhưng `conversations`/ledger ghi hỏng | lịch là sự thật; lượt sau resolver thấy `upcoming` mới; ledger ghi `ended_by=persist_failed` nếu còn ghi được |
| `decide_pending(approve)` → `failed` (SlotTaken/TARGET_CHANGED) | `PendingOutcome(failed, nearest)`; model nên `appointment_task(find_slots)` hoặc `finish`; reply "giờ này vừa có người lấy" + gợi ý |
| `decide_pending(approve)` → `not_approvable` (hết hạn giữa resolve và decide) | `PendingOutcome(not_approvable, reason)`; reply nói hết hạn, mời nhắn lại giờ |
| Lượt bị huỷ/lỗi sau approve | ledger `announced=False` → lượt sau R0 báo |
| Specialist tạo đề xuất lần 2 trong task | chặn `one_proposal_per_task` |
| Hai `propose_*` cùng vòng | call index lớn hơn bị từ chối (g)/(e); DB chỉ có một đề xuất |
| Model không `finish` | `ToolRoundLimit` → reply từ ledger |

---

## 11. Quan sát (Langfuse + log có cấu trúc)

Mọi log trong `app/agents/` mang `extra`: `user_id`, `turn_id`, `thread_id`. So với refactor.md §12: không có plan id/version/task dependency vì hybrid (§12.16); `turn_id` + `round` + `call_index` thay thế.

| Sự kiện | Trường |
|---|---|
| `resolve_done` | `rule` (R0–R5), `has_pending`, `has_open_question`, `queued_n`, `has_unannounced` |
| `orch_call` | `turn_id`, `round`, `call_index`, `tool`, `action`, `args_norm` (không chữ khách), `allowed: bool`, `reject_code` |
| `task_started` / `task_finished` | `turn_id`, `task_id`, `agent`, `action`, `status`, `error_code`, `attempt`, `duration_ms` |
| `pending_decided` | `proposal_id`, `label_from_model`, `aside`, `reason` (cụm chữ model trích), `label_final`, `veto_reason`, `outcome` |
| `turn_ended` | `ended_by`, `rounds_counted`, `rounds_rejected_only`, `calls_n`, `rejects_n`, `markers[]`, `no_progress_streak` |
| `unannounced_reported` | `proposal_id`, `outcome` |
| `guard_violation` | `codes[]`, `fixed_by` |
| `goal_progress` | `goal_id`, `turns_without_progress` |
| `proposal_*` | giữ của `ProposalService` |

Không log nguyên văn câu khách ngoài `turn_ledger` (TTL 7 ngày). Trace Langfuse: tên node/agent khớp 4 agent.

---

## 12. Quyết định kiến trúc

| # | Câu hỏi | Lựa chọn | Lý do | Xem lại khi |
|---|---|---|---|---|
| 1 | Giữ bao nhiêu Analyzer/Router? | **Bỏ hẳn.** Orchestrator vòng tool thay cả route lẫn consent; `classify_only` bỏ 06/11 theo quyết định chủ dự án — Orchestrator gọi finish(kind_hint) | hai model cùng đọc một câu là hai chỗ lệch | chi phí/lượt thành vấn đề |
| 2 | Tin nào bypass Orchestrator? | chỉ **nút bấm** (R1); `classify_only` bỏ 06/11 theo quyết định chủ dự án — Orchestrator gọi finish(kind_hint) | code không đoán xã giao bằng từ khoá | có lớp phân loại rẻ hơn |
| 3 | Conversation được resolver gọi thẳng? | không còn đường tắt riêng; `classify_only` bỏ 06/11 theo quyết định chủ dự án — Orchestrator gọi finish(kind_hint) rồi mới tới Conversation | trạng thái tin cậy thắng câu chữ | — |
| 4 | Shop Info có vòng LLM? | **Có**, 3 tool đọc (chủ dự án 29/10) | cần ghép giờ mở cửa + bận + mốc hiện tại; Orchestrator không gọi tool nghiệp vụ | `fact_mismatch` đều → executor |
| 5 | Task độc lập/phụ thuộc? | model phát nhiều tool call → LangChain chạy song song (trừ nhóm tuần tự); phụ thuộc thì gọi tuần tự | mẫu subagents | — |
| 6 | Plan/evaluate? | không có node riêng; vòng tool của `create_agent` | hybrid | — |
| 7 | Lưu plan/kết quả ở đâu? | `turn_ledger` (Mongo, TTL 7 ngày), ghi **trong vòng**; `GoalProgress`/`queued` trong `conversations.state`; **không** `interrupt` | checkpoint có TTL; bỏ interrupt bỏ lớp resume | cần "chạy dở" sống qua restart |
| 8 | No-progress xuyên lượt? | `GoalProgress` code-only, theo marker | refactor.md §6 | — |
| 9 | Execution limit vs rate limit? | §7.1–7.2 tiến độ; §7.3 chi phí | hai mục đích | — |
| 10 | Tái dùng gì? | hàm thuần: `tokens.py`, `timeparse.py`, `history.py`, format giờ (→ `app/core/vi_format.py`), `guard.strip_tool_names/unclaim/has_false_claim/repeats`, `reply.other_time`, `phrase.code_phrase`, 7 tool + `is_open_at`, `ToolRoundLimit`/`ToolFailure`/`NoParallelTools`/`ModelRetry`, services. **Không**: `claim_facts`, `supervisor`, `analyzer`, `confirm`, phần còn lại `reply`, `relatives`, `build_context_block`, middleware `confirm`/`phrase`/`layout`/`history` | code thuần đã có test | — |
| 11 | Ai viết câu cho khách? | Conversation Agent, mọi lượt trừ `stale_button` | một giọng, guard một chỗ | cần giọng khác: `ReplyBrief.register` |
| 12 | Appointment LLM hay code? | LLM `create_agent`, một task = một action, allowlist theo action, một đề xuất/task, trả `TaskResult` | giữ suy luận xâu chuỗi; chặn tự lái bằng allowlist | lỗi B vẫn ở `propose_*` → executor |
| 13 | Khối bối cảnh text có tag? | Bỏ; `TurnContext` | refactor.md §7 | — |
| 14 | Mỗi lời gọi model nhận bao nhiêu context? | `lite`/`full`; Conversation chỉ `ReplyBrief` | Anthropic "smallest set" | `classify_only` sai vì thiếu ngữ cảnh → thêm digest vào `lite` |
| 15 | Chế độ context specialist? | Appointment/Shop isolated; Conversation forked | LangChain Deep Agents | R3 sai đều → Appointment nhận 2 lượt cuối |
| 16 | Hybrid | vỏ graph + Orchestrator `create_agent` + middleware; **cơ chế thực thi đúng thư viện**: từ chối = `ToolMessage(error)`, state = `Command(update)`, ép dừng = `before_model` + `can_jump_to=["end"]`, kết thúc = `return_direct` | ít code điều phối; mọi bất biến ở code | cần kiểm toàn cục trước khi chạy → thêm node plan |
| 17 | Tool vs structured output | tool; **schema strict-safe**: không default, không `conint`; **Literal động** cho `appointment_id`/`queue_id` là lớp phụ, middleware (c) là lớp chính; đánh đổi: enum khác theo khách → mất cache liên khách cho tool schema; cho phép rơi về `str` nếu đo thấy cache miss đáng kể | `strict` của langchain-core chỉ ép `additionalProperties`/`required` | — |
| 18 | Approve giữa vòng tool | chấp nhận, nhưng **ledger ghi ngay** + `unannounced` ở lượt sau + fallback câu chốt code | approve phải đi qua `decide_pending` để Orchestrator biết kết quả mà xử lý tiếp (queued, failed→find_slots) | — |

---

## 13. Chiến lược test

- Hàm thuần: từng luật (a)–(k) của `OrchestratorGuard`, `one_proposal_per_task`, `normalize_args`, `is_duplicate_call`, `markers`, `GoalProgress.step`, `build_reply_brief` (từng dòng ánh xạ), `facts_from_brief`, resolver (R0–R5).
- Graph với model giả: fake Orchestrator phát chuỗi tool call (kể cả **song song**), fake specialist, fake Conversation. Mongo test.
- Write-fence AST: chỉ `button_path` và `decide_pending` gọi `approve`.
- **Schema strict**: test thuần "schema sinh từ Pydantic không chứa `default`/`minimum`/`maximum`"; **một** lần gọi Azure thật ở task đầu plan để xác nhận `strict=True` không 400 (xin chủ dự án trước, theo quy ước không chạy benchmark).

Ma trận: giữ toàn bộ dòng bản trước (xã giao, pending thắng câu chữ, hoãn, lạc đề, mơ hồ, veto, nhắc lại đúng giờ, câu kép, không được phép, replan sau giờ kín, trùng, không finish, hai lịch, nút, Shop Info, ngày lượt trước, lượt trước đã chào, ánh xạ, hai mức context, isolated, mạch câu chốt, social không đếm, rate limit, restart, trùng slot, hết hạn/nút cũ, injection, idempotent, stream filter, fallback, ngân sách) **và thêm**:

| Yêu cầu | Kịch bản |
|---|---|
| Approve rồi lượt bị huỷ | fake approve → `_supersede` trước `respond` → ledger `announced=False`; lượt sau "alo" → R0, reply có "đã đặt 2 giờ chiều mai" |
| Approve rồi Conversation lỗi | model Conversation timeout → `fallback` là câu chốt code đúng giờ |
| Bỏ qua decide_pending | pending 15:00, fake gọi thẳng `propose_book(15:00)` → `must_decide_pending`; DB đề xuất cũ vẫn `pending` (không bị supersede) |
| decide_pending 2 lần | lần 2 → `already_decided` |
| Specialist gọi propose 2 lần trong task | lần 2 → `one_proposal_per_task`; DB một đề xuất |
| Hai propose cùng vòng | fake phát 2 `propose_book` một AIMessage → call index 1 bị `serialize_required`; DB một đề xuất |
| finish song song | `finish` + `shop_info_task` cùng vòng → `finish` bị từ chối; vòng sau `finish` một mình → END |
| unclear ép dừng | `decide_pending(unclear)` lần 1 → `force_end`; không còn call nào chạy sau đó |
| approve → failed | `SlotTakenError` → `PendingOutcome(failed, nearest)`; reply nói giờ vừa mất + 3 giờ gần |
| approve → not_approvable | expired giữa chừng → reply nói hết hạn, không approve |
| Hoãn kèm "ừ" | "ừ để chú hỏi con đã", fake `approve` + `aside=defer_only` → `none`, đề xuất giữ |
| Dịch vụ không hỗ trợ | "có làm móng gel không" → Orchestrator `finish(kind_hint=unsupported)`, không tool nào chạy → reply hướng gọi tiệm |
| Vòng toàn từ chối không tính trần | 3 vòng bị từ chối + 5 vòng chạy → không `limit` |
| Schema strict-safe | không `default`/`minimum`/`maximum` trong JSON schema của 5 tool |
| Call thứ 4 trong vòng | `too_many_calls` |

---

## 14. Rủi ro

1. **Consent chỉ có veto giờ bằng code**; "ừ để chú hỏi con đã" phụ thuộc Orchestrator điền `aside`. Đây là rủi ro số 1, chỉ đo được bằng Azure thật trước khi mở cho khách.
2. Chi phí 2–5×/lượt (chấp nhận).
3. Kiểm trễ một nhịp (hybrid): đề xuất thứ hai chặn khi gọi; vô hại nhờ write-fence.
4. `strict=True` trên Azure với schema thật: kiểm một lần ở task đầu.
5. Literal động → cache miss tool schema liên khách; có đường lùi (§12.17).
6. Model không `finish`: `ToolRoundLimit` + reply từ ledger.
7. Bỏ interrupt: viết lại `test_run_turn_thread`, `test_graph_events`, `test_socketio_chat`.

**Review độc lập 30/10**: 4 Critical (approve giữa vòng không được báo; `create` tự supersede khi chưa decide; specialist tạo >1 đề xuất/task; ép dừng từ `wrap_tool_call` không khả thi) và 9 Important đã được đưa vào bản này: §1.2, §1.3, §4.2 (`PreviousCall`, `unannounced`), §4.3 R0/R4, §5.1 (schema strict-safe, `aside`, `finish(kind_hint)`), §5.4 (failed/not_approvable/unannounced/unsupported), §5.6, §6, §7.1, §8.1, §9.3 (e)(g)(h)(j)(k), §12.17–18, §13.

**Câu hỏi chặn**: không còn.

---

## 15. Để lại cho người xây
- Tên module (gợi ý): `graph.py`, `resolver.py`, `context.py`, `orchestrator/agent.py`, `orchestrator/guard.py`, `orchestrator/schemas.py`, `specialists/appointment.py`, `specialists/shop_info.py`, `specialists/guard.py` (one_proposal_per_task), `conversation.py`, `reply_brief.py`, `guard.py`, `ledger.py`, `events.py`.
- Ràng buộc giữ cửa đổi hướng: specialist là `run_task(TaskSpec, TurnContext) -> TaskResult`.
- Prompt tiếng Anh; cách render `TurnContext`; thứ tự xoá file cũ và viết lại test.
