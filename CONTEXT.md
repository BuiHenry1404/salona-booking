# CONTEXT — App đặt lịch tiệm nail & tóc

File này giữ phần **tại sao**. Phần *gõ gì* nằm ở [`RUNBOOK.md`](RUNBOOK.md).
Đọc trước khi viết dòng code đầu tiên.

## Đang làm gì

App đặt lịch cho **một tiệm nail–tóc nhỏ ở Việt Nam**, người dùng chính là **khách lớn tuổi**. Dựng trên `fastapi-agent-template`.

| Ai | Qua đâu |
|---|---|
| Khách | Web app React — chat với AI để đặt lịch, xem lịch của mình |
| Chủ tiệm | Cùng web app, giao diện admin — bật/tắt bận rảnh, xem lịch, quản khách |
| Chủ tiệm | Bot Telegram — nhận báo lịch mới, tra lịch, đổi bận/rảnh bằng nút bấm |

**Xong: Plan 1 (backend) và Plan 2 (agent, memory, streaming).** 281 test xanh, cộng 10 test gọi Azure thật. Đã đo đầu-cuối với `gpt-5.4-mini`: lượt "mai 3h chiều làm tóc được không con" gọi `parse_time` → `propose_appointment` rồi hỏi xác nhận mà **chưa** ghi lịch; lượt "ừ" không gọi tool nào và ghi lịch ngay; câu ngoài chủ đề bị `refuse`. Trace lên Langfuse đủ `userId`/`sessionId` và các span `TOOL`.

**Tiếp theo:** Plan 3 (Telegram) → Plan 4 (React); hai plan này song song được. Sau Plan 3 thì làm đặt lại mật khẩu admin (`specs/2026-08-15-admin-password-reset-telegram-design.md`).

## Tài liệu

```
docs/superpowers/
  specs/2026-08-06-booking-nail-toc/     ← spec chính, 8 file (README.md đọc đầu tiên)
    ui-mockup.html  ui-mockup-streaming.html   ← mở bằng trình duyệt
  specs/2026-08-08-vi-time-parser-design.md    ← parser thời gian
  specs/2026-08-15-admin-password-reset-telegram-design.md  ← HOÃN tới sau Plan 3
  plans/2026-08-06-booking-nail-toc-roadmap.md ← bản đồ 4 plan
  plans/2026-08-06-backend-foundation/         ← Plan 1, 12 task   ✅
  plans/2026-08-06-agent-memory-streaming/     ← Plan 2, 11 task   ✅
  plans/2026-08-06-telegram-bot/               ← Plan 3, 5 task
  plans/2026-08-06-react-frontend/             ← Plan 4, 9 task
```

Mỗi plan có mục **Ràng buộc toàn cục** trong `README.md` — bắt buộc đọc trước khi làm task của plan đó. Chạy bằng skill `superpowers:subagent-driven-development` hoặc `superpowers:executing-plans`; mỗi task là một file, viết theo TDD.

Plan 1 dựng toàn bộ tầng `services/`; agent, Telegram và REST chỉ bọc mỏng bên ngoài — làm ngược lại là viết logic đặt lịch hai lần.

## Quyết định đã chốt — đừng mở lại

