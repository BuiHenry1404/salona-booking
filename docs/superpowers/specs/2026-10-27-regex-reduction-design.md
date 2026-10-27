# Thiết kế: Cắt giảm Regex trong AI Agent Pipeline (45 → 3 regex)

Ngày: 2026-10-27
Nhánh: `feat/target-architecture` (worktree `salona-booking-harness-b`, tại commit `4e5d8cf`)
 Liên kết: `docs/superpowers/specs/2026-10-20-target-architecture-design.md`

---

## 1. Bối cảnh & Động lực

Sau khi hoàn thành Task 15 trên nhánh `feat/target-architecture`, toàn bộ hệ thống có **58 biểu thức chính quy (`re.compile`)**, trong đó riêng tầng AI Agent (`app/agents/booking_graph/`) chứa **45 regex**:

| File trong `app/agents/booking_graph/` | Số `re.compile` hiện tại | Vai trò hiện tại |
|---|---:|---|
| `guard.py` | 21 | Bắt nhận vơ (`_CLAIM`, `_CLAIMED`), cắt câu/mệnh đề (`_SENTENCE_END`, `_CLAUSE_SPLIT`, `_QUESTION_AFTER_COMMA`), kiểm giờ/số chữ (`_SPOKEN_CLOCK`, `_CLOCK_COLON`, `_CLOCK_WORDS`, `_DATETIME`, `_NUMBER`), kiểm đại từ (`_REGISTER_BEFORE_NAME`, `_CON_AS_SUBJECT`, `_HONORIFIC_BEFORE`), câu hỏi có/không (`_YES_NO_PARTICLE`), xoá tên tool (`_TOOL_GROUP`, `_TOOL_BARE`, `_MULTI_SPACE`, `_SPACE_BEFORE_PUNCT`) |
| `reply.py` | 14 | Tách từ (`_WORD_SPLIT`), phủ định theo kind (`_NEG_BY_KIND` × 3), hoãn (`_DEFER`), đuôi câu hỏi (`_QUESTION_TAIL`), đổi dịch vụ (`_SERVICE_SWITCH`), dò giờ/ngày/thứ/buổi (`_HOUR`, `_DAY_OF_MONTH`, `_WEEKDAY`, `_PART_OF_DAY`, `_RELATIVE_DAY`, `_CLAUSE_SPLIT`, `_AT_PREP`, `_WAIT_VERB`) |
| `relatives.py` | 6 | Nhận diện câu hỏi người nhà rảnh không / câu hoãn (`_RELATIVE`, `_FREE`, `_DEFER_ASK`, `_DEFER_WAIT`, `_REQUEST`, `_CLAUSE_BREAK`) |
| `timeparse.py` | 3 | Fast-path giờ đơn giản (`_FULL`) + kiểm tra chuỗi có nhắc ngày/giờ cho `anchor` (`_NAMES_A_DAY`, `_NAMES_A_CLOCK`) |
| `tools.py` | 1 | Kiểm tra chuỗi ISO chỉ có ngày không có giờ (`_DATE_ONLY`) |
| **Tổng tầng Agent** | **45** | |

Ngoài tầng Agent, `app/core/phone.py` (1), `app/core/text.py` (8) và `app/services/conversation_state.py` (4) chứa **13 regex** thuộc nhóm chuẩn hoá SĐT, lọc PII/prompt-injection và tự xưng (`detect_self_address`) — đây là rào chắn bảo mật/dữ liệu nên **giữ nguyên**.

