# CONTEXT — App đặt lịch tiệm nail & tóc

Bàn giao từ phiên thiết kế (2026-08-06 → 2026-08-08) sang phiên hiện thực.
Đọc file này trước khi viết dòng code đầu tiên.

## Đang làm gì

App đặt lịch cho **một tiệm nail–tóc nhỏ ở Việt Nam**, người dùng chính là **khách lớn tuổi**. Dựng trên `fastapi-agent-template`.

Ba mặt tiếp xúc:

| Ai | Qua đâu |
|---|---|
| Khách | Web app React trên trình duyệt — chat với AI để đặt lịch, xem lịch của mình |
| Chủ tiệm | Cùng web app, giao diện admin — bật/tắt bận rảnh, xem lịch hôm nay, quản khách |
| Chủ tiệm | Bot Telegram — nhận báo lịch mới, tra lịch, đổi bận/rảnh bằng nút bấm |

**Trạng thái: Plan 1 (nền tảng backend) và Plan 2 (agent, memory, streaming) đã hiện thực xong.** REST API chạy được bằng `docker compose up`, đã test end-to-end qua HTTP: đăng nhập/JWT, phân quyền admin, đặt lịch, chống trùng giờ, giờ mở cửa, bận/rảnh, huỷ lịch. Agent LangGraph đã ráp xong: supervisor → StatusAgent/BookingAgent/refuse, nhánh tắt `confirm`, parser thời gian tiếng Việt, năm tool đặt lịch, và streaming token + sự kiện tool qua Socket.IO.

**Đã đo với model thật** (Azure `gpt-5.4-mini`): 279 test xanh, 10 test tiếng Việt gọi Azure thật xanh (`pytest tests/test_timeparse_llm.py -m llm`), và bài kiểm hai lượt chạy đúng như spec — lượt "mai 3h chiều làm tóc được không con" gọi `parse_time` rồi `propose_appointment`, hỏi lại xác nhận, **chưa** ghi lịch; lượt "ừ" không gọi tool nào và ghi lịch ngay; câu ngoài chủ đề bị `refuse` từ chối lịch sự.

**Langfuse tự dựng:** `docker compose -f docker-compose.langfuse.yml up -d` → http://localhost:3100 (`dev@salon.local` / `langfuse123`). Key project được tạo sẵn bằng `LANGFUSE_INIT_*` nên không phải bấm qua UI. Đã xác nhận trace lên đúng: `userId` và `sessionId` có giá trị (tức tiền tố `langfuse_` trong `get_trace_metadata` đúng), và một trace chứa đủ `__start__ → route_from_state → supervisor → AzureChatOpenAI → refuse`. Gói `langchain` là **bắt buộc** cho phần này — `langfuse.langchain` import nó chứ không chỉ langchain-core, thiếu thì trace tắt im lặng.

Chi phí chỉ hiện khi có **model definition** khớp tên model; `gpt-5.4-mini` không nằm trong bảng dựng sẵn của server nên ban đầu mọi trace hiện 0đ dù token đếm đúng. Đơn giá đã seed ($0.75/1M input, $4.50/1M output) — lệnh tạo lại nằm ở đầu `docker-compose.langfuse.yml`. Định nghĩa chỉ áp cho trace **mới**, trace cũ vẫn 0đ.

**Hai cạm bẫy cấu hình Azure** (mất thời gian nhất khi dựng):
- `AZURE_OPENAI_ENDPOINT` phải là URL **gốc** của resource, không kèm `/openai/v1`. `AzureChatOpenAI` tự nối `/openai/deployments/<deployment>/chat/completions`; thêm đuôi vào là Azure trả `404 Resource not found`.
- `AZURE_OPENAI_DEPLOYMENT` là tên **deployment**, không phải tên model — và Settings không đọc biến `AZURE_OPENAI_DEPLOYMENT_NAME` mà nhiều máy export sẵn. Để trống thì code lấy tạm `AZURE_OPENAI_MODEL` và cũng ra 404.

**Cấu hình chỉ đến từ file, không đến từ biến môi trường.** `Settings.settings_customise_sources` bỏ hẳn nguồn env (`app/core/config.py`). Mặc định của pydantic-settings ngược lại — biến môi trường thắng file — và đúng chỗ đó đã ngốn thời gian: máy dev export sẵn `AZURE_OPENAI_*` từ dự án khác nên sửa `.env` không thấy tác dụng gì, mà cũng không có lỗi nào để lần ra. Test `test_shell_environment_cannot_override_the_env_file` chốt lại.

