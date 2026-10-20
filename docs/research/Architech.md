# Kiến trúc đích — Salona Booking trên nền Harness B

Bản sửa ngày 2026-10-20 theo các quyết định đã chốt khi brainstorm:

- Xây trên nhánh `feat/langchain-harness` (Harness B), không viết lại từ đầu.
- Không có Time Agent. `parse_time` là tool của Booking Agent; thiếu buổi/ngày/giờ
  thì agent hỏi lại khách bằng câu thường. Giờ hiện tại đến từ Context Builder.
- Gọi khách là "anh chị" là chủ ý; chỉ đổi khi khách **tự xưng** ("chị làm móng").
- Nút **Đồng ý** / **Không** dưới câu hỏi chốt lịch. Gõ chữ vẫn được, nhưng là đường phụ.
- Redis làm trong đợt này. Trí nhớ: mở rộng `ConversationState` trong phiên (mục 3)
  + hồ sơ khách do code dựng qua nhiều phiên; **không** có memory do LLM tự trích qua phiên.
- Mongo chạy đơn lẻ: không có transaction, dùng `find_one_and_update` + index unique partial.

Ô vàng là chỗ chặn an toàn. Nét đứt là đường lượt sau (khách trả lời đề xuất).

# 1. Kiến trúc tổng thể

```mermaid
flowchart TB
    USER["Khách"]
    UI["Web chat React<br/>Chữ + nút Đồng ý / Không"]

    subgraph API["1. API — FastAPI, 1 worker"]
        AUTH["JWT → user_id<br/>kiểm chủ thread"]
        INPUT["Validate + rate limit<br/>+ chống trùng message_id"]
    end

    subgraph GUARDS["2. Guardrails — code, không LLM"]
        IG["Intent & State Guard<br/>đúng 1 proposal pending, chưa hết hạn,<br/>giờ/ngày khách nhắc = giờ/ngày đề xuất"]
        TG["Tool Guard<br/>allowlist theo agent, kiểm tham số,<br/>không tool ghi, không gọi song song, tối đa 4 vòng"]
        OG["Output Guard + rewrite 1 lần<br/>xưng hô, định dạng giờ, tên tool, không lặp"]
    end

    subgraph CTX["3. Context Builder — code dựng, mỗi lượt"]
        CB["Giờ hiện tại VN · tên khách · cách gọi<br/>chủ tiệm rảnh/bận · lịch sắp tới kèm id<br/>câu vừa trả lời · hồ sơ khách (mục 4)"]
    end

    subgraph ORCH["4. LangGraph"]
        SUP["Supervisor<br/>1 lượt LLM: ý định + nhãn xác nhận<br/>approve / reject / change / unclear<br/>reasoning low, không stream"]
        SOCIAL["Social Agent<br/>0 tool"]
        BOOKING["Booking Agent<br/>parse_time, find_free_slots, list_my_appointments,<br/>propose_appointment (kind book/move), cancel_appointment"]
        SHOP["Shop Agent<br/>get_shop_status, get_shop_hours"]
        PHRASE["phrase<br/>viết câu chốt từ số liệu DB"]
    end

    subgraph SERVICES["5. Business Services — không LLM"]
        AVAIL["AvailabilityService<br/>chỉ đọc"]
        PROPOSAL["ProposalService<br/>create: kiểm giờ, kiểm trùng → PENDING 10 phút<br/>approve: tìm và sửa trong 1 lệnh,<br/>chỉ lệnh đầu tiên thành công"]
        APPT["AppointmentService<br/>create / cancel / reschedule"]
        MEM["ConversationStateService<br/>nén phiên → summary + slots mở rộng<br/>+ lưu cách khách tự xưng"]
    end

    subgraph DATA["6. Lưu trữ"]
        MONGO[("MongoDB<br/>users (+ self_address, staff_note)<br/>appointments · proposals · conversations<br/>checkpoints (TTL 7 ngày) · rate_limit")]
        REDIS[("Redis<br/>dedupe message_id (TTL)<br/>khóa 1 lượt / user<br/>cache giờ mở cửa, trạng thái tiệm")]
    end

    AZURE["Azure OpenAI gpt-5.4-mini"]

    USER --> UI --> AUTH --> INPUT
    INPUT --> CB --> SUP
    SUP --> SOCIAL
    SUP --> BOOKING
    SUP --> SHOP
    BOOKING --> TG
    SHOP --> TG
    TG --> AVAIL
    TG --> PROPOSAL
    SUP --> AZURE
    BOOKING --> AZURE
    SOCIAL --> AZURE
    SHOP --> AZURE

    INPUT -.->|"Nút Đồng ý + proposal_id"| PROPOSAL
    INPUT -.->|"Nút Không + proposal_id"| PROPOSAL
    SUP -.->|"Chữ: approve"| IG
    IG -.->|"Qua hết"| PROPOSAL
    IG -.->|"Không qua"| BOOKING
    SUP -.->|"reject / change / unclear"| BOOKING
    PROPOSAL -.->|"approve hợp lệ"| APPT
    APPT -.-> PHRASE

    SOCIAL --> OG
    BOOKING --> OG
    SHOP --> OG
    PHRASE --> OG
    OG --> UI
    OG --> MEM

    AVAIL --> MONGO
    PROPOSAL --> MONGO
    APPT --> MONGO
    MEM --> MONGO
    CB --> MONGO
    CB --> REDIS
    INPUT --> REDIS
    ORCH -.->|"checkpoint theo thread"| MONGO

    classDef guard fill:#fff3cd,stroke:#d6a329,color:#222
    classDef agent fill:#e6f1ff,stroke:#4787d4,color:#123
    classDef service fill:#e3f6ec,stroke:#37976b,color:#123
    classDef data fill:#f0eaff,stroke:#8664c5,color:#123
    class IG,TG,OG,PROPOSAL guard
    class SUP,SOCIAL,BOOKING,SHOP,PHRASE agent
    class AVAIL,APPT,MEM service
    class MONGO,REDIS data
```