| Chủ đề | Chốt | Vì sao |
|---|---|---|
| Kênh cho khách | Web app trình duyệt | Không phụ thuộc nền tảng chat nào |
| Frontend | React + Vite + TypeScript | |
| Framework agent | **LangGraph**, không phải AutoGen | Cần subagent và định tuyến tường minh |
| LLM | Azure OpenAI | |
| Memory ngữ nghĩa | **Không dùng** — chỉ 2 tầng, cả hai trên Mongo | Xem mục dưới |
| Quan sát | **Langfuse tự dựng** bằng compose riêng | Cloud cũng chạy được; tự dựng để dữ liệu khách không ra khỏi máy. Nặng (Postgres + ClickHouse + Redis + MinIO) nên tách khỏi `docker-compose.yml` |
| Kênh cho chủ tiệm | **Telegram**, không phải Zalo OA | Bot API miễn phí, không khung 48h tính phí, không cần giấy phép kinh doanh |
| Khôi phục mật khẩu admin | **Qua bot Telegram**, không phải OTP | Zalo ZNS và SMS đều đòi giấy phép kinh doanh và nhiều hạ tầng cho một rủi ro vốn đã chấp nhận. **Không có ngoại lệ**: Zalo OA không dùng ở đâu cả |
| Bot Telegram | **Không có AI**, chỉ 4 nút | Tất định, không tốn token, test không cần LLM |
| Redis | **Không dùng** | 3 điều kiện mở lại ở `specs/.../README.md` |
| Số worker | **Đúng 1** | Telegram chỉ cho một kết nối `getUpdates` mỗi token; nhiều worker → 409 Conflict liên tục |
| Nguồn cấu hình | **Chỉ file `.env`**, bỏ hẳn biến môi trường | Biến export sẵn từ dự án khác âm thầm đè lên `.env`, không có lỗi nào để lần ra. Đổi lại: `docker run -e` và biến CI không đặt được cấu hình, khác biệt của container đi bằng `.env.docker` |
| Parser thời gian | Regex đường tắt + LLM + một lớp chốt | Spec riêng |
| Ghi lịch | **Agent không có tool ghi lịch** — `propose_appointment` giữ chỗ, node `confirm` mới tạo | "Xác nhận trước khi ghi" thành ràng buộc cấu trúc; giá trị đem ghi lấy từ DB chứ không phải chuỗi model gõ lại |
| Ranh giới hội thoại | **Một phiên mỗi ngày**, cắt theo giờ VN, áp lúc ĐỌC | Lịch sử chat chỉ để hiểu tham chiếu trong cùng mạch nói ("giờ đó", "ừ") — vô nghĩa sau vài tuần. Mốc là ngày trôi qua, không phải lần đăng nhập: cookie sống 30 ngày, khách đăng nhập ba lần một buổi chiều vẫn là một mạch nói. Cắt lúc đọc nên đổi quy tắc không cần migrate |
| Số lần ghi Mongo | **Không gộp** — chấp nhận 3 `update_one` mỗi lượt giữ chỗ/xác nhận | ~350 lần ghi/ngày trên một document nhỏ. Gộp phải cho tool ghi vào state qua closure — tác dụng phụ ẩn. Cần tối ưu thì gộp hai `append` trong `run_turn` trước |

## Mười ba cái bẫy — đã trả giá để tìm ra

Cả 13 đã được vá. Đừng "sửa lại cho gọn" mà làm hỏng.

**Dữ liệu**

1. **Unique index phải PARTIAL, không phải sparse** (`partialFilterExpression: {status: "booked"}`). Mongo đánh chỉ mục mảng rỗng thành `undefined`, nên hủy bằng cách gán `slot_keys = []` thì lịch hủy thứ hai ném `E11000 dup key: { : undefined }`. Hủy chỉ đổi `status`.
2. **`DuplicateKeyError` là lớp con của `PyMongoError`.** Repository phải đổi nó thành `SlotTakenError` trước, nếu không khách trùng giờ thấy "máy của tiệm đang hỏng".

**Langfuse**

3. **v3+ đổi cách truyền credential.** `CallbackHandler()` không nhận tham số nào; credential đặt ở `Langfuse(...)` khởi tạo một lần. Truyền vào handler là `TypeError` → bị nuốt → không trace gì cả.
4. **Danh tính đi qua metadata, không qua handler**: `langfuse_user_id` / `langfuse_session_id`. Sai tên khoá thì trace vẫn lên nhưng mất đường lần ngược về khách.
5. **`langfuse.langchain` cần gói `langchain`**, không chỉ `langchain-core`. Thiếu thì trace tắt im lặng, chỉ một dòng warning. Chi phí thì cần thêm **model definition** khớp tên model, không có là mọi trace hiện 0đ dù token đếm đúng.

