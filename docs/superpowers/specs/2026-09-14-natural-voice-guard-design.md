# Lời thoại tự nhiên hơn: tầng gác, viết lại, neo thời gian, câu chốt bằng LLM — thiết kế

Ngày: 2026-09-14. Trạng thái: đã duyệt thiết kế, chờ plan.

## Bốn lỗi nhắm tới (đo từ trò chuyện thật 2026-09-14)

1. **Câu hỏi kép rớt nửa** — "mấy giờ đóng cửa, chiều nay còn giờ nào": mỗi lượt
   chỉ vào một node; `shop` không có tool lịch, `booking` không có tool giờ mở cửa.
2. **Lặp câu / lặp ý** — hai lần từ chối y nhau; khách "ừ" giữa lúc bot hỏi thiếu
   mảnh thì bot hỏi lại nguyên câu. Prompt (kể cả viết hoa), temperature 0.6 và
   trích câu cũ vào khối bối cảnh đều không lay được.
3. **Không nhớ mạch nói** — "chuyển qua 10 giờ" khi lịch cũ 9 giờ sáng → hỏi
   "sáng hay chiều"; `parse_time` chỉ đọc chuỗi được đưa.
4. **Giọng biểu mẫu / xưng hô trượt** — "Chị" khi phải "anh Tám"; câu chốt cứng
   của `confirm` không theo mạch.

Ngân sách đã chốt: **tối đa 3 lượt LLM mỗi lượt chat, +3–5 giây** so với hiện
tại (2 lượt, 4–9 giây). Streaming giữ nguyên.

## Nguyên tắc không đổi

- Ghi lịch **100 % code** ở `confirm`; LLM chỉ viết lời. Không thêm tool ghi.
- Khối bối cảnh dựng từ DB mỗi lượt và thắng mọi thứ khác.
- Chỉ token mang tag `respond` được stream. Thứ tự tin nhắn cho LLM giữ nguyên
  (System → [digest] → lịch sử → khối bối cảnh → câu khách).
- Prompt tiếng Anh, không câu thoại mẫu tiếng Việt. Validator ép, không từ chối.
- Phát hiện lỗi bằng **code tất định**; LLM chỉ dùng để sửa, tối đa một lần.

## Phương án đã chọn (B) và hai phương án bỏ

- **A — bỏ supervisor, một lễ tân 7 tool**: ít LLM hơn nhưng đi ngược phép đo
  routing-redesign (lệch 10/14 → 0/14 nhờ tách prompt) và bẫy #17.
- **C — loop supervisor↔worker + node tổng hợp**: 3–5 LLM/lượt, mất streaming.
- **B — giữ supervisor, mở rộng `booking`, thêm `guard` + `rewrite`, `parse_time`
  có neo, `confirm` → `phrase`.** Chọn B.

## Mục 1 — Hình dạng graph

```
START ─▶ route_from_state ──có pending──▶ [confirm] (code ghi) ─▶ [phrase] (LLM) ─┐
              │                              └─chưa đồng ý──▶ [booking] ─┐          │
              └──không──▶ [supervisor] ─▶ [booking | shop | social] ─────┤          │
                                                                         ▼          │
                                                                      [guard] ◀─────┘
                                                                         │ vi phạm?
                                                                    có ──▶ [rewrite] (LLM, 1 lần) ─▶ [guard] lần 2 ─▶ END
                                                                    không ─────────────────────────────────────────▶ END
```

- Mọi câu cho khách đi qua `guard` **đúng một lần** trước END; sau `rewrite`
  qua `guard` lần 2 chỉ để kiểm — hỏng thì trả **draft gốc**, log `guard_gave_up`.
- `booking` có 7 tool: 5 cũ + `get_shop_hours`, `get_shop_status` (tái dùng từ
  `make_shop_tools`). `shop` giữ cho câu thuần về tiệm.
- Tag: `booking/shop/social/phrase` = `respond`; `rewrite` = `rewrite`
  (không stream). Khách thấy draft chạy dần, `complete` thay bằng câu cuối —
  `useAgentStream.onComplete` đã thay text theo `streamId`; ghi rõ để không ai
  đổi thành append.
- `GraphState` thêm: `draft: str`, `violations: List[str]`, `rewritten: bool`,
  `confirm_fact: Optional[dict]`, `fallback: Optional[str]`. `answer` chỉ được
  set ở `guard`.

## Mục 2 — `guard` và `rewrite`

### 2.1 Năm phép kiểm tất định (`app/agents/booking_graph/guard.py`)

