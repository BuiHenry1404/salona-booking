# Kiến trúc đích trên nền Harness B — thiết kế

Ngày: 2026-10-20. Nhánh: `feat/target-architecture` (tách từ `main` @ `cdc7e35`,
tức Harness B đã gộp). Sơ đồ: `docs/research/Architech.md` (bản 20/10). Thước đo:
`bench/` trên nhánh `bench/harness-benchmark`.

## Vì sao

Benchmark 17/10 (A, k=4) và 20/10 (A vs B, 3 happy + 5 edge, k=2) cho ba kết luận:

1. **Ghi sai lịch đã về 0 trên B** (A: 8/16 lượt edge ghi sai, B: 0/16). Phần an toàn
   của Harness B làm đúng việc của nó. Giữ, không làm lại.
2. **Bot vẫn nói như máy.** Judge: tự nhiên ≈ 2.4/5, giọng máy 2.7, xưng hô 2.5, lặp ý
   2.8–3.4. Đổi harness không đổi con số này (A và B ngang nhau). Nguyên nhân nằm ở
   câu chốt rập khuôn trong `phrase`, cách gọi "anh chị" bị judge trừ điểm, slots
   quá hẹp nên bot quên điều khách vừa nói trong cùng phiên, và model nhỏ.
3. **Lượt xác nhận chậm hơn 2,5 giây** trên B (4,3 s so với 1,8 s của A) vì regex được
   thay bằng LLM phân loại + LLM viết câu chốt. Đó là giá của an toàn; nút bấm trả
   lại phần lớn thời gian này.

Thêm một lỗi thật lộ ra ở LLM-02 (B, 1/2 lượt): model gọi `find_free_slots` rồi hỏi
"giữ 2 giờ chiều được không?" mà **không gọi `propose_appointment`**. Khách "Ok em"
→ không có đề xuất chờ → supervisor xếp vào `social` → bot chào tạm biệt, lịch mất.

## Người dùng đã quyết

- Xây trên Harness B (giờ là `main`), không viết lại. Bám docs LangChain/LangGraph.
- **Không có Time Agent.** `parse_time` là tool của Booking Agent; thiếu buổi/ngày/giờ
  thì agent hỏi lại bằng câu thường (không `interrupt`). Giờ hiện tại đến từ khối bối cảnh.
- **Nút Đồng ý / Không** dưới câu hỏi chốt lịch trong web chat. Gõ chữ vẫn được.
- **"Anh chị" là chủ ý**, không đoán giới tính từ tên. Chỉ đổi khi khách tự xưng.
- **Redis** làm trong đợt này (dedupe, khóa, cache). Không fail-hard.
- **Trí nhớ**: mở rộng `ConversationState` trong phiên + hồ sơ khách do code dựng qua
  nhiều phiên. **Không** có memory do LLM tự trích qua phiên.
- Mongo đơn lẻ: không transaction; `find_one_and_update` + index unique partial.
- Thứ tự làm việc: an toàn → tốc độ → tự nhiên. Mỗi bước đo bằng benchmark.

## Mục tiêu (cổng đo, so với baseline B `harness-b/align` cùng bộ kịch bản, k=4)

- Ghi sai nhóm `edge` giữ **0**. LLM-02 đạt k/k.
- `first_token_ms` lượt xác nhận bằng nút ≤ 1,5 s (không gọi LLM phân loại); gõ chữ
  không tăng so với B.
- Judge `tu_nhien` ≥ 3.0, `giong_may` ≥ 3.5, `lap_y` ≥ 3.5 (sau khi sửa rubric xưng hô).
- Fault 12/12; `tool_name_leak` 0; `claim_without_write` 0.
- Tin nhắn Socket.IO gửi lại không tạo đề xuất hay lịch thứ hai.

## Không làm

- Transaction Mongo / replica set. Memory do LLM trích qua phiên. Episodic Memory
  theo nghĩa cũ. Đổi cấu trúc thư mục `app/`. Telegram cho khách (Plan 3 riêng).
