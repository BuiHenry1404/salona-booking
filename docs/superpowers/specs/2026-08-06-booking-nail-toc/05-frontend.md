# Màn hình

> **Vai xưng hô đã đổi sau tài liệu này.** Từ 2026-08-23 lễ tân xưng "em",
> gọi khách "anh"/"chị". Mọi câu "con", "cô", "chú", "bác" dưới đây là
> nguyên văn của thời điểm đó, giữ lại làm biên bản chứ không phải mẫu để
> chép theo. Vai hiện hành: mục "Xưng hô" trong [`CONTEXT.md`](../../../../CONTEXT.md).

## Nguyên tắc chung

Áp cho mọi màn hình, viết thành design token trong React: cỡ chữ nền tối thiểu 18px, nút cao tối thiểu 56px và rộng hết chiều ngang, tương phản cao, mỗi màn hình chỉ một hành động chính, không menu ẩn, không modal chồng nhau, mọi trạng thái đều diễn đạt bằng chữ chứ không chỉ bằng màu.

## Phía khách

**Đăng nhập** — hai ô (SĐT, mật khẩu), một nút "Đăng nhập", một link chữ to "Quên mật khẩu".

**Chat** — màn hình chính, mở app là vào thẳng. Trên cùng là thẻ trạng thái tiệm luôn hiển thị: "Chủ tiệm đang rảnh" hoặc "Chủ tiệm đang bận — xong lúc 3:30 chiều", cập nhật realtime qua Socket.IO.

### Thời gian bận: một mốc, không phải một khoảng

Chủ tiệm bấm 15 / 30 / 60 / 120 phút. **Đó là đầu vào, không phải thứ đem hiển thị.** `set_busy(minutes)` quy ngay ra `busy_until = bây giờ + minutes` và lưu mốc đó; từ giây tiếp theo, mọi màn hình chỉ làm việc với mốc.

Giao diện **không đếm ngược ở đâu cả** — không "còn 30 phút", không "sắp xong rồi". Chỉ một dòng: *Xong lúc 3:30 chiều*. Áp cho thẻ khách, bảng chủ tiệm, và câu trả lời của AI.

Ba lý do:

- **Không cũ đi.** Người lớn tuổi hay mở màn hình rồi để đó, quay lại sau. "Còn 30 phút" sai ngay sau khi hiện ra; "3:30 chiều" thì vẫn đúng. Với câu trả lời của AI còn nặng hơn — nó nằm lại trong lịch sử chat, đọc lại sau một tiếng là sai hẳn.
- **Không phải tính nhẩm.** Mốc đối chiếu thẳng với đồng hồ treo tường.
- **Không dính lệch đồng hồ.** Hiển thị chỉ là định dạng `busy_until` server gửi xuống, không đụng tới giờ máy khách.

**Hết giờ bận do client tự lật.** Backend không có timer nào phát sự kiện lúc `busy_until` hết hạn — nó chỉ tính lại khi có ai gọi `get_status()`. Frontend đặt **một** `setTimeout` hẹn đúng `minutes_left` phút; tới giờ thì đổi thẻ sang "đang rảnh" rồi hỏi lại server một lần để xác nhận. Hẹn theo `minutes_left` (một khoảng) chứ không theo `busy_until` trừ đi giờ máy (hai mốc) — điện thoại người lớn tuổi lệch giờ là chuyện thường. Chủ tiệm bấm bận thêm lần nữa thì hẹn giờ cũ phải bị hủy.

`minutes_left` vì thế vẫn có trong API nhưng **chỉ để hẹn giờ**, không bao giờ đem hiển thị.

Thẻ trạng thái luôn nằm đó nên phần lớn khách biết tiệm bận hay rảnh mà không cần hỏi AI — hỏi AI là đường dự phòng chứ không phải đường duy nhất.

### Lịch sử trò chuyện

Hội thoại cắt phiên theo ngày, nên **hôm sau mở app ra là khung chat trống**. Với khách lớn tuổi, đó dễ bị hiểu thành "mất hết rồi", hoặc tệ hơn — "lịch con đặt hôm qua có còn không?". Màn lịch sử tồn tại để trả lời đúng câu đó. Nó là phần bù cho quyết định cắt phiên, không phải tính năng thêm cho vui.

