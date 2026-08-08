# Bot Telegram cho chủ tiệm

Kênh phụ dành riêng cho **một người là chủ tiệm**, không đụng tới khách hàng. Chọn Telegram thay Zalo OA vì Bot API miễn phí hoàn toàn, không có khái niệm khung 48 giờ tính phí, và không cần giấy phép kinh doanh để đăng ký.

Đánh đổi đã biết: người lớn tuổi ở Việt Nam hầu như không dùng Telegram, nên chủ tiệm phải cài thêm một app lạ. Chấp nhận được vì chỉ một người và chỉ cài một lần.

## Ủy quyền

`TELEGRAM_ADMIN_CHAT_IDS` trong `.env`, danh sách phân cách bằng dấu phẩy. Update đến từ chat_id khác bị **bỏ qua im lặng** — không trả lời gì, để người lạ dò ra bot cũng không biết nó làm gì. Điều này quan trọng vì bot đọc được tên và số điện thoại khách.

README ghi cách lấy chat_id: nhắn bot một câu rồi mở `https://api.telegram.org/bot<TOKEN>/getUpdates`.

## Giao diện: bàn phím nút, không phải lệnh gõ tay

Chủ tiệm lớn tuổi mà phải nhớ gõ `/homnay` là hỏng. Bot gắn reply keyboard cố định hiện sẵn dưới ô nhập:

```
┌─────────────┬──────────────┐
│  Hôm nay    │  Ngày mai    │
├─────────────┼──────────────┤
│ Tôi đang bận│ Tôi rảnh rồi │
└─────────────┴──────────────┘
```

Bấm "Tôi đang bận" thì bot hiện 4 nút inline `15 phút / 30 phút / 1 tiếng / 2 tiếng` — đúng cùng thao tác như trên web app, nên chủ tiệm không phải học hai kiểu.

Thông báo đẩy: khi có lịch mới (đặt qua chat hay do admin tạo) và khi khách hủy lịch.

**Không có AI trong nhánh này.** Toàn bộ tất định: bấm nút nào gọi service nấy. Không tốn token, không có gì để hiểu sai, test không cần LLM.

## Kiến trúc

Telegram không được gọi thẳng từ `appointment_service`. Thêm `app/services/notifications.py` làm chỗ tỏa tin duy nhất:

```
appointment_service tạo lịch xong
        ↓
notifications.appointment_created(appt)
        ├→ Socket.IO  → màn hình admin web
        └→ Telegram   → điện thoại chủ tiệm
```

Chiều ngược lại cũng phải đúng: đổi bận/rảnh từ Telegram bắt buộc đi qua `shop_status_service` và phát `shop_status_changed`, nếu không thẻ trạng thái trên máy khách và trang admin web sẽ hiển thị sai. Hai đường vào cùng một trạng thái thì phải cùng đi qua một service.

## Long polling và cạm bẫy nhiều worker

Vòng lặp `getUpdates` chạy như asyncio task trong lifespan của FastAPI, chỉ bật khi có `TELEGRAM_BOT_TOKEN`. Chọn long polling thay webhook vì không cần domain public, không cần HTTPS, chạy được sau NAT — hợp với việc deploy một tiệm nhỏ.

Telegram chỉ chấp nhận **một** kết nối `getUpdates` cho mỗi bot token. Nếu deploy bằng `uvicorn --workers N` (hoặc `gunicorn -w N`), mỗi worker là một tiến trình riêng chạy lại toàn bộ lifespan, nên N tiến trình cùng poll một bot: chúng đá nhau liên tục và Telegram trả **409 Conflict** không dứt — bot lúc nhận được tin, lúc không.

**Quyết định: chạy đúng 1 worker.** Một tiệm nail/tóc không có tải cần scale ngang, và FastAPI async đủ sức xử lý. Lệnh chạy production là `uvicorn main:app --host 0.0.0.0 --port 8000`, tuyệt đối không truyền `--workers`, không dùng `gunicorn -w N`, không scale container lên nhiều replica. Ràng buộc này phải ghi vào README và giữ nguyên trong `Dockerfile`/`docker-compose.yml`.

Nếu về sau thật sự cần nhiều worker, phương án là tách bot thành tiến trình riêng và thêm cờ `RUN_TELEGRAM_POLLER` để lifespan của web không khởi động vòng lặp poll. Chưa làm bây giờ.

## Thư viện và xử lý lỗi

Gọi thẳng HTTP API bằng `httpx`, không thêm `python-telegram-bot` hay `aiogram`. Chỉ có 4 nút và 2 loại thông báo; bàn phím lẫn nút inline đều chỉ là JSON, không đáng kéo thêm một framework.

Gửi Telegram chạy trong background task và nuốt lỗi. Mạng chết hay token sai thì chỉ ghi log, tuyệt đối không làm hỏng việc đặt lịch của khách.