**Agent và streaming**

6. **Khối bối cảnh PHẢI nói hôm nay là ngày nào**, ở dòng **đầu**. Không có mốc thì "mai 3h chiều" không giải được; model suy từ dữ liệu huấn luyện và đặt lệch cả năm — mà `2025-08-08` vẫn là chuỗi ISO hợp lệ nên không gì chặn được.
7. **Agent KHÔNG có tool ghi lịch.** Năm tool: `parse_time`, `find_free_slots`, `propose_appointment`, `list_my_appointments`, `cancel_appointment`. Thêm `create_appointment` là hỏng cả hai lớp bảo vệ — khách mất bước xác nhận, và chuỗi ISO quay lại đi vòng qua model.
8. **Chỉ stream token mang tag `respond`.** Token của supervisor là JSON định tuyến; model của parser thời gian phải mang `tags=["timeparse"]` và `streaming=False`, không thì khách thấy `{"start_at": "2026-08-...` chạy ngang màn hình.
9. **Khối bối cảnh KHÔNG nằm trong system prompt.** Prompt cache ăn theo tiền tố chung; khối này đổi mỗi lượt nên đặt đầu là cache không bao giờ trúng. Thứ tự: System → lịch sử → bối cảnh → tin mới.
10. **Đừng nới regex của parser thời gian.** Nó chỉ trả lời khi câu khớp trọn vẹn; thiếu gì cũng nhường cho LLM. Thêm mẫu "thứ Năm" là mẫu đó nuốt luôn "thứ Năm tuần sau" và trả sai ngày — LLM không bao giờ được gọi để sửa. Lớp `TestRegexDefers` canh chỗ này.

**Telegram**

11. **Trả `answerCallbackQuery` TRƯỚC khi làm việc.** Callback query hết hạn sau 10 giây; chậm là nút quay mãi và chủ tiệm bấm lại → đặt bận hai lần.
12. **`get_updates` phải NÉM lỗi ra ngoài** (khác `send_message` nuốt lỗi). Nuốt thì mạng đứt trông giống hệt "không có tin mới".

**Frontend**

13. **Hết giờ bận KHÔNG có sự kiện nào từ server** — backend không có timer, `busy_until` chỉ được tính lại khi ai đó gọi `get_status()`. `useShopStatus` đặt **một** `setTimeout` theo `minutes_left` (một **khoảng**, không phải hiệu hai **mốc** — máy khách lệch giờ là chuyện thường), tới giờ lật thẻ rồi hỏi lại server; trạng thái đổi thì `clearTimeout`.
    **`socket.io-client` gộp kết nối theo URL** — hai hook cùng gọi `io(BASE)` nhận cùng một socket, hook nào unmount trước sẽ ngắt của hook kia. Dùng `acquireSocket()`/`releaseSocket()` đếm tham chiếu.

## Memory: vì sao chỉ hai tầng

| Tầng | Nguồn | Chống được gì |
|---|---|---|
| 1. Danh tính | JWT + `users` | Gọi nhầm tên, hỏi lại tên/SĐT |
| 2. Lịch sử hội thoại | `messages` trong Mongo, cắt theo ngân sách token | Lặp lại, hỏi lại thứ vừa nói |

Agent chỉ làm hai việc: trả lời tiệm bận/rảnh và đặt lịch — cả hai là câu hỏi về **trạng thái hiện tại**, đọc thẳng từ service. Không tool nào nhận sở thích khách làm đầu vào.