**Đường vào:** một link chữ to ngay dưới thẻ trạng thái tiệm ở màn Chat — "Xem các lần trò chuyện trước". Không nhét vào menu ẩn; Chat vẫn là hành động chính duy nhất của màn đó.

**Danh sách** — mỗi ngày một thẻ lớn, mới nhất trên cùng. Ngày viết đủ chữ tiếng Việt ("Thứ Ba, 12 tháng 8"), **không** viết `12/08`. Dòng thứ hai là câu đầu tiên khách nói hôm đó, cắt ngắn, để nhận ra hôm ấy nói chuyện gì. Thẻ đầu tiên là hôm nay, mở ra khung chat thật.

**Xem lại một ngày** — đúng khung chat cũ, cùng bong bóng và cỡ chữ, **không có ô nhập**. Chỗ ô nhập là một dòng chữ: "Đây là cuộc trò chuyện ngày 12 tháng 8. Cô chú muốn nhắn thì quay về hôm nay", kèm nút to quay lại.

Không diễn đạt "chỉ xem" bằng ô nhập xám hoặc bị khoá — mọi trạng thái phải nói bằng **chữ**.

**"Chỉ xem" không cần ép bằng quyền.** Backend không có đường nào để nhắn vào một ngày cũ: `append()` luôn ghi vào cuối và tin mới luôn thuộc hôm nay, không có tham số ngày ở đâu cả. Đây là thuộc tính cấu trúc, không phải một nút bị ẩn — nên giao diện chỉ cần đừng vẽ ô nhập.

**Không làm:** tìm kiếm, xoá hội thoại, đổi tên, phân trang. Khách đặt lịch vài tuần một lần nên sau nhiều năm danh sách vẫn chỉ vài chục thẻ.

### Khung chat

Bong bóng chữ to. Ô nhập có nút micro dùng Web Speech API vì gõ phím là rào cản lớn nhất với người lớn tuổi; trình duyệt không hỗ trợ thì ẩn nút.

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
| `propose_appointment` | Đang giữ chỗ cho cô… | Đã giữ chỗ |
| `list_my_appointments` | Đang xem lịch của cô… | Đã xem lịch của cô |
| `cancel_appointment` | Đang hủy lịch… | Đã hủy lịch |

Tên tool lạ không có trong bảng thì hiện câu chung "Đang xử lý…", không bao giờ hiện tên thô.

**Không có nút dừng** — câu trả lời chỉ 1–2 câu, thêm nút là thêm thứ để bấm nhầm.

**Mất mạng giữa lúc stream:** giữ nguyên phần chữ đã hiện và thêm dòng "Mất mạng, đang thử lại…". Không xóa chữ đã hiện, vì người lớn tuổi sẽ tưởng mình làm hỏng.

Mọi hiệu ứng nhấp nháy và xoay đều phải tắt khi `prefers-reduced-motion: reduce`.

## Phía chủ tiệm

**Bảng điều khiển** — nửa trên là một nút khổng lồ chuyển trạng thái. Có thêm một link nhỏ vào phần sửa giờ mở cửa (`shop_hours`), ít dùng nên không chiếm chỗ. Đang rảnh thì bấm "Tôi đang bận", hiện 4 nút chọn nhanh 15 phút / 30 phút / 1 tiếng / 2 tiếng. Đang bận thì hiện giờ xong ("Xong lúc 3:30 chiều") và nút "Tôi rảnh rồi" — không đếm ngược, giống hệt thẻ bên máy khách. Nửa dưới là lịch hôm nay dạng thẻ lớn xếp theo giờ; lịch mới được Socket.IO đẩy lên đầu kèm nhãn "MỚI".

**Khách hàng** — danh sách khách, ô tìm theo SĐT, nút "Tạo tài khoản" (admin cấp SĐT và mật khẩu ban đầu) và nút đặt lịch hộ khi khách gọi điện.