- Hạ tầng prod (compose prod, HTTPS, S-01…S-07, backup, CI): **spec riêng**, làm
  song song, không chặn spec này.

## Phản mục tiêu (làm xong mà có cái này là hỏng)

- Bất kỳ đường ghi lịch nào không đi qua `ProposalService.approve`.
- Nút Đồng ý bỏ qua kiểm chủ sở hữu / hạn / trạng thái.
- Lượt chat phải chờ Redis; Redis sập làm app sập.
- Bot gọi "anh"/"chị" khi khách chưa tự xưng.
- Thêm một lượt gọi LLM vào mỗi tin nhắn.

## Ràng buộc (giữ nguyên từ AGENTS.md / CONTEXT.md)

1 worker; config chỉ từ `.env`; logic đặt lịch ở `app/services/`; agent không có tool
ghi; chỉ stream tag `respond`; khối bối cảnh đứng ngay trước câu khách, ngoài system
prompt, dòng đầu là giờ hiện tại; thứ tự message *System → [summary+slots] → lịch sử →
khối bối cảnh → câu khách*; cancel = lật `status`; prompt tiếng Anh, không ví dụ tiếng
Việt cho luật phân loại; docs/comment tiếng Việt, commit tiếng Anh.

## Thiết kế

### 1. Xác nhận: ba lối vào, một lối ra

Mọi đường ghi (`create`/`cancel`/`reschedule`) đều đi qua `ProposalService.approve`
(tìm-và-sửa trong một lệnh: `_id, user_id, status=pending, expires_at > now`; chỉ lệnh
đầu tiên thành công). Ba lối vào:

| Lối vào | Đi đâu | Gọi LLM? |
|---|---|---|
| Nút **Đồng ý** (`proposal_id`) | `approve` thẳng → `AppointmentService` → câu chốt **bằng code** (mẫu theo `context`, mục 5) | không |
| Nút **Không** (`proposal_id`) | `proposal → rejected`; **code** viết câu hỏi tiếp ("Dạ, anh chị muốn đổi giờ khác hay đổi dịch vụ ạ?"), không chạy agent vì không có câu khách | không |
| Gõ chữ | nhãn `approve/reject/change/unclear` → `approve` chỉ khi nhãn `approve` **và** qua Intent & State Guard | phân loại + phrase |

**Intent & State Guard** (code): đúng một proposal pending của user, chưa hết hạn, và
giờ/ngày khách nhắc trong câu (nếu có) trùng đề xuất. Không qua → `superseded`, câu
khách đi tiếp như tin mới vào booking.

**Nhãn xác nhận**: structured output `Literal["approve","reject","change","unclear"]`,
`reasoning_effort="low"`, `temperature` mặc định (Azure từ chối `temperature=0` khi
bật reasoning), `streaming=False`, timeout 8 s, mọi lỗi → `unclear`. Về mặt code đây
là `reply.classify_reply` hiện có, chuyển vào module `supervisor` thành chế độ
`label` để supervisor là nơi duy nhất đọc ý khách; không thêm lượt LLM.

**Lưới phủ quyết** (`reply.py`): giữ hai phần code chắc chắn — so giờ/ngày với đề xuất
và `is_go_ahead` cho dời/hủy. Danh sách từ khóa phủ định/hoãn/hỏi để sau cờ
`CONFIRM_VETO_MODE=full|time_only` (`.env`), mặc định `full`. Chạy benchmark `edge`
k=4 ở cả hai chế độ; `time_only` không ghi sai và hỏi lại thừa ít hơn thì đổi mặc định
và xóa danh sách từ khóa ở đợt sau.

**Giao thức Socket.IO**: thêm event vào `confirm_proposal {proposal_id, decision:
"yes"|"no", message_id}`; thêm trường vào `complete`: `proposal {id, expires_at,
kind, when_spoken}` khi lượt kết thúc bằng câu hỏi chốt (frontend dựa vào đây để hiện
nút). Các event cũ giữ nguyên tên và payload. Resume vào node `confirm` bằng
`Command(resume={"button": decision, "proposal_id": ...})`; `confirm` nhận cả chuỗi
(gõ chữ) lẫn dict (nút).

