# Ponytail Review — Salona Booking Codebase

### What this codebase does:
Hệ thống backend đặt lịch làm đẹp (nail/hair) cho tiệm nhỏ tại Việt Nam: sử dụng FastAPI, MongoDB (Motor), và LangGraph agent điều khiển luồng hội thoại của khách qua Socket.IO, tích hợp bot Telegram cho chủ tiệm. Giả định tải vận hành: 1 worker đơn luồng, ~350 lượt ghi/ngày, khách hàng chủ yếu là người lớn tuổi gõ tiếng Việt tự nhiên trên điện thoại.

---

### Must fix

1. **Xưng hô sai giọng ở câu báo lỗi mất kết nối Mongo** ([`main.py:168`](file:///home/henryb1/Desktop/HenryB1/data/salona-booking/main.py#L168))
   - **What this is:** Exception handler bắt `PyMongoError` trả về mã HTTP 503 khi server mất kết nối tới cơ sở dữ liệu.
   - **Problem:** Handler trả về câu `"Máy của tiệm đang hỏng ạ. Cô chú gọi giúp con số {phone} nhé."`, vi phạm trực tiếp quy tắc xưng hô cứng trong [`CONTEXT.md`](file:///home/henryb1/Desktop/HenryB1/data/salona-booking/CONTEXT.md#L71-L85) (cấm tiệt xưng "con" và gọi "cô chú" ở bất kỳ nơi nào khách nhìn thấy).
   - **Fix:** Đổi câu trả về thành: `"Máy của tiệm đang bận một chút ạ. Anh chị gọi giúp em số {phone} nhé."`.
   - **If we skip it:** Mỗi khi Mongo chập chờn hoặc timeout, khách hàng sẽ thấy thông báo sai hoàn toàn giọng lễ tân ("con - cô chú") đã được chốt bỏ.

---

### Should fix

2. **Endpoint và file test Socket.IO thừa, mô tả sai sự kiện thật** ([`main.py:201-246`](file:///home/henryb1/Desktop/HenryB1/data/salona-booking/main.py#L201-L246), [`static/socketio_test.html`](file:///home/henryb1/Desktop/HenryB1/data/salona-booking/static/socketio_test.html))
   - **What this is:** Mount thư mục `/static` và endpoint `/socket-info` để phục vụ file HTML test giao tiếp Socket.IO ban đầu.
   - **Problem:** Endpoint `/socket-info` trả về danh sách sự kiện giả lập (`chat`, `join_conversation`, `leave_conversation`) hoàn toàn không khớp với sự kiện chạy thật trong [`SocketIOService`](file:///home/henryb1/Desktop/HenryB1/data/salona-booking/app/services/socketio_service.py#L40-L61) (`send_message`, `token`, `complete`), đồng thời file test cũ trong `static/` vẫn còn sót xưng hô "con".
   - **Fix:** Xóa endpoint `/socket-info`, bỏ lệnh mount `/static`, và tinh gọn endpoint `root()` trong [`main.py`](file:///home/henryb1/Desktop/HenryB1/data/salona-booking/main.py#L204-L215).
   - **If we skip it:** Codebase ôm 45 dòng code chết và để lộ schema tài liệu sai lệch ra ngoài môi trường chạy thật.

3. **Dùng `datetime.utcnow` tạo datetime không có múi giờ (naive)** ([`app/models/base.py:40-41`](file:///home/henryb1/Desktop/HenryB1/data/salona-booking/app/models/base.py#L40-L41))
   - **What this is:** `default_factory` tạo trường `created_at` và `updated_at` cho `BaseDocument`.
   - **Problem:** `datetime.utcnow()` bị khai tử từ Python 3.12 và trả về datetime naive (`tzinfo=None`), trong khi toàn bộ dự án quy ước múi giờ UTC nhận biết tường minh (`now_utc()` trong [`app/core/clock.py`](file:///home/henryb1/Desktop/HenryB1/data/salona-booking/app/core/clock.py#L9-L11)).
   - **Fix:** Thay `default_factory=datetime.utcnow` bằng `default_factory=now_utc`.
   - **If we skip it:** Khi so sánh trực tiếp timestamp của model mới tạo với các mốc giờ khác sẽ gặp lỗi `TypeError: can't compare offset-naive and offset-aware datetimes`.

---

### Nice to have

4. **Hàm `month_key` không có nơi nào sử dụng** ([`app/core/clock.py:49-62`](file:///home/henryb1/Desktop/HenryB1/data/salona-booking/app/core/clock.py#L49-L62))
   - **What this is:** Hàm định dạng `datetime` thành chuỗi `"YYYY-MM"` theo giờ Việt Nam.
   - **Problem:** Docstring thừa nhận *"Không service nào gọi hàm này"*, code production không dùng tới vì `recent_month_keys` đã tự format nội bộ.
   - **Fix:** Xóa hàm khỏi [`app/core/clock.py`](file:///home/henryb1/Desktop/HenryB1/data/salona-booking/app/core/clock.py) (chuyển vào test suite nếu muốn giữ test kiểm tra aggregation logic).
   - **If we skip it:** Tồn tại 13 dòng code helper không ai gọi.

---

### Thẩm định Over-engineering (LangGraph Agent Pipeline)

Luồng agent gồm nhiều tầng: `Supervisor` $\to$ `Booking / Shop / Social Agent` $\to$ `Confirm (Code)` $\to$ `Guard (Regex)` $\to$ `Rewrite (1-shot LLM)` $\to$ `Phrase (LLM)`.

Thoạt nhìn qua lăng kính thông thường, việc bọc nhiều node cho một tiệm nail nhỏ có vẻ cồng kềnh. **Tuy nhiên, qua đối chiếu với 23 cái bẫy thực tế trong [`CONTEXT.md`](file:///home/henryb1/Desktop/HenryB1/data/salona-booking/CONTEXT.md#L120-L183):**
* **Node Confirm (Code thuần 100%, 0 LLM):** Bắt buộc phải có để triệt tiêu hoàn toàn rủi ro LLM hallucinate tự đặt lịch hoặc tự xóa lịch khi khách chưa nói lời đồng ý rõ ràng.
* **Agent không có tool ghi lịch:** Chỉ có `propose_appointment` (giữ chỗ tạm) $\to$ bảo đảm mọi thao tác ghi Mongo đều được kiểm chứng hợp lệ về mặt dữ liệu.
* **Tầng Guard bằng code tất định:** Thử nghiệm thực tế đo ngày 14/9 chứng minh rằng prompt kỹ thuật không thể sửa được lỗi lặp câu và xưng hô sai giới tính khi khách chat nhiều lượt; chỉ có regex và sequence matcher mới chặn triệt để.

👉 **Kết luận:** Cụm LangGraph này là một **Safety-Critical Harness (bộ giàn giáo phòng vệ bắt buộc)**, không phải over-engineering.

---

- **Verdict:** Fix 1 và 3 trước.
- **Lean:** -58 lines possible (dọn endpoint thừa tại `main.py` và hàm không dùng tại `clock.py`).
- **Not checked:** Thư mục `docs/research/` (untracked file mới tạo).
