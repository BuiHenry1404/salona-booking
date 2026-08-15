# Lộ trình triển khai — App đặt lịch nail–tóc

Spec: [`docs/superpowers/specs/2026-08-06-booking-nail-toc/`](../specs/2026-08-06-booking-nail-toc/README.md)

Spec bao bốn hệ con. Mỗi hệ con là một plan riêng, chạy xong là có phần mềm dùng được và test được — không phải nửa vời chờ plan sau.

| # | Plan | Sản phẩm khi xong | Phụ thuộc |
|---|---|---|---|
| 1 | [Nền tảng backend](2026-08-06-backend-foundation/README.md) | API đầy đủ: đăng nhập SĐT, đặt/hủy lịch có chặn trùng nguyên tử, trạng thái bận/rảnh, giờ mở cửa. **Không có AI** — test bằng pytest và Swagger | Không |
| 2 | [Agent + memory + streaming](2026-08-06-agent-memory-streaming/README.md) | Chat đặt lịch qua LangGraph, Langfuse, streaming token + sự kiện tool qua Socket.IO | Plan 1 |
| 3 | [Bot Telegram](2026-08-06-telegram-bot/README.md) | Chủ tiệm nhận báo lịch mới, tra cứu, đổi bận/rảnh bằng nút bấm | Plan 1 |
| 4 | [Frontend React](2026-08-06-react-frontend/README.md) | Năm màn hình, hiển thị streaming và trạng thái gọi tool | Plan 1 + 2 (realtime cần thêm Plan 3) |

**Vì sao thứ tự này.** Plan 1 dựng toàn bộ tầng `services/` mà mọi thứ khác chỉ bọc mỏng bên ngoài — tool của agent, handler Telegram và REST cho React đều gọi cùng những service đó. Làm ngược lại sẽ phải viết logic đặt lịch hai lần.

Plan 3 và 4 chạy song song được sau khi Plan 2 xong, nhưng **không hoàn toàn độc lập**.
Plan 2 chỉ dựng sẵn `SocketIOService.broadcast` và `emit_to_admins`; chỗ thực sự **gọi**
chúng khi có lịch mới hay khi tiệm đổi bận/rảnh là `SocketNotifier` ở [Plan 3 task 5](2026-08-06-telegram-bot/task-05-wire-services.md).
Làm Plan 4 mà bỏ Plan 3 thì bảng điều khiển chủ tiệm vẫn dùng được — nút bận/rảnh áp
thẳng kết quả HTTP — nhưng lịch mới sẽ không tự nhảy lên, phải tải lại trang. Muốn
realtime đầy đủ thì làm task 1 và task 5 của Plan 3, kể cả khi chưa cần bot Telegram.

Cả bốn plan đã viết xong. Khi code thực tế lệch với plan, sửa file plan trước rồi mới code tiếp — để plan còn dùng được cho người sau.