| Mã | Kiểm | Nguồn sự thật |
|---|---|---|
| `repeat` | xem 2.2 | 3 câu đáp LLM gần nhất |
| `address` | khối bối cảnh nói "anh Tám" mà draft có đại từ khác giới **đứng đầu câu hoặc ngay trước tên khách**; ngược lại tương tự. "Chị chủ tiệm" không tính | `address_phrase(user.full_name)` |
| `register` | "cô", "chú", "bác", "con" làm đại từ (regex biên từ, đầu câu hoặc trước tên) | luật VOICE |
| `clock` | mẫu `\d{1,2}:\d{2}`, hoặc số giờ/ngày viết bằng chữ (bảng: một…mười hai, mười ba…ba mươi mốt + "giờ"/"tháng"/"ngày") | `format_vi_datetime` |
| `language` | câu > 6 từ mà không có ký tự tiếng Việt có dấu, hoặc > 30 % từ nằm trong bảng từ tiếng Anh thường gặp | `_VIETNAMESE_ONLY` |

Đầu ra: `List[str]` mã vi phạm. Không vi phạm → `answer = draft`.

### 2.2 `repeat` — độ giống chuỗi + theo câu

```python
def repeats(draft, previous_replies, ratio=0.85, min_words=6) -> bool:
    d = normalize(draft)                      # bỏ dấu câu, gộp khoảng trắng, thường hoá
    for prev in previous_replies[-3:]:
        p = normalize(prev)
        if SequenceMatcher(None, d, p).ratio() >= ratio:          # cả câu trả lời
            return True
        for sent in sentences(d):                                  # từng câu
            if len(sent.split()) >= min_words and any(
                SequenceMatcher(None, sent, s).ratio() >= ratio for s in sentences(p)
            ):
                return True
    return False
```

- `previous_replies` = câu đáp có `source == "llm"` — `ChatMessage` thêm trường
  `source: Literal["llm", "code"] = "llm"`; `confirm` fallback và câu lỗi append
  với `"code"`. Không migrate: document cũ mặc định `"llm"`.
- Bỏ phép `repeat` khi câu khách chứa một trong: "nhắc lại", "nói lại", "lặp
  lại", "quên rồi", "quên mất" (bảng tất định trong `guard.py`).
- Ngưỡng 0.85 / 6 từ là **điểm khởi đầu**: bước đầu của plan chạy `repeats()`
  trên toàn bộ transcript hiện có (`baseline-*.txt`, `after-*.txt`,
  `after-digest*.txt`), in các cặp bị cờ để chủ dự án duyệt rồi mới khoá số.

### 2.3 `rewrite`

- `build_chat_model(tags=["rewrite"], temperature=0.3, streaming=False)`,
  timeout 8 s. Prompt tiếng Anh: draft, danh sách mã vi phạm kèm mô tả một dòng
  mỗi mã, câu đáp trước (khi có `repeat`), cách gọi đúng. Yêu cầu: **một** câu
  trả lời tiếng Việt cùng nội dung, khác cách nói; không thêm dữ kiện.
- Kiểm cứng sau rewrite (trong `guard` lần 2, ngoài 5 phép): mọi chuỗi ngày-giờ
  dạng `Thứ … d/m` / `… giờ …` và mọi con số trong draft phải còn trong bản
  rewrite — thiếu là vi phạm `content`.
- Lỗi/timeout → giữ draft, log `rewrite_failed`. Chỉ một lần rewrite mỗi lượt.
- Log mỗi lượt: `guard_violation{codes}`, `guard_rewritten`, `guard_gave_up{codes}`.
  Đây là số đo mới, đọc từ log, không cần LLM chấm.

## Mục 3 — `parse_time` có neo

- Chữ ký: `parse_time(text: str, anchor: Optional[str] = None)`. `anchor` là ISO
  của mốc đang bàn. Docstring: *"Pass `anchor` whenever the customer is changing
  or answering about a time already on the table: the appointment being moved
  (its `iso:` in the context block) or the slot just proposed."*
- Nguồn neo: khối bối cảnh in `[id: …, iso: 2026-09-15T09:00:00+07:00]`;
  `propose_appointment` trả thêm `(iso: …)` trong chuỗi kết quả.
- Xử lý trong code (`timeparse.py`, sau regex/LLM, trước `_guard`):

| Thiếu | Có neo | Kết quả |
|---|---|---|
| ngày | có | ngày của neo |
| sáng hay chiều | có | buổi của neo nếu giờ ra nằm trong giờ mở cửa; không thì buổi còn lại nếu hợp lệ; cả hai hợp lệ hoặc cả hai hỏng → vẫn hỏi |
| cả hai | có | ngày neo + quy tắc buổi |
| bất kỳ | không | như hiện tại |

