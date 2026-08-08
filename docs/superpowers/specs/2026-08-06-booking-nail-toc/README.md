# Thiết kế: App đặt lịch nail–tóc cho người lớn tuổi

**Ngày:** 2026-08-06
**Nguồn:** [`docs/personal-project.md`](../../../personal-project.md)
**Trạng thái:** đã duyệt, sẵn sàng lập kế hoạch triển khai

Tài liệu được tách thành nhiều file để mỗi phần đọc trọn vẹn được trong một lượt. File này chứa mục tiêu, các quyết định đã chốt, và bản đồ dẫn tới phần còn lại.

## Bản đồ tài liệu

| File | Nội dung | Đọc khi làm |
|---|---|---|
| [01-architecture.md](01-architecture.md) | Ngăn xếp, năm tầng, hạ tầng, 4 sơ đồ | Bất kỳ phần nào — đọc trước tiên |
| [02-data-model.md](02-data-model.md) | Collection Mongo, chặn trùng giờ, pgvector, múi giờ | Model, repository, migration |
| [03-auth.md](03-auth.md) | Đăng nhập SĐT, quên mật khẩu, rate limit, phân quyền | Auth service, router |
| [04-agent.md](04-agent.md) | LangGraph, tool, memory, bố cục prompt, Langfuse | Agent, memory adapter |
| [05-frontend.md](05-frontend.md) | Năm màn hình React và nguyên tắc UI | Frontend |
| [06-telegram.md](06-telegram.md) | Bot cho chủ tiệm, notifications, long polling | Telegram, notifications |
| [07-errors-testing.md](07-errors-testing.md) | Bảng xử lý lỗi, bốn tầng kiểm thử | Mọi phần, và trước khi viết test |

## 1. Mục tiêu

App đặt lịch làm nail và tóc cho một tiệm nhỏ. Cả khách lẫn chủ tiệm đều là người lớn tuổi, nên ràng buộc chi phối mọi quyết định là **ít thao tác nhất có thể** và **dùng được trên điện thoại**.

Khách chat với AI để biết chủ tiệm đang bận hay rảnh, và để đặt lịch. AI gọi thẳng tool của app, không qua bên thứ ba. Lịch đặt xong thì cả khách và chủ tiệm đều thấy.

AI **chỉ** phục vụ việc đặt lịch, không dùng cho mục đích khác.

## 2. Quyết định đã chốt

| Vấn đề | Quyết định |
|---|---|
| Kênh | Web app mobile-first trên trình duyệt, user nhớ link |
| Frontend | React (app riêng, gọi API qua CORS) |
| Nội dung một lịch | Ngày + giờ + ghi chú tự do. Không có danh mục dịch vụ |
| Xác nhận lịch | AI tự chốt, chặn trùng bằng buffer cố định |
| Trạng thái bận/rảnh | Thủ công: một nút + chọn nhanh thời gian, hết giờ tự về rảnh |
| Quên mật khẩu | Đúng như doc: SĐT + mật khẩu mới. Có rate limit và log |
| Memory | Ba tầng tách bạch: danh tính (tất định) / lịch sử hội thoại / ngữ nghĩa |
| Thư viện memory | **Mem0** (self-hosted) trên pgvector, thay vì tự viết embed và trích xuất |
| DB | Mongo cho dữ liệu ứng dụng, Postgres chỉ chứa memory ngữ nghĩa |
| Framework agent | LangGraph (thay AutoGen), supervisor + 2 subagent |
| Báo lịch mới | Màn hình lịch hôm nay + Socket.IO realtime trong app |
| Kênh phụ cho chủ tiệm | **Bot Telegram** — báo lịch mới, tra cứu, đổi bận/rảnh. Miễn phí, không cần giấy phép kinh doanh |
| Quan sát (observability) | Langfuse |
| Embedding | Azure OpenAI `text-embedding-3-small` (1536 chiều) |
| Số worker | **Đúng 1** — ràng buộc từ long polling của Telegram, xem [06](06-telegram.md) |

### Rủi ro đã biết và chấp nhận

Luồng quên mật khẩu theo doc cho phép **bất kỳ ai biết số điện thoại của khách cũng đổi được mật khẩu tài khoản đó**. Chủ dự án đã cân nhắc và giữ nguyên vì tiệm nhỏ, khách quen, và ưu tiên tối giản thao tác cho người lớn tuổi. Giảm rủi ro bằng hai biện pháp rẻ: giới hạn 5 lần đổi mỗi giờ theo SĐT và theo IP, ghi log mọi lần đổi kèm thời điểm và IP.

