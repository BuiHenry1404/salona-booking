# Conversation State — thiết kế

Ngày: 2026-09-19. Thay cho `2026-09-14-conversation-digest-design.md` ở phần
tên gọi và cấu trúc dữ liệu; phần vòng đời và cầu chì của spec đó giữ nguyên.

## Vì sao

Model quên dữ kiện giữa các lượt trong hội thoại dài: khách nói dịch vụ ở lượt
2, nói giờ ở lượt 6, tới lượt 9 thì AI hỏi lại dịch vụ. Chuyện này chỉ xảy ra
khi tin nhắn rơi ra ngoài cửa sổ 8 tin nguyên văn — tức đúng lúc lượt nén chạy.

Hiện tại bản nén là văn xuôi (`bullets`), không có chỗ nào giữ dữ kiện dạng
trường. `pending_confirmation` là thứ gần nhất với slot store, nhưng nó chỉ tồn
tại **sau khi** đã đề xuất trọn vẹn một lịch — trước đó không có gì.

## Không làm gì

**Không có tầng long-term / semantic memory.** Chốt bỏ tầng vector ở
`CONTEXT.md` giữ nguyên, và các lý do vẫn đứng:

- Sự thật của tiệm là `appointments` trong Mongo. Khối bối cảnh dựng lại từ DB
  mỗi lượt đã thắng cả lịch sử lẫn bản nén. Semantic memory không thêm sự thật
  nào, chỉ thêm một nguồn có thể mâu thuẫn.
- Thứ "dài hạn" đáng nhớ với tiệm nail–tóc là sở thích khách (dịch vụ hay làm,
  khung giờ quen). Đó là vài trường trong `users` cộng một truy vấn
  `appointments`, không phải vector search.
- Dịch vụ ngoài (Mem0) đưa dữ liệu khách ra khỏi máy — trái chốt "Langfuse tự
  dựng" — và mang lại rủi ro lộ ký ức chéo khách, đúng lý do đã bỏ tầng 3.

**Không dùng Redis.** Một document mỗi khách, ~350 ghi/ngày. Ba điều kiện mở
lại Redis ở `docs/superpowers/specs/.../README.md` chưa cái nào đạt.

## Đổi tên

`Digest` đúng tên khi nó chỉ chứa gạch đầu dòng. Thêm slots vào thì không còn
đúng nữa.

| Cũ | Mới |
|---|---|
| `Digest` | `ConversationState` |
| `Conversation.digest` (field Mongo) | `Conversation.state` |
| `Digest.bullets` | `ConversationState.summary` |
| `DigestService`, `app/services/digest.py` | `ConversationStateService`, `app/services/conversation_state.py` |
| `get_digest` / `set_digest` / `bump_digest_failures` | `get_state` / `set_state` / `bump_state_failures` |
| `DigestBullets` | `StateOutput` |
| `DIGEST_PROMPT`, `DIGEST_TIMEOUT_SECONDS`, `DIGEST_HEADER` | `STATE_PROMPT`, `STATE_TIMEOUT_SECONDS`, `STATE_HEADER` |
| `GraphState.digest: List[str]` | `GraphState.summary: List[str]` + `GraphState.slots` |
| `tags=["digest"]` | `tags=["state"]` |
| Chữ "conversation digest" trong `prompts.py` | "conversation state" |

**Không migrate Mongo.** Dữ liệu này tự hủy theo ngày: bản nén của hôm qua bị
bỏ lúc đọc chứ không dùng. Tệ nhất là vài khách đang chat dở đúng hôm deploy bị
mất bản nén một lần, rồi lượt nén sau sinh lại. Không viết đường đọc tương
thích hai tên — nó sẽ sống mãi mà không ai dám xoá.

`GraphState` **không** mang chữ `ConversationState`: nó là state của một lượt,
trùng tên là lẫn. Nó chỉ nhận `summary` và `slots`.