Hệ quả: `docker run -e` và biến của CI **không** còn đặt được cấu hình. Khác biệt của container đi bằng `.env.docker` — compose mount nó vào `/run/config/env.docker`, file này đọc sau `.env` nên thắng, và chỉ chứa những khoá thật sự khác (hiện chỉ có `MONGO_URI`). Khối `environment:` trong docker-compose đã bỏ vì nay vô tác dụng.

## Tài liệu

```
docs/superpowers/
  specs/2026-08-06-booking-nail-toc/     ← spec chính, 8 file
    README.md                            ← đọc đầu tiên
    01-architecture.md                   ← có 4 sơ đồ Mermaid
    02-data-model.md  03-auth.md  04-agent.md
    05-frontend.md    06-telegram.md     07-errors-testing.md
    ui-mockup.html  ui-mockup-streaming.html   ← mở bằng trình duyệt
  specs/2026-08-08-vi-time-parser-design.md    ← spec bổ sung, parser thời gian
  specs/2026-08-15-admin-password-reset-telegram-design.md  ← spec bổ sung, HOÃN tới sau Plan 3
  plans/2026-08-06-booking-nail-toc-roadmap.md ← bản đồ 4 plan
  plans/2026-08-06-backend-foundation/         ← Plan 1, 12 task
  plans/2026-08-06-agent-memory-streaming/     ← Plan 2, 11 task (có 04b)
  plans/2026-08-06-telegram-bot/               ← Plan 3, 5 task
  plans/2026-08-06-react-frontend/             ← Plan 4, 9 task (có 04b)
```

Mỗi plan có `README.md` riêng với mục **Ràng buộc toàn cục** — bắt buộc đọc trước khi làm task nào của plan đó.

## Thứ tự chạy

```
Plan 1 (nền tảng backend)  ──┬──→ Plan 2 (agent) ──→ Plan 4 (React)
                             └──→ Plan 3 (Telegram)
```

Plan 1 dựng toàn bộ tầng `services/`; agent, Telegram và REST đều chỉ bọc mỏng bên ngoài. Làm ngược lại là viết logic đặt lịch hai lần.

Plan 3 và Plan 4 độc lập nhau, chạy song song được sau Plan 2.

**Cách chạy:** dùng skill `superpowers:subagent-driven-development` (khuyến nghị) hoặc `superpowers:executing-plans`. Mỗi task là một file, các bước là checkbox `- [ ]`, viết theo TDD: test đỏ → code → test xanh → commit.

## Quyết định đã chốt — đừng mở lại