Ghi chú so với code hiện có trên Harness B:

- Đã có: supervisor một lượt, ba agent + middleware, guard/rewrite, `proposals` với
  approve kiểu chiếm, lưới code + bộ phân loại, `interrupt()`, checkpointer Mongo,
  chống chạy song song cùng user.
- Mới: nút Đồng ý/Không (Socket.IO event mới), nhãn xác nhận gộp vào supervisor
  (thay bộ phân loại riêng), Redis,
  slots mở rộng trong `ConversationState`, hồ sơ khách trong Context Builder.
- Bỏ: Time Agent; danh sách từ khóa phủ quyết (chỉ giữ phép so giờ/ngày) — **quyết
  sau khi benchmark nhóm `edge` có lưới và không lưới**.

# 2. Luồng đặt / hủy / dời — từ tin nhắn đến lịch ghi

```mermaid
flowchart TD
    START(["Khách nhắn"]) --> DEDUPE{"message_id<br/>đã xử lý?"}
    DEDUPE -->|"Rồi"| REPLAY["Trả lại kết quả cũ"]
    DEDUPE -->|"Chưa"| PENDING_Q{"Thread có proposal<br/>đang chờ?"}

    PENDING_Q -->|"Không"| SUP["Supervisor: ý định"]
    SUP -->|"Đặt"| BOOK["Booking Agent gom dịch vụ, ngày, giờ<br/>parse_time thiếu gì → hỏi lại"]
    SUP -->|"Hủy"| FIND["list_my_appointments"]
    SUP -->|"Dời"| MOVE["list_my_appointments + parse_time giờ mới"]
    SUP -->|"Hỏi tiệm / tán gẫu"| OTHER["Shop / Social Agent"]

    FIND --> AMBIG{"1 lịch?"}
    AMBIG -->|"Nhiều"| ASK_TARGET["Hỏi lịch nào"]
    AMBIG -->|"1"| P_CANCEL["propose_cancel"]

    MOVE --> AMBIG2{"1 lịch?"}
    AMBIG2 -->|"Nhiều"| ASK_TARGET
    AMBIG2 -->|"1"| CHECK2["find_free_slots giờ mới"]
    CHECK2 -->|"Hết chỗ"| SUGGEST["Gợi ý giờ khác"]
    CHECK2 -->|"Còn"| P_MOVE["propose_appointment(appointment_id)<br/>→ proposal kind=move"]

    BOOK --> CHECK1["find_free_slots"]
    CHECK1 -->|"Hết chỗ"| SUGGEST
    CHECK1 -->|"Còn"| P_BOOK["propose_appointment"]

    P_BOOK --> PERSIST["ProposalService.create<br/>PENDING, hết hạn 10 phút,<br/>đè proposal cũ cùng thread → superseded"]
    P_CANCEL --> PERSIST
    P_MOVE --> PERSIST
    PERSIST --> ASK["Hỏi xác nhận + gửi proposal_id<br/>UI hiện nút Đồng ý / Không<br/>interrupt()"]

    ASK -.-> INPUT{"Khách trả lời"}
    PENDING_Q -.->|"Có"| INPUT

    INPUT -.->|"Nút Đồng ý"| APPROVE
    INPUT -.->|"Nút Không"| REJECT["proposal → rejected<br/>hỏi khách muốn đổi gì"]
    INPUT -.->|"Gõ chữ"| LABEL["Supervisor: nhãn xác nhận<br/>approve / reject / change / unclear"]

    LABEL -.->|"approve"| STATE["Intent & State Guard<br/>đúng 1 pending, chưa hết hạn,<br/>giờ/ngày trong câu = đề xuất"]
    LABEL -.->|"reject"| REJECT
    LABEL -.->|"change"| SUPERSEDE["proposal → superseded<br/>câu khách đi tiếp như tin mới"]
    LABEL -.->|"unclear / lỗi LLM"| CLARIFY["Hỏi lại, không ghi"]
    STATE -.->|"Qua"| APPROVE["ProposalService.approve<br/>tìm và sửa trong 1 lệnh (find_one_and_update):<br/>_id, user_id, status=pending, expires_at > now<br/>→ chỉ lệnh đầu tiên thành công"]
    STATE -.->|"Không qua"| SUPERSEDE
    SUPERSEDE -.-> SUP

    APPROVE -.->|"Chiếm được"| ACT{"Loại"}
    APPROVE -.->|"Không chiếm được<br/>(hết hạn / đã duyệt / không phải chủ)"| STALE["Báo đề xuất không còn,<br/>hỏi đặt lại"]
    ACT -.->|"Đặt"| CREATE["AppointmentService.create<br/>index unique partial chặn trùng slot"]
    ACT -.->|"Hủy"| CANCEL["AppointmentService.cancel<br/>lật status, không xóa"]
    ACT -.->|"Dời"| RESCHED["AppointmentService.reschedule<br/>create mới + cancel cũ"]
    CREATE -.->|"SlotTakenError"| SUGGEST
    CREATE -.-> PHRASE["phrase: câu chốt từ DB"]
    CANCEL -.-> PHRASE
    RESCHED -.-> PHRASE

    PHRASE --> OG["Output Guard"]
    REJECT --> OG
    CLARIFY --> OG
    STALE --> OG
    ASK_TARGET --> OG
    SUGGEST --> OG
    OTHER --> OG
    OG --> REPLY(["Trả lời khách"])

    classDef guard fill:#fff3cd,stroke:#d6a329,color:#222
    class STATE,APPROVE,PERSIST,DEDUPE guard
```