- Neo không parse được → bỏ, log `anchor_ignored`, chạy như không neo. Không ném.
- Không đụng regex (bẫy #11), không đổi enum `MissingPiece`.

## Mục 4 — `confirm` → `phrase`

- `confirm` giữ nguyên logic ghi. Đầu ra đổi: `confirm_fact = {kind:
  "booked"|"moved"|"cancelled"|"failed", when: format_vi_datetime(...), note,
  address, error}` và `fallback` = câu cứng hiện tại. Không set `answer`.
- `phrase` (LLM, tag `respond`, temp 0.3, không tool): prompt tiếng Anh: *"The
  salon has just {kind} the appointment at {when}. Tell the customer in one or
  two natural Vietnamese sentences that fit the conversation. You MUST include
  the exact string {when} and address them as {address}."* Có lịch sử để bắt mạch.
- Kiểm riêng cho `phrase` trong `guard`: draft phải chứa `when` nguyên văn và
  `address`; `failed` phải chứa `error`. Vi phạm → **`fallback` ngay, không
  rewrite**. Lỗi/timeout → `fallback`.
- Lượt chốt: 0 → 1 LLM (+3–4 s).

## Mục 5 — Supervisor và tool

- `SUPERVISOR_PROMPT` dòng `booking`: thêm *"or a message that mixes a shop
  question with anything about appointments"*.
- `make_booking_tools` trả 7 tool (thêm hai tool shop, tái dùng). `BOOKING_PROMPT`
  thêm một dòng: *"For shop hours or whether the owner is busy, use the shop
  tools — never guess."*
- Bẫy #7 sửa chữ: "7 tool, 0 tool ghi"; hàng rào là
  `test_there_is_NO_tool_that_writes_an_appointment`.
- `scripts/probe_supervisor.py` thêm 4 mẫu câu kép; kỳ vọng lệch 0/18.

## Mục 6 — Lỗi và biên

| Tình huống | Xử lý |
|---|---|
| `rewrite` lỗi/timeout | giữ draft, log `rewrite_failed` |
| `rewrite` vẫn vi phạm ở guard 2 | dùng draft **gốc**, log `guard_gave_up{codes}` |
| `rewrite` đổi nội dung | kiểm `content` (ngày-giờ, số) → coi là vi phạm → draft gốc |
| `phrase` thiếu `when`/`address`, lỗi, timeout | `fallback` ngay |
| khách xin nhắc lại | bỏ `repeat` lượt đó |
| câu đáp trước do code | không vào `previous_replies` (`source="code"`) |
| câu kép mà supervisor vẫn chọn `shop` | rớt nửa như hôm nay — không tệ hơn; đo bằng probe |
| `anchor` hỏng | bỏ neo, `anchor_ignored` |
| neo đẩy ra ngoài giờ mở cửa | thử buổi còn lại; hỏng cả hai → hỏi như cũ |
| stream đã hiện draft, `complete` đổi câu | hành vi frontend sẵn có, không đổi |
| chi phí | thường 2 LLM; rewrite kỳ vọng < 20 % lượt (đo `guard_violation`) |

## Mục 7 — Test và đo

**pytest (không LLM):**
- `guard`: mỗi phép kiểm ≥ 3 ca dương, 2 ca âm; "Chị chủ tiệm" khi khách nam
  KHÔNG bị bắt; ngoại lệ xin nhắc lại; bỏ qua `source="code"`.
- `repeats()`: test snapshot chạy trên toàn bộ transcript hiện có, in cặp bị cờ;
  ngưỡng khoá sau khi duyệt.
- Graph: mọi nhánh qua `guard` đúng một lần; `rewrite` tối đa một lần; `phrase`
  sau `confirm`; `rewrite` không mang tag `respond`; không vi phạm → `answer ==
  draft` nguyên văn.
- `parse_time` neo: bảng 8 ca (thiếu ngày/buổi × neo sáng/chiều × trong/ngoài
  giờ mở cửa) + neo hỏng.
- `phrase` guard: thiếu `when` → fallback; đủ → dùng câu LLM.
- Supervisor: probe 14 + 4 câu kép (script, LLM thật, ngoài pytest).

**Chạy thật (bẫy #18):** kịch bản `dai` (mốc rubric) + một cuộc **không kịch
bản** đọc tay. Số đo mới từ log: tỷ lệ `guard_violation`, tỷ lệ rewrite qua
guard 2, số `guard_gave_up`. Bốn ca phải đúng trong một lượt: "ừ hủy đi",
"tiệm còn làm không", câu kép, "chuyển qua 10 giờ".

## Ngoài phạm vi

Loop supervisor↔worker; embedding/LLM chấm lặp ý; đổi temperature; digest
(đã xong, độc lập); chống nén song song (REL-01).