| Chủ đề | Chốt | Vì sao |
|---|---|---|
| Kênh cho khách | Web app trình duyệt, nhớ link | Không phụ thuộc nền tảng chat nào |
| Frontend | React + Vite + TypeScript | |
| Framework agent | **LangGraph**, không phải AutoGen | Cần subagent và định tuyến tường minh |
| LLM & embedding | Azure OpenAI, `text-embedding-3-small` (1536 chiều) | |
| Memory ngữ nghĩa | **Không dùng** — chỉ 2 tầng, cả hai trên Mongo | Agent chỉ trả lời tiệm bận/rảnh và đặt lịch — hai câu hỏi về **trạng thái hiện tại**, không cần biết khách từng nói gì tháng trước. Danh tính đã có từ JWT (tầng 1), chống lặp đã có từ lịch sử hội thoại (tầng 2). Tầng 3 không thêm gì cho hai nhiệm vụ đó mà mang theo rủi ro rò dữ liệu giữa các khách. Ba điều kiện mở lại ghi ở mục dưới |
| Quan sát | Langfuse Cloud | Self-host v3 cần ClickHouse + Redis + MinIO, quá nặng |
| Kênh cho chủ tiệm | **Telegram**, không phải Zalo OA | Bot API miễn phí, không có khung 48h tính phí, không cần giấy phép kinh doanh |
| Khôi phục mật khẩu admin | **Qua bot Telegram**, không phải OTP | Đã cân nhắc Zalo ZNS và SMS rồi loại: cả hai đòi giấy phép kinh doanh và nhiều hạ tầng (xoay vòng token, duyệt mẫu tin) cho một rủi ro vốn đã chấp nhận. Dòng trên **không có ngoại lệ**: Zalo OA không dùng ở đâu cả. Spec `2026-08-15-admin-password-reset-telegram-design.md` |
| Bot Telegram | **Không có AI**, chỉ 4 nút | Tất định, không tốn token, test không cần LLM |
| Redis | **Không dùng** | Xem `specs/.../README.md` mục "Vì sao chưa dùng Redis" — có 3 điều kiện để mở lại |
| Số worker | **Đúng 1** | Telegram chỉ cho một kết nối `getUpdates` mỗi token; nhiều worker → 409 Conflict liên tục |
| Parser thời gian | Regex đường tắt + LLM + một lớp chốt | Spec riêng `2026-08-08-vi-time-parser-design.md` |
| Ghi lịch | **Agent không có tool ghi lịch** — chỉ `propose_appointment` giữ chỗ, node `confirm` mới tạo | Quy tắc "xác nhận trước khi ghi" thành ràng buộc cấu trúc, và giá trị đem ghi lấy từ DB chứ không phải chuỗi model gõ lại |
| Ranh giới hội thoại | **Một phiên mỗi ngày**, cắt theo giờ VN | Lịch sử chat chỉ để hiểu tham chiếu trong cùng mạch nói ("giờ đó", "ừ") — vô nghĩa sau vài tuần. Thông tin bền của khách đã nằm trong khối bối cảnh dựng bằng code. Mốc là **ngày trôi qua, không phải lần đăng nhập**: đăng nhập do vòng đời cookie quyết định (30 ngày), khách đăng nhập ba lần một buổi chiều vẫn là một mạch nói. Cắt lúc ĐỌC, không lúc ghi — đổi quy tắc về sau không cần migrate |
| Số lần ghi Mongo | **Không gộp** — chấp nhận 3 `update_one` ở lượt giữ chỗ / xác nhận | ~350 lần ghi/ngày trên một document nhỏ. Gộp được nhưng phải cho tool ghi vào state qua closure — tác dụng phụ ẩn, khó đọc hơn `set_pending`. Cần tối ưu thì gộp hai `append` trong `run_turn` trước |

## Mười ba cái bẫy — đã trả giá để tìm ra

Cả 13 đã được vá trong plan. Đừng "sửa lại cho gọn" mà làm hỏng.

### Dữ liệu

**1. Unique index phải là PARTIAL, không phải sparse.**
```js
db.appointments.createIndex(
  { slot_keys: 1 },
  { unique: true, partialFilterExpression: { status: "booked" } }
)
```
MongoDB đánh chỉ mục mảng rỗng thành `undefined`. Nếu hủy lịch bằng cách gán `slot_keys = []` thì lịch hủy **thứ hai** ném `E11000 dup key: { : undefined }`. Sparse không cứu được. Hủy chỉ đổi `status`, giữ nguyên `slot_keys`.

**2. `DuplicateKeyError` là lớp con của `PyMongoError`.** Repository phải bắt nó và đổi thành `SlotTakenError` trước khi tới handler `PyMongoError`, nếu không khách trùng giờ sẽ thấy "máy của tiệm đang hỏng".

### Langfuse

**3. v3 đổi hoàn toàn cách truyền credential.** `CallbackHandler()` **không nhận tham số nào**; credential đặt ở `Langfuse(public_key=..., secret_key=..., host=...)` khởi tạo một lần. Truyền vào handler là `TypeError` → bị nuốt → không trace gì cả.

**4. Danh tính đi qua metadata, không qua handler.** `config={"metadata": {"langfuse_user_id": ..., "langfuse_session_id": ...}}`. Sai tên khóa thì trace vẫn lên nhưng mất đường lần ngược về khách.

### Agent và streaming

**5. Khối bối cảnh PHẢI nói hôm nay là ngày nào.** Không có mốc thì "mai 3h chiều" là câu không giải được; model suy từ dữ liệu huấn luyện và đặt lịch lệch cả năm — mà `2025-08-08` vẫn là chuỗi ISO hợp lệ nên không gì chặn được. Dòng đó đứng **đầu** khối.

**6. Agent KHÔNG có tool ghi lịch.** Bộ tool là `parse_time`, `find_free_slots`, `propose_appointment`, `list_my_appointments`, `cancel_appointment` — không có `create_appointment`. `propose_appointment` kiểm giờ còn trống rồi lưu `pending_confirmation` vào Mongo; lịch chỉ được tạo ở node `confirm` sau khi khách đồng ý. Làm vậy vì hai lý do: quy tắc "luôn xác nhận trước khi ghi" thành ràng buộc cấu trúc thay vì lời dặn trong prompt; và giá trị đem đi ghi lịch đọc từ DB chứ không phải từ chuỗi ISO model gõ lại — model chép sai 15:00 thành 5:00 cũng không tới được database.