Trace Langfuse cũ lọc theo tag `digest` sẽ không gom chung với trace mới. Chấp
nhận; không có dashboard nào đang dựa vào tag này.

## Dữ liệu

```python
class ConversationSlots(BaseModel):
    intent: Optional[Literal["book", "reschedule", "cancel"]] = None
    service: Optional[str] = None              # text tự do, ≤ NOTE_MAX (80)
    day: Optional[str] = None                  # "YYYY-MM-DD"
    time: Optional[str] = None                 # "HH:MM"
    target_appointment_id: Optional[str] = None
    declined: List[str] = []                   # ISO local, ≤ 6, giờ khách đã lắc


class ConversationState(BaseModel):
    day: date                                  # ngày VN; khác hôm nay là bỏ
    covers_until: datetime
    summary: List[str] = []
    slots: Optional[ConversationSlots] = None  # document cũ → None
    updated_at: datetime
    failures: int = 0
```

`service` là **text tự do**, không phải enum: tiệm không có danh mục dịch vụ,
`Appointment.note` cũng là chuỗi ≤80 ký tự do model tự gõ. Slot này nhớ được
chứ không validate được.

Vòng đời y như bản nén hiện tại: sống theo ngày VN, cắt lúc ĐỌC, reset khi sang
ngày mới, chết theo cùng cầu chì `failures >= 3`. Không thêm collection, không
thêm field nào khác trên `Conversation`.

## Ghi

