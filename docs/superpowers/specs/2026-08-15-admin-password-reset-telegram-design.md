# Đặt lại mật khẩu admin qua Telegram

**Trạng thái: phần rate limit đăng nhập ĐÃ LÀM (2026-08-15). Phần còn lại hoãn tới sau Plan 3** — xem mục "Khi nào làm" ở cuối.

## Vì sao làm

Luồng quên mật khẩu hiện tại cho phép **bất kỳ ai biết SĐT cũng đổi được mật khẩu** của tài khoản đó, không cần đăng nhập. Đã kiểm chứng bằng request thật ngày 2026-08-15: gọi `POST /auth/reset-password` không kèm `Authorization` trả `204`, rồi đăng nhập được bằng mật khẩu vừa đặt và huỷ được lịch của nạn nhân.

Với **khách**, đây là quyết định có chủ đích và vẫn giữ nguyên — tiệm nhỏ, khách quen, ưu tiên tối giản thao tác cho người lớn tuổi. Thiệt hại tối đa là một khách mất lịch của chính mình.

Với **admin** thì khác hẳn. `AuthService.reset_password` không nhìn `role`, nên quyết định chấp nhận rủi ro cho khách đang tự động áp luôn cho chủ tiệm. Chiếm được admin là:

- xem SĐT toàn bộ khách qua `GET /auth/users`
- huỷ mọi lịch của mọi người
- đổi giờ mở cửa
- khoá chính chủ tiệm ra khỏi tài khoản của họ

SĐT chủ tiệm lại là thứ dán trên biển hiệu. Đây là chỗ duy nhất bản này vá.

## Phạm vi

**Trong phạm vi:**

1. Chặn `reset-password` công khai đối với tài khoản `role == "admin"`.
2. Cho admin đường khôi phục thay thế: qua bot Telegram.
3. Rate limit theo danh tính cho các endpoint nhạy cảm (mục "Rate limit tầng 1").

**Ngoài phạm vi, giữ nguyên như hiện tại:** luồng quên mật khẩu của khách. Vẫn công khai, vẫn chỉ cần SĐT.

**Đã cân nhắc và loại:**

- **Zalo ZNS.** Từng chọn rồi bỏ. ZNS gửi OTP được cho khách, nhưng nó đòi OA đã xác thực, xác thực đòi giấy phép kinh doanh, mẫu tin phải gửi Zalo duyệt, và kéo theo cả cơ chế xoay vòng access token phải lưu trong Mongo. Quá nhiều hạ tầng cho một tính năng mà rủi ro nó bịt đã được chấp nhận từ đầu. Quyết định "dùng Telegram, không dùng Zalo OA" trong `CONTEXT.md` giữ nguyên, không có ngoại lệ.
- **OTP qua SMS.** Tốn tiền mỗi tin, và brandname ở VN thường đòi đúng thứ giấy tờ đã tránh.
- **Script CLI trên server.** Chạy được nhưng bắt chủ tiệm phải có quyền SSH — không thực tế.

## Thiết kế

### Chặn admin khỏi luồng công khai

Trong `AuthService.reset_password`, sau khi tra SĐT:

```python
user = await self.user_repo.get_by_phone(normalised)
if not user or user.role == "admin":
    raise NotFoundError("Không tìm thấy tài khoản với số này")
```

Gộp chung với nhánh `not user` và dùng **đúng một thông báo** là có chủ đích: báo riêng "tài khoản admin không được reset" là chỉ thẳng cho kẻ tấn công biết số nào là chủ tiệm.

### Đường khôi phục cho admin

Bot Telegram (Plan 3) thêm một lệnh. Bot **chỉ trả lời `chat_id` nằm trong `TELEGRAM_ADMIN_CHAT_IDS`** — biến này đã có sẵn trong `config.py` nhưng hiện chưa dùng ở đâu.

Luồng: chủ tiệm nhắn lệnh cho bot → bot sinh mật khẩu tạm, đặt cho tài khoản admin, nhắn lại mật khẩu đó → chủ tiệm đăng nhập trên web.

Điều kiện bảo mật thay đổi từ "biết SĐT" thành "đang đăng nhập tài khoản Telegram đã đăng ký sẵn". Không có endpoint HTTP công khai nào mới.

Hai chi tiết:

- `TELEGRAM_ADMIN_CHAT_IDS` phải được kiểm ở **mọi** handler, không riêng lệnh này. Hiện chưa handler nào kiểm vì chưa handler nào tồn tại.
- Mật khẩu tạm gửi qua Telegram nằm lại trong lịch sử chat. Chấp nhận: nó chỉ sống tới lần đăng nhập kế, và chat đó chỉ chủ tiệm đọc được. Nếu muốn chặt hơn thì bắt đổi mật khẩu ngay lần đăng nhập đầu — chưa làm, YAGNI.

## Rate limit tầng 1

Gộp vào bản này vì cùng sửa `auth.py` và `AuthService`, cùng thêm khoá vào `RateLimitService` đã có. Tách ra là đụng cùng file hai lần.