**7. Chỉ stream token mang tag `respond`.** Token của supervisor là JSON định tuyến. Model của parser thời gian phải mang `tags=["timeparse"]` và `streaming=False`, nếu không khách thấy `{"start_at": "2026-08-...` chạy ngang màn hình.

**8. Khối bối cảnh KHÔNG nằm trong system prompt.** Prompt cache ăn theo tiền tố chung dài nhất; khối này đổi mỗi lượt nên đặt đầu là cache không bao giờ trúng. Thứ tự: System (tĩnh) → Messages (lịch sử) → Message (bối cảnh + memory) → Message (tin mới).

**9. Đừng nới regex của parser thời gian.** Nó chỉ trả lời khi câu khớp trọn vẹn (có ngày VÀ có giờ xác định); thiếu gì cũng nhường cho LLM. Thêm mẫu "thứ Năm" là mẫu đó nuốt luôn "thứ Năm tuần sau" và trả sai ngày — LLM không bao giờ được gọi để sửa. Lớp `TestRegexDefers` canh chỗ này.

### Telegram

**10. Trả `answerCallbackQuery` TRƯỚC khi làm việc.** Callback query hết hạn sau 10 giây; tỏa tin qua Socket.IO + Telegram mất vài vòng HTTP. Chậm là nút quay mãi và chủ tiệm bấm lại → đặt bận hai lần.

**11. `get_updates` phải NÉM lỗi ra ngoài** (khác `send_message` nuốt lỗi). Nuốt ở đây thì mạng đứt trông giống hệt "không có tin mới", và nhánh lùi 5 giây thành code chết.

### Frontend

**12. Hết giờ bận KHÔNG có sự kiện nào từ server.** Backend không có timer; `busy_until` chỉ được tính lại khi có ai gọi `get_status()`. Không xử ở client thì hết giờ bận, thẻ vẫn hiện "đang bận" cho tới khi khách tải lại trang. `useShopStatus` đặt **một** `setTimeout` hẹn đúng `minutes_left` phút, tới giờ thì lật thẻ rồi hỏi lại server một lần. Hẹn theo `minutes_left` (một **khoảng**) chứ không theo `busy_until` trừ `Date.now()` (hai **mốc**) — máy khách lệch giờ là chuyện thường, có máy lệch cả năm. Trạng thái đổi thì phải `clearTimeout` hẹn cũ, nếu không nó lật thẻ giữa lúc chủ tiệm vẫn đang bận.

**13. `socket.io-client` gộp kết nối theo URL.** Hai hook cùng gọi `io(BASE)` nhận về **cùng một** socket; hook nào unmount trước sẽ `disconnect()` cái mà hook kia đang dùng. Dùng `acquireSocket()` / `releaseSocket()` đếm tham chiếu, và mỗi hook tự `socket.off()` handler của mình.

## Memory: vì sao chỉ hai tầng — và ba điều kiện mở lại

Agent chỉ làm hai việc: trả lời tiệm bận/rảnh, và đặt lịch. Cả hai là câu hỏi về **trạng thái hiện tại**, đọc thẳng từ `ShopService` / `AppointmentService`. Không tool nào trong sáu tool nhận sở thích khách làm đầu vào.

| Tầng | Nguồn | Chống được gì |
|---|---|---|
| 1. Danh tính | JWT + `users` trong Mongo | Gọi nhầm tên, hỏi lại tên/SĐT |
| 2. Lịch sử hội thoại | `messages` trong Mongo, cắt theo ngân sách token | Lặp lại, hỏi lại thứ vừa nói |

Tầng 3 (ngữ nghĩa, vector) **đã bỏ**. Nó chỉ phục vụ việc nhớ ngữ cảnh tự do qua nhiều tháng — mà `plans/.../task-03-conversation.md` đã tính: khách đặt lịch vài tuần một lần nên cửa sổ trượt 1.500 token của tầng 2 **tự nó đã phủ được vài tháng**. Đổi lại, tầng 3 mang theo một container Postgres, hai thư viện, ba trong số các cạm bẫy đắt nhất của dự án, và một kho memory dùng chung mà quên truyền `user_id` một chỗ là ký ức khách này lộ sang khách khác.

