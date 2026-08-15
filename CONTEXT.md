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

**Trạng thái: Plan 1 (nền tảng backend) đã hiện thực xong.** REST API chạy được bằng `docker compose up`, đã test end-to-end qua HTTP: đăng nhập/JWT, phân quyền admin, đặt lịch, chống trùng giờ, giờ mở cửa, bận/rảnh, huỷ lịch. **Chưa có AI** — Plan 2 chưa bắt đầu.

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
  plans/2026-08-06-react-frontend/             ← Plan 4, 8 task
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
| Memory ngữ nghĩa | **Mem0 + Postgres/pgvector** | Mongo giữ dữ liệu app, pgvector giữ vector |
| Quan sát | Langfuse Cloud | Self-host v3 cần ClickHouse + Redis + MinIO, quá nặng |
| Kênh cho chủ tiệm | **Telegram**, không phải Zalo OA | Bot API miễn phí, không có khung 48h tính phí, không cần giấy phép kinh doanh |
| Khôi phục mật khẩu admin | **Qua bot Telegram**, không phải OTP | Đã cân nhắc Zalo ZNS và SMS rồi loại: cả hai đòi giấy phép kinh doanh và nhiều hạ tầng (xoay vòng token, duyệt mẫu tin) cho một rủi ro vốn đã chấp nhận. Dòng trên **không có ngoại lệ**: Zalo OA không dùng ở đâu cả. Spec `2026-08-15-admin-password-reset-telegram-design.md` |
| Bot Telegram | **Không có AI**, chỉ 4 nút | Tất định, không tốn token, test không cần LLM |
| Redis | **Không dùng** | Xem `specs/.../README.md` mục "Vì sao chưa dùng Redis" — có 3 điều kiện để mở lại |
| Số worker | **Đúng 1** | Telegram chỉ cho một kết nối `getUpdates` mỗi token; nhiều worker → 409 Conflict liên tục |
| Parser thời gian | Regex đường tắt + LLM + một lớp chốt | Spec riêng `2026-08-08-vi-time-parser-design.md` |
| Ghi lịch | **Agent không có tool ghi lịch** — chỉ `propose_appointment` giữ chỗ, node `confirm` mới tạo | Quy tắc "xác nhận trước khi ghi" thành ràng buộc cấu trúc, và giá trị đem ghi lấy từ DB chứ không phải chuỗi model gõ lại |
| Số lần ghi Mongo | **Không gộp** — chấp nhận 3 `update_one` ở lượt giữ chỗ / xác nhận | ~350 lần ghi/ngày trên một document nhỏ. Gộp được nhưng phải cho tool ghi vào state qua closure — tác dụng phụ ẩn, khó đọc hơn `set_pending`. Cần tối ưu thì gộp hai `append` trong `run_turn` trước |

## Mười sáu cái bẫy — đã trả giá để tìm ra

Cả 16 đã được vá trong plan. Đừng "sửa lại cho gọn" mà làm hỏng.

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

### Mem0

**3. `search()` đã đổi API.** Đúng: `search(query, filters={"user_id": uid}, top_k=n)`. Gọi kiểu cũ (`user_id=`, `limit=`) ném `ValueError`, mà adapter fail-soft nuốt hết → memory chết im lặng, log chỉ một dòng warning.

**4. `embedding_dims` phải khai ở CẢ HAI chỗ** — `embedder.config.embedding_dims` và `vector_store.config.embedding_model_dims`, và phải bằng nhau. Lệch thì pgvector **im lặng** nuốt lỗi ghi: API trả về thành công kèm memory ID nhưng không lưu gì, không migrate tại chỗ được.

**5. Trên Azure, `model` là TÊN DEPLOYMENT** chứ không phải tên model. Lệch tên → 404 `DeploymentNotFound`, thông báo lỗi không chỉ ra chỗ sai.

### Langfuse

**6. v3 đổi hoàn toàn cách truyền credential.** `CallbackHandler()` **không nhận tham số nào**; credential đặt ở `Langfuse(public_key=..., secret_key=..., host=...)` khởi tạo một lần. Truyền vào handler là `TypeError` → bị nuốt → không trace gì cả.

**7. Danh tính đi qua metadata, không qua handler.** `config={"metadata": {"langfuse_user_id": ..., "langfuse_session_id": ...}}`. Sai tên khóa thì trace vẫn lên nhưng mất đường lần ngược về khách.

### Agent và streaming