Tầng 3 (ngữ nghĩa, vector) **đã bỏ**. Khách đặt lịch vài tuần một lần nên cửa sổ trượt 1.500 token của tầng 2 tự nó đã phủ vài tháng. Đổi lại tầng 3 mang theo một container Postgres, hai thư viện, và một kho memory dùng chung mà quên truyền `user_id` một chỗ là ký ức khách này lộ sang khách khác. Chống lặp **không** phải việc của nó: Mem0 lưu sự kiện đã trích xuất chứ không lưu nguyên văn, và truy hồi là xác suất. Lặp lại là do lịch sử bị cắt giữa cặp hỏi–đáp, do prompt không dặn, hoặc do model quá nhỏ — kiểm theo đúng thứ tự đó.

**Ba điều kiện mở lại:** (1) phạm vi agent mở sang tư vấn dịch vụ chứ không chỉ bận/rảnh và đặt lịch; (2) đã chạy thật và **quan sát được** ca cụ thể mà nhớ ngữ cảnh tự do sẽ cứu cuộc trò chuyện; (3) đã có Postgres trong hạ tầng vì lý do khác. Cắm lại rẻ vì tầng 2 lưu đủ tin nhắn — chạy ngược trên lịch sử cũ là dựng lại được.

## Ràng buộc giao diện — không được phá

Người dùng là khách lớn tuổi. Đây không phải sở thích thẩm mỹ.

- **Cỡ chữ nền 19px. Nút cao tối thiểu 56px, rộng hết chiều ngang.**
- **Mọi trạng thái diễn đạt bằng CHỮ**, không chỉ bằng màu — người lớn tuổi hay bị lóa và mù màu nhẹ.
- **Tương phản tối thiểu 4.5:1.** Dùng `#0369A1` (5.93), `#047857` (5.48), `#B91C1C` (5.91). **Không** dùng `#0284C7` (4.10) hay `#059669` (3.77) cho chữ — trượt AA. Test chốt ở Plan 4 task 8.
- **Font Be Vietnam Pro.** Atkinson Hyperlegible dễ đọc hơn nhưng **không có bộ ký tự tiếng Việt**.
- **Không hiện tên tool kỹ thuật cho khách.** `find_free_slots` → "Đang xem lịch trống…"; tool lạ → "Đang xử lý…".
- **Mất mạng giữa lúc stream: GIỮ NGUYÊN chữ đã hiện**, chỉ thêm "Mất mạng, đang thử lại…". Xoá đi thì người lớn tuổi tưởng mình làm hỏng.
- **Hủy lịch phải qua một bước xác nhận**, tại chỗ chứ không mở modal.
- **Thời gian hiển thị bằng MỐC, không bao giờ bằng khoảng.** "Xong lúc 3:30 chiều", không "còn 30 phút". Nút 15/30/60/120 phút chỉ là **đầu vào**; `set_busy` quy ngay ra `busy_until`. Mốc không cũ đi, không bắt tính nhẩm, không dính lệch đồng hồ máy. Áp cho cả câu trả lời của AI — câu đó còn nằm lại trong lịch sử chat.
- **Tôn trọng `prefers-reduced-motion: reduce`**; icon là SVG inline, không emoji.
- **Access token giữ trong BỘ NHỚ, không `localStorage`.** Refresh token đã nằm trong cookie `HttpOnly`; cất access token vào `localStorage` là phá bỏ công sức đó. Mọi lời gọi `/api/v1/auth/*` phải `credentials: "include"`.

## Chưa có — cần trước khi mở cho khách thật

Bốn plan chỉ lo *phần mềm chạy đúng*. Phần vận hành chưa có task nào.

**Chặn đường:**

1. **Không có sao lưu Mongo.** Mất Mongo là fail-hard duy nhất của cả hệ; một `docker compose down -v` nhầm là mất sạch lịch của mọi khách.
2. **`mongo-express` trong `docker-compose.yml`** — cổng 8081, `admin`/`admin`. Lên prod là ai cũng đọc và sửa được dữ liệu khách.
3. **Mongo mở cổng `27017:27017` ra host, không auth.**
4. **`.env` dev nằm cùng chỗ với cấu hình chạy thật.** Cần `docker-compose.prod.yml` riêng và một `.env` của server. Mục 2, 3, 4 nên gộp làm một việc: tách compose dev khỏi prod.
5. **Không có HTTPS/reverse proxy.** Ngoài bảo mật, nút micro (Web Speech API) **chỉ chạy trên HTTPS**.
6. **Không có CI.** Bốn plan đầy test mà không ai chạy tự động.