Chống lặp **không** phải việc của tầng 3: Mem0 lưu sự kiện đã trích xuất chứ không lưu nguyên văn lượt trước, và truy hồi của nó là xác suất. Lặp lại là do lịch sử bị cắt giữa cặp hỏi–đáp, do prompt không dặn, hoặc do model quá nhỏ — thứ tự đó cũng là thứ tự cần kiểm khi gặp lỗi.

**Ba điều kiện để mở lại** (theo lối đã dùng cho Redis):

1. Phạm vi agent mở rộng sang tư vấn dịch vụ — dị ứng, kiểu tóc, sở thích thợ — chứ không chỉ bận/rảnh và đặt lịch.
2. Đã chạy thật, đọc log hội thoại, và **quan sát được** ca cụ thể mà nhớ ngữ cảnh tự do sẽ cứu được cuộc trò chuyện.
3. Có Postgres trong hạ tầng vì lý do khác, không phải dựng riêng cho memory.

Cắm lại rẻ vì tầng 2 lưu đủ tin nhắn trong Mongo ngay từ đầu — chạy `remember()` ngược trên lịch sử cũ là dựng lại được memory cho khách quen.

## Ràng buộc giao diện — không được phá

Người dùng là khách lớn tuổi. Đây không phải sở thích thẩm mỹ.

- **Cỡ chữ nền 19px. Nút cao tối thiểu 56px, rộng hết chiều ngang.**
- **Mọi trạng thái diễn đạt bằng CHỮ**, không chỉ bằng màu. Người lớn tuổi hay bị lóa và mù màu nhẹ.
- **Tương phản tối thiểu 4.5:1.** Bảng màu đã tính tay: `#0369A1` (5.93), `#047857` (5.48), `#B91C1C` (5.91). **Không** dùng `#0284C7` (4.10) hay `#059669` (3.77) cho chữ — chúng trượt AA. Có test chốt trong Plan 4 task 8.
- **Font Be Vietnam Pro.** Atkinson Hyperlegible dễ đọc hơn nhưng **không có bộ ký tự tiếng Việt** — mọi chữ có dấu sẽ rơi sang font dự phòng.
- **Không hiện tên tool kỹ thuật cho khách.** `find_free_slots` → "Đang xem lịch trống…". Tool lạ → "Đang xử lý…", không bao giờ hiện tên thô.
- **Mất mạng giữa lúc stream: GIỮ NGUYÊN chữ đã hiện**, chỉ thêm dòng "Mất mạng, đang thử lại…". Xóa đi thì người lớn tuổi tưởng mình làm hỏng.
- **Hủy lịch phải qua một bước xác nhận**, xác nhận tại chỗ chứ không mở modal.
- **Thời gian hiển thị bằng MỐC, KHÔNG BAO GIỜ bằng khoảng.** Chỉ "Xong lúc 3:30 chiều". Không đếm ngược ở bất kỳ đâu — không "còn 30 phút", không "sắp xong rồi". Nút 15/30/60/120 phút chỉ là **đầu vào**; `set_busy` quy ngay ra `busy_until` và từ đó mọi màn hình chỉ dùng mốc. Lý do: mốc không cũ đi (người lớn tuổi hay để màn hình đó rồi quay lại), không bắt tính nhẩm, và không dính lệch đồng hồ máy. Áp cho thẻ khách, bảng chủ tiệm, và câu trả lời của AI — câu của AI còn nằm lại trong lịch sử chat nên càng không được nói "còn N phút".
- **Tôn trọng `prefers-reduced-motion: reduce`**, icon là SVG inline không dùng emoji.
- **Access token giữ trong BỘ NHỚ, không `localStorage`.** Refresh token đã nằm trong cookie `HttpOnly` mà React không đọc được — nếu rồi lại cất access token vào `localStorage` thì công sức đó thành vô nghĩa. Mọi lời gọi tới `/api/v1/auth/*` phải đặt `credentials: "include"`, nếu không trình duyệt không gửi cookie. Mở lại tab thì gọi `/auth/refresh` để lấy access token mới.

## Chưa có — cần trước khi mở cho khách thật

Bốn plan chỉ lo *phần mềm chạy đúng*. Phần vận hành chưa có task nào.

**Chặn đường:**