**8. Khối bối cảnh PHẢI nói hôm nay là ngày nào.** Không có mốc thì "mai 3h chiều" là câu không giải được; model suy từ dữ liệu huấn luyện và đặt lịch lệch cả năm — mà `2025-08-08` vẫn là chuỗi ISO hợp lệ nên không gì chặn được. Dòng đó đứng **đầu** khối.

**9. Agent KHÔNG có tool ghi lịch.** Bộ tool là `parse_time`, `find_free_slots`, `propose_appointment`, `list_my_appointments`, `cancel_appointment` — không có `create_appointment`. `propose_appointment` kiểm giờ còn trống rồi lưu `pending_confirmation` vào Mongo; lịch chỉ được tạo ở node `confirm` sau khi khách đồng ý. Làm vậy vì hai lý do: quy tắc "luôn xác nhận trước khi ghi" thành ràng buộc cấu trúc thay vì lời dặn trong prompt; và giá trị đem đi ghi lịch đọc từ DB chứ không phải từ chuỗi ISO model gõ lại — model chép sai 15:00 thành 5:00 cũng không tới được database.

**10. Chỉ stream token mang tag `respond`.** Token của supervisor là JSON định tuyến. Model của parser thời gian phải mang `tags=["timeparse"]` và `streaming=False`, nếu không khách thấy `{"start_at": "2026-08-...` chạy ngang màn hình.

**11. Khối bối cảnh KHÔNG nằm trong system prompt.** Prompt cache ăn theo tiền tố chung dài nhất; khối này đổi mỗi lượt nên đặt đầu là cache không bao giờ trúng. Thứ tự: System (tĩnh) → Messages (lịch sử) → Message (bối cảnh + memory) → Message (tin mới).

**12. Đừng nới regex của parser thời gian.** Nó chỉ trả lời khi câu khớp trọn vẹn (có ngày VÀ có giờ xác định); thiếu gì cũng nhường cho LLM. Thêm mẫu "thứ Năm" là mẫu đó nuốt luôn "thứ Năm tuần sau" và trả sai ngày — LLM không bao giờ được gọi để sửa. Lớp `TestRegexDefers` canh chỗ này.

### Telegram

**13. Trả `answerCallbackQuery` TRƯỚC khi làm việc.** Callback query hết hạn sau 10 giây; tỏa tin qua Socket.IO + Telegram mất vài vòng HTTP. Chậm là nút quay mãi và chủ tiệm bấm lại → đặt bận hai lần.

**14. `get_updates` phải NÉM lỗi ra ngoài** (khác `send_message` nuốt lỗi). Nuốt ở đây thì mạng đứt trông giống hệt "không có tin mới", và nhánh lùi 5 giây thành code chết.

### Frontend

**15. Hết giờ bận KHÔNG có sự kiện nào từ server.** Backend không có timer; `busy_until` chỉ được tính lại khi có ai gọi `get_status()`. Không xử ở client thì hết giờ bận, thẻ vẫn hiện "đang bận" cho tới khi khách tải lại trang. `useShopStatus` đặt **một** `setTimeout` hẹn đúng `minutes_left` phút, tới giờ thì lật thẻ rồi hỏi lại server một lần. Hẹn theo `minutes_left` (một **khoảng**) chứ không theo `busy_until` trừ `Date.now()` (hai **mốc**) — máy khách lệch giờ là chuyện thường, có máy lệch cả năm. Trạng thái đổi thì phải `clearTimeout` hẹn cũ, nếu không nó lật thẻ giữa lúc chủ tiệm vẫn đang bận.

**16. `socket.io-client` gộp kết nối theo URL.** Hai hook cùng gọi `io(BASE)` nhận về **cùng một** socket; hook nào unmount trước sẽ `disconnect()` cái mà hook kia đang dùng. Dùng `acquireSocket()` / `releaseSocket()` đếm tham chiếu, và mỗi hook tự `socket.off()` handler của mình.

## Giới hạn đã biết — cố ý chưa làm

**Chi phí memory vô hình trong trace Langfuse.** Mem0 gọi LLM bằng client riêng, **không đi qua LangChain**, nên callback handler của LangGraph không thấy lượt trích xuất của nó. Trace sẽ thiếu đúng phần chi phí đó.

Spec đã cân nhắc và chấp nhận ở giai đoạn đầu (`04-agent.md:130`) — *"chấp nhận được ở giai đoạn đầu, nhưng phải biết là mình đang không nhìn thấy nó"*. Muốn đo thì bọc `remember()` trong một span Langfuse tường minh ở `app/memory/adapter.py`, khoảng 8 dòng, nhớ fail-soft như phần còn lại của adapter.