Đã vá: `HEALTHCHECK` (dùng `urllib` thay `requests` vốn không có trong requirements, và thêm dấu `/` cuối vì 307 vẫn bị `urlopen` coi là thành công), `restart: unless-stopped` cho `app` và `mongo` (cố ý **không** cho `mongo-express`), và `.dockerignore` — đã dựng image kiểm lại: không còn `.env` hay `.git` trong `/app`, container lên `healthy` sau 10 giây.

**Đáng sửa, chưa chặn đường — throttle toàn cục chống flood.** Giới hạn request mỗi IP cho mọi endpoint, đếm trong RAM. Để tới Plan 5 vì phụ thuộc mục 5 (chưa có proxy), vì nginx làm tốt hơn tầng ứng dụng, và vì bật sớm thì test e2e đỏ vì 429 chứ không phải vì code sai. Bốn điều kèm theo:

- Hàm lấy IP dùng chung, đọc `X-Forwarded-For` **chỉ khi** `TRUST_PROXY_HEADERS=true`, lấy phần tử thứ `TRUSTED_PROXY_COUNT + 1` từ phải sang — phần bên trái do client tự gửi. Tin header này khi chưa có proxy thật là tự vô hiệu hoá rate limit.
- Miễn trừ `/api/v1/health/*`: Docker gọi mỗi 30 giây, để nó ăn quota thì lúc bị flood health check trượt và Docker tự giết container.
- Bucket trong RAM phải được dọn định kỳ, không thì đổi IP liên tục là làm app phình bộ nhớ tới chết.
- Middleware phải trả thẳng `JSONResponse`, không `raise RateLimitedError` — handler `AppError` nằm bên trong lớp middleware.

Một worker là ràng buộc cứng → mỗi lần deploy đều có downtime. Đã đề xuất gom thành **Plan 5 — Vận hành và deploy**, chưa viết.

## Bảo mật — đã rà, không còn nợ

Rà ngày 2026-08-15 bằng request thật lên server đang chạy, không chỉ đọc code.

**Luồng quên mật khẩu của khách giữ nguyên** — công khai, chỉ cần SĐT. Rủi ro đã chốt chấp nhận: tiệm nhỏ, khách quen, thiệt hại tối đa là một khách mất lịch của chính mình. **Riêng admin thì không** — chiếm admin là lộ SĐT toàn bộ khách và khoá chính chủ tiệm ra ngoài, mà SĐT chủ tiệm dán trên biển hiệu. Vá bằng đường khôi phục qua bot Telegram, **hoãn tới sau Plan 3** vì cần bot tồn tại trước.

**Đã vá:**

- **Rate limit đăng nhập** — 10 lần sai trong 900 giây thì khoá tạm, đếm theo cả SĐT lẫn IP. Cốt lõi: **kiểm hạn mức TRƯỚC khi so mật khẩu**. Chỉ đếm sau mỗi lần sai thì lần đoán trúng vẫn lấy được token. `RateLimitService` vì thế tách `check()` và `hit()`. SĐT sai định dạng vẫn tính theo IP, không thì đổi SĐT mỗi lần là thoát.
- **Thu hồi token khi đổi mật khẩu** — `User.token_version` tăng mỗi lần `set_password`. Dùng **số đếm chứ không dùng mốc thời gian**: `iat` chỉ phân giải tới giây, nên đổi mật khẩu và phát token trong cùng một giây là không phân biệt được. Đổi mật khẩu cũng xoá **mọi** refresh token, thiếu bước đó thì `token_version` vô nghĩa.
- **Hạn mức đặt lịch** — 20 lịch/giờ theo `user_id`, đặt **sau** nhánh idempotency nên lượt gọi lặp lại không bị tính.
- **Refresh token có xoay vòng** — `POST /auth/refresh` đổi lấy cặp mới và đánh dấu token cũ đã dùng; phát lại thì xoá cả `family_id`.

