# Kiến trúc Hệ thống: LLM Orchestrator & 4-Agent Hybrid Workflow cho Salona Booking

**Tài liệu Tham chiếu Kiến trúc Chi tiết (Architecture Blueprint)**  
**Dựa trên Thiết kế**: `2026-10-29-llm-orchestrator-design.md`  
**Đối tượng Áp dụng**: Toàn bộ hệ thống AI điều phối đặt lịch (`app/agents/`)  
**Tác giả**: Lead System Architect  
**Trạng thái**: Hoàn thiện & Khả thi thi công (Ready for Implementation)

---

## 1. Tổng quan Hệ thống (System Architecture Overview & Topology)

### 1.1 Nguyên lý Mô hình Hybrid
Hệ thống xử lý hội thoại đặt lịch Salona Booking được thiết kế theo mô hình **Hybrid cấp cao**:
1. **Vỏ ngoài (Outer Workflow)**: Là một `StateGraph` của LangGraph thuần mã nguồn tất định (deterministic code). Vỏ này chịu trách nhiệm nạp trạng thái hội thoại từ cơ sở dữ liệu, kiểm tra các điều kiện tiên quyết (pre-conditions) thông qua **State Resolver**, và định tuyến luồng xử lý mà không cần tốn chi phí gọi LLM phân loại cồng kềnh.
2. **Lõi điều phối bên trong (Inner Orchestrator Tool-Loop)**: Sử dụng mô hình `create_agent` của LangChain hoạt động theo cơ chế *subagents-as-tools*. LLM Orchestrator đóng vai trò là "Tổng đài viên trưởng", nhận toàn bộ bối cảnh (`TurnContext full`), lập luận và triệu gọi các chuyên viên (Specialists) thông qua các công cụ chuyên biệt. Toàn bộ vòng lặp tool-calling này được kiểm soát nghiêm ngặt bởi **OrchestratorGuard** (Middleware can thiệp `awrap_tool_call`).
3. **Phát ngôn tập trung**: Duy nhất **Conversation Agent** được phép sinh văn bản trả lời khách hàng và phát luồng (stream) token mang nhãn tag `respond` ra giao diện Socket.IO.

```
                    KIẾN TRÚC HYBRID TỔNG QUAN
┌────────────────────────────────────────────────────────────────────────┐
│ VỎ NGOÀI: LangGraph StateGraph (Code tất định điều hướng)               │
│                                                                        │
│  [Socket.IO Input]                                                     │
│         │                                                              │
│         ▼                                                              │
│  [RateLimit & Dedupe]                                                  │
│         │                                                              │
│         ▼                                                              │
│  [State Resolver] ──── R1 (Button) ────► [button_path] ────────┐       │
│         │                                                      │       │
│         ├───────────── R4 (Social) ───► [classify_only] ───────┤       │
│         │                                                      │       │
│         ▼ R2, R3, R5 (Business)                                │       │
│  ┌──────────────────────────────────────────────────────────┐  │       │
│  │ LÕI ĐIỀU PHỐI: Orchestrator Loop (create_agent)          │  │       │
│  │                                                          │  │       │
│  │   [Orchestrator LLM] ◄──► [OrchestratorGuard Middleware] │  │       │
│  │         │                                                │  │       │
│  │         ├── appointment_task ──► [Appointment Agent]     │  │       │
│  │         ├── shop_info_task   ──► [Shop Info Agent]       │  │       │
│  │         ├── decide_pending   ──► [ProposalService (code)]│  │       │
│  │         ├── ask_customer     ──► [Record open_question]  │  │       │
│  │         └── finish           ──► [Return Direct]         │  │       │
│  └──────────────────────────────────────────────────────────┘  │       │
│         │                                                      │       │
│         ▼                                                      │       │
│  [ReplyBrief Builder] ◄────────────────────────────────────────┘       │
│         │                                                              │
│         ▼                                                              │
│  [Conversation Agent] (Stream DUY NHẤT tag "respond")                  │
│         │                                                              │
│         ▼                                                              │
│  [Final Guardrails] ──► [Record & Ledger] ──► [Socket.IO Output]       │
└────────────────────────────────────────────────────────────────────────┘
```

### 1.2 Phân định Trách nhiệm: 4 Agent và Các Thành phần Code Tất định

Hệ thống phân định ranh giới tuyệt đối giữa suy luận bằng AI và chốt chặn an toàn bằng code:

| Thành phần | Bản chất | Trách nhiệm chính | Điều CẤM kỵ tuyệt đối |
|---|---|---|---|
| **Orchestrator** | Agent LLM (`create_agent`) | Tiếp nhận bối cảnh, hiểu ý định tổng thể, lập kế hoạch gọi specialist hoặc công cụ điều phối. | CẤM sinh câu nói cho khách; CẤM gọi tool DB nghiệp vụ; CẤM ghi DB trực tiếp. |
| **Appointment Agent** | Specialist LLM (`create_agent`) | Tiếp nhận 1 `TaskSpec`, thực thi giải quyết thời gian, tìm slot trống, tạo đề xuất lịch (`propose_*`). | CẤM ghi lịch thật vào DB; CẤM tạo >1 đề xuất/task; CẤM nói chuyện với khách. |
| **Shop Info Agent** | Specialist LLM (`create_agent`) | Tiếp nhận 1 `TaskSpec`, truy vấn thông tin giờ mở cửa, trạng thái bận, suy luận tiệm có mở lúc $T$ không. | CẤM bịa đặt thông tin sai lệch dữ liệu gốc; CẤM nói chuyện với khách. |
| **Conversation Agent** | Agent LLM (Prompt thuần, không tool) | Nhận `ReplyBrief`, hành văn tự nhiên, thân thiện bằng tiếng Việt phù hợp với danh xưng khách hàng. | CẤM bịa sự thật ngoài brief; CẤM tự ý tuyên bố "đã đặt/huỷ" khi chưa có xác nhận; CẤM ra quyết định. |
| **State Resolver** | Mã nguồn Python thuần | Đọc DB/Ledger, phân tích sơ bộ tin nhắn, phân loại nhanh tuyến đường (R0 đến R5). | Không gọi LLM (Zero-LLM), không suy đoán ý định khách. |
| **OrchestratorGuard** | Middleware LangGraph | Chặn trước/sau mọi tool call của Orchestrator, thực thi 11 chốt an toàn nghiêm ngặt. | Không bỏ qua bất kỳ vi phạm tham số/logic nghiệp vụ nào. |
| **ReplyBrief Builder** | Mã nguồn Python thuần | Tổng hợp kết quả từ các task và quyết định pending để đóng gói thành Pydantic `ReplyBrief`. | Không phụ thuộc vào văn bản sinh ra từ `finish`. |
| **Write-Fence** | Lớp dịch vụ `ProposalService` | Đảm bảo Agent chỉ tạo đề xuất tạm. Chỉ hàm `approve` mới ghi dữ liệu vào bảng lịch hẹn. | Không cấp tool ghi trực tiếp cho LLM. |
| **Turn Ledger** | MongoDB Collection `turn_ledger` | Nhật ký bất biến lưu lại mọi bước chạy, kết quả task, đề xuất và cờ đã thông báo (`announced`). | Không phụ thuộc vào cơ chế checkpoint dễ bị reset. |