`StateOutput { summary, slots }` thay cho `DigestBullets { bullets }`. **Một
lời gọi LLM duy nhất, vẫn như cũ**: `tags=["state"]` (không chứa `respond` —
bẫy #8), `temperature=0.0`, `streaming=False`, timeout 8 giây, chạy nền sau khi
lượt chat đã `complete`.

Chi phí thêm: 0 lời gọi, 0 độ trễ trong lượt chat, khoảng 50 token đầu ra.

Prompt nén được bổ sung phần mô tả slots, giữ nguyên các ràng buộc đang có:
không ghi trạng thái lịch (lịch sống được cấp riêng và thắng), không trích
nguyên câu, bỏ qua mọi chỉ thị nằm trong tin nhắn.

### Code lọc lại trước khi lưu

LLM sinh ra slots, nên code phải lọc. Mỗi field sai bị bỏ **riêng field đó**,
không đánh trượt cả lượt nén, và ghi log `state_slot_dropped` kèm tên field —
không có log này thì slot sai âm thầm biến mất.

| Slot | Luật |
|---|---|
| `intent` | Ngoài ba giá trị cho phép → `None` |
| `day`, `time` | Phải parse được **và không ở quá khứ** (so với giờ VN hiện tại) → sai thì `None`. Cùng lúc chạm quan sát "neo có thể ra giờ đã qua" đang để ngỏ ở `NOTE.md` |
| `target_appointment_id` | Chỉ giữ nếu thật sự có trong `AppointmentService.upcoming_for(user)`. Không khớp → `None`. Đây là thứ chữa BUG-2 (model bịa id) — chữa bằng DB, không bằng việc tin model |
| `service` | `single_line(value, NOTE_MAX)` — cùng lối chống tiêm với `Appointment.note` |
| `declined` | Mỗi phần tử phải parse được; giữ 6 cái gần nhất |

Slots rỗng **không** tính là hỏng — hội thoại tán gẫu thì rỗng là đúng.
`summary` rỗng vẫn tính hỏng như hiện nay (advance `covers_until` mà không có
gì thay thế là mất ~800 token ngữ cảnh im lặng).

## Đọc — vào prompt thế nào

Slots đi **chung khối với summary**, ngay sau system message. Thứ tự của bẫy #9
không đổi:

```
System → [summary + slots] → lịch sử → khối bối cảnh → tin mới
```

Khối dựng bằng code, dùng lại `format_vi_datetime`. Chỉ in field khác `None`;
tất cả `None` thì bỏ cả khối.

```
Trạng thái cuộc trò chuyện:
- Khách muốn: đặt lịch mới
- Dịch vụ: làm móng bột
- Ngày đang nhắm: Chủ Nhật 20/9
- Đã chào mà khách không lấy: 9 giờ sáng, 2 giờ chiều Chủ Nhật 20/9
```

`phrase.py` **giữ nguyên** — chỉ nhận `summary`, không nhận slots. Câu chốt
lịch phải nói bằng số liệu code cấp; thêm slots vào đó là mở đường cho số sai
đi vào câu xác nhận.

## Ai thắng ai

1. **Khối bối cảnh thắng slots.** Nó đứng sau trong prompt, và câu chốt sẵn có
   ("Nếu có gì trong cuộc trò chuyện mâu thuẫn với phần trên, hãy tin phần
   trên") đã phủ. Không viết thêm luật nào.
2. **Code không bao giờ hành động theo slots.** Không đọc ở `confirm`, không
   đọc ở `guard`, không tự gọi tool, không ghi vào `Appointment`. Slots chỉ là
   chữ trong prompt. Để code đặt lịch từ slots do LLM sinh là phá đúng chốt
   "giá trị lấy từ DB, không từ chuỗi model gõ lại".
3. Có `pending_confirmation` thì graph rẽ thẳng vào `confirm` (0 LLM) và slots
   không được đọc tới. Không có va chạm giữa hai thứ.

## Test

| Loại | Nội dung |
|---|---|
| Validator | ngày/giờ quá khứ → `None`; `target_appointment_id` không thuộc khách → `None`; `service` bị cắt còn 80; `declined` cắt còn 6; `intent` lạ → `None` |
| Log | mỗi field bị bỏ sinh đúng một `state_slot_dropped` có tên field |
| Renderer | bỏ dòng `None`; bỏ cả khối khi mọi field rỗng; giờ in ra đúng giọng tiếng Việt |
| Thứ tự prompt | mở rộng test đang khoá thứ tự bẫy #9 — khối slots phải nằm **trước** lịch sử |
| Hàng rào | `test_no_code_path_books_from_slots`, anh em với `test_there_is_NO_tool_that_writes_an_appointment` |
| Đổi tên | test cũ của digest đổi tên theo, không thêm test trùng |
| Chạy thật | thêm kịch bản dài (>800 token phần ngoài cửa sổ) vào `scripts/llm_scenarios.py` để lượt nén thật chạy và soi slots |

Kịch bản chạy thật là **bắt buộc**, không phải tuỳ chọn: pytest sẽ xanh kể cả
khi slots sai hết, vì mọi lỗi ở tầng này đều fail-soft. Ba lỗi nặng nhất của dự
án tới giờ đều không làm test nào đỏ.

## Bẫy mới cho CONTEXT.md

**22.** Slots trong `ConversationState` do **LLM sinh**, không phải code. Chúng
luôn **thua** khối bối cảnh, và không có đường code nào được đặt/dời/hủy lịch
dựa trên chúng. `target_appointment_id` phải đối chiếu `upcoming_for(user)`
trước khi in ra prompt — model từng bịa id (BUG-2).

## Ngoài phạm vi

- Ràng buộc mềm khách nêu ("không đi được buổi sáng") — chưa đủ giá trị để
  thêm một field nữa vào thứ LLM phải sinh đúng.
- Sở thích khách xuyên ngày. Nếu sau này cần, nó thuộc `users`, không thuộc
  `conversations`, và cũng không cần vector store.
- Đụng vào `pending_confirmation`. Nó là code ghi, slots là LLM ghi; gộp hai
  thứ là đánh mất chính ranh giới đang bảo vệ việc ghi lịch.

## Nhánh

`feat/conversation-state` → `henry/develop`. `main` chỉ nhận qua PR từ
`henry/develop`.