1. **Không có sao lưu Mongo.** Spec ghi mất Mongo là fail-hard duy nhất của cả hệ. Một `docker compose down -v` nhầm là mất sạch lịch của mọi khách.
2. **`mongo-express` nằm trong `docker-compose.yml`**, cổng 8081, user/pass `admin`/`admin`. Lên prod là ai cũng đọc và sửa được tên, SĐT, lịch của mọi khách.
3. **Mongo mở cổng `27017:27017` ra host, không auth.**
4. **`docker-compose.yml` hardcode `JWT_SECRET` và `API_KEY` bản dev**, và hai giá trị này **ghi đè `.env`** — biến môi trường thắng dotenv. Ai đọc repo cũng ký được token admin. Cần `docker-compose.prod.yml` riêng, đọc secret từ `.env` của server. Mục 2, 3, 4 nên gộp làm một việc: tách file compose dev khỏi prod.
5. **`HEALTHCHECK` trong Dockerfile chạy `import requests`, mà `requests` không có trong `requirements.txt`** → container luôn `unhealthy`.
6. **Không có `restart: unless-stopped`** → máy reboot là app không tự lên.
7. **Không có HTTPS/reverse proxy.** Ngoài bảo mật, nút micro (Web Speech API) **chỉ chạy trên HTTPS**.
8. **Không có CI.** Không có `.github/`; bốn plan đầy test mà không ai chạy tự động.
9. **Thiếu `.dockerignore`** → `COPY . .` đưa cả `.env` và `.git` vào layer của image.

**Đáng sửa, chưa chặn đường:**

- **Throttle toàn cục chống flood** — giới hạn request mỗi IP cho *mọi* endpoint, đếm trong RAM (token bucket), không chạm Mongo. Cố ý để tới Plan 5 vì nó phụ thuộc mục 7 (chưa có proxy), vì nginx có thể làm tốt hơn tầng ứng dụng, và vì bật sớm thì bộ test e2e sẽ đỏ vì 429 chứ không phải vì code sai. Kèm theo:
  - Hàm lấy IP dùng chung, đọc `X-Forwarded-For` **chỉ khi** `TRUST_PROXY_HEADERS=true`, lấy phần tử thứ `TRUSTED_PROXY_COUNT + 1` từ phải sang — phần bên trái do client tự gửi nên giả mạo được. Tin header này khi chưa có proxy thật là tự vô hiệu hoá rate limit. Hàm này thay `request.client.host` ở `auth.py` và middleware log, chỗ mà sau proxy luôn ra IP proxy nên không lần ra được ai.
  - Miễn trừ `/api/v1/health/*`: Docker `HEALTHCHECK` gọi mỗi 30 giây, để nó ăn quota thì lúc bị flood health check trượt và Docker tự giết container. Phải sửa mục 5 trước thì phần này mới kiểm chứng được.
  - Bucket trong RAM phải được dọn định kỳ, nếu không đổi IP liên tục là làm app phình bộ nhớ tới chết.
  - Middleware phải **trả thẳng `JSONResponse`**, không `raise RateLimitedError` — handler `AppError` nằm bên trong lớp middleware nên không bắt được.
  - Đếm trong RAM chỉ đúng khi **một worker**, xem dòng "Số worker" ở bảng quyết định.
- Một worker là ràng buộc cứng → mỗi lần deploy đều có downtime, không rolling update.

Đã đề xuất gom thành **Plan 5 — Vận hành và deploy**, chưa viết.

## Bảo mật — đã rà, còn nợ

Rà ngày 2026-08-15 bằng request thật lên server đang chạy, không chỉ đọc code.

**Luồng quên mật khẩu của khách giữ nguyên** — công khai, chỉ cần SĐT. Rủi ro này đã chốt chấp nhận và vẫn còn hiệu lực: tiệm nhỏ, khách quen, thiệt hại tối đa là một khách mất lịch của chính mình.

**Riêng admin thì không.** `AuthService.reset_password` không nhìn `role`, nên quyết định trên đang tự động áp cả cho chủ tiệm — mà chiếm admin là lộ SĐT toàn bộ khách, huỷ mọi lịch, đổi giờ mở cửa, và khoá chính chủ tiệm ra ngoài. SĐT chủ tiệm thì dán trên biển hiệu. Vá bằng cách chặn `role == "admin"` khỏi luồng công khai, cho admin đường khôi phục qua bot Telegram — spec `2026-08-15-admin-password-reset-telegram-design.md`, **hoãn tới sau Plan 3** vì cần bot tồn tại trước.