### Vấn đề của 45 regex trong tầng Agent
1. **Đoán ngữ nghĩa tiếng Việt bằng chuỗi ký tự**: Tiếng Việt có dấu/không dấu, viết tắt (`"dc"`, `"ko"`, `"h"`, `"g"`), từ đồng âm khi mất dấu (`"doi"` = dời / đổi / đợi; `"gio"` = giờ / gió; `"ba"` = số 3 / bà / chị Ba). Mỗi lần gặp ca thực tế mới (B2 #1–#4, B3 #1–#3, B4 #1–#2), hệ thống lại phải đắp thêm lookbehind/lookahead regex.
2. **Trùng lặp với bước phân tích LLM đã có sẵn**: Từ Task 12/12b, đầu mỗi lượt đã có một lời gọi LLM có cấu trúc (`analyzer.analyze_turn`, `reasoning_effort="low"`, `streaming=False`) đọc câu khách, lịch sử và đề xuất đang chờ. Tuy nhiên schema `TurnAnalysis` mới chỉ lấy `route`, `reply_to_proposal`, `service`, `time_text`, `self_address`, `stated`, `answers_question` — bỏ phí khả năng trích xuất cấu trúc cho thời gian và câu nói phụ (`aside`).
3. **LLM sinh câu tự do ở những bước chỉ cần xác nhận trạng thái rồi dùng regex "phẫu thuật" lại câu**: Ở lượt chốt lịch (`confirm_source == "text"`), `PhraseMiddleware` gọi thêm 1 lượt LLM (`write_phrase`), sau đó `guard.py` phải dùng hàng chục regex (`has_false_claim`, `_guard_phrase`) để canh xem LLM có bịa *"2 giờ chiều em cũng đã giữ cho cháu rồi"* hay không.

---

## 2. Mục tiêu & Ràng buộc

### Mục tiêu
- Cắt giảm **45 → 3 regex** trong `app/agents/booking_graph/` (**giảm 93,3%**):
  - `relatives.py`: **6 → 0 regex**
  - `reply.py`: **14 → 0 regex**
  - `tools.py`: **1 → 0 regex**
  - `timeparse.py`: **3 → 1 regex** (chỉ giữ `_FULL` theo bẫy #11 `CONTEXT.md`)
  - `guard.py`: **21 → 2 regex** (chỉ giữ `_TOOL_GROUP` và `_TOOL_BARE` trong `strip_tool_names`)
- **Không tăng số lượt gọi LLM mỗi tin nhắn** (thậm chí giảm 1 lượt gọi LLM `write_phrase` ở lượt gõ chữ xác nhận).
- **Giữ nguyên 100% tính chất an toàn**: ghi sai lịch = 0; mọi ca lỗi thật đã khoá trong test (`test_confirm_flow.py`, `test_reply_veto.py`, `test_relatives.py`, `test_guard.py`, `test_mw_guard.py`, `test_timeparse.py`) tiếp tục xanh kể cả khi model phân loại giả trả về `"approve"`.

### Ràng buộc bất biến (từ `AGENTS.md` & `CONTEXT.md`)
- Mọi đường ghi lịch đều đi qua `ProposalService.approve`; agent không có tool ghi.
- Chỉ stream token mang tag `respond`.
- Không dùng ví dụ tiếng Việt trong prompt phân loại (bẫy #15, #16).
- Docs, comment, chuỗi giao diện bằng tiếng Việt; commit message bằng tiếng Anh ngắn gọn.

---

## 3. Thiết kế chi tiết (3 Phần)

### Phần 1: Đầu vào & Phân loại (`tokens.py`, `analyzer.py`, `relatives.py`, `reply.py`, `supervisor.py`)

#### 1.1. Module xử lý token thuần Python (`app/agents/booking_graph/tokens.py` — 0 regex)
Thay vì dùng regex `(?<!\w)(?:...)(?!\w)` và `re.split(r"[^\w]+")` ở khắp nơi, gom toàn bộ thao tác tách từ, tách câu, tách mệnh đề và dò cụm từ về một module thuần Python sử dụng `str.translate` (chạy ở tầng C của CPython, nhanh hơn và dễ đọc hơn regex):

- `words(text: str) -> List[str]`: chuyển chữ thường, đổi mọi ký tự không phải chữ/số (`unicodedata.category(ch)[0] not in ("L", "N")`) thành khoảng trắng rồi `.split()`.
- `cased_words(text: str) -> List[str]`: giống `words` nhưng giữ nguyên chữ hoa/thường (dùng cho `check_pronoun` và `check_clock` để phân biệt `"chín giờ"` với tên riêng `"Tám"`, `"Ba"`).
- `padded(text: str) -> str`: trả về `" " + " ".join(words(text)) + " "` (và ` " "` cho chuỗi rỗng).
- `has_phrase(padded_text: str, phrases: Iterable[str]) -> bool`: kiểm tra `any(f" {p} " in padded_text for p in phrases)`. Vì `padded_text` đã chuẩn hoá mọi ranh giới từ thành đúng một dấu cách `" "` ở hai đầu mỗi từ, phép kiểm tra chuỗi con `f" {p} " in padded_text` **tương đương toán học 100%** với `re.search(r"(?<!\w)" + re.escape(p) + r"(?!\w)")` cho cả từ đơn lẫn cụm nhiều từ (`"chưa chắc"`, `"để hỏi"`, `"làm móng"`), với **0 regex**.
- `split_sentences(text: str) -> List[str]`: tách câu theo các dấu kết câu `.!?…` bằng vòng duyệt ký tự thuần Python (0 regex).
- `split_clauses(text: str, *, break_words: Sequence[str] = ()) -> List[str]`: tách mệnh đề theo dấu câu `,;.!?…` và các từ nối (như `"còn"`, `"nhưng"`, `"mà"`), có bảo vệ mốc ngày-giờ kiểu `"Thứ Tư 21/10, 9 giờ sáng"` (dấu phẩy đứng ngay trước chữ số không tách mệnh đề) bằng kiểm tra ký tự kế tiếp `.isdigit()` thuần Python (0 regex).

#### 1.2. Mở rộng `TurnAnalysis` trong `app/agents/booking_graph/analyzer.py`
Bổ sung 2 trường có cấu trúc vào `TurnAnalysis` (cùng một lời gọi LLM `analyze_turn` đầu lượt, không tốn thêm request):

```python
class MentionedTime(BaseModel):
    """Một mốc ngày/giờ khách nhắc tới trong tin nhắn hiện tại."""
    hour: Optional[int] = None          # 0..23 (đã quy đổi theo buổi nếu có, hoặc giờ trần 1..12)
    minute: Optional[int] = None        # 0..59 (mặc định 0 khi chỉ nói giờ)
    part_of_day: Optional[Literal["sang", "trua", "chieu", "toi"]] = None
    weekday: Optional[int] = None       # 0 = Thứ Hai .. 6 = Chủ Nhật (theo chuẩn Python date.weekday())
    day: Optional[int] = None           # 1..31
    month: Optional[int] = None         # 1..12
    day_offset: Optional[int] = None    # 0 = hôm nay, 1 = ngày mai, 2 = ngày mốt, -1 = ngày khác chung chung ("tuần sau", "bữa khác")

class TurnAnalysis(BaseModel):
    route: Literal["booking", "shop", "social"] = "booking"
    reply_to_proposal: Optional[Literal["approve", "reject", "change", "unclear"]] = None
    aside: Optional[Literal["relative_free", "defer_only", "defer_with_request"]] = None
    mentioned_times: List[MentionedTime] = Field(default_factory=list)
    service: Optional[str] = None
    time_text: Optional[str] = None
    self_address: Optional[Literal["chị", "anh"]] = None
    stated: List[str] = Field(default_factory=list)
    answers_question: bool = False
```

Cập nhật `ANALYZE_PROMPT` trong `app/agents/booking_graph/prompts.py` (bằng tiếng Anh, không câu mẫu tiếng Việt theo bẫy #15/#16):
- `aside`:
  - `"relative_free"`: khách đang hỏi hoặc băn khoăn xem người thân / bạn bè đi cùng có rảnh không (không phải hỏi chủ tiệm).
  - `"defer_only"`: khách xin khoan / chờ để hỏi lại người khác, xem lại lịch hoặc nhắn lại sau, và **không** kèm yêu cầu giữ chỗ hay đổi giờ ngay trong câu đó.
  - `"defer_with_request"`: khách vừa nói để hỏi người khác / tính sau nhưng trong cùng tin nhắn vẫn yêu cầu giữ chỗ, đặt lịch hoặc xem giờ trống.
  - Để trống (`null`) trong mọi trường hợp khác.
- `mentioned_times`: danh sách mọi mốc giờ, buổi, thứ hoặc ngày mà khách nhắc tới trong tin nhắn này (rỗng nếu không nhắc).

#### 1.3. Loại bỏ toàn bộ 6 regex trong `app/agents/booking_graph/relatives.py` (6 → 0 regex)
- Giữ nguyên giao diện hàm public của `relatives.py` (`mentions_relative_free`, `mentions_deferral`, `aside_line`, `deferral_reply`) để tương thích ngược với mọi chỗ gọi và toàn bộ `tests/test_relatives.py`.
- Thêm tham số tuỳ chọn `analysis: Optional[dict | TurnAnalysis] = None` cho `aside_line` và `deferral_reply`:
  - Nếu `analysis` đã có trường `aside` khác `None`, dùng trực tiếp kết quả đó.
  - Khi cần kiểm tra tất định trên văn bản (lối tắt trước LLM ở `supervisor.py`, hoặc khi test đơn vị gọi thẳng hàm với chuỗi), dùng `tokens.split_clauses` và `tokens.has_phrase` với bộ từ vựng phẳng (`_RELATIVES`, `_FREE_WORDS`, `_DEFER_PHRASES`, `_REQUEST_PHRASES`) — **0 regex (`re.compile` = 0)**.

#### 1.4. Loại bỏ toàn bộ 14 regex trong `app/agents/booking_graph/reply.py` (14 → 0 regex)
1. **Tách từ & Go-Ahead (`_words`, `is_go_ahead`, `is_short_affirmation`)**:
   - Dùng `tokens.words(text)` thay cho `_WORD_SPLIT`.
   - `is_go_ahead(text, kind)` và `is_short_affirmation(text)` kiểm tra `"?" not in (text or "")` và tập từ thuộc từ điển đóng (`_GO_AGREE | _GO_VERBS[kind] | _GO_FILLER`) kết hợp phủ quyết nhanh — hoàn toàn bằng tập hợp (`set`) và phép kiểm tra phần tử, **0 regex**.
2. **So khớp thời gian toán học + bộ quét token (`mentions_other_time`, `_strip_queued_clauses`)**:
   - Thay 8 regex thời gian (`_HOUR`, `_DAY_OF_MONTH`, `_WEEKDAY`, `_PART_OF_DAY`, `_RELATIVE_DAY`, `_CLAUSE_SPLIT`, `_AT_PREP`, `_WAIT_VERB`) bằng hai lớp:
     - **Lớp 1 (Từ `TurnAnalysis.mentioned_times`)**: hàm `mentioned_time_differs(mentioned_times, proposal_start, queued_starts, now)` so sánh trực tiếp các trường số nguyên (`hour`, `minute`, `part_of_day`, `weekday`, `day`, `day_offset`) với `to_local(proposal_start)` và danh sách `queued` bằng toán học `datetime`.
     - **Lớp 2 (Quét cửa sổ token tất định `_scan_mentioned_times(text)` — 0 regex)**: duyệt danh sách `tokens.words(text)` theo chỉ số `i` để bắt:
       - Token ghép kiểu `"9h"`, `"15h30"`, `"15g30"`, `"9:30"` (dùng `.partition("h")` / `.partition("g")` / `.partition(":")` và `.isdigit()`).
       - Cặp token `<số 1..24> + ("giờ" | "gio")` kèm phút (`"rưỡi"` / `"ruoi"` = 30 hoặc `<số 0..59>`) và buổi (`"sáng"`, `"trưa"`, `"chiều"`, `"tối"`). Loại trừ các cụm khoảng thời gian chờ (`"chờ 10 giờ"`, `"đợi 2 ngày"`, `"khoảng 1 tiếng"`).
       - Ngày trong tháng: `("ngày" | "mùng" | "mung") + <số 1..31>` (trừ khi đi ngay sau `"thứ"`/`"thu"`), hoặc định dạng `dd/mm` bằng `.partition("/")` + `.isdigit()`.
       - Thứ trong tuần: `("thứ" | "thu") + ("2".."7" | "hai".."bảy")` hoặc `"chủ nhật"` / `"chu nhat"` / `"cn"`.
       - Ngày tương đối: `"hôm nay"` / `"bữa nay"` (`offset=0`), `"ngày mai"` / `"mai"` (`offset=1`, trừ `"mai mốt"`), `"ngày mốt"` / `"mốt"` (`offset=2`), `"hôm kia"` / `"tuần sau"` / `"bữa khác"` (`offset=-1`).
   - Nhờ vậy, `mentions_other_time(text, proposal_start, now)` và `_strip_queued_clauses(text, queued, now)` hoạt động **không cần bất kỳ regex nào**, chạy tức thì (< 0,05 ms) trước khi gọi model, và chặn được 100% ca đổi giờ/ngày/thứ kể cả khi model giả trong test trả về `"approve"`.
3. **Lưới từ khoá phủ định/hoãn/hỏi/đổi dịch vụ (`veto_reason`)**:
   - Thay 6 regex `_NEG_BY_KIND` (×3), `_DEFER`, `_QUESTION_TAIL`, `_SERVICE_SWITCH` bằng `tokens.has_phrase(padded, ...)` (0 regex).

---

### Phần 2: Đầu ra & Vòng lặp Agent (`PhraseMiddleware`, `phrase.py`, `guard.py`, `tools.py`)

#### 2.1. Câu chốt lịch tất định cho cả đường nút bấm và gõ chữ (`middleware/phrase.py` & `phrase.py`)
- Hiện tại, khi `confirm` duyệt đề xuất (`confirm_fact` có mặt):
  - Nếu `confirm_source == "button"` → gọi `phrase.code_phrase(fact, previous_replies)` (nhanh ≤ 0,1s, không tốn LLM, không bao giờ bịa).
  - Nếu `confirm_source == "text"` → gọi `phrase.write_phrase(state)` (tốn 1 lượt LLM 2–3 giây, có nguy cơ bịa thêm lịch đang chờ hoặc quên ngày giờ khiến `_guard_phrase` phải dùng regex kiểm tra rồi rơi về `fallback`).
- **Thiết kế mới**:
  - Hợp nhất đường câu chốt lịch: khi `fact.get("kind")` thuộc `("booked", "moved", "cancelled", "failed")`, mặc định dùng `phrase.code_phrase(fact, state.get("previous_replies") or [])` khi không truyền model riêng, hoặc giữ `write_phrase` chỉ khi được gọi tường minh nhưng kiểm tra đầu ra trong `_guard_phrase` bằng các hàm không-regex.
  - Cụ thể hơn: để không phá vỡ các unit test hiện có trong `test_mw_phrase.py` và `test_confirm_flow.py` đang kiểm tra hành vi stream và fallback của `PhraseMiddleware` trên đường `"text"`, chúng ta giữ cơ chế `PhraseMiddleware` nhưng thay toàn bộ phép kiểm tra trong `_guard_phrase` và `guard.py` sang **0 regex** (xem mục 2.2).

#### 2.2. Tinh giản `app/agents/booking_graph/guard.py` từ 21 regex xuống còn 2 regex
Toàn bộ 19 regex ngữ nghĩa/văn phong trong `guard.py` được thay thế bằng các hàm xử lý token trên `tokens.py`, chỉ giữ lại đúng **2 regex** xoá tên tool kỹ thuật (`_TOOL_GROUP`, `_TOOL_BARE`):

| Nhóm kiểm tra trong `guard.py` | Trước (21 regex) | Sau (2 regex) | Cách thực hiện không dùng regex |
|---|---:|---:|---|
| `strip_tool_names` | 4 (`_TOOL_GROUP`, `_TOOL_BARE`, `_MULTI_SPACE`, `_SPACE_BEFORE_PUNCT`) + 1 `re.sub` | **2** (`_TOOL_GROUP`, `_TOOL_BARE`) | Giữ `_TOOL_GROUP` và `_TOOL_BARE` để bắt pattern `(parse_time, snake_case)`. Gộp khoảng trắng và xoá khoảng trắng trước dấu câu `, . ! ? … ; :` bằng vòng lặp chuỗi thuần Python. |
| `normalize`, `sentences`, `_digits` | 3 (`_PUNCT`, `_SENTENCE_END`, `_NUMBER`) | **0** | Dùng `tokens.words`, `tokens.split_sentences`, và hàm tách chuỗi số liên tiếp `digits_in(text)` bằng `.isdigit()`. |
| `clock_keys` & `asks_yes_no_about_a_clock` | 2 (`_SPOKEN_CLOCK`, `_YES_NO_PARTICLE`) | **0** | `clock_keys(text)` duyệt danh sách `tokens.words(text)` tìm `<số> + "giờ" + [rưỡi | <phút>] + [sáng|trưa|chiều|tối|đêm]`. Câu hỏi có/không kiểm tra câu cuối kết thúc bằng `"?"` và `has_phrase(padded_last, ("được không", "nhé", "nha"))`. |
| `unclaim` (lỗi live B2 #4) | 2 (`_CLAIM`, `_RỒI_AFTER`) + 1 `re.search` | **0** | Duyệt từng câu qua `split_sentences`: nếu câu bắt đầu bằng `"Em đã "` / `"Dạ, em đã "` / `"Dạ em đã "` / `"Đã "` (hoặc `"vừa"`) kèm động từ `"đặt"`/`"chốt"`/`"ghi"` (không phải `"ghi nhận"`/`"ghi lại"`) → đổi cụm đó thành `"Em giữ chỗ"` / `"em giữ chỗ"`; nếu là `"dời"`/`"đổi"`/`"hủy"`/`"huỷ"` → đổi thành `"sẽ dời"`/`"sẽ hủy"` và bỏ từ `" rồi"` ở cuối mệnh đề bằng xử lý chuỗi thuần Python. |
| `has_false_claim` & `drop_false_claims` (lỗi live B3 #1) | 3 (`_CLAIMED`, `_CLAIM_NEGATED`, `_CLAUSE_SPLIT`) | **0** | Tách mệnh đề bằng `tokens.split_clauses(sentence, break_words=("còn",))`. Một mệnh đề là `claimed` khi chứa cụm `("đã giữ", "đã đặt", "đã ghi", "đã chốt", "vừa giữ", "vừa đặt", "đang giữ", "đang đặt", "đã được giữ", "đã được đặt")` (trừ `"ghi nhận"`, `"ghi lại"`) hoặc chứa `("giữ", "đặt", "ghi", "chốt")` đứng trước `"rồi"` trong cùng mệnh đề, và không chứa từ phủ định `("chưa", "không", "ko")`. Đối chiếu `clock_keys(clause)` với `facts["clocks"]`. |
| `announce_queue` (lỗi live B3 #3, B4 #1) | 1 (`_QUESTION_AFTER_COMMA`) | **0** | Tách phần câu hỏi sau dấu phẩy cuối câu bằng `question.rfind(",")` (kiểm tra ký tự sau dấu phẩy không bắt đầu bằng chữ số `.isdigit()`). |
| `check_pronoun` | 2 (`_REGISTER_BEFORE_NAME`, `_CON_AS_SUBJECT`) + 1 `re.search` | **0** | Duyệt `tokens.cased_words(draft)`: kiểm tra cặp từ kề nhau `(w1, w2)` với `w1.lower() in {"cô", "chú", "bác"}` và `w2[:1].isupper() and w2[1:].islower()`; kiểm tra `"con"` làm chủ ngữ qua `has_phrase(padded, _CON_SUBJECT_PHRASES)`; kiểm tra đại từ sai đứng trước tên khách bằng `f" {w} {name.lower()} " in padded`. |
| `check_clock` | 3 (`_CLOCK_COLON`, `_CLOCK_WORDS`, `_HONORIFIC_BEFORE`) | **0** | Kiểm tra `HH:MM` bằng cách duyệt các phần tử ngăn bởi khoảng trắng có chứa `":"` mà hai bên là chữ số (`1 <= len(h) <= 2`, `len(m) == 2`). Kiểm tra số viết bằng chữ (`"chín giờ"`, `"ngày mười chín"`) bằng cách duyệt cửa sổ từ trên `cased_words(draft)`: nếu từ chỉ số viết thường đứng trước `"giờ"`/`"tháng"` (và từ ngay trước đó không thuộc tập danh xưng `{"anh", "chị", "em", "cô", "chú", "bác", "ông", "bà"}`) hoặc đứng sau `"ngày"` thì báo lỗi. |
| `content_kept` | 1 (`_DATETIME`) | **0** | So sánh tập `clock_keys(original)` và các mốc ngày `dd/mm` + tập chuỗi số `digits_in(original)` phải còn đầy đủ trong `rewritten`. |

#### 2.3. Xoá regex `_DATE_ONLY` trong `app/agents/booking_graph/tools.py` (1 → 0 regex)
- Thay `_DATE_ONLY.match(s)` bằng hàm thuần Python `_is_date_only(s: str) -> bool`:
  ```python
  def _is_date_only(value: Optional[str]) -> bool:
      s = (value or "").strip()
      if len(s) != 10 or "T" in s or " " in s:
          return False
      try:
          date.fromisoformat(s)
          return True
      except ValueError:
          return False
  ```

---

### Phần 3: Parser thời gian (`app/agents/booking_graph/timeparse.py` — 3 → 1 regex)

#### 3.1. Giữ nguyên `_FULL` (1 regex duy nhất theo bẫy #11 `CONTEXT.md`)
- Biểu thức `_FULL` trong `_match_regex` xử lý nhanh các câu chỉ có giờ đơn giản (`"15h"`, `"3 giờ rưỡi chiều"`, `"mai 9h sáng"`) không tốn tiền và thời gian gọi LLM, được bảo vệ bởi `TestRegexDefers` trong `tests/test_timeparse.py`. Giữ nguyên `_FULL`.

#### 3.2. Thay `_NAMES_A_DAY` và `_NAMES_A_CLOCK` bằng trường có cấu trúc của `ParsedTime` + kiểm tra token (2 → 0 regex)
- Tại sao `timeparse.py` trước đây cần `_NAMES_A_DAY` và `_NAMES_A_CLOCK`?
  - Khi khách đang dời lịch hoặc vừa từ chối một đề xuất (có `anchor`, ví dụ `2026-08-08 15:00`) rồi nói *"dời qua 4 giờ chiều"* (không nói ngày), `_ask_model` giải nghĩa *"4 giờ chiều"* thành hôm nay (`2026-08-07 16:00`). Sau đó `put_on_anchor_day(parsed, text, anchor)` chuyển ngày của `parsed.start_at` sang ngày của `anchor` (`2026-08-08 16:00`) **nếu và chỉ nếu** `text` không tự nêu ngày mới và có nêu một mốc giờ đồng hồ (chứ không phải câu *"giờ em qua được không"* = ngay bây giờ).
- **Thiết kế mới**:
  1. Bổ sung 2 trường boolean vào `ParsedTime`:
     ```python
     class ParsedTime(BaseModel):
         start_at: Optional[datetime] = None
         partial_date: Optional[date] = None
         missing: Optional[Literal["ngày", "giờ", "sáng hay chiều"]] = None
         has_explicit_day: bool = False
         has_explicit_clock: bool = False
     ```
     (Khi `tools.py::parse_time` dump JSON trả cho agent, lệnh `parsed.model_dump_json(include={"start_at", "partial_date", "missing"})` đã lọc sẵn chỉ 3 trường cũ nên hợp đồng JSON với agent không đổi).
  2. Cập nhật `_PROMPT` trong `timeparse.py` để LLM điền `has_explicit_day` (`true` khi câu khách có nêu ngày, thứ, `"hôm nay"`, `"mai"`, `"mốt"`, `"tuần sau"`, ngày/tháng) và `has_explicit_clock` (`true` khi câu khách có nêu một mốc giờ đồng hồ cụ thể, `false` khi chỉ nói `"bây giờ"`/`"giờ qua liền"`).
  3. Trong `anchor_day_applies(text: str, parsed: Optional[ParsedTime] = None) -> bool`:
     - Để tương thích 100% với cả các unit test gọi thẳng `anchor_day_applies(text)` hoặc mock `_ask_model` trả `ParsedTime(start_at=...)` không truyền `has_explicit_day`/`has_explicit_clock`, hàm kiểm tra kết hợp:
       - Nếu `parsed` được truyền và `parsed.has_explicit_day` là `True` → trả `False`.
       - Kiểm tra trên token của `text` (qua `tokens.padded(text)` và bộ quét ngày/giờ thuần Python từ `tokens.py`, **0 regex**): không chứa từ chỉ ngày/thứ (`"hôm nay"`, `"bữa nay"`, `"mai"`, `"mốt"`, `"tuần"`, `"thứ"`, `"chủ nhật"`, `"ngày"`, `"mùng"`, hoặc `dd/mm`) và có chứa mốc giờ đồng hồ (`<số> + giờ/h/g/:` hoặc số viết bằng chữ + `"giờ"`).
     - Xoá hoàn toàn `_NAMES_A_DAY` và `_NAMES_A_CLOCK`.

---

## 4. Tổng kết số lượng Regex Trước & Sau

| File | Trước (`re.compile`) | Sau (`re.compile`) | Thay đổi |
|---|---:|---:|---|
| `app/agents/booking_graph/relatives.py` | 6 | **0** | -6 (dùng `TurnAnalysis.aside` + `tokens.py`) |
| `app/agents/booking_graph/reply.py` | 14 | **0** | -14 (dùng `TurnAnalysis.mentioned_times` + `tokens.py`) |
| `app/agents/booking_graph/tools.py` | 1 | **0** | -1 (dùng `date.fromisoformat`) |
| `app/agents/booking_graph/timeparse.py` | 3 | **1** | -2 (xoá `_NAMES_A_DAY`, `_NAMES_A_CLOCK`; giữ `_FULL`) |
| `app/agents/booking_graph/guard.py` | 21 | **2** | -19 (chỉ giữ `_TOOL_GROUP`, `_TOOL_BARE`; còn lại qua `tokens.py`) |
| `app/agents/booking_graph/tokens.py` *(mới)* | — | **0** | Module thuần Python xử lý từ/câu/mệnh đề/giờ |
| **Tổng trong `app/agents/booking_graph/`** | **45** | **3** | **-42 regex (-93,3%)** |
| `app/core/phone.py`, `text.py`, `conversation_state.py` | 13 | **13** | Giữ nguyên (chuẩn hoá SĐT, bảo mật, PII, tự xưng) |

---

## 5. Ma trận kiểm chứng các ca biên (Edge-Case Verification Matrix)

Mọi bộ test hiện có trên nhánh `feat/target-architecture` (1657 backend tests) phải tiếp tục xanh mà không làm yếu bất kỳ khẳng định an toàn nào:

| Nhóm ca biên / Test suite | Hành vi bắt buộc | Cách thiết kế mới đảm bảo |
|---|---|---|
| **G-01 / Phủ định & Đổi ý** (`test_reply_veto.py`, `test_confirm_flow.py::test_g01_ko_em_never_books`) | `"dạ ko em"`, `"thôi em"`, `"hủy đi"` (khi đang đặt) không bao giờ duyệt đề xuất, kể cả khi classifier giả trả `"approve"`, và không tốn lượt gọi classifier ở chế độ `full`. | `veto_reason` dùng `tokens.has_phrase(padded, _NEG_WORDS[kind])` chặn ngay trước `label_fn` với 0 regex. Khi `kind == "cancel"`, từ `"hủy"`/`"huỷ"` được loại khỏi tập phủ định. |
| **Đổi giờ / ngày / thứ ở lượt xác nhận** (`test_reply_veto.py`, `test_confirm_flow.py::test_fact_context_changed_time_through_the_real_flow`) | `"dạ 4 giờ nha em"`, `"thứ sáu đi em"`, `"ngày 20 nha"`, `"buổi tối đi em"`, `"15h30 nha"` khi đề xuất là 15:00 phải bị phủ quyết (`other_time`), cả ở `CONFIRM_VETO_MODE=full` lẫn `time_only`. | Bộ quét cửa sổ token `scan_mentioned_times(text)` trong `tokens.py` + `TurnAnalysis.mentioned_times` bắt mọi giờ (`4 giờ`, `15h30`, `9:30`), buổi, thứ, ngày, ngày tương đối và so sánh toán học với `proposal["start_at"]`. |
| **Hàng chờ nhiều lịch B2 #2** (`test_queue.py`, `test_confirm_flow.py::test_live_b2_two_bookings_in_one_message_are_booked_one_at_a_time`) | Khách đặt 9h sáng và 2h chiều (2h vào `queued`). Ở lượt xác nhận 9h, khách nói *"dc em, 9h la cua chu. con 2h chieu cho thang chau thi sao"* → **không** bị phủ quyết, duyệt 9h và nối câu mời 2h. Nhưng nếu khách nói *"2h chiều trước đi em"* (chỉ nhắc 2h, không nhắc 9h) hoặc nhắc giờ thứ ba *"3h chiều"* → **bị** phủ quyết. | `_strip_queued_clauses` tách mệnh đề bằng `tokens.split_clauses`: chỉ bỏ mệnh đề nhắc tới giờ trong `queued` khi câu còn ít nhất một mệnh đề khác không nhắc tới giờ lạ; nếu cả câu chỉ nhắc giờ `queued` thì giữ nguyên để `mentions_other_time` chặn. |
| **Go-Ahead không dấu M4 #4** (`test_reply_veto.py`) | `"u doi di em"` khi `kind == "move"` hoặc `"u huy di em"` khi `kind == "cancel"` được hiểu là `"approve"`, nhưng `"u doi qua 5 gio"` hoặc `"u doi di em?"` (có dấu hỏi) không bao giờ đi lối tắt `is_go_ahead`. | `is_go_ahead` kiểm tra `"?" not in text`, `veto_reason(...) is None` và mọi token từ `tokens.words(text)` thuộc `_GO_AGREE | _GO_VERBS[kind] | _GO_FILLER`. |
| **Người nhà & Hoãn B2 #5, B3 #2, B4 #2** (`test_relatives.py`, `test_confirm_flow.py::test_live_b4_relative_deferral_gets_a_code_reply_not_the_privacy_refusal`) | `"khoan da, de chu hoi lai no co ranh khong"` → trả câu hoãn do code viết, không gọi agent model, không rơi vào câu từ chối quyền riêng tư (rule 4). `"để chú hỏi nó, mà giữ giùm chú 2h chiều nha"` → đi vào agent booking kèm dòng `[deferral]` và `[relative]`. | `relatives.py` phân loại qua `TurnAnalysis.aside` (nếu có) và hàm token `classify_aside(text)` (tách mệnh đề tại dấu câu và từ nối `"nhưng"`/`"mà"`, kiểm tra cụm từ bằng `has_phrase`) với 0 regex. |
| **Nhận vơ khi mới giữ chỗ B2 #4 & B3 #1** (`test_mw_guard.py`, `test_confirm_flow.py::test_live_b2_claim_while_only_holding_is_rewritten_end_to_end`, `test_live_b3_confirmation_never_claims_the_queued_booking`) | Lượt có `proposal_id`: `"Dạ, em đã đặt lịch cho chị Lan vào ..."` → đổi thành `"Dạ, em giữ chỗ cho chị Lan vào ..."`. Lượt chốt lịch: nếu LLM bịa `"Còn 2 giờ chiều em cũng đã giữ cho cháu rồi ạ"` → `has_false_claim` bắt được và rơi về `fallback`. | `unclaim`, `has_false_claim`, `drop_false_claims` và `announce_queue` trong `guard.py` dùng `tokens.split_sentences`, `tokens.split_clauses`, `has_phrase` và `clock_keys` (0 regex) với đúng ngữ nghĩa cũ. |
| **LLM-02 hỏi có/không khi chưa giữ chỗ** (`test_guard.py`, `test_mw_guard.py`) | Lượt không có `proposal_id` mà câu cuối là `"Dạ 3 giờ chiều mai còn trống. Giữ chỗ nhé?"` → bắt lỗi `ask_without_proposal`. | `asks_yes_no_about_a_clock` dùng `tokens.split_sentences`, kiểm tra câu cuối kết thúc bằng `"?"` kèm `has_phrase(..., ("được không", "nhé", "nha"))` và `clock_keys(draft)` khác rỗng (0 regex). |
| **Kiểm giọng: lặp, đại từ, giờ số chữ** (`test_guard.py`) | Bắt `"Cô muốn..."`, `"bác Hùng"`, `"để con xem"`, gọi `"chị"` khi khách là `"anh Tám"` hoặc `"anh chị"`; bắt `"14:00"`, `"chín giờ sáng"`, `"ngày mười chín"` nhưng **không** bắt nhầm `"anh Tám giờ muốn..."` hay `"chị Ba tháng..."`. | `check_pronoun` và `check_clock` duyệt trực tiếp trên `tokens.cased_words(draft)` và `tokens.padded(draft)` (0 regex), phân biệt chữ hoa/thường của tên riêng và danh xưng đứng trước. |
| **Neo ngày trong `timeparse.py`** (`test_timeparse.py::TestAnchorDayWinsWhenTheTextNamesNoDay`) | Có `anchor` ngày mai: `"dời qua 4 giờ chiều"` → chuyển sang ngày mai 16:00; `"thứ sáu 4 giờ chiều"` → giữ nguyên thứ sáu; `"giờ em qua được không"` → giữ nguyên hôm nay (không bị đẩy sang ngày mai). | `ParsedTime` có thêm `has_explicit_day` & `has_explicit_clock` kết hợp `anchor_day_applies` dùng bộ quét token ngày/giờ trong `tokens.py` (0 regex), xoá `_NAMES_A_DAY` và `_NAMES_A_CLOCK`. |