### 1.3 Sơ đồ Kiến trúc Chi tiết và Topology Thành phần

```mermaid
graph TD
    classDef clientStyle fill:#e1f5fe,stroke:#0288d1,stroke-width:2px;
    classDef codeStyle fill:#f1f8e9,stroke:#558b2f,stroke-width:2px;
    classDef agentStyle fill:#fff3e0,stroke:#f57c00,stroke-width:2px;
    classDef storageStyle fill:#fce4ec,stroke:#c2185b,stroke-width:2px;
    classDef guardStyle fill:#ffebee,stroke:#d32f2f,stroke-width:2px;

    Client["Khách hàng (Client / Socket.IO)"]:::clientStyle

    subgraph OuterGraph ["LangGraph StateGraph Workflow (Vỏ Ngoài - Code Tất Định)"]
        DedupeRate["Message Dedupe & Rate Limiter"]:::codeStyle
        Resolver["State Resolver (R0 - R5)"]:::codeStyle
        BtnPath["button_path (Xử lý nút bấm R1)"]:::codeStyle
        ClassifyOnly["classify_only (Phân loại rẻ R4)"]:::codeStyle
        BriefBuilder["ReplyBrief Builder (Tổng hợp sự thật)"]:::codeStyle
        FinalGuard["Final Guardrails (Kiểm tra sự thật)"]:::guardStyle
        RecordNode["Record Node (Lưu Ledger & Cập nhật State)"]:::codeStyle
    end

    subgraph OrchestratorModule ["Lõi Điều phối: Orchestrator Loop"]
        OrchAgent["Orchestrator Agent (create_agent)"]:::agentStyle
        OrchGuard["OrchestratorGuard Middleware (11 Luật chốt chặn)"]:::guardStyle
        RoundLimit["ToolRoundLimit Middleware (Tối đa 8 vòng)"]:::guardStyle
    end

    subgraph SpecialistModule ["Chuyên viên Thực thi (Specialist Subagents - Isolated Context)"]
        ApptAgent["Appointment Agent (Allowlist 5 Action)"]:::agentStyle
        ShopAgent["Shop Info Agent (Allowlist 3 Action)"]:::agentStyle
    end

    subgraph SpeakModule ["Phát ngôn Duy nhất"]
        ConvAgent["Conversation Agent (Stream tag: respond)"]:::agentStyle
    end

    subgraph StorageLayer ["Tầng Lưu trữ & Write-Fence (MongoDB & Memory)"]
        WriteFence["ProposalService (Write-Fence Lịch)"]:::guardStyle
        DB_Proposals[("Collection: proposals")]:::storageStyle
        DB_Appointments[("Collection: appointments")]:::storageStyle
        DB_Ledger[("Collection: turn_ledger (TTL 7 ngày)")]:::storageStyle
        DB_Conv[("Collection: conversations")]:::storageStyle
        Checkpointer[("Memory/Mongo Checkpointer")]:::storageStyle
    end

    Client -->|"send_message / confirm_proposal"| DedupeRate
    DedupeRate --> Resolver
    Resolver -->|"R1: ButtonReply"| BtnPath
    Resolver -->|"R4: Text xã giao/không ngày giờ"| ClassifyOnly
    Resolver -->|"R2/R3/R5 hoặc R4 business"| OrchAgent

    BtnPath -->|"Outcome đã duyệt / từ chối"| BriefBuilder
    ClassifyOnly -->|"social / unsupported"| BriefBuilder
    ClassifyOnly -->|"business"| OrchAgent

    OrchAgent <-->|"Kiểm tra & can thiệp từng call"| OrchGuard
    OrchGuard -->|"Tool hợp lệ: appointment_task"| ApptAgent
    OrchGuard -->|"Tool hợp lệ: shop_info_task"| ShopAgent
    OrchGuard -->|"Tool hợp lệ: decide_pending"| WriteFence
    WriteFence <-->|"Kiểm duyệt & Ghi Atomic"| DB_Proposals
    WriteFence -->|"Chỉ khi approve hợp lệ"| DB_Appointments
    WriteFence -.->|"Ghi nhận ngay outcome"| DB_Ledger

    ApptAgent -->|"TaskResult (JSON)"| OrchGuard
    ShopAgent -->|"TaskResult (JSON)"| OrchGuard
    OrchGuard -->|"Trả kết quả Task/Error"| OrchAgent

    OrchAgent -->|"finish / ask_customer / force_end"| BriefBuilder
    BriefBuilder -->|"ReplyBrief có kiểu"| ConvAgent
    ConvAgent -->|"Văn bản phản hồi"| FinalGuard
    FinalGuard -->|"Văn bản chuẩn"| RecordNode
    RecordNode -->|"Cập nhật trạng thái"| DB_Conv
    RecordNode -->|"Đánh dấu announced=True"| DB_Ledger
    RecordNode -->|"Phát sự kiện: complete"| Client
```

---

## 2. Vòng đời Một lượt Xử lý (Turn Lifecycle & Request Flow)

### 2.1 Sơ đồ Trình tự Hoàn chỉnh (Sequence Diagram)

Sơ đồ thể hiện chu trình nhận tin nhắn từ Socket.IO, đi qua các bước lọc, điều phối, thực thi công cụ cho đến khi trả lời khách hàng:

```mermaid
sequenceDiagram
    autonumber
    actor Khach as Khách hàng (Client)
    participant SocketIO as Socket.IO Handler
    participant Resolver as State Resolver (Code)
    participant Orchestrator as Orchestrator LLM
    participant Guard as OrchestratorGuard
    participant Specialists as Appointment / Shop Specialist
    participant WriteFence as ProposalService (Write-Fence)
    participant Ledger as Turn Ledger (MongoDB)
    participant Brief as ReplyBrief Builder
    participant Speaker as Conversation Agent
    participant Output as Final Guard & Record

    Khach->>SocketIO: send_message("Đặt cho anh 2h chiều mai cắt tóc")
    SocketIO->>SocketIO: Kiểm tra Rate Limit & Dedupe Message
    SocketIO->>Resolver: Khởi tạo lượt (turn_id, asked_at)
    
    Note over Resolver: Đọc DB proposals, appointments, turn_ledger<br/>Dựng TurnContext full<br/>Đánh giá R0 -> R5
    Resolver-->>Orchestrator: Định tuyến R5 (Business Flow) + TurnContext full

    loop Vòng lặp Orchestrator Tool-Loop (Tối đa 8 vòng)
        Orchestrator->>Guard: tool_call(appointment_task: propose_book, 14:00 mai)
        Note over Guard: Kiểm tra 11 luật an toàn<br/>(Allowlist, Range, ID, Serialize, Pending Fence)
        alt Vi phạm luật
            Guard-->>Orchestrator: ToolMessage(status="error", code="...")
        else Hợp lệ
            Guard->>Specialists: run_task(TaskSpec)
            Specialists->>WriteFence: create(kind="book", start_at=14:00)
            WriteFence-->>Specialists: ProposalCreated(id="p123", held_at=14:00)
            Specialists-->>Guard: TaskResult(status="ok", data=ProposalCreated)
            Guard->>Ledger: Ghi ProposalCreated ngay vào calls[]
            Guard-->>Orchestrator: ToolMessage(status="ok", data=TaskResult)
        end
        Orchestrator->>Guard: tool_call(finish, service="cắt tóc")
        Note over Guard: finish có return_direct=True -> Kích hoạt thoát vòng
        Guard-->>Orchestrator: ToolMessage(status="ok")
    end

    Orchestrator->>Brief: Kết thúc điều phối -> Chuyển giao calls[] & TurnContext
    Note over Brief: Ánh xạ dữ kiện Pydantic Fact:<br/>- must_say: "2 giờ chiều mai"<br/>- must_not_claim: ["booked"] (vì chưa approve!)
    Brief->>Speaker: ReplyBrief + Ngữ cảnh danh xưng + Lịch sử hội thoại

    loop Stream token duy nhất
        Speaker-->>SocketIO: emit("token", chunk) [chỉ token tag="respond"]
        SocketIO-->>Khach: Hiển thị chữ chạy realtime
    end

    Speaker->>Output: Hoàn tất phát ngôn câu đáp
    Note over Output: Final Guardrails kiểm tra lặp từ & sự thật<br/>Record cập nhật ConversationState
    Output->>Ledger: Cập nhật announced=True, ended_by="finish"
    Output->>SocketIO: emit("complete", {proposal: {id: "p123", kind: "book", ...}})
    SocketIO-->>Khach: Hiện nút Đồng ý / Không trên UI
```

---

## 3. Cây Quyết định State Resolver (Decision Tree R0 -> R5)

State Resolver là trạm kiểm soát đầu tiên, chạy hoàn toàn bằng mã nguồn Python tĩnh (Zero-LLM), đảm bảo tính dự đoán tuyệt đối (predictability) và tốc độ phản hồi cực nhanh.

### 3.1 Sơ đồ Phân nhánh Quyết định (Decision Tree Flowchart)

```mermaid
flowchart TD
    classDef startNode fill:#e0f7fa,stroke:#006064,stroke-width:2px;
    classDef checkNode fill:#fff9c4,stroke:#fbc02d,stroke-width:2px;
    classDef routeNode fill:#c8e6c9,stroke:#2e7d32,stroke-width:2px;
    classDef orchNode fill:#ffe0b2,stroke:#e65100,stroke-width:2px;

    Start([Tin nhắn Khách hàng]):::startNode --> CheckR0{"R0: Có unannounced outcome<br/>từ lượt trước (lịch đã ghi mà chưa báo)?"}:::checkNode

    CheckR0 -- Có --> FlagR0["Đánh dấu cờ R0: Buộc ReplyBrief.must_say<br/>chứa kết quả lượt trước"]:::checkNode
    CheckR0 -- Không --> CheckR1
    FlagR0 --> CheckR1

    CheckR1{"R1: Input là ButtonReply<br/>(Bấm nút Đồng ý / Không)?"}:::checkNode
    CheckR1 -- Đúng --> R1_ButtonPath["Điều hướng: button_path<br/>- Khớp pending.id: approve / reject<br/>- Không khớp: stale_button"]:::routeNode

    CheckR1 -- Sai --> CheckR2{"R2: Đang có PendingProposal còn hạn<br/>VÀ Khách gửi tin nhắn văn bản?"}:::checkNode
    CheckR2 -- Đúng --> R2_Orch["Điều hướng: orchestrate<br/>(Bật chốt chặn bắt buộc: decide_pending)"]:::orchNode

    CheckR2 -- Sai --> CheckR3{"R3: Đang có OpenQuestion<br/>VÀ Câu ngắn ≤6 từ chứa mốc thời gian<br/>(Khớp tokens.scan_*)?"}:::checkNode
    CheckR3 -- Đúng --> R3_Orch["Điều hướng: orchestrate<br/>(Kèm gợi ý answers_open_question_hint=True)"]:::orchNode

    CheckR3 -- Sai --> CheckR4{"R4: KHÔNG pending, open_question, queued, unannounced<br/>VÀ Câu KHÔNG chứa số, giờ, ngày, thứ?"}:::checkNode
    CheckR4 -- Đúng --> R4_Classify["Điều hướng: classify_only<br/>(Chạy mô hình LLM rẻ tiền phân loại 1 lượt)"]:::routeNode
    
    R4_Classify --> ClassifyChoice{"Kết quả Classify?"}:::checkNode
    ClassifyChoice -- "social / unsupported" --> R4_Direct["Chuyển thẳng sang ReplyBrief -> Conversation Agent"]:::routeNode
    ClassifyChoice -- "business" --> R5_Orch

    CheckR4 -- Sai --> R5_Orch["R5 (Fallback): Điều hướng orchestrate<br/>(Đầy đủ năng lực xử lý nghiệp vụ phức tạp)"]:::orchNode
```