**Nút cũng là một tin nhắn**: server ghi `user: "Đồng ý"` / `user: "Không"` vào
`conversations` và thread checkpointer (để màn lịch sử, cửa sổ model, nén và transcript
judge đều thấy), nhưng **không** tính vào `chat_max_per_hour` (đường nút không gọi LLM).
Nút hết hạn (`approve` trả `None`) → câu code "Đề xuất lúc nãy hết hạn rồi, anh chị
nhắn lại giờ giúp em nhé", không ghi gì.

**Tin gửi lại khi lượt còn chạy**: cùng `message_id` → bỏ qua (giữ khóa
`turn:{user_id}:{message_id}` tới khi `complete`, rồi trả lại `complete` đã cache cho
mọi lần gửi lại trong 24 h). `message_id` **khác** → vẫn huỷ lượt cũ như hiện nay
(`_ACTIVE_TURNS`, events.py). Hai cơ chế không thay nhau.

### 2. Sửa lỗi LLM-02

Hai lớp, cả hai đều phải có:

- **Prompt booking**: câu hỏi "giữ X được không?" chỉ được hỏi **sau** khi
  `propose_appointment` đã trả về; không có đề xuất thì không hỏi dạng có/không.
  Guard thêm luật code: lượt không có `proposal_id` mà câu cuối là câu hỏi có/không về
  một giờ cụ thể → `violation: ask_without_proposal` → rewrite một lần thành câu mời
  khách chọn giờ.
- **Supervisor**: tin chỉ là khẳng định ngắn ("ok", "ừ", "được") mà câu bot trước đó
  thuộc agent `booking` → route `booking`, không bao giờ `social`. Code, không LLM.

Kịch bản benchmark mới `edge/ok-khong-de-xuat`: bot (scripted) hỏi có/không không kèm
đề xuất, khách "Ok em" → kỳ vọng: lịch được ghi **sau** một câu hỏi xác nhận thật, hoặc
bot hỏi giờ; không được chào tạm biệt.

### 3. Trí nhớ trong phiên: bộ phân tích câu khách + `ConversationState.slots` mở rộng

**Sửa 25/10/2026 (người dùng duyệt mockup "Bộ phân tích câu khách").** Thay cho việc
nén nền mỗi lượt, một **bộ phân tích** (`analyze_turn`) đọc câu khách ở đầu lượt và
trả bản phân tích có cấu trúc. Nó **thay** lượt LLM của supervisor (tin mới) và của
bộ phân loại câu xác nhận (lượt resume vào `confirm`) — mỗi lượt vẫn tối đa một lượt
LLM trước agent, không thêm lượt nào.

| Trường | Giá trị | Dùng vào | Code kiểm |
|---|---|---|---|
| `route` | booking · shop · social | chọn agent (thay supervisor) | ngoài 3 nhãn / lỗi → booking |
| `reply_to_proposal` | approve · reject · change · unclear · null | chỉ khi có đề xuất chờ (thay `classify_reply`) | lưới phủ quyết + `is_go_ahead` vẫn chạy TRƯỚC; null/lỗi → unclear; không bao giờ tự ghi |
| `service` | chuỗi ≤ 60 ký tự · null | slot "dịch vụ" ngay trong lượt | lọc như dưới |
| `time_text` | cụm giờ khách nói · null | gợi ý cho agent gọi `parse_time` | phải là chuỗi con của câu khách, không thì bỏ |
| `self_address` | chị · anh · null | mục 4 | chỉ nhận khi `detect_self_address` (regex) cũng ra cùng giá trị |
| `stated` | ≤ 2 mục | thêm vào slots `stated` ngay | lọc như dưới |
| `answers_question` | bool | đánh dấu câu bot vừa hỏi là đã đáp | chỉ có nghĩa khi câu bot trước kết thúc bằng `?` |