**Đã vá — rate limit đăng nhập.** `POST /auth/login` sai quá `LOGIN_MAX_ATTEMPTS` (mặc định 10) trong `LOGIN_WINDOW_SECONDS` (900) thì khoá tạm, đếm theo cả SĐT lẫn IP. Điểm cốt lõi: **kiểm hạn mức TRƯỚC khi so mật khẩu**. Chỉ đếm sau mỗi lần sai thì mật khẩu vẫn được kiểm ở mọi lần thử — kẻ dò nhận 429 thay vì 401, nhưng lần đoán trúng vẫn lấy được token. `RateLimitService` vì thế tách làm `check()` (đếm, không ghi) và `hit()` (ghi, không đếm); `reset-password` giữ nguyên `check_and_hit`. Lần đăng nhập đúng không tốn hạn mức. SĐT sai định dạng vẫn tính vào hạn mức theo IP, nếu không đổi SĐT mỗi lần là thoát.

**Đã vá — thu hồi token khi đổi mật khẩu.** `User.token_version` tăng mỗi lần `set_password`; token mang `tv` khác giá trị hiện tại bị từ chối. Dùng **số đếm chứ không dùng mốc thời gian**: `iat` của JWT chỉ có độ phân giải giây, nên đổi mật khẩu và phát token trong cùng một giây là không phân biệt được — hoặc token cũ sống sót, hoặc người vừa đổi mật khẩu bị đá ra ngay. So sánh số nguyên thì không có vùng mờ. Mọi chỗ phát token phải đi qua `create_access_token_for(user)`.

**Đã vá — hạn mức đặt lịch.** 20 lịch/giờ theo `user_id` (`BOOKING_MAX_PER_HOUR`). Đặt **sau** nhánh idempotency, nên lượt gọi lặp lại trả về lịch cũ không bị tính là lần đặt mới.

**Cố ý KHÔNG vá — `reset-password` phân biệt 204 / 404.** Đúng là nó cho phép dò xem SĐT nào có tài khoản. Cách vá thường thấy là luôn trả 204, nhưng ở luồng này thì **hại nhiều hơn lợi**: endpoint không gửi mã xác thực mà **đổi mật khẩu ngay**, nên 204 cho một SĐT không tồn tại nghĩa là nói với khách "đổi xong rồi" trong khi không có gì đổi cả — rồi họ không đăng nhập được và không hiểu vì sao. Với khách lớn tuổi, đó là thiệt hại chắc chắn đổi lấy một rủi ro nhỏ đã bị rate limit 5 lần/giờ chặn. Luôn-trả-204 chỉ hợp với luồng "đã gửi mã cho bạn", không hợp với luồng "đã đổi xong".

**Đã vá — refresh token có xoay vòng.** Đăng nhập trả về cặp `access_token` (30 phút) + `refresh_token` (30 ngày). `POST /auth/refresh` đổi lấy cặp mới và **xoay**: token cũ bị đánh dấu đã dùng. `POST /auth/logout` xoá cả phiên.

Bốn điểm đáng nhớ:

- **Refresh token đi bằng cookie `HttpOnly`, KHÔNG nằm trong body.** Trả trong body là buộc React cất ở nơi JavaScript đọc được, và một lỗ XSS là mất sạch phiên của khách. Cookie kèm `Secure` (bật bằng `COOKIE_SECURE`, bắt buộc khi có HTTPS), `SameSite=Strict`, và `Path=/api/v1/auth` để nó không đính vào mọi request. Theo IETF *OAuth 2.0 for Browser-Based Applications*, khuyến nghị mạnh cho ứng dụng xử lý dữ liệu cá nhân — app này giữ tên, SĐT và lịch của khách.
- **Refresh token là chuỗi ngẫu nhiên, không phải JWT.** Nó phải tra được trong DB để thu hồi; đã tra DB thì JWT không thêm gì ngoài độ phức tạp.
- **Chỉ lưu hash (SHA-256).** DB rò rỉ không được tương đương trao phiên đăng nhập. Không dùng bcrypt vì đây là bí mật ngẫu nhiên 256 bit, không có gì để dò, mà bcrypt lại không tra được bằng đúng giá trị.
- **`family_id` gom mọi token xoay ra từ một lần đăng nhập.** Phát hiện phát lại thì xoá cả family — đá đúng một phiên, không đụng máy khác của cùng khách.
- **Cửa sổ ân hạn 30 giây** (`REFRESH_GRACE_SECONDS`) — bằng mặc định của Okta; Auth0 gọi cùng thứ này là *rotation overlap period*. Không có nó thì chính cơ chế xoay vòng tự tạo lỗi: điện thoại mạng chập chờn bắn hai request song song lúc access token hết hạn, cả hai cùng trình một token, cái thứ hai trông y hệt token bị đánh cắp và khách bị đăng xuất dù không ai tấn công. Trong ân hạn, lần dùng lại cấp **một cặp mới** trong cùng family chứ không phát lại đúng cặp cũ — phát lại cặp cũ đòi lưu token thô, tức bỏ đi chính lý do phải băm.

