# Đặt lại mật khẩu bằng OTP qua Zalo ZNS

## Vì sao làm

Luồng quên mật khẩu hiện tại cho phép **bất kỳ ai biết SĐT cũng đổi được mật khẩu** của tài khoản đó, không cần đăng nhập. Đây từng là quyết định có chủ ý (xem `CONTEXT.md` mục "Rủi ro đã biết và chấp nhận"), đánh đổi bảo mật lấy thao tác tối giản cho khách lớn tuổi.

Hai điều làm quyết định đó không còn đứng vững:

1. Nó áp cả cho **tài khoản admin**, điều spec không nhắc tới. Chiếm được admin là xem được SĐT toàn bộ khách, huỷ mọi lịch, đổi giờ mở cửa. Khác hẳn mức thiệt hại khi một khách bị chiếm.
2. Endpoint còn phân biệt SĐT có tài khoản (204) với SĐT không có (404), nên dò được ai là khách của tiệm.

OTP thay điều kiện "biết SĐT" bằng "đang cầm SIM đó" — vẫn không cần khách nhớ email hay câu hỏi bí mật.

Mục "Rủi ro đã biết và chấp nhận" trong `CONTEXT.md` hết hiệu lực khi bản này triển khai xong.

## Phạm vi

**Trong phạm vi:** đặt lại mật khẩu bằng OTP, áp cho mọi vai trò (khách và admin). Kèm rate limit theo danh tính cho các endpoint nhạy cảm (xem mục "Rate limit tầng 1").

**Ngoài phạm vi (YAGNI):** OTP khi đăng nhập, OTP khi đăng ký tài khoản, đổi mật khẩu lúc đang đăng nhập.

**Cố ý để sang Plan 5:** throttle toàn cục chống flood theo IP cho mọi request. Nó phụ thuộc reverse proxy (chưa có), có thể để nginx làm, và bật sớm thì bộ test e2e đỏ vì `429` chứ không phải vì code sai. Chi tiết đã ghi trong `CONTEXT.md` mục "Chưa có — cần trước khi mở cho khách thật".

## Kênh gửi

**Zalo ZNS** (Zalo Notification Service), mẫu tin loại Xác thực.

Điều kiện đã kiểm chứng:

- ZNS chỉ dùng được với **OA đã xác thực**; xác thực đòi giấy phép kinh doanh — Zalo chấp nhận cả **hộ kinh doanh**, và tiệm có giấy phép này.
- Mẫu tin OTP phải gửi Zalo duyệt trước, dùng template mặc định, không được gắn nút CTA.
- OTP là **loại tin duy nhất được gửi cho người chưa từng tương tác với OA** — đúng nhu cầu ở đây, vì khách quên mật khẩu chưa chắc đã follow OA.

Điều này mở lại quyết định "dùng Telegram, không dùng Zalo OA" ở `CONTEXT.md`. Quyết định đó vẫn đúng **cho kênh chủ tiệm** (thông báo lịch mới, bot quản lý) và không đổi. Zalo chỉ thêm vào đúng một việc: gửi OTP cho khách, thứ Telegram không làm được vì khách không dùng Telegram.

## Phân tầng

Theo đúng cấu trúc sẵn có: `infrastructure/` cho thứ nói chuyện ra ngoài, `services/` cho nghiệp vụ.

```
app/infrastructure/otp_sender.py   OtpSender (protocol)
                                   ├── ZnsSender      gọi Zalo thật
                                   └── LogOtpSender   in mã ra log (dev/test)
app/infrastructure/zalo_token.py   giữ và làm mới access token
app/services/otp.py                OtpService: sinh, lưu, xác minh mã
app/services/auth.py               reset_password nhận thêm otp
app/api/v1/routers/auth.py         + forgot-password, sửa reset-password
```

Chọn bản cài qua `OTP_PROVIDER=zns|log`.

`OtpService` tách khỏi `AuthService` vì nó không phụ thuộc gì vào `User` — nó chỉ biết SĐT, mục đích, và mã. Tách sẵn thì sau này dùng OTP cho việc khác không phải gỡ ra.

`OtpService` **không biết Zalo tồn tại**. Nó nhận một `OtpSender` qua constructor. Test chạy với `LogOtpSender`, không chạm mạng.

## Hợp đồng API

Hai bước. Chọn hai thay vì ba (OTP đổi lấy reset-token rồi mới đặt mật khẩu) vì ưu tiên tối giản thao tác cho người lớn tuổi vẫn còn hiệu lực — hai màn hình thay vì ba.

### `POST /auth/forgot-password`

```json
{ "phone": "0912345678" }
```

Luôn trả `204`, kể cả khi SĐT không có tài khoản (chỉ là không gửi gì). Đây là thay đổi so với hành vi cũ (404) và là chủ đích: bịt đường dò danh sách khách của tiệm.

### `POST /auth/reset-password`

```json
{ "phone": "0912345678", "otp": "483920", "new_password": "matkhaumoi" }
```