Luật bất biến của luồng này:

1. Không có tool nào ghi lịch. `propose_*` chỉ tạo PENDING.
2. Mọi đường ghi (`CREATE`/`CANCEL`/`RESCHED`) đều đi qua `APPROVE`, kể cả nút bấm.
3. `approve` chỉ là **ứng viên**. Chiếm proposal thất bại thì không ghi gì.
4. LLM lỗi, trả sai schema, hoặc `unclear` → không ghi, hỏi lại.
5. Một thread chỉ có một proposal pending. Proposal mới đè proposal cũ thành `superseded`.
6. Ghi lịch dựng từ `proposal` trong DB, không từ chuỗi model gõ.

# 3. Context Builder và hồ sơ khách

```mermaid
flowchart LR
    subgraph SRC["Nguồn — tất cả do code đọc"]
        CLOCK["Đồng hồ hệ thống<br/>giờ VN"]
        USERS[("users<br/>full_name, self_address, staff_note")]
        APPTS[("appointments<br/>sắp tới + 3 lần gần nhất")]
        SHOP[("shop status<br/>qua Redis cache")]
        CONV[("conversations<br/>summary + slots mở rộng (phiên hôm nay)<br/>+ summary 7 ngày gần nhất")]
        LAST["Câu bot vừa nói"]
    end

    CB["Context Builder"]

    CLOCK --> CB
    USERS --> CB
    APPTS --> CB
    SHOP --> CB
    CONV --> CB
    LAST --> CB

    CB --> BLOCK["Khối bối cảnh<br/>đứng ngay trước câu khách,<br/>ngoài system prompt (cache)"]
```

