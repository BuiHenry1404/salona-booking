# Kiến trúc Prompt Caching 2 Lớp cho Azure OpenAI (`gpt-5.4-mini`) — Thiết kế

Ngày: 2026-10-27.

## 1. Vì sao

Hệ thống chatbot đặt lịch chạy trên **Azure OpenAI** với model **`gpt-5.4-mini`**. Mỗi lượt hội thoại đi qua đồ thị LangGraph với nhiều lượt gọi model (`supervisor`, `booking`, `shop`, `timeparse`, `phrase`, `rewrite`).

Cơ chế **Automatic Prompt Caching** của Azure OpenAI hoạt động hoàn toàn tự động ở phía server với 2 đặc tính cốt lõi:
1. **Cửa sổ lưu đệm tính bằng PHÚT (5 – 10 phút):** Cache tồn tại trên GPU của Azure trong khoảng 5–10 phút kể từ lượt gọi gần nhất có cùng tiền tố. Mỗi lượt gọi trúng cache sẽ gia hạn thêm thời gian sống.
2. **Ngưỡng kích hoạt tối thiểu 1.024 tokens:** Azure OpenAI chỉ lưu cache khi toàn bộ prompt đạt từ **1.024 tokens** trở lên và có tiền tố khớp chính xác theo từng khối 128 tokens.

### Hiện trạng và Khoảng trống kỹ thuật

| Node trong hệ thống | Kích thước Prompt | Trạng thái Cache trên Azure | Vấn đề / Khoảng trống |
|---|---|---|---|
| **Subagents (`booking`, `shop`)** | ~1.200 – 1.600 tokens (System + 7 tools) | **ĐÃ ĐƯỢC CACHE** trong phiên 5–10 phút | Tiền tố tĩnh đã được đưa lên đầu nhờ Bẫy #9 (`CONTEXT.md`). |
| **`supervisor`** | ~500 tokens | **KHÔNG THỂ CACHE** | Dưới 1.024 tokens $\rightarrow$ Azure tính phí đầy đủ và mất ~1–2s mạng mỗi lượt. |
| **`timeparse`** | ~300 tokens | **KHÔNG THỂ CACHE** | Dưới 1.024 tokens, và chuỗi giờ:phút đổi mỗi 60 giây làm vỡ tiền tố. |

Các câu chào hỏi ("chào em", "alo"), câu hỏi lặp lại hoặc câu thời gian thông dụng ("sáng mai 9h", "chiều nay 3h") trong cùng một phiên chat vẫn buộc hệ thống phải gửi request mạng ra Azure OpenAI cho `supervisor` và `timeparse`.

Bản thiết kế này tập trung 100% vào **Azure OpenAI (`gpt-5.4-mini`)**, kết hợp tối ưu tiền tố và bộ đệm Redis tầng ứng dụng với TTL theo phút.

---

## 2. Kiến trúc 2 Lớp (Two-Tier Architecture)

```
                       [ Khách gửi tin nhắn ]
                                 │
                                 ▼
                   ┌───────────────────────────┐
                   │ Tầng 2: Redis Cache (App) │ ──── (Hit: < 2ms, 0 Token) ────┐
                   │ TTL: 10 – 15 Phút         │                                 │
                   │ (Supervisor / Timeparse)  │                                 │
                   └───────────────────────────┘                                 │
                                 │ (Miss)                                        │
                                 ▼                                               │
                   ┌───────────────────────────┐                                 │
                   │ Tầng 1: Azure Prompt      │                                 │
                   │ Caching (gpt-5.4-mini)    │                                 │
                   │ Subagents (> 1.024 tokens)│                                 │
                   └───────────────────────────┘                                 │
                                 │                                               │
                                 ▼                                               ▼
                   [ Xử lý / Sinh câu trả lời ] ◄────────────────────────────────┘
```

*   **Tầng 1 (Azure OpenAI Automatic Prefix Caching):** Tối ưu hóa các node lớn (`booking`, `shop`) có prompt $\ge 1.024$ tokens để tối đa hóa tỷ lệ trúng cache 5–10 phút của Azure.
*   **Tầng 2 (Application-level Redis Cache):** Bù đắp cho giới hạn 1.024 tokens của Azure bằng cách lưu đệm kết quả của các node ngắn (`supervisor`, `timeparse`) trong **10 – 15 phút** (đúng bằng vòng đời 1 phiên chat của khách).