Bốn điểm đáng nhớ về refresh token:

- **Đi bằng cookie `HttpOnly`, KHÔNG nằm trong body** — trả trong body là buộc React cất ở nơi JavaScript đọc được, một lỗ XSS là mất sạch phiên. Kèm `Secure` (`COOKIE_SECURE`), `SameSite=Strict`, `Path=/api/v1/auth`.
- **Là chuỗi ngẫu nhiên, không phải JWT** — phải tra được DB để thu hồi; đã tra DB thì JWT không thêm gì ngoài độ phức tạp.
- **Chỉ lưu hash SHA-256.** Không bcrypt: đây là bí mật ngẫu nhiên 256 bit, không có gì để dò, mà bcrypt lại không tra được bằng đúng giá trị.
- **Ân hạn 30 giây** (bằng mặc định của Okta). Không có nó thì chính cơ chế xoay vòng tự tạo lỗi: điện thoại mạng chập chờn bắn hai request song song, cái thứ hai trông y hệt token bị đánh cắp. Trong ân hạn, lần dùng lại cấp **một cặp mới** chứ không phát lại cặp cũ — phát lại đòi lưu token thô, tức bỏ đi lý do phải băm.

**Cố ý KHÔNG vá — `reset-password` phân biệt 204/404.** Nó cho dò xem SĐT nào có tài khoản, nhưng luôn-trả-204 ở đây **hại nhiều hơn lợi**: endpoint đổi mật khẩu ngay chứ không gửi mã, nên 204 cho SĐT không tồn tại là nói với khách "đổi xong rồi" trong khi không có gì đổi — rồi họ không đăng nhập được và không hiểu vì sao. Luôn-trả-204 chỉ hợp với luồng "đã gửi mã".

**Chưa có hạn tuyệt đối cho phiên.** BCP cho chọn một trong hai: trần thời gian sống, hoặc hết hạn khi không dùng. Dự án chọn vế sau (không dùng 30 ngày thì chết) — khách lớn tuổi không phải đăng nhập lại là điều mong muốn, nên đây là **quyết định sản phẩm**.

Đối chiếu với RFC 9700 và IETF *OAuth 2.0 for Browser-Based Applications*: xoay vòng mỗi lần dùng, phát hiện phát lại, thu hồi cả family, cookie HttpOnly — đủ cả bốn. Đã kiểm và không có vấn đề: NoSQL injection bị chặn bởi kiểu `str` của Pydantic; `appointment_id` rác trả 404 chứ không 500; kiểm quyền huỷ lịch đúng, không IDOR; lỗi trả cho khách không lộ traceback.

## Trạng thái git

- **Repo:** GitHub `BuiHenry1404/salona-booking`, remote `origin`. Nhánh `main`; Plan 2 nằm ở `feat/agent-memory-streaming` đã push, **chưa merge**.
- Repo tạo lại từ đầu, **không còn** lịch sử Azure DevOps mà bản bàn giao nhắc tới.
- Ảnh sinh bằng Gemini (`ai-avatar.*`, `Gemini_Generated_Image_*.png`) bị `.gitignore` bỏ qua vì nặng ~6MB — ai clone mới sẽ thấy mockup vỡ ảnh.

## Việc còn dở

**Avatar chatbot** — sinh bằng Gemini, mockup trỏ tới `ai-avatar.jpg`, hiển thị tròn 44×44px. Kiểm bắt buộc: thu nhỏ về 44px, nhìn ở khoảng cách cầm điện thoại; không phân biệt được thì sinh lại, **không** phóng to trên giao diện vì 44px đã là con số chốt trong mockup.