Thứ tự message model nhận (giữ nguyên bẫy #9): **System → [summary + slots] → lịch sử
hôm nay → khối bối cảnh → câu khách**. Dòng đầu khối bối cảnh luôn là giờ hiện tại.

## Trí nhớ — hai tầng

### Tầng 1: trong phiên — `ConversationState` mở rộng

Benchmark cho thấy bot quên 4 thứ ngay trong một phiên (LLM-09, compaction-long,
pending-het-han). Mở rộng `slots` hiện có (ý định, dịch vụ, ngày, giờ, giờ đã từ
chối) thêm 4 trường, LLM điền lúc nén, code lọc, không thêm lượt LLM:

| Trường | Lưu gì | Dùng để | Ví dụ |
|---|---|---|---|
| `stated` | điều khách đã nói rõ hoặc từ chối | không hỏi lại điều khách đã trả lời | "để tiệm chọn giờ", "chỉ rảnh sau 5 giờ" |
| `asked` | câu bot đã hỏi + đã có đáp chưa | câu có đáp thì thôi; khách phớt lờ 2 lần thì đổi cách | "hỏi dịch vụ: chưa đáp ×2" |
| `told` | điều bot đã khẳng định | dữ liệu mới mâu thuẫn thì phải nói "em xem lại thì…" | "chiều mai hết chỗ" |
| `agreed_at` | khách đã đồng ý đề xuất nào, lúc nào | viết câu cho đúng khi đề xuất hết hạn; **không** là căn cứ ghi | "đồng ý 15:00 18/10 lúc 14:02" |

Luật: mỗi trường tối đa 5 mục, mỗi mục ≤ 60 ký tự, code loại mục chứa số điện
thoại/địa chỉ/câu mệnh lệnh. Vào khối `[summary + slots]` sẵn có.

### Tầng 2: qua nhiều phiên — hồ sơ khách do code dựng

| Lưu gì | Ai ghi | Ở đâu | Ví dụ |
|---|---|---|---|
| cách khách tự xưng | code bắt khi khách tự xưng trong câu | `users.self_address` | "chị" → gọi "chị Lan" từ đó |
| ghi chú nhân viên | admin nhập tay | `users.staff_note` | "đi lại khó, cần ghế gần cửa" |
| 3 lần đến gần nhất | truy vấn `appointments` | không lưu thêm | "12/10 làm móng gel" |
| việc dang dở | summary của 7 ngày gần nhất | `conversations` sẵn có | "hôm qua hỏi giá gel, chưa đặt" |

Không có tầng memory do LLM tự trích qua nhiều phiên trong đợt này: chưa có bằng
chứng cần, và nhớ sai còn tệ hơn không nhớ. Xem lại sau khi chạy thật.

# 4. Redis — dùng cho gì, không dùng cho gì

| Dùng | Lý do |
|---|---|
| Dedupe `message_id` với TTL 24 giờ | Socket.IO gửi lại không tạo proposal đôi |
| Khóa "tin đang xử lý" `turn:{user_id}:{message_id}` | cùng `message_id` gửi lại khi lượt còn chạy → bỏ qua, không huỷ lượt. Tin KHÁC vẫn huỷ lượt cũ như hiện nay (`_ACTIVE_TURNS`) |
| Cache giờ mở cửa, trạng thái tiệm (TTL ngắn) | bớt 2 truy vấn Mongo mỗi lượt |

| Không dùng | Lý do |
|---|---|
| Lưu proposal, lịch, memory | Mongo là nguồn sự thật duy nhất |
| Rate limit | đã nằm trong Mongo, không đổi |
| Checkpointer | đã là `MongoDBSaver` trong DB app |

Redis sập thì app vẫn chạy: dedupe và khóa lùi về Mongo (`message_inbox` + biến tiến
trình), cache bỏ qua. Không fail-hard như Mongo.

# 5. Tự nhiên hơn — phần sơ đồ không vẽ được

Benchmark: tự nhiên 1.98/5, giọng máy 2.67, lặp ý 2.82. Ba việc, đo lại sau mỗi việc:

1. **Câu chốt** trong `phrase`: đổi khung câu theo ngữ cảnh (lần đầu / khách quen /
   vừa đổi giờ), không còn một mẫu "Dạ em đã giữ lịch cho anh chị vào … ạ".
2. **Xưng hô**: gọi theo cách khách tự xưng, lưu vào `users.self_address`, chưa tự xưng
   thì giữ "anh chị". Sửa rubric judge cho khớp ý đồ này.
3. **Model**: chạy benchmark với deployment lớn hơn `gpt-5.4-mini` để biết model là
   nút thắt tới đâu, trước khi tốn công vào prompt.

# 6. Cổng đo

So với baseline A (17/10, k=4) trên cùng bộ benchmark:

- Ghi sai nhóm `edge`: 28 → **0**.
- Nhóm `edge` pass: 30% → ≥ 90%. Nhóm `llm` không thấp hơn 96% quá nhiễu.
- `first_token_ms` trung vị không tăng quá 500 ms so với A (6.8 s).
- Judge `tu_nhien` ≥ 3.0, `giong_may` ≥ 3.5 sau mục 5.
- Fault 12/12, không lộ lỗi kỹ thuật, không lộ tên tool.
