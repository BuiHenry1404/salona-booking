# Kiến trúc Prompt Caching 2 Lớp (Two-Tier Prompt Caching) — Thiết kế

Ngày: 2026-10-27.

## 1. Vì sao

Hệ thống chatbot đặt lịch hiện tại xử lý toàn bộ hội thoại thông qua đồ thị LangGraph với nhiều lượt gọi LLM (`supervisor`, `booking`, `shop`, `social`, `timeparse`, `phrase`, `rewrite`). 

Qua rà soát tại commit `f5b59ff`, hệ thống đã có **thiết kế bố cục prompt tối ưu hóa cho Provider Prefix Caching** (Bẫy #9 trong `CONTEXT.md`: tách `context_block` động ra cuối để giữ `SystemMessage` tĩnh trên đầu). Tuy nhiên, vẫn còn các khoảng trống kỹ thuật lớn:

1. **Các node ngắn không chạm ngưỡng cache của Provider:**
   - OpenAI và Azure OpenAI chỉ kích hoạt Automatic Prompt Caching với prompt từ **1.024 tokens** trở lên.
   - Node `supervisor` (~500 tokens) và `timeparse` (~300 tokens) ngắn hơn ngưỡng này nên 100% lượt gọi đều bị tính phí đầy đủ và chịu độ trễ mạng trọn vẹn (1–2 giây/lượt).
   - `timeparse.py` còn nhúng trực tiếp chuỗi giờ phút `{hour:02d}:{minute:02d}` vào `_PROMPT`, làm vỡ tiền tố mỗi phút một lần.
2. **Các câu hỏi lặp lại chịu chi phí token không đáng có:**
   - Khách thường xuyên gửi các câu chào hỏi ("chào em", "alo", "tiệm ơi"), câu hỏi giờ cố định ("tiệm mở mấy giờ"), hoặc câu chỉ thời gian phổ biến ("sáng mai 9h", "chiều nay 3h").
   - Dù câu hỏi giống hệt nhau, hệ thống vẫn phải kích hoạt toàn bộ đồ thị LLM từ `supervisor` trở đi vì chưa có bộ đệm tầng ứng dụng.
3. **Chưa hỗ trợ Explicit Prompt Caching cho Provider mở rộng:**
   - Khi cấu hình dùng Anthropic Claude (`claude-3-5-sonnet`), API yêu cầu cờ đánh dấu tường minh `cache_control: {"type": "ephemeral"}` ở các khối tin nhắn hoặc tool tĩnh. Code hiện tại trong `app/agents/llm.py` chưa hỗ trợ provider này và chưa gắn cờ cache.

Bản thiết kế này giải quyết triệt để 3 vấn đề trên bằng **Kiến trúc Caching 2 Lớp (Two-Tier Cache)**.

---

## 2. Kiến trúc Tổng thể (Overview)

```
                       [ Khách gửi tin nhắn ]
                                 │
                                 ▼
                   ┌───────────────────────────┐
                   │ Tầng 2: Redis Cache (App) │ ──── (Hit: < 2ms, 0 Token) ────┐
                   │ (Supervisor / Timeparse)  │                                 │
                   └───────────────────────────┘                                 │
                                 │ (Miss)                                        │
                                 ▼                                               │
                   ┌───────────────────────────┐                                 │
                   │ Tầng 1: Provider Cache    │                                 │
                   │ Azure / OpenAI (Prefix)   │                                 │
                   │ Anthropic (cache_control) │                                 │
                   └───────────────────────────┘                                 │
                                 │                                               │
                                 ▼                                               ▼
                   [ Xử lý / Sinh câu trả lời ] ◄────────────────────────────────┘
```

*   **Tầng 1 (Provider Prompt Caching):** Tối ưu hóa các lượt gọi bắt buộc phải tới LLM. Đảm bảo tiền tố `SystemMessage` tĩnh 100%, chuẩn hóa prompt `timeparse`, và gắn thẻ `cache_control` cho Anthropic.
*   **Tầng 2 (Application-level Redis Cache):** Chặn các câu hỏi/cụm từ lặp lại ngay tại tầng ứng dụng bằng Exact-match trên chuỗi đã chuẩn hóa. Phản hồi tức thì (< 2ms) và tốn **0 token**.

---

## 3. Tầng 1: Tối ưu Provider-level Prompt Caching

### 3.1. Hỗ trợ Anthropic với Explicit Cache Control (`app/agents/llm.py`)

Anthropic hỗ trợ prompt caching bằng cách lưu trữ các khối prompt có đánh dấu `cache_control: {"type": "ephemeral"}` với thời gian sống 5 phút (tự động gia hạn mỗi khi trúng cache).

1. **Bổ sung provider `anthropic` vào `SUPPORTED_CHAT_PROVIDERS`**:
   - Khởi tạo `ChatAnthropic` từ `langchain-anthropic` khi `LLM_PROVIDER=anthropic`.
   - Kiểm tra `ANTHROPIC_API_KEY` và `ANTHROPIC_MODEL`.
2. **Cấu trúc đánh dấu Cache:**
   - Với các subagent (`booking`, `shop`, `social`):
     Gắn `cache_control` vào `SystemMessage` tĩnh dài:
     ```python
     SystemMessage(
         content=[
             {
                 "type": "text",
                 "text": prompt,
                 "cache_control": {"type": "ephemeral"},
             }
         ]
     )
     ```
   - Với Tool definitions: Tool cuối cùng trong danh sách bind tools được đánh dấu `cache_control` để toàn bộ schema của 7 tools được cache trọn vẹn.
3. **Hiệu quả:**
   - Trong một phiên hội thoại thông thường (5–15 tin nhắn), lượt thứ 2 trở đi tiết kiệm tới **90% chi phí input token** và giảm thời gian phản hồi (TTFT) từ ~1.8s xuống ~300ms.

### 3.2. Chuẩn hóa System Prompt cho `timeparse` (`app/agents/booking_graph/timeparse.py`)

**Thay đổi:**
- Tách `_PROMPT` hiện tại thành `TIMEPARSE_SYSTEM_PROMPT` **hoàn toàn tĩnh**: chứa quy tắc ngôn ngữ, quy ước các buổi, định dạng ISO +07:00, và danh sách enum `missing`.
- Đưa các thông số thời gian thực (`weekday`, `day`, `month`, `year`, `hour`, `minute`, `today`) và câu nói của khách vào `HumanMessage` động phía sau:
  ```python
  messages = [
      SystemMessage(content=TIMEPARSE_SYSTEM_PROMPT),
      HumanMessage(
          content=(
              f"Bây giờ là {weekday} {day}/{month}/{year}, {hour:02d}:{minute:02d} (hôm nay là {today}).\n"
              f"Câu của khách: {text}"
          )
      ),
  ]
  ```
- **Ưu tiên quy tắc cứng:** Đường dẫn regex (`_match_regex`) và neo thời gian (`apply_anchor`) luôn được thực thi trước. Chỉ khi các bước này không xác định được đầy đủ mới chuyển tiếp sang `_ask_model`.

---

## 4. Tầng 2: Application-level Redis Cache

### 4.1. Hạ tầng & Cấu hình
*   **Docker Compose (`docker-compose.yml`)**:
    ```yaml
    redis:
      image: redis:7-alpine
      restart: unless-stopped
      ports:
        - "6379:6379"
      command: redis-server --maxmemory 128mb --maxmemory-policy allkeys-lru
      volumes:
        - redis_data:/data
    ```
*   **Cấu hình (`app/core/config.py`)**:
    - `redis_uri: Optional[str] = None` (ví dụ `redis://localhost:6379/0`).
    - `cache_enabled: bool = True`.
    - Biến cấu hình chỉ nạp từ `.env`, tuân thủ nguyên tắc không đọc shell environment.

### 4.2. Chuẩn hóa Khóa Cache (Key Normalization)
Để Exact-match đạt hiệu quả cao mà không bị phân mảnh bởi ký tự thừa, câu nói của khách được chuẩn hóa trước khi tạo hash:
```python
def normalize_text(text: str) -> str:
    """Chuẩn hóa câu khách để làm khóa cache:
    1. Chuyển chữ thường và strip khoảng trắng hai đầu.
    2. Bỏ dấu câu đầu và đuôi (., !, ?, ~, ,, ...).
    3. Gộp nhiều khoảng trắng liền nhau thành 1 khoảng trắng duy nhất.
    """
    text = text.lower().strip()
    text = re.sub(r'^[^\w\s]+|[^\w\s]+$', '', text)
    return re.sub(r'\s+', ' ', text)
```

### 4.3. Các Vùng Cache và Thời gian sống (TTL)

#### A. Supervisor Route Cache
*   **Chức năng:** Cache kết quả phân loại ý định (`booking`, `shop`, `social`).
*   **Khóa:** `cache:sup:{sha256(normalized_text)[:16]}`
*   **Giá trị:** Chuỗi nhãn tuyến (`booking`, `shop`, `social`).
*   **TTL:** **86.400 giây (24 giờ)**.
*   **Phạm vi an toàn:** Áp dụng cho tin nhắn đầu cuộc hội thoại hoặc các câu hỏi độc lập (chiếm hơn 70% các lượt phân loại định tuyến). Khi khách có `pending_confirmation`, nhánh rẽ `route_from_state` đã xử lý bằng 0 lượt LLM trước đó.

#### B. Timeparse Result Cache
*   **Chức năng:** Cache kết quả phân tích ngày/giờ của các cụm từ tương đối nhưng cố định trong ngày ("sáng mai 9h", "chiều mai lúc 3 giờ", "thứ Hai tuần sau 10h sáng").
*   **Khóa:** `cache:tp:{today_iso}:{sha256(normalized_text)[:16]}`
*   **Giá trị:** JSON của `ParsedTime` (`start_at`, `missing`, `partial_date`, `partial_hour`, `partial_minute`).
*   **TTL:** **43.200 giây (12 giờ)** — cô lập theo ngày `today_iso`.
*   **Hàng rào an toàn (`_guard` Gate):**
    *   Mọi kết quả đọc ra từ Cache **bắt buộc phải chạy qua hàm `_guard(candidate, now)`** trước khi trả về cho caller.
    *   *Ngăn chặn đặt lịch quá khứ:* Nếu câu `"10 giờ sáng"` được cache lúc 9h sáng, đến 14h chiều cùng ngày khách khác hỏi lại `"10 giờ sáng"`, cache trả về 10:00 nhưng `_guard` đối chiếu `10:00 <= 14:00` và tự động gán `start_at = None`, `missing = ["ngày khác — giờ đó qua mất rồi"]`. An toàn tuyệt đối 100%.

### 4.4. Cơ chế Fail-Soft
*   Tất cả thao tác với Redis trong `CacheService` đều được cài đặt `socket_timeout = 0.05` (50ms) và bọc trong khối `try...except Exception`.
*   Nếu Redis down, nghẽn mạng hoặc lỗi kết nối:
    - Ghi log: `logger.warning("cache_error_bypass", extra={"error": str(exc)})`.
    - Trả về `None` (coi như Cache Miss).
    - Toàn bộ luồng chat tiếp tục đi qua LLM bình thường mà không làm gián đoạn khách hàng.

---

## 5. Tích hợp LangGraph & Dữ liệu

1. **Tại `app/agents/booking_graph/supervisor.py`**:
   - Trước khi gọi `model.ainvoke`: kiểm tra `CacheService.get_supervisor_route(text)`.
   - Nếu trúng: trả về `{"route": cached_route}` (bỏ qua LLM).
   - Nếu trượt: gọi model, lưu kết quả hợp lệ vào cache và trả về.
2. **Tại `app/agents/booking_graph/timeparse.py`**:
   - Thứ tự ưu tiên:
     1. `_match_regex(text, now)` (Quy tắc regex)
     2. `CacheService.get_parsed_time(text, today_iso)` (Redis cache)
     3. `_ask_model(text, now)` (Gọi LLM)
     4. Chạy qua `_guard(candidate, now)` và `apply_anchor(...)`.
     5. Lưu vào cache nếu kết quả từ LLM hợp lệ.

---

## 6. Chiến lược Kiểm thử (Testing Strategy)

Đảm bảo bộ test suite của hệ thống tiếp tục xanh 100% mà không bắt buộc phải có Redis thật chạy ngầm:

1. **Fixture `fake_redis` (`tests/conftest.py`)**:
   - Sử dụng `fakeredis.aioredis.FakeRedis` hoặc Mock Cache Service cho môi trường test.
   - Tự động xóa sạch dữ liệu cache sau mỗi hàm test (tương tự việc wipe DB test).
2. **Ca kiểm thử bắt buộc**:
   - `test_normalize_text`: Kiểm tra tính tương đương của các câu có dấu câu, chữ hoa, khoảng trắng thừa.
   - `test_supervisor_cache_hit_skips_llm`: Khẳng định lần gọi thứ hai lấy kết quả từ cache và LLM được gọi đúng 0 lần.
   - `test_timeparse_cache_and_guard_safety`: Kiểm tra việc `_guard` vẫn chặn thành công giờ quá khứ dù dữ liệu đọc ra từ cache.
   - `test_redis_down_fails_soft`: Giả lập Redis ném lỗi kết nối, khẳng định luồng xử lý vẫn hoàn thành mượt mà qua LLM và ghi đúng log warning.
   - `test_anthropic_cache_control_headers`: Kiểm tra định dạng payload của `SystemMessage` khi dùng provider `anthropic`.

---

## 7. Kế hoạch triển khai (Phasing)

*   **Pha 1 (Provider Prompt Caching & Timeparse Refactor):**
    1. Chuẩn hóa `TIMEPARSE_SYSTEM_PROMPT` tĩnh trong `timeparse.py`.
    2. Bổ sung provider `anthropic` kèm `cache_control` vào `app/agents/llm.py`.
    3. Viết unit tests kiểm chứng cấu trúc prompt và tính bất biến của tiền tố cache.
*   **Pha 2 (Application-level Redis Cache):**
    1. Thêm cấu hình Redis vào `docker-compose.yml`, `config.py`, `.env.example`.
    2. Xây dựng `CacheService` với chuẩn hóa khóa và cơ chế fail-soft.
    3. Tích hợp cache vào `supervisor.py` và `timeparse.py`.
    4. Viết unit tests kiểm thử cache hit, TTL, fail-soft và an toàn thời gian với `_guard`.