### Vì sao chưa dùng Redis

Đã rà từng chỗ Redis thường được dùng, chỗ nào cũng đã có lời giải khác hoặc không áp dụng: rate limit dùng Mongo + TTL index; Socket.IO không cần message queue vì chỉ chạy 1 worker; `shop_status` là một document bé nên cache là thừa; `pending_confirmation` lưu trong `conversations`; chống đặt trùng đã do unique partial index làm ở tầng DB, nguyên tử hơn khóa phân tán.

Điểm yếu đang chấp nhận: `save_memory` và thông báo Telegram chạy bằng `BackgroundTasks` nên **mất nếu app restart đúng lúc**. Hậu quả nhẹ — mất một mẩu memory hoặc một thông báo, không hỏng dữ liệu.

Ba điều kiện khiến câu trả lời đổi thành "có": cần chạy nhiều worker hoặc nhiều instance (Socket.IO sẽ cần message queue); cần job nền bền có retry (ví dụ nhắc lịch trước giờ hẹn); hoặc mở rộng ra nhiều tiệm. Thêm Redis lúc đó không phải viết lại gì.

## 3. Phạm vi cố tình bỏ ra ngoài

Không danh mục dịch vụ. Không thời lượng riêng theo dịch vụ. Không nhiều thợ. Không thanh toán. Không SMS. Không nhắc lịch tự động. Không đánh giá. Không báo cáo doanh thu. Không đa ngôn ngữ.

**Không Zalo.** Đã cân nhắc và loại: Zalo OA gói Cơ bản (0đ) không gửi được tin ngoài khung 48 giờ, nên thông báo chủ động sẽ tốn phí (ZBS Template Message ~55đ/tin, hoặc gói Tăng trưởng 2.500.000đ/năm). Rào cản lớn hơn là đăng ký OA có thể cần giấy phép kinh doanh — thứ một tiệm nail nhỏ chưa chắc có, và nằm ngoài tầm kiểm soát của code. Telegram làm đúng việc đó, miễn phí, không giấy tờ.

**Khách hàng không dùng Telegram.** Bot chỉ dành cho chủ tiệm. Khách vẫn chỉ dùng web app.

## 4. Việc phải làm với code hiện có

**Xóa:** `app/agents/soulcare_team.py`, `tests/test_youtube_search.py`, cấu hình `youtube_api_key`.

**Viết lại:** `app/models/user.py` (email/username → phone, thêm `role`), `app/services/auth.py` (đăng nhập bằng SĐT, bỏ đăng ký tự do, thêm luồng quên mật khẩu), `app/repositories/user.py`, `app/api/v1/routers/auth.py`, `app/api/v1/schemas.py`.

**Thêm mới:** `app/models/appointment.py`, `app/models/shop_status.py`, `app/models/shop_hours.py`, `app/repositories/appointment.py`, `app/services/appointment.py`, `app/services/shop_status.py`, `app/services/rate_limit.py`, `app/api/v1/routers/appointments.py`, `app/api/v1/routers/shop_status.py`, `app/memory/` (adapter async bọc Mem0, cấu hình, kiểm tra số chiều lúc khởi động), `app/agents/booking_graph/` (state, supervisor, hai subagent, tool, khối bối cảnh tất định), `app/core/langfuse.py`, `app/services/notifications.py` (tỏa tin Socket.IO + Telegram), `app/telegram/` (`bot.py` vòng lặp long polling, `handlers.py` xử lý nút bấm, `notify.py` gửi tin đi).

**Giữ nguyên:** `app/services/socketio_service.py`, `app/core/security.py`, `app/core/logging.py`, `app/infrastructure/database.py`, `app/repositories/base.py`.

**Phụ thuộc mới:** `mem0ai`, `langgraph`, `langchain-openai`, `langfuse`, `psycopg`. Gỡ: `autogen-agentchat`, `autogen-core`, `google-api-python-client`. Telegram dùng `httpx` — đã có sẵn, không thêm phụ thuộc.

**Biến môi trường mới:** `POSTGRES_*`, `AZURE_OPENAI_EMBEDDING_MODEL`, `EMBEDDING_DIMS`, `LANGFUSE_PUBLIC_KEY`, `LANGFUSE_SECRET_KEY`, `LANGFUSE_HOST`, `TELEGRAM_BOT_TOKEN`, `TELEGRAM_ADMIN_CHAT_IDS`, `BOOKING_SLOT_MINUTES`. Mọi tích hợp ngoài (Langfuse, Telegram, Mem0) đều tắt được bằng cách bỏ trống biến, app vẫn chạy.