### 3.2 Bảng Đặc tả Chi tiết các Tuyến Định tuyến

| Nhánh | Tên điều kiện | Logic kiểm tra dữ liệu | Đường đi kế tiếp | Chi phí LLM |
|---|---|---|---|---|
| **R0** | Unannounced Outcome | `TurnContext.unannounced is not None` (Lượt trước `ProposalService.approve` thành công nhưng ngắt kết nối trước khi phát tin). | Tiếp tục xét R1–R5 nhưng tiêm bắt buộc câu thông báo lịch thành công vào `must_say`. | Không tốn thêm LLM |
| **R1** | Button Reply | Tin nhắn gửi lên từ nút UI (`ButtonReply` chứa `proposal_id` và `decision`). | Gọi thẳng `button_path` (code thuần), không qua Orchestrator. Nếu stale trả câu mẫu. | 0 hoặc 1 lượt Conversation Agent |
| **R2** | Pending + Text | `pending is not None` (còn hạn) và khách nhập văn bản tự do. | `orchestrate`. OrchestratorGuard áp luật (h): từ chối mọi tool khác cho đến khi `decide_pending` được gọi. | LLM Orchestrator |
| **R3** | Answer Open Question | `open_question is not None` và số từ $\le 6$ và regex `tokens.scan_*` bắt được giờ/ngày/buổi. | `orchestrate` với `answers_open_question_hint=True` giúp Orchestrator hiểu ngay câu trả lời cho câu hỏi bot vừa hỏi. | LLM Orchestrator |
| **R4** | Classify Only | Trạng thái sạch (không pending/hỏi dở/hàng chờ) và câu thuần chữ không chứa bất kỳ mốc thời gian nào. | Gọi `classify_only` (prompt phân loại siêu nhẹ). Nếu xã giao hoặc ngoài phạm vi, bỏ qua Orchestrator đi thẳng vào Conversation. | 1 lượt LLM nhẹ |
| **R5** | Fallback Business | Các trường hợp còn lại (yêu cầu đặt lịch, dời lịch, huỷ lịch, hỏi giờ phức tạp). | Đưa toàn bộ vào `orchestrate` để Orchestrator lập luận và điều phối công cụ. | LLM Orchestrator |

---

## 4. Kiến trúc Orchestrator Tool Loop & OrchestratorGuard (Middleware Architecture)

Orchestrator không gọi trực tiếp API hay DB mà phát ra các tool calls. Toàn bộ các tool calls đều bị chặn lại bởi `OrchestratorGuard` tại hook `awrap_tool_call`.

### 4.1 Quy trình Vận hành Vòng lặp Orchestrator

```mermaid
flowchart TD
    classDef startEnd fill:#eceff1,stroke:#455a64,stroke-width:2px;
    classDef modelFill fill:#e3f2fd,stroke:#1565c0,stroke-width:2px;
    classDef guardFill fill:#ffebee,stroke:#c62828,stroke-width:2px;
    classDef actionFill fill:#e8f5e9,stroke:#2e7d32,stroke-width:2px;

    StartLoop([Bắt đầu Vòng Điều phối]):::startEnd --> CheckBeforeModel{"abefore_model:<br/>force_end == True<br/>HOẶC Rounds ≥ 8?"}:::guardFill

    CheckBeforeModel -- Có --> JumpEnd["Ép nhảy sang END (jump_to='end')<br/>ended_by = 'forced' | 'limit'"]:::guardFill --> EndLoop([Thoát Vòng lặp]):::startEnd

    CheckBeforeModel -- Không --> CallModel["Orchestrator Model Sinh Tool Calls"]:::modelFill
    CallModel --> CatchModelErr{"Model Timeout / Lỗi?"}:::guardFill
    
    CatchModelErr -- Có --> ModelErrHandler["ended_by = 'error'<br/>pending = kept<br/>Thoát vòng"]:::guardFill --> EndLoop
    CatchModelErr -- Không --> ToolEval["OrchestratorGuard.awrap_tool_call<br/>Kiểm tra 11 Chốt Chặn (a -> k)"]:::guardFill

    ToolEval --> CheckValid{"Hợp lệ 100%?"}:::guardFill
    CheckValid -- Từ chối --> RejectTool["Trả ToolMessage(status='error', code, hint)<br/>streak_no_progress += 1"]:::guardFill
    RejectTool --> EvalStreak{"streak_no_progress ≥ 2?"}:::guardFill
    EvalStreak -- Đúng --> SetForceEnd["Đặt force_end = True"]:::guardFill --> LoopBackModel["Quay lại Model ở vòng sau"]:::modelFill
    EvalStreak -- Sai --> LoopBackModel

    CheckValid -- Hợp lệ --> ExecTool["Thực thi Công cụ<br/>- Specialist Task (isolated)<br/>- decide_pending (code)<br/>- ask_customer / finish"]:::actionFill
    ExecTool --> UpdateLedger["Ghi ngay TaskResult / Outcome<br/>vào State & turn_ledger"]:::actionFill

    UpdateLedger --> CheckReturnDirect{"Tool có return_direct=True?<br/>(finish HOẶC ask_customer)"}:::guardFill
    CheckReturnDirect -- Đúng --> CheckAllReturnDirect{"MỌI tool chạy cùng vòng<br/>đều là return_direct?"}:::guardFill
    CheckAllReturnDirect -- Đúng --> NormalEnd["Thoát vòng bình thường<br/>ended_by = 'finish' | 'ask_customer'"]:::actionFill --> EndLoop
    CheckAllReturnDirect -- Sai --> LoopBackModel
    CheckReturnDirect -- Sai --> LoopBackModel
```

### 4.2 Chi tiết 11 Chốt chặn Kiểm soát Tất định (Luật a đến k)

Mỗi lời gọi công cụ trong `messages[-1].tool_calls` đều phải vượt qua ma trận 11 chốt kiểm soát code tĩnh sau:

```mermaid
graph TD
    classDef ruleStyle fill:#fff8e1,stroke:#ffa000,stroke-width:2px;
    classDef rejectStyle fill:#ffebee,stroke:#d32f2f,stroke-width:2px;

    Call[Tool Call từ Orchestrator] --> Ra{"(a) Tool Allowlist?<br/>Thuộc 5 tool quy định"}:::ruleStyle
    Ra -- Không --> ErrA["Từ chối: unknown_tool"]:::rejectStyle
    Ra -- Có --> Rb{"(b) Range Checks?<br/>hour 0-23, min 0-59, weekday 0-6,<br/>day_offset -1..90, stated ≤ 2"}:::ruleStyle
    Rb -- Sai phạm --> ErrB["Từ chối: invalid_range"]:::rejectStyle
    Rb -- Hợp lệ --> Rc{"(c) ID Exists?<br/>appointment_id / queue_id<br/>phải tồn tại trong TurnContext"}:::ruleStyle
    Rc -- Không khớp --> ErrC["Từ chối: entity_not_found"]:::rejectStyle
    Rc -- Khớp --> Rd{"(d) Real Anchor?<br/>Đối tượng neo (pending, declined,<br/>appointment, queued) có thật?"}:::ruleStyle
    Rd -- Không thật --> ErrD["Từ chối: invalid_anchor"]:::rejectStyle
    Rd -- Thật --> Re{"(e) Tối đa 1 Proposal/Lượt?<br/>proposals_created < 1 VÀ<br/>không có propose_* anh em index nhỏ hơn"}:::ruleStyle
    Re -- Đã có --> ErrE["Từ chối: one_proposal_per_turn"]:::rejectStyle
    Re -- Hợp lệ --> Rf{"(f) Duplicate Call Check?<br/>Cùng agent, action, args chuẩn hoá<br/>và không có progress marker mới"}:::ruleStyle
    Rf -- Bị trùng --> ErrF["Từ chối: duplicate_call"]:::rejectStyle
    Rf -- Mới --> Rg{"(g) Serialize Required?<br/>Nhóm tuần tự (propose_*, decide_pending,<br/>ask_customer, finish) KHÔNG chạy song song"}:::ruleStyle
    Rg -- Chạy song song --> ErrG["Từ chối: serialize_required"]:::rejectStyle
    Rg -- Tuần tự --> Rh{"(h) Pending Fence?<br/>Có pending mà chưa decide_pending<br/>thì CẤM propose_*, ask_customer, finish"}:::ruleStyle
    Rh -- Vi phạm --> ErrH["Từ chối: must_decide_pending"]:::rejectStyle
    Rh -- Hợp lệ --> Ri{"(i) Structured Consent Veto?<br/>Veto mốc thời gian lệch -> change<br/>Mốc trùng khi change -> từ chối<br/>Hạ cấp aside: relative_free/defer"}:::ruleStyle
    Ri -- Veto từ chối --> ErrI["Từ chối: same_time_as_pending / invalid_consent"]:::rejectStyle
    Ri -- Duyệt --> Rj{"(j) Chặn propose_* sau approved?<br/>Đã duyệt lịch trong lượt thì<br/>không được tạo thêm đề xuất mới"}:::ruleStyle
    Rj -- Vi phạm --> ErrJ["Từ chối: already_approved"]:::rejectStyle
    Rj -- Hợp lệ --> Rk{"(k) decide_pending Gọi 1 Lần?<br/>Chỉ được gọi đúng 1 lần duy nhất mỗi lượt"}:::ruleStyle
    Rk -- Lần 2 --> ErrK["Từ chối: already_decided"]:::rejectStyle
    Rk -- Lần 1 --> Allow[CHO PHÉP THỰC THI TOOL]
```

### 4.3 Phân tích Sâu Cơ chế Veto và Quản lý Đề xuất Chờ (Pending Proposal)
- **Luật (h) - Pending Fence**: Khi hệ thống ghi nhận có một đề xuất đang chờ (`TurnContext.pending`), Orchestrator **bắt buộc** phải giải quyết số phận của đề xuất đó thông qua `decide_pending` trước khi được phép đặt lịch mới, hỏi khách câu mới hoặc kết thúc. Nếu Orchestrator cố tình vi phạm 2 lần liên tiếp, code sẽ tự động can thiệp ghi nhận trạng thái `kept` (giữ nguyên đề xuất) và đánh dấu `pending_decided=True` để tránh kẹt hệ thống.
- **Luật (i) - Veto có cấu trúc**:
  - Nếu Orchestrator gán nhãn `approve` nhưng câu khách có mốc giờ khác với `pending.start_at` $\rightarrow$ Code tự động hạ cấp thành `change`.
  - Nếu Orchestrator gán nhãn `change` nhưng khách chỉ nhắc lại đúng giờ của đề xuất $\rightarrow$ Code từ chối công cụ với mã `same_time_as_pending`.
  - Nếu khách đồng ý nhưng kèm hoãn (`aside="defer_only"` hoặc `"defer_with_request"`) $\rightarrow$ Code hạ cấp thành `none` (`kept`), bảo lưu đề xuất cho khách.

---

## 5. Ranh giới và Hợp đồng 4 Agent (Agent Contracts & Context Modes)

Hệ thống thiết lập 3 chế độ cung cấp ngữ cảnh (Context Modes) riêng biệt nhằm cô lập dữ liệu, tối ưu hóa kích thước prompt và loại bỏ hoàn toàn lỗi "Agent tự suy diễn lệch khỏi chỉ đạo" (Lỗi nhóm B).

```mermaid
graph LR
    classDef orchStyle fill:#fff3e0,stroke:#e65100,stroke-width:2px;
    classDef specStyle fill:#e8f5e9,stroke:#2e7d32,stroke-width:2px;
    classDef convStyle fill:#e1f5fe,stroke:#0288d1,stroke-width:2px;
    classDef dataStyle fill:#f3e5f5,stroke:#7b1fa2,stroke-width:2px;

    ContextFull[("TurnContext Full<br/>- Thông tin khách<br/>- Giờ mở cửa shop<br/>- Lịch sắp tới<br/>- Đề xuất pending<br/>- 2 lượt trước có kiểu")]:::dataStyle

    subgraph ModeOrch ["Full Context Mode"]
        OrchAgent["Orchestrator Agent"]:::orchStyle
    end

    subgraph ModeIsolated ["Isolated Context Mode (Task Message Độc lập)"]
        ApptAgent["Appointment Agent"]:::specStyle
        ShopAgent["Shop Info Agent"]:::specStyle
    end

    subgraph ModeForked ["Forked Context Mode (Cửa sổ Lịch sử + Fact)"]
        Brief["ReplyBrief (Facts)"]:::dataStyle
        ConvAgent["Conversation Agent"]:::convStyle
    end

    ContextFull -->|"Đọc toàn bộ ngữ cảnh"| OrchAgent
    OrchAgent -->|"Dựng TaskSpec độc lập"| ApptAgent
    OrchAgent -->|"Dựng TaskSpec độc lập"| ShopAgent
    ContextFull -.->|"Tham chiếu sự thật (Read-only)"| ApptAgent
    ContextFull -.->|"Tham chiếu sự thật (Read-only)"| ShopAgent

    ApptAgent -->|"TaskResult (Dữ liệu cấu trúc)"| Brief
    ShopAgent -->|"TaskResult (Dữ liệu cấu trúc)"| Brief
    Brief -->|"Chỉ truyền sự thật & ràng buộc từ ngữ"| ConvAgent
```