| Endpoint | Giới hạn | Khoá | |
|---|---|---|---|
| `POST /auth/login` | 10 lần **sai** / 15 phút | SĐT + IP | ✅ đã làm |
| `POST /auth/reset-password` | 5 / giờ | SĐT + IP | ✅ đã có từ trước |
| `POST /appointments` | 20 / giờ | `user_id` | ✅ đã làm |

Bỏ Zalo làm phần này **quan trọng hơn**, không phải ít đi: khách không có OTP, nên rate limit là lớp bảo vệ duy nhất còn lại cho tài khoản của họ. Trước bản vá, `POST /auth/login` không có giới hạn nào — đã kiểm, 25 lần sai liên tiếp đều trả 401. Nay: 10 lần 401 rồi 429, và mật khẩu đúng cũng bị từ chối khi đã khoá.

Ba chỗ dễ làm sai:

**Login: kiểm trước, ghi dấu sau.** `RateLimitService` tách làm hai — `check()` đếm mà không ghi, `hit()` ghi mà không đếm.

Thứ tự bắt buộc là `check` → so mật khẩu → `hit` nếu sai.

Bản nháp đầu của spec này ghi "gọi `check_and_hit` sau khi `verify_password` thất bại" — **sai**. Làm vậy thì mật khẩu vẫn được kiểm ở mọi lần thử: kẻ dò nhận 429 thay vì 401, nhưng lần đoán trúng vẫn lấy được token. Kiểm trước thì khoá rồi là mật khẩu đúng cũng bị từ chối, đúng ý nghĩa của một lần khoá tạm.

Chỉ ghi dấu khi sai, vì đếm cả lần đúng nghĩa là người dùng thật đăng nhập nhiều lần trong ngày sẽ tự khoá mình. `reset-password` giữ nguyên `check_and_hit` (đếm mọi lần gọi) — ở đó chặn nhầm còn an toàn hơn cho lọt.

**SĐT sai định dạng vẫn tính vào hạn mức theo IP.** Không thì kẻ dò chỉ cần đổi SĐT mỗi lần là thoát giới hạn.

**Chuẩn hoá SĐT trước khi làm khoá.** Không thì `0912345678` và `+84912345678` thành hai bucket riêng và đi vòng được qua giới hạn.

**Ngưỡng đọc từ cấu hình** — `LOGIN_MAX_ATTEMPTS=10`, `LOGIN_WINDOW_SECONDS=900`. Bộ e2e hiện tại bắn hàng chục request liên tiếp; ngưỡng cứng sẽ làm nó đỏ vì `429` chứ không phải vì code sai.

`request.client.host` sau reverse proxy trả IP của proxy, nên khoá `ip:*` gộp mọi người dùng vào một bucket. Bản này chấp nhận vì khoá theo SĐT vẫn đúng; hàm lấy IP tử tế thuộc Plan 5.

## Cấu hình mới

```
LOGIN_MAX_ATTEMPTS=10
LOGIN_WINDOW_SECONDS=900
```

`TELEGRAM_BOT_TOKEN` và `TELEGRAM_ADMIN_CHAT_IDS` đã có trong `config.py`, không cần thêm.

## Test

TDD, không ca nào chạm mạng.

- `reset-password` với SĐT của **admin** → 404, mật khẩu **không** đổi
- `reset-password` với SĐT của khách → vẫn 204, vẫn đổi được (không được vá nhầm sang khách)
- SĐT admin và SĐT không tồn tại trả **cùng một** phản hồi
- quá `LOGIN_MAX_ATTEMPTS` lần sai → 429
- login **đúng** không tốn quota; login sai thì có
- `0912345678` và `+84912345678` dùng chung một bucket
- hết cửa sổ thời gian → cho đăng nhập lại
- quá 20 lịch/giờ trên cùng `user_id` → 429
- lệnh Telegram từ `chat_id` lạ → bot không trả lời và không đổi gì

Ca thứ hai quan trọng ngang ca đầu: nó chốt rằng bản vá **chỉ** đụng admin, không âm thầm lấy mất đường khôi phục của khách lớn tuổi.

## Việc còn để ngỏ

- ~~Đổi mật khẩu không thu hồi token cũ~~ — đã vá bằng `token_version`, xem `CONTEXT.md`.
- **`reset-password` phân biệt 204 / 404** — đã cân nhắc và **cố ý giữ nguyên**; lý do trong `CONTEXT.md`.
- **Mật khẩu tối thiểu 4 ký tự** (`schemas.py`). Nên nâng, nhưng phải cân với ràng buộc người dùng lớn tuổi.
- Throttle toàn cục chống flood theo IP → Plan 5, chi tiết trong `CONTEXT.md`.

## Khi nào làm

**Sau Plan 3**, vì đường khôi phục cho admin cần bot Telegram tồn tại.

Một ngoại lệ đáng cân nhắc: **rate limit login không phụ thuộc Telegram** và chỉ khoảng vài chục dòng. Nó là lớp bảo vệ duy nhất cho tài khoản khách khi đã bỏ OTP, và nó nằm ở tầng `services/` mà Plan 2/3/4 đều bọc mỏng bên ngoài — để càng lâu thì càng nhiều đường vào phải rà lại. Tách riêng làm sớm được.
