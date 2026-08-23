# Task 4b · Lịch sử trò chuyện

> **Vai xưng hô đã đổi sau tài liệu này.** Từ 2026-08-23 lễ tân xưng "em",
> gọi khách "anh"/"chị". Mọi câu "con", "cô", "chú", "bác" dưới đây là
> nguyên văn của thời điểm đó, giữ lại làm biên bản chứ không phải mẫu để
> chép theo. Vai hiện hành: mục "Xưng hô" trong [`CONTEXT.md`](../../../../CONTEXT.md).

> Thuộc plan [Frontend React](README.md). **Đọc [Ràng buộc toàn cục](README.md#ràng-buộc-toàn-cục) trước khi bắt đầu** — chúng áp cho mọi task, kể cả khi không nhắc lại ở đây.

**Vì sao có màn này.** Hội thoại cắt phiên theo ngày, nên hôm sau mở app ra là khung chat trống. Với khách lớn tuổi, đó dễ bị hiểu thành "mất hết rồi", hoặc tệ hơn — "lịch con đặt hôm qua có còn không?". Màn này trả lời đúng câu đó. Nó là phần bù cho quyết định cắt phiên, không phải tính năng thêm cho vui.

Đánh số `4b` thay vì chèn `5` rồi dồn cả loạt: task 5–8 được tham chiếu chéo ở nhiều chỗ, đổi số là phải sửa hết và dễ sót. Cùng lý do đã dùng cho `4b` ở Plan 2.

**Files:**
- Create: `frontend/src/screens/ChatHistoryScreen.tsx`, `frontend/src/screens/ChatHistoryScreen.test.tsx`, `frontend/src/screens/PastChatScreen.tsx`, `frontend/src/screens/PastChatScreen.test.tsx`
- Modify: `frontend/src/App.tsx`, `frontend/src/screens/ChatScreen.tsx`

**Interfaces:**
- Consumes: `api` (task 2), `MessageBubble` (task 4), `Button` (task 1), `formatViDate` (task 3)
- Produces:
  - `<ChatHistoryScreen />` tại route `/lich-su`
  - `<PastChatScreen />` tại route `/lich-su/:day`

## Backend đã có sẵn

```
GET /api/v1/conversations/days
    → [{ day: "2026-08-12", message_count: 6,
          preview: "mai 3 giờ chiều làm tóc được không con" }]

GET /api/v1/conversations/days/2026-08-12
    → [{ role: "user", content: "...", created_at: "..." }]
```

Cả hai lấy `user_id` từ JWT, không nhận từ tham số — khách chỉ bao giờ thấy dữ liệu của chính mình.

## Ràng buộc riêng của màn này

**Không có ô nhập ở màn xem lại.** Và **không** diễn đạt điều đó bằng ô nhập xám hay bị khoá — ràng buộc toàn cục yêu cầu mọi trạng thái nói bằng **chữ**. Chỗ ô nhập là một dòng: *"Đây là cuộc trò chuyện ngày 12 tháng 8. Cô chú muốn nhắn thì quay về hôm nay."* kèm nút to quay lại.

**Không cần chặn ở tầng quyền.** Backend không có đường nào để nhắn vào một ngày cũ: `append()` luôn ghi vào cuối và tin mới luôn thuộc hôm nay, không có tham số ngày ở đâu cả. "Chỉ xem" là thuộc tính cấu trúc — giao diện chỉ cần đừng vẽ ô nhập, không phải canh thêm gì.

**Ngày viết đủ chữ tiếng Việt** — "Thứ Ba, 12 tháng 8", không viết `12/08`. Cùng lý do với mọi chỗ hiển thị thời gian khác trong app.

**Đường vào là link chữ to dưới thẻ trạng thái tiệm** ở màn Chat, không nhét vào menu ẩn. Chat vẫn là hành động chính duy nhất của màn đó.

- [ ] **Step 1: Viết test màn danh sách (sẽ fail)**

Tạo `frontend/src/screens/ChatHistoryScreen.test.tsx`:

```tsx
import { render, screen, waitFor } from "@testing-library/react";

it("hiện ngày bằng chữ tiếng Việt, không phải 12/08", async () => {
  mockApi("/conversations/days", [
    { day: "2026-08-12", message_count: 6, preview: "mai 3 giờ chiều làm tóc" },
  ]);

  renderWithRouter(<ChatHistoryScreen />);

  await waitFor(() => expect(screen.getByText(/12 tháng 8/)).toBeInTheDocument());
  expect(screen.queryByText("12/08")).not.toBeInTheDocument();
});

it("hiện câu mở đầu của khách để nhận ra hôm đó nói chuyện gì", async () => {
  mockApi("/conversations/days", [
    { day: "2026-08-12", message_count: 6, preview: "mai 3 giờ chiều làm tóc" },
  ]);

  renderWithRouter(<ChatHistoryScreen />);

  await waitFor(() =>
    expect(screen.getByText(/mai 3 giờ chiều làm tóc/)).toBeInTheDocument()
  );
});

it("chưa từng trò chuyện thì nói bằng chữ, không để trống trơn", async () => {
  mockApi("/conversations/days", []);

  renderWithRouter(<ChatHistoryScreen />);

  await waitFor(() =>
    expect(screen.getByText(/chưa có cuộc trò chuyện nào/i)).toBeInTheDocument()
  );
});
```

- [ ] **Step 2: Viết test màn xem lại (sẽ fail)**

Tạo `frontend/src/screens/PastChatScreen.test.tsx`:

```tsx
it("không có ô nhập ở ngày cũ", async () => {
  mockApi("/conversations/days/2026-08-12", [
    { role: "user", content: "mai 3 giờ chiều làm tóc", created_at: "..." },
  ]);

  renderWithRouter(<PastChatScreen />, { route: "/lich-su/2026-08-12" });

  await waitFor(() => screen.getByText(/mai 3 giờ chiều/));
  expect(screen.queryByRole("textbox")).not.toBeInTheDocument();
});

it("nói bằng CHỮ rằng đây là ngày cũ, không chỉ ẩn ô nhập", async () => {
  mockApi("/conversations/days/2026-08-12", [
    { role: "user", content: "xin chào", created_at: "..." },
  ]);

  renderWithRouter(<PastChatScreen />, { route: "/lich-su/2026-08-12" });

  await waitFor(() =>
    expect(screen.getByText(/cuộc trò chuyện ngày 12 tháng 8/i)).toBeInTheDocument()
  );
  expect(screen.getByRole("button", { name: /quay về hôm nay/i })).toBeInTheDocument();
});
```

- [ ] **Step 3: Chạy test để xác nhận fail**

Run: `npm test -- ChatHistoryScreen PastChatScreen`
Expected: FAIL — hai màn hình chưa tồn tại

- [ ] **Step 4: Viết `ChatHistoryScreen.tsx` và `PastChatScreen.tsx`**

Dùng lại `MessageBubble` của task 4 để bong bóng ở ngày cũ giống hệt bong bóng hôm nay — khách không phải học lại cách đọc.

- [ ] **Step 5: Thêm link vào `ChatScreen.tsx`**

Ngay dưới thẻ trạng thái tiệm: "Xem các lần trò chuyện trước". Cỡ chữ như chữ nền, không phải chữ nhỏ.

- [ ] **Step 6: Chạy test để xác nhận pass**

Run: `npm test`
Expected: PASS, và các test của task 4 vẫn xanh

- [ ] **Step 7: Commit**

## Không làm

Tìm kiếm, xoá hội thoại, đổi tên, phân trang. Khách đặt lịch vài tuần một lần nên sau nhiều năm danh sách vẫn chỉ vài chục thẻ — cuộn là đủ.