### 5.1 Ma trận Đặc tả Hợp đồng Giao tiếp giữa 4 Agent

| Đặc tính | 1. Orchestrator | 2. Appointment Agent | 3. Shop Info Agent | 4. Conversation Agent |
|---|---|---|---|---|
| **Mục đích** | Phân tích & điều phối vòng lặp tool | Xử lý logic đặt/dời/hủy/tìm slot | Tra cứu thông tin shop & giờ giấc | Tạo câu trả lời tự nhiên bằng tiếng Việt |
| **Loại hình** | LLM Agent (`create_agent`) | LLM Specialist (`create_agent`) | LLM Specialist (`create_agent`) | LLM Chat Model (Prompt chuẩn) |
| **Stream Token** | Không (`streaming=False`) | Không (`streaming=False`) | Không (`streaming=False`) | **CÓ** (Duy nhất stream tag `respond`) |
| **Chế độ Ngữ cảnh** | **Full Context** (Bối cảnh đầy đủ + 2 lượt gần nhất + câu khách) | **Isolated Context** (Chỉ nhận `TaskSpec` + Full context tham chiếu, không đọc lịch sử thô) | **Isolated Context** (Chỉ nhận `TaskSpec` + Full context tham chiếu) | **Forked Context** (`ReplyBrief` + Cửa sổ lịch sử hiển thị + câu khách) |
| **Công cụ được cấp** | 5 Tool điều phối: `appointment_task`, `shop_info_task`, `decide_pending`, `ask_customer`, `finish` | Allowlist động theo action: `parse_time`, `find_free_slots`, `propose_appointment`, `cancel_appointment`, `list_my_appointments` | Allowlist động: `get_shop_hours`, `get_shop_status`, `is_open_at` | **Không có công cụ nào (Zero-tool)** |
| **Đầu ra (Output)** | Danh sách Tool Calls có cấu trúc | `TaskResult` (Pydantic model) | `TaskResult` (Pydantic model) | Chuỗi văn bản tiếng Việt tự nhiên |
| **Cơ chế Hậu kiểm** | `OrchestratorGuard` kiểm 11 luật | Code đối chiếu `proposal_id` và DB | Code đối chiếu lại `ShopService` (phát hiện `fact_mismatch`) | `Final Guardrails` kiểm `must_say` và `must_not_claim` |

### 5.2 Hợp đồng Bắt buộc của ReplyBrief (Mapping sang Conversation Agent)

Để loại bỏ hoàn toàn hiện tượng LLM "nói hớ" (nhận đã đặt lịch trong khi DB chưa ghi), `ReplyBrief` đóng vai trò là hợp đồng dữ kiện bắt buộc:
- **`must_say`**: Danh sách chuỗi ký tự **bắt buộc phải xuất hiện** trong câu trả lời (ví dụ: mốc thời gian cụ thể `14:00 chiều mai`, số điện thoại hotline).
- **`must_not_claim`**: Danh sách các từ khoá hành động **tuyệt đối không được khẳng định**:
  - `booked`: Cấm nói "đã đặt xong" nếu đề xuất chỉ mới ở trạng thái `pending`.
  - `cancelled`: Cấm nói "đã huỷ xong" khi chưa qua `ProposalService.approve`.
  - `moved`: Cấm nói "đã dời xong" khi chưa xác nhận.
- **`fallback`**: Câu chốt an toàn được code sinh sẵn từ trước. Nếu mô hình Conversation Agent gặp sự cố hoặc vi phạm guardrails, hệ thống lập tức sử dụng câu `fallback` này để phản hồi cho khách.

---

## 6. State Machine của Write-Fence & Quản lý Đề xuất / Consent

Hệ thống Salona Booking tuân thủ nguyên tắc bảo mật tối thượng: **LLM không bao giờ có quyền ghi trực tiếp vào cơ sở dữ liệu lịch hẹn (`appointments`)**.

### 6.1 Vòng đời của một Đề xuất Lịch hẹn (Proposal Lifecycle)

```mermaid
stateDiagram-v2
    [*] --> Pending: Specialist gọi propose_appointment / cancel_appointment<br/>(Lưu vào collection proposals)

    Pending --> Approving: Khách đồng ý qua ButtonReply<br/>HOẶC decide_pending(label="approve")<br/>[Atomic Claim: status=pending -> approving]
    
    Pending --> Rejected: Khách bấm "Không"<br/>HOẶC decide_pending(label="reject")
    
    Pending --> Superseded: Khách đổi giờ (label="change")<br/>HOẶC có proposal mới tạo đè lên
    
    Pending --> Expired: Quá thời hạn 10 phút chưa xác nhận

    Approving --> Approved: ProposalService.approve()<br/>- Ghi appointment vào DB appointments<br/>- Ghi turn_ledger (announced=False)<br/>[Ghi lịch THỰC THẾ thành công]
    
    Approving --> Failed: SlotTakenError (Trùng slot với người khác)<br/>HOẶC TARGET_CHANGED (Lịch dời đã bị thay đổi)
    
    Approving --> Failed: Treo ở approving > 60 giây<br/>(Hệ thống tự động đánh dấu failed khi đọc)

    Approved --> [*]
    Rejected --> [*]
    Superseded --> [*]
    Expired --> [*]
    Failed --> [*]
```

### 6.2 Giải cứu Tranh chấp và Bẫy C1 (Lịch đã Ghi nhưng Khách Chưa Nghe)

