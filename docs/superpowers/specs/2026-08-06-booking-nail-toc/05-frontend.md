# Màn hình

## Nguyên tắc chung

Áp cho mọi màn hình, viết thành design token trong React: cỡ chữ nền tối thiểu 18px, nút cao tối thiểu 56px và rộng hết chiều ngang, tương phản cao, mỗi màn hình chỉ một hành động chính, không menu ẩn, không modal chồng nhau, mọi trạng thái đều diễn đạt bằng chữ chứ không chỉ bằng màu.

## Phía khách

**Đăng nhập** — hai ô (SĐT, mật khẩu), một nút "Đăng nhập", một link chữ to "Quên mật khẩu".

**Chat** — màn hình chính, mở app là vào thẳng. Trên cùng là thẻ trạng thái tiệm luôn hiển thị: "Chủ tiệm đang rảnh" hoặc "Đang bận, khoảng 30 phút nữa xong", cập nhật realtime qua Socket.IO. Nhờ vậy phần lớn khách không cần hỏi AI mới biết; hỏi AI là đường dự phòng chứ không phải đường duy nhất. Dưới là khung chat với bong bóng chữ to. Ô nhập có nút micro dùng Web Speech API vì gõ phím là rào cản lớn nhất với người lớn tuổi; trình duyệt không hỗ trợ thì ẩn nút.

**Lịch của tôi** — danh sách thẻ lớn dạng "Thứ Năm, 7/8 — 3:00 chiều — làm tóc", kèm nút "Hủy lịch". Hủy phải qua một bước xác nhận.

### Trạng thái khi AI đang xử lý

Bốn trạng thái nối tiếp trong khung chat, xem mockup [`ui-mockup-streaming.html`](ui-mockup-streaming.html):

1. **Đang đọc** — ba chấm nhấp nháy, hiện ngay khi `turn_started`, lấp khoảng chết trước lượt LLM đầu.
2. **Đang gọi tool** — spinner kèm câu tiếng Việt, theo `tool_started`.
3. **Tool xong** — thu thành một dòng có dấu tích, **giữ lại trên màn hình** để khách thấy máy đã làm gì thay vì nghi máy tự bịa.
4. **Đang trả lời** — chữ hiện dần theo `token`, con trỏ nhấp nháy ở cuối.

Ánh xạ tên tool sang tiếng Việt, đặt ở frontend:

| Tool | Đang chạy | Xong |
|---|---|---|
| `get_shop_status` | Đang xem chủ tiệm có rảnh không… | Đã xem trạng thái tiệm |
| `parse_time` | Đang xem lịch… | Đã xem lịch |
| `find_free_slots` | Đang xem lịch trống… | Đã xem lịch trống |
| `create_appointment` | Đang ghi lịch cho cô… | Đã ghi lịch |
| `list_my_appointments` | Đang xem lịch của cô… | Đã xem lịch của cô |
| `cancel_appointment` | Đang hủy lịch… | Đã hủy lịch |

Tên tool lạ không có trong bảng thì hiện câu chung "Đang xử lý…", không bao giờ hiện tên thô.

**Không có nút dừng** — câu trả lời chỉ 1–2 câu, thêm nút là thêm thứ để bấm nhầm.

**Mất mạng giữa lúc stream:** giữ nguyên phần chữ đã hiện và thêm dòng "Mất mạng, đang thử lại…". Không xóa chữ đã hiện, vì người lớn tuổi sẽ tưởng mình làm hỏng.

Mọi hiệu ứng nhấp nháy và xoay đều phải tắt khi `prefers-reduced-motion: reduce`.

## Phía chủ tiệm

**Bảng điều khiển** — nửa trên là một nút khổng lồ chuyển trạng thái. Có thêm một link nhỏ vào phần sửa giờ mở cửa (`shop_hours`), ít dùng nên không chiếm chỗ. Đang rảnh thì bấm "Tôi đang bận", hiện 4 nút chọn nhanh 15 phút / 30 phút / 1 tiếng / 2 tiếng. Đang bận thì hiện đồng hồ đếm ngược và nút "Tôi rảnh rồi". Nửa dưới là lịch hôm nay dạng thẻ lớn xếp theo giờ; lịch mới được Socket.IO đẩy lên đầu kèm nhãn "MỚI".

**Khách hàng** — danh sách khách, ô tìm theo SĐT, nút "Tạo tài khoản" (admin cấp SĐT và mật khẩu ban đầu) và nút đặt lịch hộ khi khách gọi điện.

