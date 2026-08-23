# TÀI LIỆU KIỂM THỬ TOÀN HỆ THỐNG — SALONA BOOKING

> **Hệ thống:** Ứng dụng đặt lịch làm Nail & Tóc (FastAPI + LangGraph + MongoDB + Socket.IO + Telegram Bot + React).  
> **Ngôn ngữ chuẩn:** Tiếng Việt (cả UI, thông báo lỗi, logic nghiệp vụ).  
> **Mục đích tài liệu:** Quy định toàn bộ kịch bản kiểm thử (Test Scenarios), ca kiểm thử chi tiết (Test Cases), các trường hợp biên/cực hạn (Edge Cases) và ma trận sự cố (Failure Modes) cho toàn bộ hệ thống từ Backend, Agent AI, Realtime, Bot đến Frontend.

---

## MỤC LỤC

1. [Tổng quan Kiến trúc & Chiến lược Kiểm thử](#1-tổng-quan-kiến-trúc--chiến-lược-kiểm-thử)
2. [Tầng 1: Core Domain Utilities & Thuật toán nền tảng](#2-tầng-1-core-domain-utilities--thuật-toán-nền-tảng)
   - 1.1 Chuẩn hoá Số điện thoại (`core/phone.py`)
   - 1.2 Múi giờ & Đồng hồ hệ thống (`core/clock.py`)
   - 1.3 Lưới Lượng tử hoá Slot 15 phút (`core/slots.py`)
   - 1.4 Bảo mật, Mã hoá & JWT (`core/security.py`)
3. [Tầng 2: MongoDB Repositories & Ràng buộc toàn vẹn dữ liệu](#3-tầng-2-mongodb-repositories--ràng-buộc-toàn-vẹn-dữ-liệu)
   - 2.1 `UserRepository`
   - 2.2 `AppointmentRepository` (Partial Unique Index & Chặn trùng slot)
   - 2.3 `RefreshTokenRepository` (Xoay vòng token & Cửa sổ ân hạn)
   - 2.4 `ShopRepository` (`shop_status` & `shop_hours`)
   - 2.5 `ConversationRepository` (Phiên theo ngày & Ngân sách token)
4. [Tầng 3: Domain Services & Nghiệp vụ cốt lõi](#4-tầng-3-domain-services--nghiệp-vụ-cốt-lõi)
   - 3.1 `AuthService` (Đăng nhập, Quên mật khẩu, Thu hồi phiên, Phân quyền)
   - 3.2 `AppointmentService` (Đặt lịch, Idempotency, Chống trùng, Giới hạn quota, Huỷ lịch)
   - 3.3 `ShopService` (Bận/Rảnh, Giờ kết thúc tuyệt đối, Giờ mở cửa)
   - 3.4 `RateLimitService` (Kiểm soát tần suất truy cập dựa trên Mongo TTL)
   - 3.5 `NotificationService` (Tỏa tin Socket.IO & Telegram Fail-soft)
5. [Tầng 4: AI Agent & LangGraph Booking Graph](#5-tầng-4-ai-agent--langgraph-booking-graph)
   - 4.1 Bộ Parser thời gian Tiếng Việt 3 tầng (`timeparse.py`)
   - 4.2 Khối bối cảnh tất định (`context.py`) & Caching Prompt
   - 4.3 Supervisor Routing Node (`supervisor.py`)
   - 4.4 Booking Subagent & Bộ 5 Read-only Tools (`agents.py`, `tools.py`)
   - 4.5 Confirm Node & Cơ chế tắt 0-LLM (`confirm.py`)
   - 4.6 Status Subagent (`agents.py`)
   - 4.7 Langfuse Observability & Tracing Callback
6. [Tầng 5: Realtime Communication & Socket.IO Streaming](#6-tầng-5-realtime-communication--socketio-streaming)
   - 6.1 Lọc Token Stream qua thẻ Tag `respond`
   - 6.2 Vòng đời sự kiện Tool (`turn_started`, `tool_started`, `tool_finished`, `complete`, `error`)
   - 6.3 Broadcast Trạng thái Tiệm & Phòng Admin Realtime
   - 6.4 Quản lý Kết nối & Đếm tham chiếu (`acquireSocket` / `releaseSocket`)
7. [Tầng 6: Telegram Bot (Kênh chủ tiệm)](#7-tầng-6-telegram-bot-kênh-chủ-tiệm)
   - 7.1 Lọc quyền Admin Chat ID & Silent Drop
   - 7.2 Bàn phím Reply Keyboard & Inline Callback Buttons
   - 7.3 Phản hồi `answerCallbackQuery` dưới 10 giây
   - 7.4 Vòng lặp Long Polling Đơn Worker (Chống 409 Conflict)
   - 7.5 Đặt lại Mật khẩu Admin qua Bot Telegram
8. [Tầng 7: REST API Endpoints & Middlewares](#8-tầng-7-rest-api-endpoints--middlewares)
   - 8.1 Authentication Endpoints (`/api/v1/auth/*`)
   - 8.2 Appointments Endpoints (`/api/v1/appointments/*`)
   - 8.3 Shop Management Endpoints (`/api/v1/shop/*`)
   - 8.4 Conversation History Endpoints (`/api/v1/conversations/*`)
   - 8.5 Health Check & Ping (`/api/v1/health/`)
   - 8.6 Middleware Bắt lỗi Toàn cục & Định dạng JSON Tiếng Việt
9. [Tầng 8: Frontend React (UI/UX dễ đọc)](#9-tầng-8-frontend-react-uiux-dễ-đọc)
   - 9.1 Design Tokens & Tiêu chuẩn Accessibility (AA 4.5:1, Font >= 18px, Nút >= 56px)
   - 9.2 Trạng thái Tiệm không đếm ngược (Mốc giờ tuyệt đối)
   - 9.3 Client-side Timer lật thẻ trạng thái bằng `minutes_left`
   - 9.4 Trạng thái Streaming AI & Bảng ánh xạ Tool thân thiện
   - 9.5 Khả năng chịu lỗi mất mạng khi chat
   - 9.6 Giao diện Xem lại Lịch sử trò chuyện Read-only
10. [Tầng 9: Ma trận Edge Cases & Failure Modes Toàn diện](#10-tầng-9-ma-trận-edge-cases--failure-modes-toàn-diện)
11. [Hướng dẫn Thực thi Kiểm thử (Execution Runbook)](#11-hướng-dẫn-thực-thi-kiểm-thử-execution-runbook)

---

## 1. TỔNG QUAN KIẾN TRÚC & CHIẾN LƯỢC KIỂM THỬ

### 1.1 Nguyên tắc Thiết kế Bất biến (Hard Constraints)
Mọi kịch bản kiểm thử phải tuân thủ và xác minh 14 nguyên tắc cốt lõi:
1. **Đúng 1 Worker:** Không chạy đa tiến trình/đa worker để tránh lỗi Telegram `409 Conflict` trên kết nối `getUpdates`.
2. **Cấu hình độc quyền từ `.env`:** Biến môi trường hệ điều hành không được phép đè cấu hình.
3. **Mọi logic nghiệp vụ nằm ở `app/services/`:** Router REST, Agent Tools, Telegram Bot chỉ là lớp bọc mỏng (thin wrappers).
4. **Agent KHÔNG CÓ tool ghi lịch:** Chỉ có `propose_appointment` ghi cờ giữ chỗ `pending_confirmation`. Lịch chỉ được ghi tại node `confirm` từ giá trị trong DB.
5. **Chỉ stream token mang tag `respond`:** Token supervisor và timeparse (JSON) tuyệt đối không stream cho người dùng.
6. **Khối bối cảnh nêu rõ mốc ngày hôm nay ở đầu:** Không đặt trong cached system prompt.
7. **Unique index trên MongoDB phải là PARTIAL:** `{ status: "booked" }` trên `slot_keys`. Hủy lịch chỉ đổi status, không bao giờ gán mảng rỗng `slot_keys = []`.
8. **Chuyển đổi `DuplicateKeyError` thành `SlotTakenError`:** Xảy ra tại repository layer trước khi ném ra ngoài.
9. **Refresh Token HttpOnly Cookie:** Random 256-bit string, lưu dạng SHA-256 hash trong DB, xoay vòng kèm ân hạn 30s. Access Token chỉ giữ trong RAM.
10. **Đổi mật khẩu tăng `token_version` và huỷ mọi Refresh Token:** Vô hiệu hoá lập tức mọi phiên đăng nhập cũ.
11. **Thời gian hiển thị bằng MỐC tuyệt đối:** "Xong lúc 3:30 chiều", không hiển thị khoảng đếm ngược "còn 30 phút".
12. **Frontend tự lật thẻ trạng thái bằng `minutes_left`:** Server không có timer nền khi hết giờ bận.
13. **Chat history cắt theo ngày:** 1 phiên/ngày theo múi giờ Việt Nam (`Asia/Ho_Chi_Minh`), lấy ngược tối đa 1.500 tokens, luôn giữ nguyên cặp hỏi-đáp.
14. **Phân quyền và bảo vệ Admin:** Endpoint `POST /auth/reset-password` công khai bị chặn đối với tài khoản `role == "admin"`. Admin chỉ khôi phục qua Telegram bot.

---

## 2. TẦNG 1: CORE DOMAIN UTILITIES & THUẬT TOÁN NỀN TẢNG

### 1.1 Chuẩn hoá Số điện thoại (`app/core/phone.py`)

| ID | Tên Kịch bản | Dữ liệu Đầu vào (Input) | Kết quả Mong đợi (Expected Output) | Loại kiểm thử |
|---|---|---|---|---|
| `TC-PHONE-01` | SĐT chuẩn 10 chữ số nội địa | `"0912345678"` | Trả về `"0912345678"` | Unit / Happy |
| `TC-PHONE-02` | SĐT có mã quốc gia `+84` | `"+84912345678"` | Chuẩn hoá thành `"0912345678"` | Unit / Happy |
| `TC-PHONE-03` | SĐT có mã quốc gia `84` không dấu cộng | `"84912345678"` | Chuẩn hoá thành `"0912345678"` | Unit / Happy |
| `TC-PHONE-04` | SĐT chứa khoảng trắng xen kẽ | `"0912 345 678"`, `" 0912345678 "` | Loại bỏ khoảng trắng, trả về `"0912345678"` | Unit / Edge Case |
| `TC-PHONE-05` | SĐT chứa dấu chấm, gạch ngang | `"0912.345.678"`, `"0912-345-678"` | Loại bỏ ký tự phân cách, trả về `"0912345678"` | Unit / Edge Case |
| `TC-PHONE-06` | Tất cả các đầu số di động VN hợp lệ | `"03x"`, `"05x"`, `"07x"`, `"08x"`, `"09x"` (10 số) | Chuẩn hoá thành công định dạng 10 chữ số | Unit / Boundary |
| `TC-PHONE-07` | SĐT thiếu chữ số | `"091234567"` (9 số) | Ném `InvalidPhoneError` | Unit / Negative |
| `TC-PHONE-08` | SĐT thừa chữ số | `"09123456789"` (11 số), `"012345678901"` | Ném `InvalidPhoneError` | Unit / Negative |
| `TC-PHONE-09` | SĐT đầu số bàn hoặc không hợp lệ | `"0243123456"`, `"0112345678"` | Ném `InvalidPhoneError` | Unit / Negative |
| `TC-PHONE-10` | SĐT chứa chữ cái hoặc ký tự đặc biệt | `"091234567a"`, `"091234567@"`, `"0912<script>"` | Ném `InvalidPhoneError` | Unit / Security |
| `TC-PHONE-11` | Chuỗi rỗng hoặc None | `""`, `None` | Ném `InvalidPhoneError` | Unit / Edge Case |
| `TC-PHONE-12` | Ký tự Unicode ẩn / Zero-width space | `"0912\u200B345678"` | Ném `InvalidPhoneError` hoặc làm sạch đúng chuẩn | Unit / Security |

---

### 1.2 Múi giờ & Đồng hồ Hệ thống (`app/core/clock.py`)

| ID | Tên Kịch bản | Dữ liệu Đầu vào | Kết quả Mong đợi | Loại kiểm thử |
|---|---|---|---|---|
| `TC-CLOCK-01` | Múi giờ chuẩn hệ thống | `TZ` | `ZoneInfo("Asia/Ho_Chi_Minh")` (+07:00) | Unit / Contract |
| `TC-CLOCK-02` | Lấy mốc UTC hiện tại (`now_utc()`) | Hàm `now_utc()` | Trả về aware datetime có `tzinfo=timezone.utc` | Unit / Happy |
| `TC-CLOCK-03` | Chuyển UTC sang giờ địa phương (`to_local`) | `datetime(2026, 8, 7, 8, 0, tzinfo=timezone.utc)` | Trả về `2026-08-07 15:00:00+07:00` | Unit / Happy |
| `TC-CLOCK-04` | Chuyển giờ địa phương sang UTC (`to_utc`) | `datetime(2026, 8, 7, 15, 0, tzinfo=TZ)` | Trả về `2026-08-07 08:00:00+00:00` | Unit / Happy |
| `TC-CLOCK-05` | Biên ngày địa phương (`local_day_bounds`) | `date(2026, 8, 7)` | Start: `2026-08-06 17:00:00 UTC` (00:00 VN)<br>End: `2026-08-07 17:00:00 UTC` (24:00 VN). Độ dài đúng 24h | Unit / Boundary |
| `TC-CLOCK-06` | Xử lý ngày nhuận (Leap Year) | `date(2028, 2, 29)` | `local_day_bounds` tính toán chính xác 24h, không crash | Unit / Edge Case |
| `TC-CLOCK-07` | Xử lý chuyển giao năm mới | `date(2026, 12, 31)` sang `date(2027, 1, 1)` | Mốc UTC chuyển năm chính xác | Unit / Edge Case |
| `TC-CLOCK-08` | Naive datetime truyền vào `to_local`/`to_utc` | `datetime(2026, 8, 7, 15, 0)` | Ném lỗi hoặc gán timezone mặc định an toàn, không sinh lệch ngầm | Unit / Negative |

---

### 1.3 Lưới Lượng tử hoá Slot 15 phút (`app/core/slots.py`)

| ID | Tên Kịch bản | Dữ liệu Đầu vào | Kết quả Mong đợi | Loại kiểm thử |
|---|---|---|---|---|
| `TC-SLOT-01` | Tạo danh sách slot chuẩn 60 phút | Start: `08:00 UTC`, duration: `60` | `["...T08:00", "...T08:15", "...T08:30", "...T08:45"]` (4 slots) | Unit / Happy |
| `TC-SLOT-02` | Tạo danh sách slot đơn 15 phút | Start: `08:00 UTC`, duration: `15` | `["...T08:00"]` (1 slot) | Unit / Happy |
| `TC-SLOT-03` | Slot vắt qua nửa đêm (Midnight Span) | Start: `23:45 UTC`, duration: `30` | `["2026-08-07T23:45", "2026-08-08T00:00"]` | Unit / Edge Case |
| `TC-SLOT-04` | Giờ bắt đầu không khớp lưới 15 phút | Start: `08:07 UTC`, duration: `60` | Ném `MisalignedSlotError` | Unit / Negative |
| `TC-SLOT-05` | Truyền Naive Datetime vào `slot_keys_for` | `datetime(2026, 8, 7, 8, 0)` (không tzinfo) | Ném `MisalignedSlotError` | Unit / Negative |
| `TC-SLOT-06` | Làm tròn xuống (`quantize`) giờ lệch | `08:14 UTC` -> `quantize()`<br>`08:15 UTC` -> `quantize()`<br>`08:29 UTC` -> `quantize()` | Kết quả:<br>`08:00 UTC`<br>`08:15 UTC`<br>`08:15 UTC` | Unit / Happy |
| `TC-SLOT-07` | Thời lượng duration không chia hết cho 15 | Start: `08:00 UTC`, duration: `20` | Ném lỗi hoặc lượng tử hoá trần an toàn | Unit / Edge Case |
| `TC-SLOT-08` | Thời lượng duration âm hoặc bằng 0 | Start: `08:00 UTC`, duration: `0` hoặc `-15` | Ném `ValueError` / `MisalignedSlotError` | Unit / Edge Case |

---

### 1.4 Bảo mật, Mã hoá & JWT (`app/core/security.py`)

| ID | Tên Kịch bản | Dữ liệu Đầu vào | Kết quả Mong đợi | Loại kiểm thử |
|---|---|---|---|---|
| `TC-SEC-01` | Hash mật khẩu và Verify đúng | Password: `"MatKhau@123"` | Hash sinh ra khác bản rõ; `verify_password` trả về `True` | Unit / Happy |
| `TC-SEC-02` | Verify mật khẩu sai | Hash của `"MatKhau@123"`, input `"MatKhauSai"` | `verify_password` trả về `False` | Unit / Negative |
| `TC-SEC-03` | Tính ổn định của Bcrypt (Pin 4.0.1) | Chuỗi hash tiêu chuẩn | Tương thích ngược, không crash với Passlib | Unit / Contract |
| `TC-SEC-04` | Sinh và giải mã Access Token JWT | `user_id="usr_123"`, `role="user"`, `token_version=1` | JWT hợp lệ, payload giải mã chứa đúng `sub`, `role`, `token_version`, `exp` | Unit / Happy |
| `TC-SEC-05` | Access Token hết hạn (Expired JWT) | Token tạo với `exp = now - 1s` | Giải mã ném `ExpiredSignatureError` | Unit / Negative |
| `TC-SEC-06` | Access Token bị sửa chữ ký (Tampered JWT) | Thay đổi 1 ký tự trong JWT payload | Giải mã ném `JWTError` / `InvalidTokenError` | Unit / Security |
| `TC-SEC-07` | Hash Refresh Token bằng SHA-256 | Chuỗi ngẫu nhiên raw 256-bit | Trả về chuỗi hex SHA-256 64 ký tự; không thể khôi phục token gốc | Unit / Happy |
| `TC-SEC-08` | Sinh token bí mật ngẫu nhiên (`generate_token_secret`) | Hàm tạo token ngẫu nhiên | Độ dài >= 32 bytes entropy, không trùng lặp qua 10.000 lần lặp | Unit / Security |

---

## 3. TẦNG 2: MONGODB REPOSITORIES & RÀNG BUỘC DỮ LIỆU

### 2.1 `UserRepository`

| ID | Tên Kịch bản | Dữ liệu Đầu vào | Kết quả Mong đợi | Loại kiểm thử |
|---|---|---|---|---|
| `TC-REPO-USER-01` | Tạo User mới thành công | `User(phone="0912345678", full_name="Chị Lan", role="user")` | Lưu vào collection `users`, sinh `_id`, `token_version=1`, `is_active=True` | Integration / Happy |
| `TC-REPO-USER-02` | Chặn trùng SĐT ở tầng Database | Tạo 2 user cùng SĐT `"0912345678"` | Lần 2 ném `DuplicateKeyError` do Unique Index trên trường `phone` | Integration / Constraint |
| `TC-REPO-USER-03` | Tra cứu theo SĐT đã chuẩn hoá | Tìm `"0912345678"` | Trả về đúng User document | Integration / Happy |
| `TC-REPO-USER-04` | Tăng `token_version` khi đổi mật khẩu | Gọi `increment_token_version(user_id)` | Trường `token_version` tăng từ `1` lên `2` | Integration / Happy |
| `TC-REPO-USER-05` | Cập nhật mật khẩu mới | Gọi `update_password(user_id, new_hashed_pwd)` | Mật khẩu hash thay đổi, `updated_at` cập nhật | Integration / Happy |
| `TC-REPO-USER-06` | Vô hiệu hoá tài khoản | `set_active(user_id, is_active=False)` | `is_active = False` | Integration / Happy |

---

### 2.2 `AppointmentRepository` (Chặn trùng slot & Partial Unique Index)

| ID | Tên Kịch bản | Dữ liệu Đầu vào | Kết quả Mong đợi | Loại kiểm thử |
|---|---|---|---|---|
| `TC-REPO-APPT-01` | Lưu lịch hẹn hợp lệ | `Appointment(status="booked", slot_keys=["2026-08-07T15:00", "2026-08-07T15:15"])` | Lưu thành công vào collection `appointments` | Integration / Happy |
| `TC-REPO-APPT-02` | Chặn trùng slot bằng Partial Unique Index | 2 lịch có cùng slot `"2026-08-07T15:00"`, đều có `status="booked"` | DB ném `DuplicateKeyError`. Repository chuyển đổi thành `SlotTakenError` | Integration / Constraint |
| `TC-REPO-APPT-03` | **Bẫy mảng rỗng (Bẫy 1):** Huỷ 2 lịch liên tiếp | Huỷ lịch A và B bằng cách cập nhật `status="cancelled"` (giữ nguyên `slot_keys`) | **Thành công.** Không ném lỗi trùng khoá `undefined` | Integration / Trap Validation |
| `TC-REPO-APPT-04` | **Đặt lại khung giờ đã huỷ:** | Lịch A bị huỷ (`status="cancelled"`). Khách B đặt đúng khung giờ của A | **Thành công.** Do Partial Index chỉ index các doc có `{ status: "booked" }` | Integration / Happy |
| `TC-REPO-APPT-05` | **Bẫy DuplicateKeyError (Bẫy 2):** Kiểm tra chuyển đổi Exception | Gây ra trùng slot ở DB | Repository bắt `DuplicateKeyError` (em của `PyMongoError`) và `raise SlotTakenError` trước khi bắt PyMongoError chung | Integration / Error Handling |
| `TC-REPO-APPT-06` | Race condition đặt trùng đồng thời (`asyncio.gather`) | 2 coroutine đồng thời cố gắng lưu 2 lịch trùng `slot_keys` | Đúng 1 lịch thành công, 1 lịch ném `SlotTakenError`. Không bao giờ ghi đè cả 2 | Integration / Concurrency |
| `TC-REPO-APPT-07` | Lấy danh sách lịch trong ngày theo UTC range | Query từ `start_utc` đến `end_utc` | Trả về chính xác các lịch có `start_at` trong khoảng, sắp xếp tăng dần | Integration / Happy |

---

### 2.3 `RefreshTokenRepository` (Quản lý Phiên & Xoay vòng Token)

| ID | Tên Kịch bản | Dữ liệu Đầu vào | Kết quả Mong đợi | Loại kiểm thử |
|---|---|---|---|---|
| `TC-REPO-REF-01` | Không bao giờ lưu token thô trong DB | Gọi `create(user_id, raw_token)` | DB chỉ lưu `token_hash = sha256(raw_token)`, không chứa `raw_token` | Integration / Security |
| `TC-REPO-REF-02` | Tiêu thụ token (Consume & Rotate) | Gửi `raw_token` hợp lệ | Trả về record; cập nhật `used_at = now()`, phát hành token mới cùng `family_id` | Integration / Happy |
| `TC-REPO-REF-03` | Dùng lại token trong cửa sổ ân hạn 30s | Gửi lại token vừa dùng cách đây < 30s | Chấp nhận, cấp token mới thay vì coi là bị đánh cắp (xử lý mạng mobile chập chờn) | Integration / Edge Case |
| `TC-REPO-REF-04` | Dùng lại token sau cửa sổ ân hạn (> 30s) | Gửi lại token đã dùng cách đây > 30s | Ném `InvalidRefreshTokenError`, **xoá toàn bộ token cùng `family_id`** (thu hồi cả chuỗi) | Integration / Security |
| `TC-REPO-REF-05` | Thu hồi toàn bộ session khi đổi mật khẩu | Gọi `revoke_all_for_user(user_id)` | Xoá toàn bộ refresh tokens của user khỏi DB | Integration / Security |
| `TC-REPO-REF-06` | Đăng xuất 1 thiết bị (`revoke_family`) | Gọi `revoke_family(raw_token)` | Chỉ xoá các tokens thuộc `family_id` đó, session ở thiết bị khác vẫn sống | Integration / Happy |
| `TC-REPO-REF-07` | Token hết hạn TTL Index | Token có `expires_at < now` | MongoDB TTL index tự động dọn document; query trả về None | Integration / TTL |

---

### 2.4 `ShopRepository` (`shop_status` & `shop_hours`)

| ID | Tên Kịch bản | Dữ liệu Đầu vào | Kết quả Mong đợi | Loại kiểm thử |
|---|---|---|---|---|
| `TC-REPO-SHOP-01` | Đọc trạng thái tiệm mặc định | Chưa có doc trong DB | Trả về trạng thái mặc định: `is_busy=False`, `busy_until=None` (Singleton pattern) | Integration / Happy |
| `TC-REPO-SHOP-02` | Cập nhật bận (`set_busy`) | `busy_until = 2026-08-07T15:30:00+07:00` | Cập nhật `is_busy=True`, `busy_until`, `updated_at` | Integration / Happy |
| `TC-REPO-SHOP-03` | Cập nhật rảnh (`set_free`) | Gọi `set_free()` | Cập nhật `is_busy=False`, `busy_until=None` | Integration / Happy |
| `TC-REPO-SHOP-04` | Cập nhật giờ mở cửa (`shop_hours`) | `open_time="08:00"`, `close_time="19:00"`, `closed_days=[0]` | Lưu thành công thông tin giờ hoạt động | Integration / Happy |

---

### 2.5 `ConversationRepository` (Phiên hội thoại & Giới hạn Memory)

| ID | Tên Kịch bản | Dữ liệu Đầu vào | Kết quả Mong đợi | Loại kiểm thử |
|---|---|---|---|---|
| `TC-REPO-CONV-01` | Thêm tin nhắn mới (`append_message`) | `user_id`, `role="user"`, `content="Chào em"` | Lưu tin nhắn kèm `created_at` UTC; liên kết đúng `user_id` | Integration / Happy |
| `TC-REPO-CONV-02` | Phân tách phiên theo ngày VN (`history`) | Tin nhắn từ hôm qua và tin nhắn hôm nay | Chỉ lấy tin nhắn phát sinh trong ngày hôm nay theo giờ VN (+ tin trong 30p gần nhất) | Integration / Boundary |
| `TC-REPO-CONV-03` | Cắt lịch sử theo ngân sách 1.500 tokens | Hội thoại dài > 30 tin nhắn | Lấy ngược từ tin mới nhất, không vượt quá 1.500 token, luôn giữ trọn cặp hỏi-đáp | Integration / Algorithm |
| `TC-REPO-CONV-04` | Đặt và đọc cờ `pending_confirmation` | Lưu `{start_at: "...", note: "...", asked_at: "..."}` | Đọc lại chính xác payload cờ xác nhận | Integration / Happy |
| `TC-REPO-CONV-05` | Xoá cờ `pending_confirmation` | Gọi `clear_pending(user_id)` | Trường `pending_confirmation` trở về `None` | Integration / Happy |
| `TC-REPO-CONV-06` | Cờ `pending_confirmation` quá hạn 10 phút | Cờ có `asked_at = now - 11 phút` | Khi đọc coi như hết hạn, trả về `None` | Integration / Edge Case |

---

## 4. TẦNG 3: DOMAIN SERVICES & NGHIỆP VỤ CỐT LÕI

### 3.1 `AuthService`

| ID | Tên Kịch bản | Dữ liệu Đầu vào | Kết quả Mong đợi | Loại kiểm thử |
|---|---|---|---|---|
| `TC-SVC-AUTH-01` | Đăng nhập thành công | SĐT `"0912345678"`, mật khẩu đúng | Trả về User, sinh Access Token và Refresh Token | Service / Happy |
| `TC-SVC-AUTH-02` | Đăng nhập sai mật khẩu | SĐT đúng, mật khẩu sai | Ném `UnauthorizedError`, ghi nhận `hit` vào rate limiter | Service / Negative |
| `TC-SVC-AUTH-03` | Đăng nhập khi tài khoản bị khoá (`is_active=False`) | SĐT của tài khoản không hoạt động | Ném `UnauthorizedError` ("Tài khoản đã bị khoá") | Service / Negative |
| `TC-SVC-AUTH-04` | Đăng nhập bị chặn do Rate Limit | Thử sai 10 lần liên tiếp trong 15 phút | Lần thứ 11 ném `RateLimitedError` ngay cả khi nhập đúng mật khẩu | Service / Security |
| `TC-SVC-AUTH-05` | Đổi mật khẩu thành công cho Khách (`role="user"`) | SĐT `"0912345678"`, mật khẩu mới | Đổi mật khẩu thành công, tăng `token_version`, xoá toàn bộ Refresh Tokens | Service / Happy |
| `TC-SVC-AUTH-06` | **Chặn Reset Password công khai với Admin (Bảo mật 2026-08-15):** | SĐT của tài khoản `role="admin"` gọi `reset_password` | Ném `NotFoundError` ("Không tìm thấy tài khoản với số này"). Mật khẩu không đổi | Service / Security |
| `TC-SVC-AUTH-07` | Đồng nhất phản hồi Reset Password | SĐT admin vs SĐT không tồn tại trong DB | Trả về cùng một thông điệp lỗi `NotFoundError` (ngăn kẻ tấn công dò SĐT admin) | Service / Security |
| `TC-SVC-AUTH-08` | Thu hồi token sau khi đổi mật khẩu | Đổi mật khẩu -> Trình Access Token cũ hoặc Refresh Token cũ | Access Token bị từ chối do lệch `token_version`; Refresh Token bị từ chối do đã bị xoá | Service / Security |

---

### 3.2 `AppointmentService`

| ID | Tên Kịch bản | Dữ liệu Đầu vào | Kết quả Mong đợi | Loại kiểm thử |
|---|---|---|---|---|
| `TC-SVC-APPT-01` | Đặt lịch hợp lệ trong tương lai | `user`, `start_at = 15:00 ngày mai`, `note = "làm tóc"` | Tạo appointment `status="booked"`, `duration=60`, tính đủ 4 `slot_keys` | Service / Happy |
| `TC-SVC-APPT-02` | Từ chối đặt lịch trong quá khứ | `start_at = now - 1 giờ` | Ném `PastTimeError` ("Không thể đặt lịch trong quá khứ") | Service / Negative |
| `TC-SVC-APPT-03` | Từ chối đặt ngoài giờ mở cửa | `start_at = 03:00 sáng` (tiệm mở 08:00 - 19:00) | Ném `OutsideShopHoursError` | Service / Negative |
| `TC-SVC-APPT-04` | Từ chối đặt vào ngày nghỉ (`closed_days`) | `start_at` rơi vào ngày Chủ Nhật (`closed_days=[0]`) | Ném `OutsideShopHoursError` hoặc `ClosedDayError` | Service / Negative |
| `TC-SVC-APPT-05` | Tự động làm tròn mốc giờ về lưới 15 phút | `start_at = 15:07` -> gọi `create()` | Lịch được tạo với `start_at = 15:00` | Service / Happy |
| `TC-SVC-APPT-06` | **Khoá Idempotency (Chống tạo đúp):** | Cùng 1 user gọi `create()` 2 lần liên tiếp với cùng `start_at` | Lần 2 trả về chính Appointment đã tạo lần 1, không sinh lịch mới | Service / Edge Case |
| `TC-SVC-APPT-07` | Hai khách khác nhau đặt cùng khung giờ | Khách A đặt 15:00 thành công; Khách B đặt 15:00 | Khách B nhận `SlotTakenError` | Service / Conflict |
| `TC-SVC-APPT-08` | Giới hạn đặt lịch (Rate Limit 20 lịch/giờ) | 1 user tạo lịch thứ 21 trong vòng 1 giờ | Ném `RateLimitedError` (kiểm tra sau nhánh idempotency) | Service / Security |
| `TC-SVC-APPT-09` | Khách xem danh sách lịch sắp tới của mình | User A gọi `upcoming_for(user_A)` | Chỉ trả về các lịch `status="booked"` và `start_at >= now` của User A | Service / Happy |
| `TC-SVC-APPT-10` | Khách huỷ lịch của chính mình | User A huỷ lịch của User A | Cập nhật `status="cancelled"`, giải phóng slot | Service / Happy |
| `TC-SVC-APPT-11` | Khách cố tình huỷ lịch của người khác (IDOR) | User B cố huỷ lịch của User A | Ném `ForbiddenError` ("Bạn không có quyền huỷ lịch này") | Service / Security |
| `TC-SVC-APPT-12` | Admin huỷ lịch của bất kỳ khách nào | Admin gọi `cancel(admin_user, appt_id)` | Huỷ thành công lịch của khách | Service / Happy |
| `TC-SVC-APPT-13` | Huỷ lịch không tồn tại hoặc ID sai định dạng | Truyền `appt_id="invalid_id"` hoặc random ObjectId | Ném `NotFoundError` | Service / Negative |
| `TC-SVC-APPT-14` | Tìm slot trống (`find_free_slots`) | Ngày mai có tiệm mở 08:00-19:00, đã có lịch 10:00-11:00 | Trả về danh sách các slot 15p từ 08:00 đến 19:00 trừ khoảng 10:00-11:00 và quá khứ | Service / Happy |

---

### 3.3 `ShopService`

| ID | Tên Kịch bản | Dữ liệu Đầu vào | Kết quả Mong đợi | Loại kiểm thử |
|---|---|---|---|---|
| `TC-SVC-SHOP-01` | Lấy trạng thái tiệm khi đang rảnh | `is_busy=False` | Trả về `is_busy=False, busy_until=None` | Service / Happy |
| `TC-SVC-SHOP-02` | Lấy trạng thái tiệm khi đang bận trong hạn | `is_busy=True, busy_until = now + 30 phút` | Trả về `is_busy=True, busy_until` chính xác | Service / Happy |
| `TC-SVC-SHOP-03` | **Tự động về Rảnh khi hết giờ (Lazy Calculation):** | `is_busy=True` nhưng `busy_until < now` (ví dụ đã qua 5 phút) | Trả về `is_busy=False, busy_until=None` mà không cần admin bấm huỷ | Service / Business Logic |
| `TC-SVC-SHOP-04` | Chủ tiệm đặt bận theo khoảng phút (`set_busy`) | Chọn `30 phút` | Quy đổi thành `busy_until = now + 30m`, lưu DB, phát sự kiện realtime | Service / Happy |
| `TC-SVC-SHOP-05` | Chủ tiệm bấm rảnh tay trước hạn (`set_free`) | Tiệm đang bận, admin bấm "Tôi rảnh rồi" | Cập nhật `is_busy=False`, phát sự kiện realtime | Service / Happy |
| `TC-SVC-SHOP-06` | Cập nhật giờ mở cửa hợp lệ | Mở: `"08:30"`, Đóng: `"20:00"`, Nghỉ: `[1]` (Thứ Hai) | Lưu cấu hình thành công | Service / Happy |
| `TC-SVC-SHOP-07` | Cập nhật giờ mở cửa bất hợp lý | Giờ mở sau giờ đóng (`open_time="20:00"`, `close_time="08:00"`) | Ném `ValidationError` | Service / Negative |

---

### 3.4 `RateLimitService`

| ID | Tên Kịch bản | Dữ liệu Đầu vào | Kết quả Mong đợi | Loại kiểm thử |
|---|---|---|---|---|
| `TC-SVC-RATE-01` | Tách biệt `check()` và `hit()` | Gọi `check()` 10 lần | Bộ đếm không tăng, không bị khoá nhầm | Service / Unit |
| `TC-SVC-RATE-02` | Khoá IP sau 10 lần đăng nhập thất bại | 10 lần `hit()` từ cùng 1 IP | Lần `check()` thứ 11 trả về `is_limited=True` | Service / Security |
| `TC-SVC-RATE-03` | Chuẩn hoá khoá theo SĐT | Thử sai với `"0912345678"` và `"+84912345678"` | Cả 2 cùng tính vào 1 bucket giới hạn | Service / Security |
| `TC-SVC-RATE-04` | Tự động mở khoá sau khi hết thời gian TTL | Hết `LOGIN_WINDOW_SECONDS` (900s) | TTL index xoá doc; `check()` cho phép đăng nhập lại | Service / Happy |

---

### 3.5 `NotificationService` (Tỏa tin Realtime & Telegram Fail-soft)

| ID | Tên Kịch bản | Dữ liệu Đầu vào | Kết quả Mong đợi | Loại kiểm thử |
|---|---|---|---|---|
| `TC-SVC-NOTIF-01` | Phát thông báo khi tạo lịch mới | Gọi `appointment_created(appt)` | Bắn Socket.IO tới admin room và gửi tin nhắn Telegram tới chủ tiệm | Service / Happy |
| `TC-SVC-NOTIF-02` | Phát thông báo khi trạng thái tiệm đổi | Gọi `shop_status_changed(status)` | Broadcast Socket.IO tới toàn bộ client online | Service / Happy |
| `TC-SVC-NOTIF-03` | **Fail-soft khi Telegram sập mạng:** | Gửi Telegram gặp lỗi kết nối (Timeout, ConnectionRefused) | Nuốt lỗi, ghi log cảnh báo; việc đặt lịch trên web vẫn thành công 100% | Service / Resilience |
| `TC-SVC-NOTIF-04` | **Fail-soft khi Socket.IO lỗi:** | Socket.IO emit gặp lỗi nội bộ | Nuốt lỗi, ghi log; nghiệp vụ chính không bị gián đoạn | Service / Resilience |

---

## 5. TẦNG 4: AI AGENT & LANGGRAPH BOOKING GRAPH

### 4.1 Bộ Parser Thời Gian Tiếng Việt 3 Tầng (`timeparse.py`)

#### Tầng 1: Regex Matcher (`_match_regex`)

| ID | Câu đầu vào (Prompt) | Mốc hiện tại (`now`) | Kết quả mong đợi | Hành vi |
|---|---|---|---|---|
| `TC-PARSE-REG-01` | `"mai 3h chiều"` | `Thứ Sáu 07/08/2026 14:30` | `2026-08-08T15:00:00+07:00` | Khớp trọn vẹn, không gọi LLM |
| `TC-PARSE-REG-02` | `"hôm nay 10 giờ sáng"` | `Thứ Sáu 07/08/2026 08:00` | `2026-08-07T10:00:00+07:00` | Khớp trọn vẹn, không gọi LLM |
| `TC-PARSE-REG-03` | `"ngày kia 15h"` | `Thứ Sáu 07/08/2026 14:30` | `2026-08-09T15:00:00+07:00` | Khớp trọn vẹn, không gọi LLM |
| `TC-PARSE-REG-04` | `"mốt 9g sáng làm móng"` | `Thứ Sáu 07/08/2026 14:30` | `2026-08-09T09:00:00+07:00` | Khớp trọn vẹn, không gọi LLM |
| `TC-PARSE-REG-05` | `"mai 12 giờ trưa"` | `Thứ Sáu 07/08/2026 14:30` | `2026-08-08T12:00:00+07:00` | Khớp trọn vẹn, không gọi LLM |
| `TC-PARSE-REG-06` | `"3h chiều"` | Bất kỳ | `None` (Nhường LLM do thiếu ngày) | Ca âm: Không được đoán |
| `TC-PARSE-REG-07` | `"sáng mai"` | Bất kỳ | `None` (Nhường LLM do thiếu giờ) | Ca âm: Không được đoán |
| `TC-PARSE-REG-08` | `"thứ Năm"` | Bất kỳ | `None` (Nhường LLM do không rõ tuần nào) | Ca âm: Tránh nuốt "thứ Năm tuần sau" |
| `TC-PARSE-REG-09` | `"cuối tuần", "mùng 2"` | Bất kỳ | `None` (Nhường LLM) | Ca âm |

#### Tầng 2 & 3: LLM Structured Output & Lớp Chốt (`_guard`)

| ID | Tình huống kiểm thử | Dữ liệu Đầu vào / Ứng viên | Kết quả sau Lớp Chốt (`_guard`) | Rationale / Ý nghĩa |
|---|---|---|---|---|
| `TC-PARSE-GRD-01` | Mốc thời gian hợp lệ | `ParsedTime(start_at=2026-08-08 15:00+07:00)` | Giữ nguyên `2026-08-08 15:00+07:00` | Ca dương |
| `TC-PARSE-GRD-02` | **Thiếu Timezone Offset (Bẫy hay gặp):** | `ParsedTime(start_at=2026-08-08 15:00)` (Naive) | Tự động gắn timezone VN `+07:00` | Ngăn chặn lỗi lệch 7 tiếng khi lưu DB |
| `TC-PARSE-GRD-03` | Thời gian trong quá khứ | `"hôm nay 10h sáng"` khi đồng hồ là `14:30` | `start_at=None`, `missing=["ngày khác — giờ đó qua mất rồi"]` | Chặn giờ đã qua |
| `TC-PARSE-GRD-04` | **Lệch năm (Year Drift Bug - Bẫy 6 & 10):** | LLM trả `2025-08-08` do dữ liệu pre-train | `start_at=None`, `missing=["ngày khác — giờ đó qua mất rồi"]` | Bắt lỗi lệch năm của mô hình AI |
| `TC-PARSE-GRD-05` | Đặt lịch quá xa (> 90 ngày) | `start_at = now + 120 ngày` | `start_at=None`, `missing=["ngày gần hơn"]` | Giới hạn khung 90 ngày tối đa cho tiệm |
| `TC-PARSE-GRD-06` | Biên 90 ngày | `start_at = now + 89 ngày` | Cho phép qua (`start_at` hợp lệ) | Biên cho phép |
| `TC-PARSE-GRD-07` | **Không kiểm tra giờ mở cửa ở Guard:** | `start_at = 03:00 sáng` | `_guard` cho qua (để `find_free_slots` và Service xử lý) | Tránh trùng lặp logic ở 2 nơi |
| `TC-PARSE-GRD-08` | Timeout hoặc LLM lỗi | Azure timeout > 8s hoặc API chết | Trả về `ParsedTime(start_at=None, missing=["giờ cụ thể"])` | Fail-soft: Agent hỏi lại khách lịch sự |
| `TC-PARSE-GRD-09` | **Tag và Streaming Filter (Bẫy 8):** | Model parser khởi tạo | Phải mang `tags=["timeparse"]` và `streaming=False` | Tránh stream JSON rác ra màn hình khách |

---

### 4.2 Khối Bối Cảnh Tất Định (`context.py`)

| ID | Tên Kịch bản | Dữ liệu Đầu vào | Kết quả Mong đợi | Loại kiểm thử |
|---|---|---|---|---|
| `TC-CTX-01` | **Mốc ngày hiện tại ở dòng đầu (Bẫy 6):** | `now = 2026-08-07 14:30 (Thứ Sáu)` | Dòng đầu tiên của khối bối cảnh: `"Hôm nay là Thứ Sáu, ngày 07/08/2026, bây giờ là 14:30."` | Prompt / Integrity |
| `TC-CTX-02` | Danh tính và xưng hô | User: `"Nguyễn Thị Lan"`, Phone: `"0912345678"` | `"Bạn đang nói chuyện với: Nguyễn Thị Lan (0912345678). Xưng hô: gọi 'chị Lan', tự xưng 'em'."` | Prompt / Integrity |
| `TC-CTX-03` | Lịch sắp tới của khách | Khách có lịch `Thứ Bảy 08/08 15:00 làm tóc` | Nêu rõ lịch sắp tới kèm ngày giờ và ghi chú | Prompt / Integrity |
| `TC-CTX-04` | Trạng thái tiệm hiện tại | Tiệm đang bận đến 16:00 | `"Chủ tiệm: đang bận, xong lúc 4:00 chiều."` (Không nói "còn 90 phút") | Prompt / Integrity |
| `TC-CTX-05` | **Không đặt bối cảnh vào System Prompt (Bẫy 9):** | Khối bối cảnh | Được inject dưới dạng 1 `HumanMessage`/`SystemMessage` động ngay trước tin nhắn mới, không trộn vào System Prompt tĩnh | Prompt Caching |

---

### 4.3 Supervisor Routing Node (`supervisor.py`)

| ID | Tin nhắn của khách | Phân loại mong đợi | Rationale |
|---|---|---|---|
| `TC-SUP-01` | `"Mai 3h chiều còn chỗ làm tóc không em?"` | `route = "booking"` | Ý định đặt lịch |
| `TC-SUP-02` | `"Tiệm mình đang đông không em ơi?"` | `route = "status"` | Ý định hỏi bận/rảnh |
| `TC-SUP-03` | `"Cháu bán bảo hiểm nhân thọ không?"` | `route = "refuse"` | Ngoài phạm vi tiệm nail-tóc |
| `TC-SUP-04` | `"Thời tiết hôm nay thế nào em?"` | `route = "refuse"` | Ngoài phạm vi tiệm nail-tóc |
| `TC-SUP-05` | `"Xem giùm chị mấy giờ chị có lịch"` | `route = "booking"` | Tra cứu lịch cá nhân |
| `TC-SUP-06` | `"Huỷ cái lịch ngày mai giúp chị"` | `route = "booking"` | Huỷ lịch |
| `TC-SUP-07` | Ngân sách token của Supervisor | Prompt + 4 tin nhắn gần nhất | Tổng token input < 300 tokens (không nhồi memory/tool schemas) |

---

### 4.4 Booking Subagent & Bộ 5 Read-only Tools (`tools.py`)

| ID | Tên Kịch bản | Hành vi của Subagent / Tool | Kết quả Mong đợi |
|---|---|---|---|
| `TC-TOOL-01` | **Không có tool ghi lịch (Bẫy 7):** | Kiểm tra danh sách tools cấp cho BookingAgent | Đúng 5 tools: `parse_time`, `find_free_slots`, `propose_appointment`, `list_my_appointments`, `cancel_appointment`. **Tuyệt đối không có `create_appointment`** |
| `TC-TOOL-02` | `parse_time` | Nhận chuỗi văn bản ngày giờ từ khách | Gọi bộ phân tích thời gian 3 tầng |
| `TC-TOOL-03` | `find_free_slots` | Nhận ngày `YYYY-MM-DD` | Trả về danh sách mốc 15p còn trống trong giờ mở cửa |
| `TC-TOOL-04` | `propose_appointment` | Khách chọn được giờ hợp lệ (VD: 15:00 mai) | Kiểm tra slot còn trống -> Ghi `pending_confirmation` vào Mongo -> Hỏi khách xác nhận |
| `TC-TOOL-05` | `propose_appointment` khi giờ vừa bị người khác lấy | Giờ đã bị đặt trước đó 1 giây | Báo giờ đã kín, gợi ý 2 khung giờ trống gần nhất, **không ghi cờ pending** |
| `TC-TOOL-06` | `list_my_appointments` | Khách hỏi lịch của mình | Lấy danh sách lịch từ DB của chính `user_id` từ JWT |
| `TC-TOOL-07` | `cancel_appointment` | Khách yêu cầu huỷ lịch | Bắt buộc gọi `list_my_appointments` trước để lấy ID; chỉ huỷ đúng lịch của khách |
| `TC-TOOL-08` | Chống tiêm nhiễm `user_id` qua câu chat | Khách gõ: "Huỷ lịch của số 0988888888" | Tool lấy `user_id` cố định từ JWT context, không thể bị lừa thao tác dữ liệu người khác |

---

### 4.5 Confirm Node & Cơ Chế 0-LLM (`confirm.py`)

| ID | Tên Kịch bản | Trạng thái & Tin nhắn khách | Hành vi & Kết quả mong đợi |
|---|---|---|---|
| `TC-CONF-01` | Xác nhận đồng ý bằng tiếng Việt | Cờ `pending` có hiệu lực + Khách nhắn: `"ừ"`, `"Ừ"`, `"đúng rồi"`, `"ok"`, `"OK em"`, `"vâng"`, `"dạ đúng"`, `"được"` | Nhánh `route_from_state` đi thẳng vào node `confirm` (0 lượt LLM). Đọc `start_at` từ Mongo -> Gọi `AppointmentService.create` -> Tạo lịch thành công -> Xoá cờ pending |
| `TC-CONF-02` | **Đọc giờ từ Mongo, không đọc từ LLM (Bẫy 115):** | Cờ pending lưu `15:00`. Model ở lượt trước nhắc lại sai thành `05:00` | Lịch được tạo đúng vào lúc `15:00` do node `confirm` lấy trực tiếp từ Mongo |
| `TC-CONF-03` | Từ chối hoặc đổi ý | Cờ `pending` có hiệu lực + Khách nhắn: `"không"`, `"thôi khỏi"`, `"đổi giờ khác"`, `"để mai đi"` | Xoá cờ `pending_confirmation`, không tạo lịch, trả lời nhẹ nhàng |
| `TC-CONF-04` | Xung đột slot lúc bấm xác nhận | Cờ pending hợp lệ nhưng trong lúc chờ, admin đã xếp người khác vào giờ đó | Node `confirm` bắt `SlotTakenError`, xoá cờ pending, báo khách thông cảm chọn giờ khác |
| `TC-CONF-05` | Cờ xác nhận đã hết hạn (> 10 phút) | Cờ tạo cách đây 15 phút + Khách nhắn: `"ừ"` | Bỏ qua node confirm, định tuyến lại qua Supervisor như tin nhắn mới |

---

### 4.6 Status Subagent & Langfuse Tracing

| ID | Tên Kịch bản | Dữ liệu & Môi trường | Kết quả Mong đợi |
|---|---|---|---|
| `TC-STAT-01` | Trả lời trạng thái bận | Tiệm bận đến 15:30 | Trả lời "Dạ hiện chủ tiệm đang bận, dự kiến xong lúc 3:30 chiều ạ" (Không nói "còn 45 phút") |
| `TC-FUSE-01` | **Langfuse Callback Handler (Bẫy 3 & 4):** | `CallbackHandler()` khởi tạo | Không truyền credential vào constructor của CallbackHandler. Metadata mang đúng `langfuse_user_id` và `langfuse_session_id` |
| `TC-FUSE-02` | Langfuse Server không khả dụng | Tắt kết nối tới Langfuse | App chạy bình thường, hội thoại không bị gián đoạn, log warning |

---

## 6. TẦNG 5: REALTIME COMMUNICATION & SOCKET.IO STREAMING

### 6.1 Lọc Token Stream qua Thẻ Tag (`respond`)

| ID | Nguồn Token | Thẻ Tag đính kèm | Socket.IO Event | Kết quả ra phía Client |
|---|---|---|---|---|
| `TC-STREAM-01` | Subagent phản hồi cho khách | `tags=["respond"]` | Emit `token` (`{text: "..."}`) | Chữ xuất hiện dần trên màn hình khách |
| `TC-STREAM-02` | **Supervisor Router (Bẫy 8):** | `tags=["supervisor"]` | **Bị chặn (Không emit)** | Khách KHÔNG thấy JSON định tuyến |
| `TC-STREAM-03` | **TimeParser LLM (Bẫy 8):** | `tags=["timeparse"]` | **Bị chặn (Không emit)** | Khách KHÔNG thấy JSON parse ngày giờ |

---

### 6.2 Chuỗi Sự Kiện Gọi Tool & Trạng Thái Chat

| ID | Giai đoạn thực thi Agent | Socket.IO Event | Payload | Giao diện React hiển thị |
|---|---|---|---|---|
| `TC-EVT-01` | Nhận tin nhắn từ khách | `turn_started` | `{}` | Hiện 3 chấm nhấp nháy "Đang đọc..." |
| `TC-EVT-02` | Bắt đầu gọi tool `find_free_slots` | `tool_started` | `{"name": "find_free_slots"}` | Spinner + "Đang xem lịch trống..." |
| `TC-EVT-03` | Tool `find_free_slots` chạy xong | `tool_finished` | `{"name": "find_free_slots", "ok": true}` | Thu gọn thành dấu tích: "Đã xem lịch trống" |
| `TC-EVT-04` | Lượt chat kết thúc thành công | `complete` | `{"message_id": "..."}` | Ẩn loading, cố định tin nhắn |
| `TC-EVT-05` | Đồ thị gặp lỗi hệ thống | `error` | `{"message": "..."}` | Hiện thông báo lỗi thân thiện kèm nút gọi tiệm |

---

### 6.3 Broadcast & Quản Lý Kết Nối

| ID | Tên Kịch bản | Hành động kích hoạt | Phạm vi gửi | Kết quả Mong đợi |
|---|---|---|---|---|
| `TC-SOC-01` | Đổi trạng thái tiệm | Admin bấm bận 30p trên web/Telegram | Broadcast toàn server | Mọi màn hình khách đang mở tự động cập nhật thẻ trạng thái |
| `TC-SOC-02` | Lịch mới được tạo | Khách đặt xong qua AI hoặc Admin tạo | Phòng `admins` | Màn hình admin tự động chèn lịch mới lên đầu kèm tag "MỚI" |
| `TC-SOC-03` | **Đếm tham chiếu Socket (Bẫy 95):** | 2 React hooks cùng dùng `acquireSocket` | 1 hook unmount | Kết nối Socket.IO vẫn giữ cho hook còn lại, chỉ ngắt khi ref count = 0 |

---

## 7. TẦNG 6: TELEGRAM BOT (KÊNH CHỦ TIỆM)

### 7.1 Lọc Quyền & Xử Lý Nút Bấm Tất Định

| ID | Tên Kịch bản | Dữ liệu Đầu vào | Hành vi của Bot Telegram | Kết quả |
|---|---|---|---|---|
| `TC-TELE-01` | Tin nhắn từ Chat ID lạ (Không phải Admin) | `chat_id = 999999` (không có trong `TELEGRAM_ADMIN_CHAT_IDS`) | **Bỏ qua im lặng (Silent drop)**, không trả lời, không log thông tin nhạy cảm | An toàn: Người lạ không dò được bot |
| `TC-TELE-02` | Admin bấm nút "Hôm nay" | Admin gửi message `"Hôm nay"` | Bot tra cứu và trả về danh sách lịch trong ngày hôm nay | Trả lời nhanh, không tốn token LLM |
| `TC-TELE-03` | Admin bấm nút "Ngày mai" | Admin gửi message `"Ngày mai"` | Bot trả về danh sách lịch ngày mai | Trả lời chính xác |
| `TC-TELE-04` | Admin bấm "Tôi đang bận" | Admin gửi message `"Tôi đang bận"` | Bot gửi Inline Keyboard: `[15 phút] [30 phút] [1 tiếng] [2 tiếng]` | Hiển thị 4 nút chọn nhanh |
| `TC-TELE-05` | **Trả lời Callback dưới 10s (Bẫy 12):** | Admin click nút `[30 phút]` | Bot gọi `answerCallbackQuery` **TRƯỚC** khi gọi service set_busy | Nút không bị quay vòng, chống bấm đúp |
| `TC-TELE-06` | Admin bấm "Tôi rảnh rồi" | Admin click nút "Tôi rảnh rồi" | Bot gọi `shop_status_service.set_free()`, broadcast Socket.IO | Tiệm về trạng thái rảnh |
| `TC-TELE-07` | Nhận thông báo lịch mới | Khách đặt lịch qua web app | Bot tự động gửi tin nhắn báo cho Admin gồm: Tên khách, SĐT, Giờ hẹn, Ghi chú | Nhận tin realtime |

---

### 7.2 Long Polling & Khôi Phục Mật Khẩu Admin

| ID | Tên Kịch bản | Môi trường / Lệnh | Hành vi hệ thống | Kết quả |
|---|---|---|---|---|
| `TC-TELE-08` | **Ràng buộc 1 Worker (Bẫy 51 & 53):** | Chạy app với 1 worker | 1 kết nối duy nhất tới Telegram `getUpdates` | Long polling ổn định, không lỗi 409 |
| `TC-TELE-09` | Chạy nhiều worker (Cố tình vi phạm) | Chạy app với `--workers 2` | 2 tiến trình cùng poll | Telegram trả về `409 Conflict` liên tục (Xác nhận đúng thiết kế chặn) |
| `TC-TELE-10` | Mất mạng khi `get_updates` | Mạng ngắt trong lúc đang polling | Hàm ném ngoại lệ ra ngoài để vòng lặp ghi log và thử lại | Không bị nuốt lỗi ngầm |
| `TC-TELE-11` | Đặt lại mật khẩu Admin qua Telegram | Admin gửi lệnh `/reset_admin_password` | Bot sinh mật khẩu tạm ngẫu nhiên, cập nhật User Admin, nhắn mật khẩu cho Admin | Admin dùng mật khẩu mới đăng nhập web |

---

## 8. TẦNG 7: REST API ENDPOINTS & MIDDLEWARES

### 8.1 Authentication Endpoints (`/api/v1/auth`)

| Endpoint | Method | Kịch bản kiểm thử | Input | Mã HTTP | Cookie / Header |
|---|---|---|---|---|---|
| `/login` | `POST` | Đăng nhập hợp lệ | `{"phone": "0912345678", "password": "..."}` | `200 OK` | Set-Cookie: `refresh_token` (HttpOnly, Secure, SameSite=Strict, Path=/api/v1/auth) |
| `/login` | `POST` | Sai mật khẩu | `{"phone": "0912345678", "password": "wrong"}` | `401 Unauthorized` | Không có cookie |
| `/login` | `POST` | Vượt quá 10 lần sai | Sau 10 lần 401 | `429 Too Many Requests` | Khoá 15 phút |
| `/refresh` | `POST` | Xoay vòng Refresh Token | Cookie `refresh_token` hợp lệ | `200 OK` | Cấp Access Token mới trong JSON body + Cookie Refresh Token mới |
| `/refresh` | `POST` | Refresh Token đã bị dùng lại (Replay) | Cookie `refresh_token` cũ ngoài 30s | `401 Unauthorized` | Xoá toàn bộ chuỗi token |
| `/logout` | `POST` | Đăng xuất | Cookie `refresh_token` | `204 No Content` | Set-Cookie xoá refresh token (`Max-Age=0`) |
| `/reset-password` | `POST` | Khách quên mật khẩu | `{"phone": "0912345678", "new_password": "..."}` | `204 No Content` | Đổi mật khẩu thành công |
| `/reset-password` | `POST` | Admin gọi quên mật khẩu | `{"phone": "0901234567", "new_password": "..."}` | `404 Not Found` | Bị từ chối (bảo vệ tài khoản admin) |
| `/users` | `GET` | Khách thường cố xem danh sách User | Header `Authorization: Bearer <user_token>` | `403 Forbidden` | Chặn truy cập trái phép |
| `/users` | `GET` | Admin xem danh sách User | Header `Authorization: Bearer <admin_token>` | `200 OK` | Trả về danh sách khách hàng |

---

### 8.2 Appointments & Shop Endpoints

| Endpoint | Method | Kịch bản kiểm thử | Input | Mã HTTP | Kết quả |
|---|---|---|---|---|---|
| `/appointments` | `POST` | Khách tạo lịch trực tiếp | `{"start_at": "...", "note": "..."}` | `201 Created` | Trả về thông tin lịch |
| `/appointments` | `POST` | Tạo lịch trùng giờ | Cùng khung giờ đã có người đặt | `409 Conflict` | Thông báo slot đã kín |
| `/appointments/my` | `GET` | Xem lịch cá nhân | Token của User A | `200 OK` | Chỉ trả về lịch của User A |
| `/appointments/{id}` | `DELETE` | Khách huỷ lịch của mình | `id` lịch của chính mình | `204 No Content` | Huỷ thành công |
| `/appointments/{id}` | `DELETE` | Khách huỷ lịch người khác | `id` lịch của người khác | `403 Forbidden` | Bị từ chối |
| `/shop/status` | `GET` | Xem trạng thái tiệm (Public) | Không cần token | `200 OK` | `{"is_busy": false, "busy_until": null}` |
| `/shop/busy` | `POST` | Admin đặt bận | `{"minutes": 30}` + Token Admin | `200 OK` | Trả về `busy_until` mới |
| `/shop/busy` | `POST` | Khách thường cố đặt bận | Token User | `403 Forbidden` | Bị từ chối |
| `/shop/hours` | `PUT` | Admin đổi giờ mở cửa | Token Admin + Cấu hình giờ | `200 OK` | Giờ mở cửa được cập nhật |

---

### 8.3 Health Check & Middlewares

| Endpoint | Method | Tình huống kiểm thử | Kết quả mong đợi | Rationale |
|---|---|---|---|---|
| `/api/v1/health/` | `GET` | MongoDB đang hoạt động tốt | `200 OK` `{"status": "healthy"}` | Hệ thống sẵn sàng |
| `/api/v1/health/` | `GET` | MongoDB bị sập hoặc mất kết nối | `503 Service Unavailable` | Báo container unhealthy để restart |
| `/api/v1/health` | `GET` | Không có dấu gạch chéo cuối `/` | `307 Temporary Redirect` tới `/api/v1/health/` | Ràng buộc trailing slash |
| `/api/v1/*` | Mọi | Gặp lỗi nghiệp vụ `AppError` | HTTP status code tương ứng (400, 401, 403, 404, 409, 422, 429, 503), Body JSON: `{"error": {"code": "...", "message": "tiếng Việt"}}` | Không bao giờ để lộ Python traceback ra ngoài |

---

## 9. TẦNG 8: FRONTEND REACT (UI/UX CHO NGƯỜI LỚN TUỔI)

### 9.1 Design Tokens & Tiêu chuẩn Accessibility

| ID | Tiêu chuẩn UI/UX | Quy định thiết kế | Kiểm tra thực tế | Kết quả |
|---|---|---|---|---|
| `TC-UI-01` | **Cỡ chữ nền tối thiểu:** | Base font size >= 18px (hoặc 19px) | Đo computed style trên các phần tử văn bản | Đạt chuẩn đọc thoải mái |
| `TC-UI-02` | **Kích thước nút bấm:** | Chiều cao >= 56px, full width (rộng 100%) | Đo chiều cao và chiều rộng button | Dễ bấm bằng ngón tay cái, không bấm trượt |
| `TC-UI-03` | **Tương phản màu sắc (AA >= 4.5:1):** | Dùng `#0369A1` (5.93), `#047857` (5.48), `#B91C1C` (5.91). **Cấm dùng `#0284C7` (4.10) hay `#059669` (3.77)** | Chạy công cụ kiểm tra độ tương phản WCAG AA | Đạt độ tương phản cao, chống lóa cho mắt kém |
| `TC-UI-04` | **Phông chữ tiếng Việt:** | Dùng `Be Vietnam Pro` | Kiểm tra CSS font-family | Hiển thị đầy đủ dấu tiếng Việt không bị lỗi font |
| `TC-UI-05` | **Trạng thái bằng Chữ:** | Mọi trạng thái bận/rảnh, thành công/thất bại phải diễn đạt bằng chữ | Không dùng icon/màu sắc đơn lẻ | Người mù màu hoặc nhìn kém vẫn hiểu rõ |
| `TC-UI-06` | **Giảm chuyển động:** | `prefers-reduced-motion: reduce` | Tắt mọi hiệu ứng xoay spinner, nhấp nháy | Tránh gây chóng mặt cho người già |

---

### 9.2 Trạng Thái Tiệm & Client-side Timer

| ID | Tên Kịch bản | Trạng thái từ Server | Hành vi của Frontend | Kết quả |
|---|---|---|---|---|
| `TC-UI-STAT-01` | Hiển thị mốc giờ xong | `busy_until = "2026-08-07T15:30:00+07:00"` | Hiện dòng chữ: `"Chủ tiệm đang bận — xong lúc 3:30 chiều"` | **Không đếm ngược** "còn 30 phút" |
| `TC-UI-STAT-02` | **Hẹn giờ lật thẻ bằng `minutes_left` (Bẫy 14):** | `minutes_left = 30` | `useShopStatus` đặt `setTimeout(30 * 60 * 1000)` | Tới giờ, thẻ tự lật sang "Chủ tiệm đang rảnh" và gọi nhẹ `get_status()` để đồng bộ |
| `TC-UI-STAT-03` | Admin đổi trạng thái giữa chừng | Đang hẹn giờ 30p, server gửi `shop_status_changed` mới | Huỷ `clearTimeout` cũ, đặt hẹn giờ mới theo giá trị mới | Không bị lật thẻ sai thời điểm |

---

### 9.3 Streaming Chat & Khả Năng Chịu Lỗi Mạng

| ID | Tên Kịch bản | Sự kiện mạng | Hành vi giao diện | Trải nghiệm người dùng |
|---|---|---|---|---|
| `TC-UI-CHAT-01` | Tiến trình gọi Tool | Nhận `tool_started` | Hiển thị câu tiếng Việt tương ứng (VD: "Đang xem lịch trống...") | Khách biết máy đang làm việc |
| `TC-UI-CHAT-02` | **Tên tool kỹ thuật không lộ ra ngoài:** | Nhận tool lạ không có trong bảng ánh xạ | Hiện dòng chữ chung: `"Đang xử lý..."` | Không hiển thị `find_free_slots` hay `parse_time` |
| `TC-UI-CHAT-03` | **Mất mạng khi đang stream chữ (Bẫy 119):** | Mất kết nối internet giữa lúc đang nhận token | **Giữ nguyên toàn bộ chữ đã hiện**, thêm dòng thông báo `"Mất mạng, đang thử lại..."` | **Không được xoá chữ** (người già không bị hoảng tưởng mình làm hỏng) |
| `TC-UI-CHAT-04` | Nhập liệu bằng giọng nói (Speech API) | Trình duyệt hỗ trợ Web Speech API | Hiện nút Micro to, bấm để nói tiếng Việt | Hỗ trợ người không quen gõ phím |
| `TC-UI-CHAT-05` | Trình duyệt không hỗ trợ Web Speech | Trình duyệt cũ / WebView | Ẩn nút Micro nhẹ nhàng, giữ ô nhập text | Không báo lỗi crash |

---

### 9.4 Lịch Sử Trò Chuyện & Xem Lại Ngày Cũ

| ID | Tên Kịch bản | Thao tác người dùng | Giao diện hiển thị | Rationale |
|---|---|---|---|---|
| `TC-UI-HIST-01` | Mở app vào ngày hôm sau | Khung chat hôm nay trống | Hiển thị link to: `"Xem các lần trò chuyện trước"` | Tránh người dùng tưởng bị mất lịch đã đặt |
| `TC-UI-HIST-02` | Danh sách lịch sử các ngày | Mở màn hình lịch sử | Thẻ lớn từng ngày: `"Thứ Ba, 12 tháng 8"` (viết đủ chữ, không viết `12/08`) kèm trích dẫn câu đầu | Dễ nhận diện câu chuyện cũ |
| `TC-UI-HIST-03` | Xem lại hội thoại ngày cũ (Read-only) | Bấm vào ngày 12 tháng 8 | Xem lại bong bóng chat cũ. **Không có ô nhập tin nhắn**. Chỗ ô nhập là dòng chữ: `"Đây là cuộc trò chuyện ngày 12 tháng 8. Anh chị muốn nhắn thì quay về hôm nay"` kèm nút to | Rõ ràng, không dùng ô input xám gây khó hiểu |

---

## 10. TẦNG 9: MA TRẬN EDGE CASES & FAILURE MODES TOÀN DIỆN

| ID | Lĩnh vực | Tình huống Cực hạn (Edge Case / Failure Mode) | Rủi ro nếu không xử lý | Cơ chế bảo vệ & Xử lý chuẩn |
|---|---|---|---|---|
| `EC-01` | **Concurrency** | 2 khách cùng xác nhận 1 khung giờ duy nhất trong cùng 1 mili-giây | Cả 2 đều nghĩ mình đặt thành công (Overbooking) | MongoDB Unique Partial Index trên `slot_keys` chỉ cho phép 1 transaction ghi thành công. Người thứ 2 nhận `SlotTakenError` và AI gợi ý ngay 2 khung giờ trống kế tiếp. |
| `EC-02` | **Data Trap** | Huỷ liên tiếp nhiều lịch hẹn trong ngày | Mongo sparse index coi mảng rỗng `[]` là `undefined` và ném lỗi trùng khoá `E11000 dup key: { : undefined }` làm hỏng hệ thống | Dùng **Partial Index** `{ status: "booked" }`. Khi huỷ chỉ cập nhật `status="cancelled"`, giữ nguyên `slot_keys`. |
| `EC-03` | **AI Hallucination** | LLM suy đoán ngày dựa trên dữ liệu huấn luyện (VD: trả về năm 2025 thay vì 2026) | Lịch bị lưu sai năm mà chuỗi ISO vẫn hợp lệ | Lớp chốt `_guard` chặn mọi ngày ngoài khoảng `[now, now + 90 ngày]`. Mọi mốc sai năm bị từ chối lập tức. |
| `EC-04` | **AI Hallucination** | LLM chép sai giờ từ lượt trước (15:00 thành 05:00) | Khách hẹn 3h chiều bị lưu thành 5h sáng | Node `confirm` lấy giá trị `start_at` trực tiếp từ MongoDB, **không cho phép LLM truyền lại chuỗi thời gian**. |
| `EC-05` | **Security / Auth** | Điện thoại sóng yếu gửi 2 request Refresh Token song song khi Access Token hết hạn | Cơ chế Token Rotation thông thường coi request thứ 2 là Replay Attack và khoá luôn tài khoản của khách | **Cửa sổ ân hạn 30 giây (Grace Period):** Cấp cặp token mới hợp lệ cho cả 2 request trong vòng 30s. Sau 30s mới kích hoạt thu hồi toàn bộ token family. |
| `EC-06` | **Security / Auth** | Kẻ xấu biết SĐT Admin, gọi `POST /auth/reset-password` để chiếm quyền điều khiển tiệm | Lộ toàn bộ SĐT khách hàng, mất quyền kiểm soát tiệm | Chặn `role == "admin"` tại `AuthService.reset_password`, trả về `404 Not Found` giống hệt SĐT không tồn tại. Admin chỉ được reset qua Bot Telegram đã uỷ quyền. |
| `EC-07` | **Security / Auth** | Đổi mật khẩu nhưng kẻ tấn công vẫn giữ Refresh Token cũ | Kẻ tấn công tiếp tục lấy Access Token mới vô thời hạn | Đổi mật khẩu thực hiện 2 việc nguyên tử: tăng `User.token_version` (vô hiệu Access Token) và **xoá sạch mọi Refresh Token** trong DB. |
| `EC-08` | **Rate Limit Bypass** | Kẻ tấn công đổi format SĐT (`0912...`, `+84912...`, `84912...`) hoặc đổi IP liên tục | Vượt qua bộ đếm rate limit để brute-force mật khẩu | SĐT được chuẩn hoá trước khi làm khoá rate limit. SĐT sai format vẫn bị tính vào bucket rate limit theo IP. |
| `EC-09` | **Infrastructure** | Telegram Bot deploy với nhiều worker (`--workers 4`) | Telegram liên tục trả về `409 Conflict`, lúc nhận được webhook/polling lúc không | Cố định kiến trúc đúng **1 worker duy nhất**. Long polling chạy trong lifespan asyncio task. |
| `EC-10` | **Infrastructure** | MongoDB bị ngắt kết nối đột ngột | Ứng dụng treo ngầm, khách chờ vô hạn | Fail-hard lập tức: Health check trả 503, API trả lỗi 503 tiếng Việt kèm hotline gọi điện trực tiếp cho tiệm. |
| `EC-11` | **AI / Network** | Azure OpenAI gặp sự cố hoặc phản hồi chậm (> 8s) | Giao diện chat của khách bị treo đơ | Fail-soft: Timeout 8s trả về fallback `missing=["giờ cụ thể"]`, AI lịch sự hỏi lại khách hoặc hiện hotline tiệm. |
| `EC-12` | **Observability** | Langfuse Cloud/Self-hosted bị sập hoặc quá tải | Lượt chat của khách bị báo lỗi 500 | Callback handler nuốt mọi lỗi kết nối của Langfuse; ghi log warning; lượt chat vẫn stream mượt mà cho khách. |
| `EC-13` | **Timezone** | Khách nhắn lúc 23:59 ngày hôm nay, xác nhận lúc 00:01 ngày hôm sau | Lịch sử chat bị cắt theo ngày làm mất ngữ cảnh câu xác nhận "ừ" | Cơ chế nạp lịch sử lấy toàn bộ tin trong ngày VN **cộng thêm mọi tin nhắn trong 30 phút gần nhất**. |
| `EC-14` | **Client Clock Skew** | Đồng hồ trên điện thoại của khách bị chỉnh sai 15 phút | Thẻ trạng thái tiệm đếm ngược sai hoặc lật thẻ sai giờ | Client dùng `minutes_left` (khoảng tương đối) để đặt `setTimeout`, không lấy `busy_until` trừ giờ máy khách. Hiển thị thì dùng mốc giờ tuyệt đối của server. |

---

## 11. HƯỚNG DẪN THỰC THI KIỂM THỬ (EXECUTION RUNBOOK)

```bash
# 1. Chạy toàn bộ test suite (yêu cầu Mongo chạy tại localhost:27017)
pytest

# 2. Chạy riêng tầng Domain Service & Repository (Unit & Integration không AI)
pytest tests/test_slots.py tests/test_phone.py tests/test_appointment_service.py tests/test_auth_service.py

# 3. Chạy kiểm tra bộ Parser Thời gian (Regex + Guard + Mock LLM)
pytest tests/test_timeparse.py -v

# 4. Chạy kiểm tra đồ thị LangGraph & Stream Events (Mocked LLM)
pytest tests/test_supervisor.py tests/test_subagents.py tests/test_confirm.py tests/test_graph_events.py

# 5. Chạy kiểm tra Telegram Bot & Socket Notifier (Tất định)
pytest tests/test_telegram_handlers.py tests/test_telegram_bot.py tests/test_socket_notifier.py

# 6. Chạy Integration test gọi Azure OpenAI thật (Chỉ chạy thủ công khi cần đo lường, tốn token)
pytest tests/test_timeparse_llm.py -m llm -v
```

---
*Tài liệu được biên soạn dựa trên các quyết định kiến trúc tại `CONTEXT.md`, `specs/2026-08-06-booking-nail-toc/` và 283+ automated test cases của dự án Salona Booking.*