Đây là **quyết định**, không phải sót. Đừng coi là bug rồi tự vá mà không hỏi.

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

## Chưa có — cần trước khi mở cho khách thật

Bốn plan chỉ lo *phần mềm chạy đúng*. Phần vận hành chưa có task nào.

**Chặn đường:**

1. **Không có sao lưu Mongo.** Spec ghi mất Mongo là fail-hard duy nhất của cả hệ. Một `docker compose down -v` nhầm là mất sạch lịch của mọi khách.
2. **`mongo-express` nằm trong `docker-compose.yml`**, cổng 8081, user/pass `admin`/`admin`. Lên prod là ai cũng đọc và sửa được tên, SĐT, lịch của mọi khách.
3. **Mongo mở cổng `27017:27017` ra host, không auth.**
4. **`docker-compose.yml` hardcode `JWT_SECRET` và `API_KEY` bản dev**, và hai giá trị này **ghi đè `.env`** — biến môi trường thắng dotenv. Ai đọc repo cũng ký được token admin. Cần `docker-compose.prod.yml` riêng, đọc secret từ `.env` của server. Mục 2, 3, 4 nên gộp làm một việc: tách file compose dev khỏi prod.
5. **`HEALTHCHECK` trong Dockerfile chạy `import requests`, mà `requests` không có trong `requirements.txt`** → container luôn `unhealthy`.
6. **`docker-compose.yml` thiếu Postgres/pgvector** mà Plan 2 cần.
7. **Không có `restart: unless-stopped`** → máy reboot là app không tự lên.
8. **Không có HTTPS/reverse proxy.** Ngoài bảo mật, nút micro (Web Speech API) **chỉ chạy trên HTTPS**.
9. **Không có CI.** Không có `.github/`; bốn plan đầy test mà không ai chạy tự động.
10. **Thiếu `.dockerignore`** → `COPY . .` đưa cả `.env` và `.git` vào layer của image.

**Đáng sửa, chưa chặn đường:**

- `JWT_EXPIRE_MINUTES=30` — bắt khách lớn tuổi đăng nhập lại mỗi nửa tiếng là họ bỏ app. Chưa có refresh token. Cần quyết lại con số.
- **Throttle toàn cục chống flood** — giới hạn request mỗi IP cho *mọi* endpoint, đếm trong RAM (token bucket), không chạm Mongo. Cố ý để tới Plan 5 vì nó phụ thuộc mục 8 (chưa có proxy), vì nginx có thể làm tốt hơn tầng ứng dụng, và vì bật sớm thì bộ test e2e sẽ đỏ vì 429 chứ không phải vì code sai. Kèm theo:
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

**Còn nợ, theo thứ tự nên làm:**

1. **`POST /auth/login` không có giới hạn nào.** Đã kiểm: 25 lần sai liên tiếp đều trả 401. Cộng với mật khẩu tối thiểu 4 ký tự và SĐT là định danh (không gian hẹp, đầu số đoán được) thì dò mật khẩu khả thi. Vì đã bỏ OTP nên đây là **lớp bảo vệ duy nhất** cho tài khoản khách. Không phụ thuộc Telegram, tách ra làm sớm được — và nên, vì nó nằm ở tầng `services/` mà Plan 2/3/4 đều bọc mỏng bên ngoài.
2. **Đổi mật khẩu không thu hồi token đang sống.** Đã kiểm: token lấy trước khi đổi vẫn dùng được sau đó, tối đa 30 phút. (`is_active=False` thì có thu hồi ngay — chỉ thiếu cơ chế tương tự cho đổi mật khẩu.)
3. **`reset-password` phân biệt 204 / 404** theo SĐT có tài khoản hay không → dò được ai là khách của tiệm.

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

1. **Plan 2** (agent) — `plans/2026-08-06-agent-memory-streaming/`. Cần thêm Postgres/pgvector vào compose trước.
2. **Plan 3** (Telegram) — `plans/2026-08-06-telegram-bot/`. Chạy song song Plan 4 được.
3. **Đặt lại mật khẩu admin** — spec `specs/2026-08-15-admin-password-reset-telegram-design.md`, sau Plan 3.

Chen ngang lúc nào cũng được, không phụ thuộc gì: **rate limit cho `POST /auth/login`** — vài chục dòng, xem mục "Bảo mật".