---

## 3. Tầng 1: Tối ưu Azure OpenAI Prefix Caching

### 3.1. Bảo toàn bố cục phân tầng cho Subagents (`agents.py`)
Tiếp tục duy trì nghiêm ngặt thứ tự tin nhắn đã được chốt trong [`CONTEXT.md#L138`](file:///home/henryb1/Desktop/HenryB1/data/salona-booking/CONTEXT.md#L138) (Bẫy #9):
```
1. [SystemMessage]  Hướng dẫn tĩnh + Schema 7 Tools (BẤT BIẾN -> Azure cache 100%)
2. [HumanMessage]   Diễn biến cuộc trò chuyện (summary + slots)
3. [History]        Lịch sử chat (chỉ nối thêm đuôi)
4. [HumanMessage]   Khối bối cảnh (context_block - chứa ngày giờ động nhích từng phút)
5. [HumanMessage]   Lời nhắn mới nhất của khách
```
Khi khách chat liên tục trong phiên (cách nhau dưới 5–10 phút), Azure OpenAI tự động nhận diện tiền tố tĩnh ở mục (1), giảm 50% chi phí token input và giảm thời gian phản hồi (TTFT) xuống còn ~300ms.

### 3.2. Chuẩn hóa System Prompt cho `timeparse` (`timeparse.py`)

*   **Vấn đề hiện tại:** `_ask_model` nhúng trực tiếp `{hour:02d}:{minute:02d}` vào `_PROMPT` ở dòng 207, khiến prompt thay đổi mỗi 60 giây.
*   **Cấu trúc lại:**
    *   Tách riêng `TIMEPARSE_SYSTEM_PROMPT` tĩnh: chứa toàn bộ quy tắc ngôn ngữ tiếng Việt, quy ước các buổi sáng/trưa/chiều/tối, định dạng ISO +07:00 và enum `missing`.
    *   Chuyển mốc thời gian động sang `HumanMessage`:
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
    *   **Ưu tiên quy tắc cứng (Regex + Anchor):** Luôn thử `_match_regex` và `apply_anchor` trước. Nếu giải quyết được thì trả về ngay với **0 lượt gọi model**.

---

## 4. Tầng 2: Application-level Redis Cache (TTL theo Phút)

### 4.1. Hạ tầng & Cấu hình
*   **Dịch vụ Redis (`docker-compose.yml`)**:
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
    *   `redis_uri: Optional[str] = None` (mặc định `redis://localhost:6379/0`).
    *   `cache_enabled: bool = True`.
    *   `cache_supervisor_ttl_seconds: int = 900` (15 phút).
    *   `cache_timeparse_ttl_seconds: int = 600` (10 phút).
    *   Chỉ nạp từ file `.env` (tuân thủ nguyên tắc không đọc shell env).

### 4.2. Chuẩn hóa Khóa Cache (Key Normalization)
Chuẩn hóa câu khách để các biến thể gõ câu hỏi đều ánh xạ về cùng một khóa:
```python
def normalize_text(text: str) -> str:
    """1. Chuyển chữ thường, strip khoảng trắng.
    2. Bỏ dấu câu đầu/cuối (., !, ?, ~, ,, ...).
    3. Gộp nhiều khoảng trắng liền nhau thành 1 khoảng trắng duy nhất.
    """
    text = text.lower().strip()
    text = re.sub(r'^[^\w\s]+|[^\w\s]+$', '', text)
    return re.sub(r'\s+', ' ', text)
```

### 4.3. Các Vùng Cache và TTL theo Phút

#### A. Supervisor Route Cache
*   **Chức năng:** Bỏ qua lượt gọi `gpt-5.4-mini` cho các câu chào hỏi, hỏi giờ thông dụng.
*   **Khóa:** `cache:sup:{sha256(normalized_text)[:16]}`
*   **Giá trị:** `"booking"` | `"shop"` | `"social"`.
*   **TTL:** **15 phút (900 giây)** — vừa trọn vòng đời một phiên chat của khách.
*   **Phạm vi an toàn:** Áp dụng cho các câu hỏi độc lập hoặc tin mở đầu phiên chat.

#### B. Timeparse Result Cache
*   **Chức năng:** Lưu kết quả phân tích thời gian cho các câu nói lặp lại trong phiên.
*   **Khóa:** `cache:tp:{today_iso}:{sha256(normalized_text)[:16]}`
*   **Giá trị:** JSON chuỗi của `ParsedTime`.
*   **TTL:** **10 phút (600 giây)**.
*   **Hàng rào an toàn kép (`_guard` Gate):**
    *   Dữ liệu lấy từ cache **bắt buộc vẫn chạy qua `_guard(candidate, now)`**.
    *   Nếu một câu nói chỉ giờ quá khứ so với thời điểm hiện tại, `_guard` lập tức phát hiện và chuyển thành `missing: ["ngày khác — giờ đó qua mất rồi"]`. An toàn tuyệt đối 100%.

### 4.4. Cơ chế Fail-Soft
*   Tất cả thao tác Redis có `socket_timeout = 0.05` (50ms).
*   Nếu Redis gặp sự cố hoặc timeout: ghi log `logger.warning("cache_error_bypass")`, trả về `None` (Cache Miss) và chuyển tiếp cho `gpt-5.4-mini` xử lý. Khách hàng không bao giờ bị đứt quãng cuộc trò chuyện.

---

## 5. Tích hợp LangGraph & Dữ liệu

1. **Tại `app/agents/booking_graph/supervisor.py`**:
   - Trước khi gọi LLM: kiểm tra `CacheService.get_supervisor_route(text)`.
   - Hit: Trả về `{"route": cached_route}` (tiết kiệm ~500 tokens, 0ms LLM).
   - Miss: Gọi Azure OpenAI `gpt-5.4-mini`, lưu cache và trả về.
2. **Tại `app/agents/booking_graph/timeparse.py`**:
   - Thứ tự xử lý:
     1. Thử `_match_regex(text, now)` (Regex nhanh)
     2. Đọc `CacheService.get_parsed_time(text, today_iso)` (Redis 10 phút)
     3. Gọi `_ask_model(text, now)` (Azure OpenAI `gpt-5.4-mini`)
     4. Áp dụng `_guard(candidate, now)` và `apply_anchor(...)`
     5. Lưu cache nếu kết quả hợp lệ.

---

## 6. Chiến lược Kiểm thử (Testing Strategy)

*   **Fixture `fake_redis` (`tests/conftest.py`)**: Sử dụng `fakeredis.aioredis.FakeRedis`, tự động dọn sạch cache sau mỗi test, đảm bảo test suite chạy độc lập không cần bật container Redis thật.
*   **Các bài test cốt lõi**:
    1.  `test_normalize_text`: Kiểm tra khử dấu câu (`!`, `?`), khoảng trắng thừa, chữ hoa/thường.
    2.  `test_supervisor_cache_hit_skips_llm`: Lần 1 gọi LLM, lần 2 gọi câu tương tự $\rightarrow$ khẳng định LLM được gọi **0 lần**.
    3.  `test_timeparse_cache_and_guard_safety`: Kiểm tra câu nói lấy từ cache nhưng giờ đã qua $\rightarrow$ `_guard` vẫn chặn chuẩn xác.
    4.  `test_redis_down_fails_soft`: Tắt Redis giả lập $\rightarrow$ hệ thống tự động fallback gọi `gpt-5.4-mini` bình thường.
    5.  `test_timeparse_prompt_structure`: Kiểm tra `TIMEPARSE_SYSTEM_PROMPT` không chứa giờ phút động.

---

## 7. Kế hoạch triển khai (Phasing)

*   **Pha 1 (Tối ưu Prompt Timeparse & Azure Prefix Caching):**
    1. Tách `TIMEPARSE_SYSTEM_PROMPT` tĩnh trong `timeparse.py`.
    2. Đưa giờ phút vào `HumanMessage` động.
    3. Kiểm thử đảm bảo 14 test của `test_timeparse.py` và các test liên quan tiếp tục xanh.
*   **Pha 2 (Tích hợp Redis Cache 10–15 phút):**
    1. Thêm cấu hình Redis vào `docker-compose.yml`, `config.py`, `.env.example`.
    2. Xây dựng `app/services/cache.py` (chuẩn hóa khóa, TTL theo phút, fail-soft).
    3. Tích hợp vào `supervisor.py` và `timeparse.py`.
    4. Viết bộ unit tests với `fakeredis`.