Gọi: structured output, `reasoning_effort="low"`, `streaming=False`, tag `analyze`,
trần 8 s; lỗi/quá giờ/sai schema → bản phân tích lùi an toàn (`route="booking"`,
`reply_to_proposal="unclear"` nếu có đề xuất, các trường khác rỗng). Lối tắt code
của supervisor (hàng chờ, người nhà/hoãn, "ok" ngắn sau lượt booking) vẫn chạy trước
và bỏ qua bộ phân tích. Đường nút bấm không gọi bộ phân tích.

`asked` do **code** dựng: câu bot cuối kết thúc bằng `?` → thêm/cập nhật mục
(`times += 1` nếu trùng câu hỏi trước); `answers_question=true` → `answered=true`.
`told` và `agreed_at` cũng do code ghi (`told` từ câu bot có khẳng định "hết chỗ/còn
chỗ"; `agreed_at` khi `approve` thành công). Nén nền giữ nguyên ngưỡng 800 token, chỉ
còn làm summary; không chạy mỗi lượt nữa. Mỗi trường slots mới:

| Trường | Lưu gì | Giới hạn |
|---|---|---|
| `stated` | điều khách đã nói rõ/từ chối ("để tiệm chọn giờ", "chỉ rảnh sau 5 giờ") | ≤5 mục, ≤60 ký tự |
| `asked` | câu bot đã hỏi + `answered: bool` + `times: int` | ≤5 |
| `told` | điều bot đã khẳng định ("chiều mai hết chỗ") | ≤5 |
| `agreed_at` | `{proposal_id, at}` khách đã đồng ý; **chỉ để viết câu**, không là căn cứ ghi | 1 |

Lọc: bỏ mục chứa số điện thoại, địa chỉ, câu mệnh lệnh ("hãy", "bỏ qua", "ignore");
`single_line`. Render vào khối `[summary + slots]` hiện có. Prompt booking/social dặn:
`asked.answered=true` → không hỏi lại; `times ≥ 2` → đổi cách hỏi; `told` mâu thuẫn dữ
liệu mới → nói "em xem lại thì…".

### 4. Hồ sơ khách qua nhiều phiên (code dựng)

Thêm vào `User`: `self_address: Optional[Literal["anh","chị"]]`, `staff_note:
Optional[str]` (admin nhập qua REST admin, ≤200 ký tự). Khối bối cảnh thêm:

- "Gọi khách là: chị Lan" khi `self_address` có; ngược lại "anh chị" (không ghép tên).
- "Ghi chú tiệm: …" khi có `staff_note`.
- "Khách đã đến: 12/10 làm móng gel; 28/9 làm tóc" — 3 lịch `status=done|booked` đã
  qua, truy vấn `appointments`, không lưu thêm.
- "Lần trước (hôm qua): …" — `summary` của phiên gần nhất trong 7 ngày nếu phiên đó
  còn ý định chưa xong (`slots.intent` có mà không có lịch mới sau đó). `conversations.
  state` hiện chỉ giữ MỘT bản của ngày hôm nay (ghi đè), nên thêm collection
  `conversation_sessions` {user_id, day, summary, slots, closed_at}, 1 doc/user/ngày,
  ghi lúc nén cuối cùng của ngày hoặc khi thread sang ngày mới, TTL 30 ngày. Không có
  doc → bỏ dòng.

`self_address` do **code** đặt: regex tất định trên câu khách bắt "chị/em/anh +
động từ" ở ngôi thứ nhất ("chị làm móng", "cho anh 3 giờ", "em muốn…" → khách xưng
"em" thì bot gọi "anh chị" vì không biết giới). Chỉ ghi khi chưa có; khách sửa ("gọi
chị là cô") → admin sửa tay, bot không tự đổi. Bỏ số điện thoại khỏi khối bối cảnh
(không tool nào cần).

**Tên có tiền tố** ("Cô Lan", "Bác Bảy" — 13/13 user seed hiện nay): tiền tố do
admin gõ là thông tin đã biết, không phải bot đoán → `derive_address` giữ nguyên và
dùng để **seed** `self_address` một lần (migration lúc khởi động: `cô/chị/bà → chị`,
`chú/anh/ông → anh`, `bác` → để trống). Tên thật nhập dạng đầy đủ ("Nguyễn Thị Lan")
không có tiền tố → "anh chị" cho tới khi khách tự xưng. Guard `_pronouns` đọc
`self_address`, không đọc tên nữa.

### 5. Tự nhiên hơn

- **`phrase`** (giữ là middleware): fact thêm `context ∈ {first_time, returning,
  changed_time, after_decline, cancel, move}`. Đường **gõ chữ**: LLM viết câu theo
  context, đọc `last_reply` để không lặp khung. Đường **nút**: câu chốt **bằng code**
  — mỗi context 3–4 mẫu câu, chọn mẫu chưa dùng trong phiên (đọc `previous_replies`);
  đây là cách duy nhất đạt cổng ≤ 1,5 s vì `write_phrase` mất 2–3 s. Fallback khi
  số liệu sai giữ nguyên chữ hiện có.
- **Guard**: giữ `claim`, `tool_name`; `repeat/pronoun/clock` để sau cờ
  `GUARD_VOICE_RULES=on|off` (mặc định `on`), đo benchmark; `pronoun` sửa theo mục 4
  (cho phép "chị Lan" khi `self_address="chị"`).
- **Rubric judge** (`bench/judge.py:38-40`): hiện bắt "anh/chị + tên" nên trừ điểm
  mọi câu "anh chị". Sửa: "anh chị" khi khách chưa tự xưng là **đúng**; trừ điểm khi
  khách đã tự xưng mà bot vẫn "anh chị", hoặc bot đoán giới. Judge nhận thêm
  `self_address` trong input để chấm được.
- **Model**: một lần chạy benchmark `llm` k=2 với deployment lớn hơn `gpt-5.4-mini`
  nếu có (xem "Để người dùng quyết"). Kết quả ghi vào CONTEXT.md; không đổi model
  mặc định trong spec này.
- `reasoning_effort="low"` cho supervisor (route + label) và timeparse. **Không** bật
  cho agent trả lời. Đã kiểm 19/10 trên deployment thật `gpt-5.4-mini`
  (`.env`, không phải `gpt-4.1-nano` trong `.env.example`): `low` trả 17 token
  reasoning, 1,7 s; Azure trả 400 nếu kèm `temperature=0.0` → các lượt bật reasoning
  không truyền temperature. `build_chat_model` thêm tham số `reasoning_effort`
  (mặc định `None` = không gửi).

### 6. Redis

| Dùng | Khóa | TTL | Fallback khi Redis lỗi |
|---|---|---|---|
| Dedupe tin đã xong | `msg:{user_id}:{message_id}` → payload `complete` | 24 h | `message_inbox` Mongo (unique `(user_id, message_id)`) |
| Tin đang xử lý | `turn:{user_id}:{message_id}` (SET NX) — gửi lại cùng id khi chưa `complete` → bỏ qua | 90 s | set trong tiến trình |
| (không đổi) | Tin **khác** id tới khi lượt cũ còn chạy → huỷ lượt cũ (`_ACTIVE_TURNS`, hiện có) | — | — |
| Cache | `shop:status`, `shop:hours` | 30 s / 10 phút | đọc Mongo |

Config: `REDIS_URL` (comment = tắt, như Langfuse). Khởi động: Redis không kết nối
được → `warning`, chạy fallback. `docker-compose.yml` thêm `redis:7-alpine`, không
publish port ra ngoài. Frontend sinh `message_id` (uuid) cho mỗi `send_message` và
`confirm_proposal`; thiếu `message_id` thì server tự sinh (không dedupe).

### 7. Frontend (React, `frontend/`)

Dưới câu hỏi chốt (khi `complete.proposal` có): hai nút lớn **Đồng ý** (chính) và
**Không** (phụ), chữ ≥ 18 px, cao ≥ 48 px, theo ràng buộc UI cho khách lớn tuổi trong
CONTEXT.md. Bấm → gửi `confirm_proposal`, hiện bong bóng "Đồng ý"/"Không" bên khách,
ẩn nút, chờ `complete` như lượt thường. Hết hạn (`expires_at`) → nút mờ, tooltip
"đề xuất đã hết hạn, nhắn lại giúp em". Gõ chữ khi nút đang hiện vẫn gửi
`send_message` như cũ.

### 8. Đường đi node (graph ngoài)

```
Tin mới:   START → supervisor(route) → booking|shop|social → record → confirm → END
                                                                       └ có proposal: interrupt()
Gõ chữ:    Command(resume=str) → confirm → supervisor(label)
             approve+guard qua → approve → AppointmentService → booking(phrase) → record → END
             reject → rejected → booking ; change/guard trượt → superseded → supervisor(route)
             unclear/lỗi → booking hỏi lại, không ghi
Nút:       Command(resume={"button","proposal_id"}) → confirm
             yes → approve → … → phrase ; no → rejected → booking
```

`phrase` vẫn là `before_agent` middleware của booking (sơ đồ vẽ node riêng cho dễ đọc).

## Benchmark

- Kịch bản mới: `edge/ok-khong-de-xuat` (mục 2), `edge/nut-dong-y` và `edge/nut-khong`
  (adapter gửi `confirm_proposal`), `edge/tin-gui-lai` (gửi cùng `message_id` hai lần:
  một lần sau `complete` → nhận lại cùng `complete`, một lần khi lượt còn chạy → lần
  hai bị bỏ qua; kỳ vọng đúng một đề xuất), `llm/khach-tu-xung-chi` (judge kiểm "chị
  Lan").
- Thứ tự đo: (1) baseline B `harness-b/align` k=4 đầy đủ; (2) sau mục 1+2; (3) sau mục
  3+4+5; (4) `CONFIRM_VETO_MODE` và `GUARD_VOICE_RULES` mỗi cờ một lần `edge` k=4.
- Rubric judge đổi ở mục 5 → chấm lại baseline bằng `rescore --rejudge` để so cùng thước.

## Đầu ra

Code trên `feat/target-architecture`, mỗi mục một mốc commit + tag `target/m<k>`;
`.env.example` thêm `REDIS_URL`, `CONFIRM_VETO_MODE`, `GUARD_VOICE_RULES`; CONTEXT.md
ghi kết quả đo và quyết định cờ; AGENTS.md cập nhật event Socket.IO mới; Architech.md
sửa theo cái đã làm nếu lệch.

## Để builder quyết

Tên trường/collection chính xác; vị trí regex tự xưng (`context.py` hay `core/text.py`);
cách frontend giữ `proposal_id` khi reload trang (gợi ý: đọc lại từ `GET
/conversations/me` nếu có, không thì chỉ gõ chữ); thứ tự thực hiện trong mỗi mục; kịch
bản scripted cho các ca mới; giá trị TTL cụ thể trong khoảng đã nêu.

## Quyết định của người viết sau vòng kiểm tra builder (20/10)

Bảy câu builder hỏi, người viết tự chốt (người dùng kiểm lại khi đọc):
1. Tin gửi lại khi lượt còn chạy: cùng `message_id` → bỏ qua; khác id → huỷ lượt cũ như cũ.
2. Nén nền mỗi lượt từ lượt 2 để slots mở rộng có dữ liệu (tốn thêm 1 lượt LLM nền/lượt).
3. Summary 7 ngày cần kho mới → `conversation_sessions`, TTL 30 ngày.
4. Tiền tố admin gõ ("Cô Lan") là đã biết → seed `self_address`; tên đầy đủ → "anh chị".
5. Nút là một tin nhắn: ghi nhật ký + thread, không tính rate limit chat.
6. Đường nút viết câu chốt bằng code (mẫu theo context) để đạt ≤ 1,5 s; LLM phrase chỉ cho đường gõ chữ.
7. Reasoning đã kiểm chạy được trên `gpt-5.4-mini`; thêm tham số vào `build_chat_model`.

## Để người dùng quyết (chưa có câu trả lời)

- Có deployment Azure lớn hơn `gpt-5.4-mini` để chạy thử không? Tên deployment?
- Server prod đặt ở đâu, bao nhiêu khách/ngày? (ảnh hưởng spec hạ tầng, không chặn spec này)