**Bẫy C1 là gì?** Khi khách đồng ý đặt lịch, `ProposalService.approve` đã ghi dữ liệu vào bảng `appointments`, nhưng ngay sau đó tiến trình bị ngắt mạng, server sập hoặc Conversation Agent bị timeout. Khách hàng chưa nhận được câu "Đã đặt lịch cho anh thành công".

**Cơ chế Giải cứu Tự động (Recovery Mechanism):**
1. Ngay khi `ProposalService.approve` thành công trong vòng tool, hệ thống ghi ngay một bản ghi vào `turn_ledger` với cờ `announced = False`.
2. Khi tiến trình hoàn thành trọn vẹn và Socket.IO phát sự kiện `complete`, cờ này mới được lật thành `announced = True`.
3. Nếu lượt bị đứt gãy giữa chừng, ở lượt nhắn tin kế tiếp của khách, **State Resolver** đọc `turn_ledger`, phát hiện có `unannounced outcome` và kích hoạt ngay **Luật R0**.
4. **Luật R0** bắt buộc tiêm sự thật: *"Lịch hẹn lúc 14:00 chiều mai của anh đã được ghi nhận thành công từ trước đó"* vào trường `must_say` của `ReplyBrief`, đảm bảo khách luôn được thông báo về lịch hẹn của mình.

```mermaid
sequenceDiagram
    autonumber
    actor Khach as Khách hàng
    participant System as Hệ thống (Lượt N)
    participant Ledger as Collection: turn_ledger
    participant DB as Collection: appointments
    participant NextTurn as Hệ thống (Lượt N+1 - Giải cứu R0)

    Khach->>System: "Ừ chốt giờ đó đi em"
    System->>DB: ProposalService.approve() -> Ghi lịch thành công!
    System->>Ledger: Ghi nhận turn_ledger: {outcome: "approved", announced: False}
    
    Note over System: SỰ CỐ: Mất kết nối / Crash server / Timeout!<br/>Khách chưa nghe câu xác nhận!
    
    Note over Khach: 5 phút sau, khách nhắn tin lại
    Khach->>NextTurn: "Alo em ơi?"
    NextTurn->>Ledger: State Resolver quét ledger lượt trước
    Note over NextTurn: Phát hiện: outcome="approved" VÀ announced=False!<br/>KÍCH HOẠT NHÁNH R0!
    NextTurn->>NextTurn: Dựng ReplyBrief.must_say = ["Lịch hẹn 14:00 chiều mai đã đặt thành công"]
    NextTurn-->>Khach: "Dạ chào anh, lịch hẹn lúc 2h chiều mai của anh tiệm đã ghi nhận thành công rồi ạ! Anh cần hỗ trợ thêm gì không ạ?"
```

---

## 7. Mô hình Dữ liệu & Trạng thái (Data Architecture & Persistence)

### 7.1 Sơ đồ Phân tầng Lưu trữ (Storage Tiers)

Hệ thống phân tách rạch ròi giữa 3 tầng dữ liệu với vòng đời khác nhau:
1. **Checkpoint State (LangGraph)**: Lưu trạng thái thực thi từng bước trong RAM hoặc MongoDB checkpoint, có TTL 7 ngày. Đây là dữ liệu tạm, không phải nguồn chân lý (not source of truth).
2. **Turn Ledger (`turn_ledger`)**: Lưu trữ nhật ký kiểm toán (audit log) có kiểu của từng lượt hội thoại. TTL 7 ngày. Là nguồn tham chiếu tin cậy cho State Resolver đọc trạng thái lượt trước.
3. **Business Collections (MongoDB Core)**: `appointments`, `proposals`, `conversations`, `users`. Đây là nguồn dữ liệu nghiệp vụ trường tồn, quản lý trạng thái thực tế của tiệm và khách hàng.

```mermaid
classDiagram
    class TurnContext {
        +str turn_id
        +datetime asked_at
        +datetime now_local
        +date today
        +CustomerRef customer
        +ShopStatusView shop_status
        +ShopHours shop_hours
        +List~AppointmentRef~ upcoming
        +PendingProposal pending
        +UnannouncedOutcome unannounced
        +List~QueuedRequest~ queued_requests
        +OpenQuestion open_question
        +bool answers_open_question_hint
        +List~PreviousTurn~ previous_turns
        +GoalProgress goal
    }

    class PendingProposal {
        +str id
        +Literal kind
        +datetime start_at
        +str spoken
        +datetime expires_at
        +int unclear_count
        +AppointmentRef target
    }

    class TaskSpec {
        +str task_id
        +Literal agent
        +Literal action
        +Union~AppointmentArgs, ShopArgs~ args
    }

    class TaskResult {
        +str task_id
        +str agent
        +str action
        +Literal status
        +Any data
        +List~str~ missing
        +str error_code
        +bool retryable
        +int attempts
    }

    class ReplyBrief {
        +Literal kind
        +str address
        +List~Fact~ facts
        +ClarifyRequest ask
        +List~str~ must_say
        +List~str~ must_not_claim
        +Literal tone_context
        +str fallback
    }

    class TurnLedger {
        +str turn_id
        +str user_id
        +str route
        +List~CallRecord~ calls
        +PendingOutcome pending_outcome
        +str ended_by
        +str reply_kind
        +bool announced
        +datetime created_at
    }

    class GoalProgress {
        +str goal_id
        +int turns_without_progress
        +str last_marker
    }

    TurnContext --> PendingProposal : chứa
    TurnContext --> GoalProgress : theo dõi
    TaskSpec --> TaskResult : thực thi trả về
    ReplyBrief <-- TaskResult : tổng hợp sự thật
    TurnLedger --> PendingProposal : ghi nhận outcome
```

---

## 8. Cơ chế Phục hồi Sự cố & Giới hạn Ngân sách (Resilience & Limits)

### 8.1 Sơ đồ Phục hồi Lỗi Toàn diện (Fault Recovery Matrix)

