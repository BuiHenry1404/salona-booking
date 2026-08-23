# Thiết Kế Chi Tiết Nâng Cấp Giao Diện Prototype (UI/UX Pro Max)

> **Vai xưng hô đã đổi sau tài liệu này.** Từ 2026-08-23 lễ tân xưng "em",
> gọi khách "anh"/"chị". Mọi câu "con", "cô", "chú", "bác" dưới đây là
> nguyên văn của thời điểm đó, giữ lại làm biên bản chứ không phải mẫu để
> chép theo. Vai hiện hành: mục "Xưng hô" trong [`CONTEXT.md`](../../../../CONTEXT.md).

Tài liệu này đặc tả các cải tiến về giao diện, bảng màu, tính tương tác và hoạt họa cho ứng dụng đặt lịch nail-tóc Cô Ba.

---

## 1. Hệ Thống Token Thiết Kế (Design Tokens)

Chúng ta chuyển đổi toàn bộ giao diện sang hệ màu **Soft Warm Rose & Sage**
 tạo cảm giác ấm cúng, thư giãn, đồng thời tối ưu độ tương phản cho người lớn tuổi.

```css
:root {
  /* Bảng màu chính - Soft Warm Rose & Sage */
  --primary: #A26B5E;          /* Hồng đất ấm - màu thương hiệu chính */
  --primary-hover: #8D5A4E;
  --primary-weak: #F8F3F1;     /* Nền hồng nhạt */
  --on-primary: #FFFFFF;
  
  --accent: #5B8266;           /* Xanh Sage - dùng cho trạng thái "Đang rảnh" / "Đúng rồi" */
  --accent-hover: #4A6B53;
  --accent-weak: #EEF4F0;      /* Nền xanh nhạt */
  --on-accent: #FFFFFF;

  --danger: #C05642;           /* Đỏ gạch ấm - dùng cho "Đang bận" / "Hủy lịch" */
  --danger-weak: #FAF0EE;      /* Nền đỏ nhạt */
  --on-danger: #FFFFFF;

  --warning: #B45309;          /* Nhãn "Mới" của lịch hẹn */
  --warning-weak: #FEF3C7;

  /* Màu giao diện nền */
  --bg-page: #FAF8F5;          /* Màu kem ấm áp dịu mắt cho trang showcase */
  --bg-phone: #FCFBF9;         /* Màu nền bên trong điện thoại */
  --surface: #FFFFFF;          /* Các thẻ card, input, appbar */
  --fg: #2C2523;               /* Chữ chính màu nâu đen đậm (tương phản cao tốt hơn đen xì) */
  --fg-muted: #6E615D;         /* Chữ phụ */
  --border: #EFEAE4;           /* Viền mềm mại */

  /* Kích thước & Trải nghiệm */
  --r: 16px;                   /* Bo góc mượt mà */
  --tap: 56px;                 /* Chiều cao vùng chạm tối thiểu */
  --shadow-sm: 0 2px 8px rgba(44, 37, 35, 0.04);
  --shadow-md: 0 8px 24px rgba(44, 37, 35, 0.08);
}
```

---

## 2. Cấu Trúc File Prototype Sau Nâng Cấp

### 2.1. File `ui-mockup.html` (Trang Chính)

Giao diện trang chính sẽ có thanh chuyển đổi (Tab Bar) lớn ở phía trên cùng:

1.  **Tab 1: Toàn cảnh (Showcase Grid):**
    *   Hiển thị 5 màn hình thiết kế tĩnh xếp cạnh nhau trong các khung điện thoại CSS 3D hoặc phẳng bóng bẩy.
    *   Có chú thích chi tiết kèm nhãn tương tác bên dưới từng màn hình.
2.  **Tab 2: Trải nghiệm thực tế (Live Sandbox):**
    *   Hiển thị 2 điện thoại hoạt động song song cạnh nhau: **Khách** (Trái) và **Chủ Tiệm** (Phải).
    *   **Tương tác đồng bộ (Real-time Simulation):**
        *   Khi máy Admin đổi trạng thái thành **Bận trong 15/30/60 phút**, máy Khách sẽ đổi thanh trạng thái trực quan ngay lập tức kèm đếm ngược thời gian thực.
        *   Khi máy Khách gửi tin nhắn đặt lịch $\rightarrow$ Kích hoạt chuỗi giả lập AI (con đang đọc $\rightarrow$ đang kiểm tra $\rightarrow$ xác nhận). Nếu bấm **"Đúng rồi"** trên máy Khách $\rightarrow$ Lịch hẹn sẽ tự động thêm vào danh sách lịch hẹn của máy Admin với nhãn "MỚI" nền vàng nhạt.
        *   Khi bấm **"Hủy lịch"** ở máy Khách $\rightarrow$ Hiện hộp xác nhận inline $\rightarrow$ Bấm đồng ý hủy $\rightarrow$ Lịch hẹn biến mất ở cả máy Khách và máy Admin.

### 2.2. File `ui-mockup-streaming.html` (Trang Stream Tool)

*   Nâng cấp giao diện máy Khách theo bảng màu Soft Warm Rose & Sage.
*   Cải tiến bảng điều khiển mô phỏng bên phải:
    *   Cho phép người dùng bấm gửi tin nhắn tự chọn thay vì chỉ chạy kịch bản cố định.
    *   Thêm công tắc **"Mất kết nối mạng"** để kiểm chứng trạng thái xử lý lỗi (giữ nguyên tin nhắn cũ, hiện thông báo lỗi bằng chữ đỏ "Mất mạng, đang thử lại...").
    *   Hiển thị log sự kiện Socket.IO thời gian thực dưới dạng code block sáng đẹp mắt khi quá trình stream đang diễn ra.

---

## 3. Hoạt Họa & Tiếp Cận (Animations & Accessibility)

*   **Hiệu ứng gõ chữ (Typewriter):** Tin nhắn phản hồi của AI sẽ xuất hiện từng từ một kèm con trỏ `.caret` nhấp nháy, mang lại cảm giác sinh động.
*   **Reduced Motion:** Tích hợp CSS Media Query `@media (prefers-reduced-motion: reduce)` để tắt hoàn toàn hiệu ứng quay của spinner, nhấp nháy của con trỏ và đếm ngược nếu người dùng yêu cầu trên thiết bị của họ.
*   **Hỗ trợ chạm:** Tất cả các nút tương tác đều hỗ trợ active state (`transform: scale(0.97)`), có padding rộng tối thiểu 56px chiều cao.

---

## 4. Tự Đánh Giá Spec (Self-Review)
1.  **Placeholder scan:** Không có giá trị TBD hoặc TODO nào. Bảng màu và các hành vi tương tác đã được định nghĩa chi tiết.
2.  **Tính nhất quán:** Bảng màu Soft Warm Rose & Sage sẽ được dùng chung cho cả hai file prototype để tạo cảm giác đồng bộ.
3.  **Khả năng kiểm thử:** Trạng thái tương tác nội bộ được mô phỏng trực tiếp qua JS và có bảng điều khiển dễ dàng kiểm nghiệm trực quan.