Trả `204`. Trường `otp` là **bắt buộc** — đây là thay đổi phá vỡ tương thích với hợp đồng cũ. Plan 4 (React) chưa dựng nên chưa có client nào phải sửa.

## Dữ liệu — collection `otp_codes`

| Trường | Ghi chú |
|---|---|
| `phone` | đã qua `normalize_phone` |
| `code_hash` | bcrypt qua `pwd_context` sẵn có |
| `purpose` | `"password_reset"` |
| `expires_at` | TTL index tự dọn |
| `attempts` | số lần nhập sai |
| `consumed_at` | `None` cho tới khi dùng |

Index: `(phone, purpose)`, và TTL trên `expires_at` — cùng cách `rate_limits` đang tự dọn.

**Lưu hash chứ không lưu mã thô.** Mã 6 số chỉ có 10⁶ khả năng; ai đọc được DB mà mã lưu thô hoặc băm nhanh thì dò ra tức thì. Bcrypt chậm là điểm cộng ở đây, không phải nhược điểm — mỗi lần xác minh chỉ tốn ~100ms và chỉ xảy ra khi có người thật đang đổi mật khẩu.

## Chính sách

| Mục | Giá trị | Cấu hình |
|---|---|---|
| Độ dài mã | 6 chữ số, `secrets.randbelow` | — |
| Hạn dùng | 5 phút | `OTP_TTL_SECONDS` |
| Nhập sai tối đa | 5 lần, quá thì mã chết | `OTP_MAX_ATTEMPTS` |
| Xin mã mới | 3 lần/giờ theo SĐT và theo IP | — |
| Xin mã mới | huỷ mã cũ ngay | — |
| Dùng lại mã | không, một lần duy nhất | — |

Giới hạn số lần xin mã dùng lại `RateLimitService` đang có, thêm khoá `otp:phone:*` và `otp:ip:*`. Không viết cơ chế đếm thứ hai.

## Rate limit tầng 1

Gộp vào bản này vì cùng sửa `auth.py` và `AuthService`, cùng thêm khoá vào `RateLimitService`. Tách ra là đụng cùng file hai lần.

| Endpoint | Giới hạn | Khoá |
|---|---|---|
| `POST /auth/login` | 10 lần **sai** / 15 phút | SĐT + IP |
| `POST /auth/forgot-password` | 3 / giờ | SĐT + IP |
| `POST /auth/reset-password` | 5 / giờ | SĐT + IP |
| `POST /appointments` | 20 / giờ | `user_id` |

Ba khác biệt cần làm đúng:

**Login chỉ đếm lần sai.** Gọi `check_and_hit` *sau* khi `verify_password` thất bại, không phải đầu hàm. Khác `reset-password` (đếm mọi lần gọi) — ở đó chặn nhầm còn an toàn hơn cho lọt, còn ở login thì đếm cả lần đúng nghĩa là người dùng thật đăng nhập nhiều lần trong ngày sẽ tự khoá mình.

**Chuẩn hoá SĐT trước khi làm khoá.** Không thì `0912345678` và `+84912345678` thành hai bucket riêng và đi vòng được qua giới hạn.

**Ngưỡng đọc từ cấu hình.** `LOGIN_MAX_ATTEMPTS`, `LOGIN_WINDOW_SECONDS` — để test đặt ngưỡng cao hoặc tắt hẳn. Bộ e2e hiện tại bắn hàng chục request liên tiếp; ngưỡng cứng sẽ làm nó đỏ vì `429`.

`request.client.host` sau reverse proxy trả IP của proxy, nên khoá `ip:*` sẽ gộp mọi người dùng vào một bucket. Bản này chấp nhận điều đó vì khoá theo SĐT vẫn đúng; hàm lấy IP tử tế thuộc Plan 5.

## Lỗi

**Mã sai, mã hết hạn, mã đã dùng — cùng một phản hồi:** `400 "Mã xác thực không đúng hoặc đã hết hạn"`. Phân biệt ba trường hợp là nói cho kẻ tấn công biết mã nào từng tồn tại.

**Quá số lần nhập sai hoặc quá số lần xin mã:** `429`, dùng `RateLimitedError` sẵn có.

**ZNS lỗi hoặc không gọi được:** ghi log, vẫn trả `204`.

Đánh đổi đã cân nhắc: trả `503` thì khách biết ngay là hệ thống hỏng, nhưng `503` chỉ xảy ra với SĐT có tài khoản, nên nó khôi phục lại đúng đường dò danh sách mà `204` vừa bịt. Bù bằng giao diện: màn hình nhập mã ghi *"Không nhận được mã sau 2 phút, cô chú gọi giúp con số {shop_phone}"* — tái dùng `SHOP_PHONE` đã có sẵn cho tình huống Mongo hỏng.

## Token Zalo

Access token của Zalo hết hạn theo giờ, làm mới bằng refresh token **xoay vòng**: mỗi lần làm mới thì refresh token cũ mất hiệu lực.

Hệ quả bắt buộc với thiết kế:

- Token **không để trong `.env`**. Giá trị đổi liên tục lúc chạy, mà `.env` chỉ đọc một lần lúc khởi động.
- Lưu vào Mongo (collection `zalo_tokens`, một document singleton — cùng kiểu `_SINGLETON` mà `ShopService` đang dùng).
- Chỉ một tiến trình được làm mới. Đây là thêm một lý do nữa để giữ **một worker duy nhất**, cùng hàng với lý do Telegram getUpdates đã ghi trong `Dockerfile`.
- Mất refresh token thì phải vào trang quản trị Zalo lấy tay. Cần ghi vào tài liệu vận hành (Plan 5).

Giá trị khởi tạo (`ZALO_APP_ID`, `ZALO_OA_SECRET`, refresh token lần đầu, `ZNS_TEMPLATE_ID`) đọc từ `.env`; từ đó trở đi token sống trong Mongo.

## Cấu hình mới

```
OTP_PROVIDER=log          # log | zns
OTP_TTL_SECONDS=300
OTP_MAX_ATTEMPTS=5
LOGIN_MAX_ATTEMPTS=10
LOGIN_WINDOW_SECONDS=900
# ZALO_APP_ID=
# ZALO_OA_SECRET=
# ZALO_REFRESH_TOKEN=     # chỉ dùng lần đầu, sau đó token sống trong Mongo
# ZNS_TEMPLATE_ID=
```

Ba khoá đầu bắt buộc, có giá trị mặc định. Bốn khoá Zalo để dạng comment — theo đúng quy ước đã ghi trong `.env.example`: khoá tuỳ chọn giữ nguyên comment, đừng gán chuỗi rỗng, vì code phân biệt `None` ("chưa cấu hình") với `""`.

`OTP_PROVIDER=zns` mà thiếu khoá Zalo thì **chết ngay lúc khởi động**, không chạy tiếp âm thầm — cùng triết lý fail-fast với 23 field bắt buộc còn lại trong `config.py`. Khác với LLM (thiếu key thì vẫn đặt lịch được), thiếu OTP là khách mất hẳn đường khôi phục tài khoản.

## Test

TDD. Không ca nào chạm mạng — dùng `LogOtpSender`; đẩy thời gian bằng `app/core/clock.py` như các test hiện có.

- mã đúng → đổi được mật khẩu
- mã sai → 400, `attempts` tăng
- mã hết hạn → 400, cùng thông báo với mã sai
- mã đã dùng → 400, không đổi được lần hai
- quá `OTP_MAX_ATTEMPTS` → 429, mã chết kể cả sau đó nhập đúng
- xin mã quá 3 lần/giờ → 429
- xin mã mới → mã cũ hết hiệu lực ngay
- SĐT không tồn tại → 204, và **không** có lệnh gửi nào được phát ra
- SĐT viết dạng `+84`/`84` → cùng bucket rate limit với dạng `0` (chuẩn hoá trước khi làm khoá)
- **hồi quy:** `reset-password` thiếu `otp` → 422, không đổi được mật khẩu

Ca cuối là ca quan trọng nhất của bản này — nó chốt rằng lỗ hổng cũ đã đóng và không mở lại được khi refactor.

Cho rate limit tầng 1:

- quá `LOGIN_MAX_ATTEMPTS` lần sai → 429
- login **đúng** không tốn quota; login sai thì có
- `0912345678` và `+84912345678` dùng chung một bucket
- hết cửa sổ thời gian → cho đăng nhập lại
- quá 20 lịch/giờ trên cùng `user_id` → 429

## Tài liệu phải sửa theo

| File | Sửa gì |
|---|---|
| `CONTEXT.md` mục "Rủi ro đã biết và chấp nhận" | Bỏ — rủi ro đã vá, không còn là quyết định đang có hiệu lực |
| `CONTEXT.md` bảng quyết định đã chốt (dòng Zalo) | Zalo OA nay có dùng, chỉ cho ZNS OTP; Telegram vẫn là kênh chủ tiệm |
| `specs/2026-08-06-booking-nail-toc/03-auth.md` | Viết lại đoạn quên mật khẩu theo luồng hai bước |
| `.env.example` | Thêm khối cấu hình OTP |

## Việc còn để ngỏ

Chưa nằm trong bản này, ghi lại để không quên:

- **Đổi mật khẩu không thu hồi token cũ đang sống** (tối đa 30 phút). Nạn nhân bị chiếm tài khoản, đổi lại mật khẩu, nhưng token của kẻ tấn công vẫn dùng được. `is_active=False` thì có thu hồi ngay — chỉ thiếu cơ chế tương tự cho đổi mật khẩu, ví dụ lưu `password_changed_at` và so với `iat` của token.
- **Mật khẩu tối thiểu 4 ký tự** (`schemas.py`). Nên nâng, nhưng phải cân với ràng buộc người dùng lớn tuổi.
- Các mục hạ tầng (`docker-compose.yml` hardcode secret, Mongo mở cổng không auth, mongo-express, `.dockerignore`) — đã ghi trong `CONTEXT.md`, thuộc Plan 5.