```mermaid
flowchart TD
    classDef errNode fill:#ffebee,stroke:#c62828,stroke-width:2px;
    classDef handleNode fill:#e8f5e9,stroke:#2e7d32,stroke-width:2px;
    classDef endNode fill:#e0f2f1,stroke:#00695c,stroke-width:2px;

    Issue([Sự cố Phát sinh]):::errNode --> CheckType{Loại sự cố?}:::errNode

    CheckType -- "1. Orchestrator Timeout vòng đầu" --> Fallback1["ended_by = 'error'<br/>pending coi như 'kept'<br/>Trả câu phản hồi cứng an toàn"]:::handleNode
    
    CheckType -- "2. Tool Call sai quy tắc / lạ ID" --> Reject2["Trả ToolMessage lỗi có hint cấu trúc<br/>Orchestrator tự sửa ở vòng sau"]:::handleNode

    CheckType -- "3. Specialist Lỗi / Tool Timeout" --> Retry3{"Có quyền retry?<br/>(attempts < 1)"}:::errNode
    Retry3 -- Có --> DoRetry["Thực hiện 1 lần retry nội bộ specialist"]:::handleNode
    Retry3 -- Không --> FailSpecialist["Trả TaskResult(status='failed') về Orchestrator"]:::handleNode

    CheckType -- "4. Shop Info Bịa đặt (Fact Mismatch)" --> FactCheck["Code phát hiện lệch dữ liệu gốc<br/>Đánh dấu failed, fact_mismatch (Không retry)"]:::handleNode

    CheckType -- "5. Vòng toàn từ chối (All-rejected)" --> RejectRound["Không tính vào 8 vòng tối đa<br/>Tăng bộ đếm no_progress_streak += 1"]:::handleNode

    CheckType -- "6. Bế tắc lặp lại (Streak ≥ 2)" --> CircuitBreak["CẮT MẠCH: Đặt force_end = True<br/>Ép kết thúc vòng điều phối ngay lập tức"]:::handleNode

    Fallback1 --> EndRes([Hoàn tất xử lý sự cố]):::endNode
    FailSpecialist --> EndRes
    FactCheck --> EndRes
    CircuitBreak --> EndRes
```

### 8.2 Giới hạn Ngân sách 3 Tầng (3-Tier Budget & Rate Limit Architecture)

Hệ thống áp dụng 3 tầng kiểm soát tài nguyên độc lập nhằm ngăn chặn triệt để tình trạng tiêu tốn token vô hạn và tấn công từ chối dịch vụ:

```
┌────────────────────────────────────────────────────────────────────────┐
│ TẦNG 1: KIỂM SOÁT TIẾN ĐỘ TRONG MỘT LƯỢT (INTRA-TURN LIMITS)          │
│ • orch_max_tool_rounds = 8 (Chỉ đếm các vòng có ít nhất 1 tool chạy)   │
│ • orch_max_calls_per_round = 3 (Chặn call thứ 4: too_many_calls)       │
│ • max_retries_per_task = 1 (Chỉ retry 1 lần khi lỗi mạng tạm thời)     │
│ • no_progress_streak = 2 (Hai vòng không tiến triển -> force_end)      │
│ • proposals_created = 1 / lượt (Orchestrator) & 1 / task (Specialist)  │
│ • Trần thời gian toàn bộ lượt: 150 giây                                │
└──────────────────────────────────┬─────────────────────────────────────┘
                                   │
                                   ▼
┌────────────────────────────────────────────────────────────────────────┐
│ TẦNG 2: KIỂM SOÁT TIẾN ĐỘ XUYÊN LƯỢT (CROSS-TURN GOAL PROGRESS)        │
│ • Quản lý bằng Pydantic model: GoalProgress                            │
│ • Mở goal_id khi có yêu cầu đặt/đổi lịch mới                           │
│ • turns_without_progress += 1 nếu lượt không sinh ra marker tiến triển │
│ • Ngưỡng tối đa: 3 lượt bế tắc -> Kích hoạt ReplyBrief(limit_reached)   │
│   (Hướng dẫn khách hàng gọi hotline tiệm qua settings.shop_phone)      │
│ • KHÔNG khoá tài khoản khách hàng, tự động reset khi có tiến triển mới │
└──────────────────────────────────┬─────────────────────────────────────┘
                                   │
                                   ▼
┌────────────────────────────────────────────────────────────────────────┐
│ TẦNG 3: TRẦN CHI PHÍ & RATE LIMIT KỸ THUẬT (TECHNICAL RATE LIMITS)     │
│ • RateLimitService: chat_max_per_hour = 30 tin nhắn / giờ              │
│ • Nút bấm Đồng ý / Không (confirm_proposal) KHÔNG tính vào rate limit  │
│ • Trần chi phí mô hình: llm_calls_per_user_per_hour = 150 calls / giờ │
│ • Khi vượt trần chi phí: Không từ chối phục vụ mà tự động ép trần      │
│   orch_max_tool_rounds từ 8 xuống còn 2 vòng/lượt                      │
└────────────────────────────────────────────────────────────────────────┘
```

---

## 9. Danh mục Kiểm tra Thực thi (Architectural Implementation Checklist)

Dành cho Đội ngũ Kỹ sư Phát triển khi hiện thực hóa mã nguồn:

- [ ] **Mô hình Hybrid**: Tạo vỏ ngoài LangGraph StateGraph kết hợp `create_agent` cho Orchestrator, cấu hình `streaming=False` cho mọi subagent trừ Conversation Agent.
- [ ] **State Resolver**: Hiện thực hóa đầy đủ 6 nhánh quyết định từ R0 đến R5 bằng code Python tĩnh; không phụ thuộc vào LLM phân loại.
- [ ] **OrchestratorGuard**: Cài đặt hook `awrap_tool_call` với đầy đủ 11 luật chốt chặn kiểm soát (a) đến (k).
- [ ] **Chế độ Context**: Đảm bảo Specialist nhận `TaskSpec` độc lập (`Isolated Context`), không nhận lịch sử hội thoại thô; Conversation Agent chỉ nhận `ReplyBrief` (`Forked Context`).
- [ ] **Write-Fence**: Tuyệt đối không tạo công cụ ghi trực tiếp vào collection `appointments`; chỉ `ProposalService.approve` mới có thẩm quyền chuyển đổi từ proposal sang appointment.
- [ ] **Turn Ledger & C1 Recovery**: Đảm bảo ghi nhận kết quả `approved` với `announced=False` ngay trong vòng tool, và kích hoạt R0 giải cứu ở lượt kế tiếp nếu có sự cố ngắt mạng.
- [ ] **Giới hạn & Ngắt mạch**: Hiện thực hóa `ToolRoundLimit` (8 vòng), `no_progress_streak` (ngưỡng 2), `GoalProgress` (ngưỡng 3) và cơ chế giảm trần vòng lặp khi vượt rate limit chi phí.