Đối chiếu ngày 2026-08-15 với RFC 9700 (BCP OAuth 2.0, 1/2025) và IETF *OAuth 2.0 for Browser-Based Applications*: xoay vòng mỗi lần dùng, phát hiện phát lại, thu hồi cả family, refresh token trong cookie HttpOnly — đủ cả bốn.

**Chưa có hạn tuyệt đối cho phiên.** BCP cho phép chọn một trong hai: trần thời gian sống, **hoặc** hết hạn khi không dùng. Dự án chọn vế sau (không dùng 30 ngày thì chết). Hệ quả: khách dùng app đều đặn sẽ không bao giờ phải đăng nhập lại — với khách lớn tuổi thì đó là điều mong muốn, nên đây là **quyết định sản phẩm**, không phải thiếu sót.

Đổi mật khẩu xoá **mọi** refresh token của user. Thiếu bước đó thì vá `token_version` là vô nghĩa: kẻ chiếm tài khoản vẫn tự cấp access token mới bằng refresh token cũ.

**Còn nợ:** không còn món nào từ đợt rà bảo mật.

**Đã kiểm và không có vấn đề:** NoSQL injection bị chặn bởi kiểu `str` của Pydantic; `appointment_id` rác trả 404 chứ không 500; kiểm quyền huỷ lịch đúng, không IDOR; lỗi trả cho khách không lộ traceback.

## Trạng thái git

- **Repo:** GitHub `BuiHenry1404/salona-booking`, remote `origin`
- **Nhánh:** `main`
- Repo được tạo lại từ đầu, **không còn** lịch sử Azure DevOps (`agentbox-eco-system`, nhánh `henryb1/personal-project-hehe`, commit `8d3a4c090`) mà bản bàn giao nhắc tới.
- `ui-mockup.html` và `ui-mockup-streaming.html` **đã commit**. Ảnh sinh bằng Gemini (`ai-avatar.*`, `Gemini_Generated_Image_*.png`) bị `.gitignore` bỏ qua vì nặng ~6MB — ai clone mới sẽ **không có** chúng, mockup sẽ hiện ảnh vỡ.

## Việc còn dở

- **Avatar chatbot** — sinh bằng Gemini, mockup trỏ tới `ai-avatar.jpg`, hiển thị tròn 44×44px. Kiểm bắt buộc: thu nhỏ về 44px, nhìn ở khoảng cách cầm điện thoại — không phân biệt được thì sinh lại, **không** phóng to trên giao diện vì 44px đã là con số chốt trong mockup. File không nằm trong git, xem mục trên.

## Bắt đầu từ đâu

Plan 1 đã xong. Chạy thử trước khi làm gì tiếp:

```bash
docker compose up -d app mongo
curl localhost:8000/api/v1/health/     # nhớ dấu / cuối, thiếu là 307
```

Chưa có tài khoản nào trong DB và **không có đăng ký tự do** — user đầu tiên phải seed thẳng vào Mongo bằng `AuthService.create_user(...)` chạy trong container, sau đó mọi thứ đi qua REST.

Việc tiếp theo, theo thứ tự:

1. **Plan 3** (Telegram) — `plans/2026-08-06-telegram-bot/`. Chạy song song Plan 4 được.
2. **Plan 4** (React) — `plans/2026-08-06-react-frontend/`.
3. **Đặt lại mật khẩu admin** — spec `specs/2026-08-15-admin-password-reset-telegram-design.md`, sau Plan 3.

Rate limit đăng nhập đã xong. Xem mục "Bảo mật" cho phần còn nợ.
