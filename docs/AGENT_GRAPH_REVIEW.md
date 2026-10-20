# Review agent LangGraph — 2026-10-04

Bổ sung cho [`ARCH_SECURITY_REVIEW.md`](ARCH_SECURITY_REVIEW.md), lần này soi riêng phần agent:
`app/agents/booking_graph/*`, `app/agents/llm.py`, các service agent gọi (conversation, conversation_state,
appointment, shop) và phần frontend nhận stream. Chỉ đọc code; không gọi LLM thật.

## Cách làm

- 7 reviewer song song, mỗi người một khía cạnh: luồng đồ thị, toàn vẹn đặt lịch, thời gian, prompt + guard,
  streaming + LLM, bộ nhớ hội thoại, độ phủ test.
- Mỗi phát hiện có **một agent riêng cố bác bỏ** (đọc lại code, tìm guard ở chỗ khác, đối chiếu `CONTEXT.md`
  và review cũ). Kết quả: 18 xác nhận, 15 xác nhận một phần (thường bị hạ mức), 1 bác bỏ.
- Không báo lại các mục đã có trong review trước (A-04, A-06, A-07, A-10, A-18, S-07, bug `is_affirmative`
  trong `CONTEXT.md`).
- Mục **[đã chạy]**: tôi đã tái hiện bằng `.venv` khi viết file này (gọi thẳng hàm, không cần LLM).

Mức độ dưới đây là mức **sau** bước kiểm chứng. ID mới: `G-xx`.

---

## Tóm tắt

Điểm yếu lớn nhất của agent **không nằm ở LLM mà ở các lớp tất định quanh nó**: bộ luật đồng ý/từ chối
trong `confirm.py`, regex đường tắt của `timeparse.py`, và việc `pending_confirmation` không gắn với câu
hỏi khách thật sự được hỏi. Cả ba đều có thể **ghi hoặc hủy lịch thật trái ý khách** mà không cần model
sai — và test hiện tại không phủ.

| Mức | Số mục | Nhóm |
|---|---|---|
| Cao | 4 | G-01, G-02, G-03, G-04 |
| Trung bình | 10 | G-05 … G-14 |
| Thấp | 13 | G-15 … G-27 |

**Nên sửa trước khi lên prod:** G-01 → G-07. Phần lớn sửa trong `confirm.py`, `timeparse.py`, `agents.py`,
mỗi cái vài dòng + test tham số hoá; không cần gọi LLM để kiểm.

---

## Cao

### G-01 — Lời từ chối viết tắt / giọng miền Nam bị hiểu là đồng ý → ghi lịch thật [đã chạy]

- **Vị trí:** `app/agents/booking_graph/confirm.py:27-30` (`_NO`), `:44-47` (`_NO_BARE`), `:83`
- **Vấn đề:** `"ko"` chỉ có trong `_NO_BARE`, mà `_NO_BARE` chỉ dùng khi **cả câu** không có dấu. Câu có
  một chữ có dấu ("dạ", "được") đi nhánh `_NO`, nơi không có "ko", "k", "hông", "hổng", "khoan".
- **Tái hiện:** `is_affirmative("dạ ko em")`, `"dạ hông"`, `"dạ khoan"` → `True`. Reviewer chạy cả graph
  thật (LLM giả, DB tạm): pending 3 giờ chiều + "dạ ko em" → lịch `booked`, khách nhận "Xong rồi ạ".
- **Khác bug đã ghi trong CONTEXT:** bug cũ là câu tán gẫu có từ đồng ý. Ở đây khách **nói không** rõ ràng.
  Cách sửa đã định (`_YES` khớp cụm) không chữa được, vì "dạ" đứng đầu vẫn phải là đồng ý.
- **Sửa:** luôn chạy thêm bộ phủ định không dấu trên bản đã bỏ dấu, kể cả khi câu có dấu; bổ sung
  `hông|hổng|hem|khoan|đợi|để sau|k`. Test tham số hoá cho câu trộn dấu.

### G-02 — Đang chờ chốt HỦY: chữ "hủy"/"Huy" ở bất kỳ đâu, kể cả câu hỏi, là đồng ý [đã chạy]

- **Vị trí:** `confirm.py:93`, `_NO_WHEN_CANCELLING` `:32-35`; test `tests/test_confirm.py:370`
- **Vấn đề:** `re.search(r"\b(hủy|huy)\b", target, re.IGNORECASE)` → `True`. Không xét dấu hỏi, từ hỏi,
  không phân biệt tên riêng "Huy". Thiếu "dời", "chuyển", "lùi" trong bộ phủ định.
- **Tái hiện:** `cancelling=True` với `"hủy cái nào vậy em?"`, `"để chị hỏi thằng Huy đã"`,
  `"ừ dời qua thứ Sáu"` → `True` → `service.cancel` ngay.
- **Hậu quả:** khách hỏi lại cho chắc là mất lịch; giờ đó có thể bị người khác lấy. Không có undo qua chat.
- **Lưu ý tài liệu:** `CONTEXT.md` (VIỆC TIẾP THEO, bẫy #19) ghi nhánh `cancelling` là "đang đúng, giữ
  nguyên" → người sửa bug `is_affirmative` sẽ cố ý giữ lỗi này. Cần sửa câu đó.
- **Sửa:** chỉ coi "hủy" là đồng ý trong cụm mệnh lệnh ngắn ("hủy đi", "ừ hủy", "hủy luôn"); câu có `?`
  hoặc từ hỏi (nào/sao/hả/vậy/không) → chưa đồng ý; bỏ `IGNORECASE`; thêm "dời/chuyển/lùi".

### G-03 — "Dạ, 4 giờ nha em" chốt lịch theo giờ CŨ trong pending [đã chạy]

- **Vị trí:** `confirm.py:123`, `:157-161`
- **Vấn đề:** confirm chỉ hỏi `is_affirmative()` rồi ghi `pending["start_at"]`; không xem câu trả lời có
  nhắc giờ/ngày khác không. `_NO` có "đổi" nhưng thiếu "dời", trong khi `_NO_BARE` có "doi" → cùng câu,
  gõ có dấu và không dấu cho kết quả ngược nhau.
- **Tái hiện:** `"dạ 4 giờ nha em"`, `"ừ mà dời qua 4 giờ nha"` → `True`. Bot hỏi "3 giờ chiều được không
  chị?" → lịch ghi 3 giờ, khách tưởng đã đổi sang 4 giờ.
- **Sửa:** trước khi ghi, nếu câu khách có mốc giờ/ngày (dùng lại pattern của `timeparse`) khác pending →
  coi là chưa đồng ý, chuyển sang booking. Thêm "dời/chuyển/lùi" vào `_NO`.

### G-04 — Màn "Giờ mở cửa" và backend đánh số ngày nghỉ khác nhau → tiệm nghỉ sai ngày [đã chạy]

- **Vị trí:** `frontend/src/screens/ShopHoursScreen.tsx:9-11` (0 = Thứ Hai … 6 = Chủ Nhật, comment khẳng
  định "khớp Python `weekday()`"); `app/models/shop.py:8` (0 = Chủ Nhật … 6 = Thứ Bảy),
  `app/services/shop.py:77`, `tools.py:88`. `ShopHoursRequest` (`app/api/v1/schemas.py:96`) không kiểm 0..6.
- **Kịch bản:** chủ tiệm tick "Chủ Nhật" → lưu `[6]` = **Thứ Bảy**. Thứ Bảy (ngày đông nhất) bị chặn,
  Chủ Nhật vẫn nhận lịch — AI giữ chỗ cho ngày tiệm đóng cửa. Màn admin đọc lại `[6]` theo quy ước của
  nó nên vẫn hiện "Chủ Nhật" → chủ tiệm không phát hiện được.
- **Không trùng A-12** (A-12 chỉ nói bảng bị lặp, không nói hai bên đang lệch).
- **Sửa:** giữ quy ước backend; frontend map index tường minh; validator `0 <= d <= 6`; test e2e
  "tick Chủ Nhật → `is_within(Chủ Nhật) == False`".

---

## Trung bình

### G-05 — Regex đường tắt của `parse_time` lấy nhầm ngày, bỏ qua phủ định / đổi ý / tên "Mai" [đã chạy]

- **Vị trí:** `timeparse.py:156` (`_FULL = DAY + ".{0,20}?" + TIME`), `:189` (`.search` trên `text.lower()`)
- **Tái hiện** (now = 4/10 9:00): `"hôm nay không được, mai 3h chiều"` → **hôm nay** 15:00. Reviewer chạy
  thêm: `"mai chị bận, mốt 3 giờ chiều"` → mai; `"mai mốt chị ghé…"` (thành ngữ "bữa nào đó") → mai;
  `"đặt cho bé Mai hôm nay 3 giờ chiều"` → mai; `"9 giờ sáng hay 10 giờ sáng"` → giữ luôn 9:00.
- **Vấn đề:** regex trả kết quả ngay, LLM không được gọi — trái luật "chỉ trả khi khớp TRỌN VẸN" (docstring
  và bẫy #11). Docstring tool còn bảo model truyền "FULL phrase", nên câu nhiều mốc đi thẳng vào regex.
- **Giảm nhẹ:** propose chỉ giữ chỗ, câu xác nhận đọc rõ ngày → còn một lớp người kiểm (nhưng xem G-01/G-03).
- **Sửa (siết, không nới):** chỉ nhận khi cả câu, sau khi bỏ từ đệm, gần như chỉ còn cụm ngày + giờ
  (`fullmatch`); có ≥ 2 từ chỉ ngày, ≥ 2 cụm giờ, hoặc từ phủ định → trả `None` cho LLM. Thêm vào
  `TestRegexDefers`.

### G-06 — Regex cắt mất phút: "mai 15h30" → 15:00 [đã chạy]

- **Vị trí:** `timeparse.py:154` (`_TIME_PATTERN` không có nhóm phút), `:200` (luôn `minute=0`)
- **Tái hiện:** `"mai 15h30"` → 15:00. Reviewer: `"mai 14g45"` → 14:00, `"mai 15 giờ 30 phút"` → 15:00.
- **Sửa:** lookahead âm `(?!\s*(\d|rưỡi|kém|phút))` sau giờ/buổi → nhường LLM. Không tự parse phút trong
  regex (giữ luật "regex không suy luận").

### G-07 — Hết 4 vòng tool sau khi đã giữ chỗ: khách nghe "gọi tiệm", câu "dạ vâng" kế tiếp ghi lịch

- **Vị trí:** `agents.py:13, 67-93`; `tools.py:265, 304`
- **Vấn đề:** vòng thứ 4 vẫn chạy tool (kể cả `propose`/`cancel` → `set_pending`), rồi node trả
  `FALLBACK_ANSWER` ("Dạ em chưa tra được, anh chị gọi trực tiếp cho tiệm…") mà không xoá pending. Model
  trả content rỗng (`agents.py:76`) cũng vậy. Lượt chat kết thúc "bình thường" → cách sửa A-07 (nhánh
  exception/answer rỗng) **không phủ** đường này.
- **Kịch bản:** parse_time → find_free_slots → propose 16h `slot_unavailable` → propose 16h30 giữ được (vòng
  4) → khách thấy "gọi trực tiếp cho tiệm" → đáp "Dạ vâng, để chị gọi" → START → confirm →
  `is_affirmative` True → lịch 16h30 được ghi, khách chưa từng được hỏi.
- **Sửa:** vòng cuối không chạy tool; hoặc trả FALLBACK thì `set_pending(None)` nếu lượt có gọi
  propose/cancel. Test: model giả gọi propose cả 4 vòng → pending phải là `None`.

### G-08 — Nhiều propose/cancel trong một lượt ghi đè nhau, chỉ cái cuối được thực hiện

- **Vị trí:** `agents.py:34` (`bind_tools` không tắt `parallel_tool_calls`), `:85`; `conversation.py:172`
  (`set_pending` một ô)
- **Kịch bản hợp lệ** (đặt hộ người khác đã bị prompt rule 4 chặn): "đặt cho chị thứ Năm với thứ Sáu", hoặc
  "hủy thứ Năm, đặt thứ Sáu" mà model gọi cancel rồi propose không kèm `replaces` → ý hủy bị đè, khách có 2
  lịch. Câu xác nhận của model liệt kê cả hai, confirm chỉ làm một.
- **Sửa:** trong vòng tool, propose/cancel thứ hai trong cùng lượt trả `{"error": "one_at_a_time"}`; thêm
  luật "mỗi lượt một lịch" vào prompt. Chỉ `parallel_tool_calls=False` là chưa đủ (model vẫn gọi ở 2 vòng).

### G-09 — Pending không gắn với câu hỏi cuối của bot

- **Vị trí:** `tools.py:262-265`, `confirm.py:112-161`
- **Kịch bản:** (a) "mai 9 giờ hay 10 giờ còn chỗ không" → model propose 9h rồi 10h → pending = 10h → bot
  hỏi "chị chọn giờ nào?" → "dạ 9 giờ" → ghi **10 giờ**. (b) model propose với `note=None` rồi hỏi dịch vụ →
  "dạ sơn gel" → lịch ghi không có dịch vụ, khách chưa từng được hỏi "đúng giờ đó không".
- **Sửa:** G-08 (một lần giữ mỗi lượt) + G-03 (câu có giờ khác pending → không chốt); pending `note=None`
  mà câu khách có nội dung ngoài từ đồng ý → chuyển sang booking để propose lại.

### G-10 — Dời lịch an toàn hay không phụ thuộc vào việc model nhớ `replaces_appointment_id`

- **Vị trí:** `tools.py:221-265`, `confirm.py:155-182`, `prompts.py:122-126`, `PHRASE_KIND_SENTENCES["booked"]`
- **Vấn đề:** thiếu id thì đó là lịch mới; chỉ chặn khi trùng đúng mốc. Câu chốt "Xong rồi ạ, hẹn gặp…"
  không nhắc lịch cũ vẫn còn. Lớp chặn duy nhất là prompt — đúng gốc của BUG-1.
- **Sửa:** propose không có `replaces` mà khách đã có lịch khác **cùng ngày** → trả thêm `other_same_day` để
  model hỏi "giữ cả hai hay dời?"; hoặc câu chốt nói rõ "lịch 9 giờ vẫn giữ nguyên". Test không cần LLM.

### G-11 — Confirm xoá pending trước khi quyết; trạng thái pending không có trong khối bối cảnh

- **Vị trí:** `confirm.py:115`, `context.py:196-236`
- **Vấn đề:** câu bị coi là chưa đồng ý (kể cả âm tính giả như "ừ không đổi gì đâu", "đặt luôn khỏi hỏi
  lại") → pending mất hẳn. Pending hết hạn 10 phút cũng vậy. Model không được báo "không có chỗ nào đang
  giữ", trong khi lịch sử còn câu hỏi xác nhận.
- **Hậu quả đã kiểm:** prompt + danh sách "Lịch sắp tới" làm model nhiều khả năng propose lại và hỏi chốt
  lần nữa (khách bị hỏi lặp) hơn là nói "đã đặt" sai. Chưa có bằng chứng mất lịch.
- **Sửa:** in một dòng trạng thái pending vào khối bối cảnh mỗi lượt; kiểm "không/đổi" theo cụm.

### G-12 — Guard bỏ lọt "cô/chú/bác/con" sau "Dạ/Vâng", và đoán giới tính giữa câu

- **Vị trí:** `guard.py:44, 47, 54, 155-172`
- **Tái hiện (reviewer):** `check_pronoun` trả `False` cho "Dạ bác muốn làm lúc mấy giờ ạ?", "Dạ cô muốn đặt
  mấy giờ ạ?", "Dạ con chào cô ạ…", "Dạ vâng ạ, chị muốn…" (khách "anh Tám"), "…giữ chỗ cho chị…" (khách
  "anh chị"). Nguyên nhân: so `{"Cô","Chú","Bác"}` phân biệt hoa thường; `_LEADING_PARTICLES` thiếu
  "ạ/thưa"; đại từ sai giới chỉ soi đầu câu.
- **Vì sao trung bình:** guard là hàng rào duy nhất cho câu trả lời thật (CONTEXT: "không dùng con/cô/chú/bác
  ở bất kỳ đâu"); khách tự xưng "bác" thì model rất dễ nói theo.
- **Sửa:** so sau `.lower()` token đầu (sau tiểu từ); thêm "ạ/thưa/ok" và "chào"; soi `cho/giúp/của/với +
  chị|anh` khi có `wrong`. Test cho từng câu.

### G-13 — Prompt hứa "chủ tiệm sẽ trả lời" câu hỏi giá, nhưng không có đường chuyển tới chủ tiệm

- **Vị trí:** `prompts.py:176-178` (BOOKING rule 5), `:225-226` (SOCIAL rule 4); test khoá câu hứa
  `tests/test_guard.py:32`, `tests/test_rewrite.py:63`
- **Vấn đề:** `NotificationService` chỉ có created/cancelled/status; khối bối cảnh không có SĐT tiệm. Khách
  lớn tuổi ngồi chờ tin báo giá không bao giờ đến.
- **Sửa:** đổi lời thành không hứa ("giá anh chị hỏi chủ tiệm khi tới hoặc gọi số tiệm") + đưa SĐT tiệm vào
  bối cảnh; hoặc thêm thông báo Telegram "khách X hỏi: …". Sửa test đi kèm.

### G-14 — Gửi tin khi lượt trước chưa xong: `complete` của lượt cũ đè bong bóng "Xong rồi" của lượt mới

- **Vị trí:** `frontend/src/screens/ChatScreen.tsx:173` (nút Gửi không khoá theo `phase`),
  `frontend/src/hooks/useAgentStream.ts:60-66, 139-153, 215`
- **Kịch bản:** câu hỏi xác nhận đã stream xong, guard cờ `repeat`, rewrite chạy tới 8s → khách gõ "ừ" →
  lượt 2 ghi lịch, stream "Xong rồi…" → `complete` của lượt 1 tới, thay bong bóng bằng câu hỏi xác nhận;
  `complete` lượt 2 bị bỏ. DB có lịch, màn hình hỏi "xác nhận giúp em nhé?".
- **Quan hệ:** gốc là REL-01 (đã có); đây là đường kích hoạt **không cần mất mạng** (khác A-06). Tần suất
  `repeat` đo được 1/26 lượt (CONTEXT).
- **Sửa:** khoá Gửi khi `phase !== 'idle'` hoặc xếp hàng; `turn_id` trong mọi event (như A-05); khoá theo
  user ở server (REL-01).

---

## Thấp

| ID | Vấn đề | Vị trí | Sửa |
|---|---|---|---|
| G-15 | Câu chốt (`phrase`) stream ra trước guard: khách có thể thấy ngày/giờ sai rồi chữ tự đổi. `rewrite` đã cố ý không stream, `phrase` thì chưa | `phrase.py:39`, `events.py:45` | `streaming=False` + tag riêng cho `phrase`, hoặc làm mờ bong bóng tới khi có `complete` |
| G-16 | Chữ model nói kèm `tool_call` cũng được stream; khi vòng sau lỗi, chữ chưa qua guard ("em giữ 3 giờ cho chị nha") nằm lại cạnh "Máy đang bận" | `events.py:45`, `useAgentStream.ts:164-177` | `onError` xoá/đánh dấu bong bóng đang stream; hoặc buffer token theo vòng, bỏ vòng có tool_call |
| G-17 | Câu chốt khi **hủy** thất bại bị nói thành "không đặt được, chọn giờ khác" (dùng chung kind `failed`) | `confirm.py:143-144`, `prompts.py:261-262, 272` | Kind riêng `cancel_failed` |
| G-18 | Prompt dạy `slot_unavailable` = "đã có người đặt", nhưng mã này gộp cả ngoài giờ mở cửa / ngày nghỉ / giờ đã qua → bot nói "8 giờ tối có người đặt rồi" khi tiệm đã đóng | `prompts.py:149`, `tools.py:252-258` | Sửa câu prompt: "không đặt được giờ đó (đã có người, ngoài giờ, hoặc đã qua)" |
| G-19 | `check_clock` cờ nhầm khoảng thời gian ("mất khoảng một giờ", "giữ được hai tháng"); gợi ý rewrite có thể biến nó thành mốc "1 giờ chiều". `_DATETIME` thiếu "trưa/đêm" nên rewrite đổi trưa → đêm vẫn qua | `guard.py:62, 68, 186-191`, `prompts.py:245` | Chỉ cờ khi có buổi đi sau hoặc "lúc" đứng trước; bỏ "tháng"; thêm trưa\|đêm |
| G-20 | Miễn trừ "khách xin nhắc lại" chỉ khớp có dấu → "em nhac lai gio giup chi" bị cờ `repeat`, tốn một lượt rewrite (tới 8s) | `guard.py:34, 113-115` | So trên bản đã bỏ dấu (`strip_diacritics` của confirm) |
| G-21 | Prompt timeparse không có bảng ngày tới / quy ước tuần → "thứ Hai tuần sau" nói vào Chủ Nhật có thể lệch 1 tuần; "thứ Năm" khi đã qua vẫn hỏi "tuần này hay tuần sau" | `timeparse.py:207, 218` | Chèn bảng 14 ngày dựng bằng code + quy ước tuần; thêm ca Chủ Nhật vào test `-m llm` |
| G-22 | Regex hiểu "mai" theo ngày lịch: 0h40 "mai 9 giờ sáng" → ngày hôm sau, không hỏi lại | `timeparse.py:198` | Khung 0h–4h: nhường LLM + hỏi lại ngày |
| G-23 | Slots trễ ≥ 4 lượt nhưng in dưới tiêu đề "Trạng thái cuộc trò chuyện", không xoá sau khi chốt, không lọc giờ đã qua lúc đọc → model có thể nhắc giờ khách đã bỏ | `conversation_state.py:74, 133`, `context.py:79, 91-125` | Xoá slots khi confirm chốt; lọc `time`/`declined` theo `now` trong `render_slots`; đổi tiêu đề |
| G-24 | Khe giữa ngưỡng nén (800) và ngân sách lịch sử (1500): đoạn cũ chưa nén có thể tạm rơi khỏi prompt ("lấy cái giờ sau đi em" mà model không thấy 2 giờ đã chào) | `conversation_state.py:134`, `conversation.py:117-124` | Ngân sách history ≥ ngưỡng nén + cửa sổ recent, hoặc nén ngay khi history cắt mất tin sau `covers_until` |
| G-25 | Cầu chì nén nổ thì summary/slots cũ đóng băng tới hết ngày; `CONTEXT.md` bẫy #20 nói "về đúng hành vi cũ". Giữ state cũ là cố ý theo spec — **chỉ sửa tài liệu** + lọc slots lúc đọc (G-23) | `conversation.py:227-248` | Sửa câu trong CONTEXT |
| G-26 | Lượt nén state không có callback Langfuse → phép đo token "trace tag `state`" trong CONTEXT không bao giờ thấy chi phí này | `conversation_state.py:163-166` | Truyền `config={"callbacks": get_callbacks(), "tags": ["state"], ...}` |
| G-27 | Test: không test nào chạy **graph đã biên dịch** (mọi test `run_turn` dùng FakeGraph); fence slots chỉ grep chuỗi; test supervisor cho model giả trả đúng nhãn rồi khẳng định lại; nhãn "shop." / "\*\*shop\*\*" rơi về booking | `tests/test_graph_events.py:143`, `test_graph_wiring.py:47, 71`, `test_supervisor.py:38`, `supervisor.py:32-36` | Test tích hợp LLM giả theo tag trên `test_db`, tối thiểu: ừ→booked, "dạ ko"→không ghi, pending + câu lạc đề→booking, guard→rewrite→end. Bỏ dấu câu/markdown trước khi so nhãn |

**Bác bỏ (1):** "Rule 4 cấm đặt hộ người nhà mâu thuẫn với guard" — test được dẫn chỉ kiểm xưng hô, không nói
agent được phép đặt hộ.

---

## Điểm mạnh

- Lời "ừ" đi tắt START → confirm bằng code, supervisor không thể hiểu nhầm; confirm ghi từ pending trong
  Mongo, không từ chuỗi model.
- Supervisor có whitelist route + fallback về booking, và booking có cả tool của shop → route lạ không làm
  vỡ đồ thị.
- Đồ thị không có chu trình mở: đường dài nhất 6 bước, `route_after_guard` dừng chắc chắn sau một lần rewrite.
- State dựng mới mỗi lượt; `user` đóng trong closure của tool.
- Vòng tool có trần cứng, bắt lỗi từng tool, xử lý tên tool lạ.
- `complete` mang `answer` đã qua guard và thay toàn bộ bong bóng → guard/rewrite là nguồn sự thật cuối.
- `rewrite` cố ý không stream; `timeparse` dùng tag riêng + `streaming=False` đúng ràng buộc.

## Đề xuất thứ tự sửa

1. **`confirm.py`** — G-01, G-02, G-03 cùng một chỗ: bộ phủ định chạy trên bản bỏ dấu, nhánh hủy chỉ nhận
   cụm mệnh lệnh, câu có mốc giờ khác pending → không chốt. Sửa luôn bug `is_affirmative` đã ghi trong
   CONTEXT (khớp cụm) và câu "giữ nguyên" sai trong CONTEXT.
2. **G-04** — một dòng map index ở frontend + validator.
3. **`agents.py` / `tools.py`** — G-07, G-08 (+ A-07): một lần giữ mỗi lượt, không chạy tool ở vòng cuối,
   xoá pending khi trả fallback.
4. **`timeparse.py`** — G-05, G-06: siết regex, nhường LLM.
5. **G-27** — harness tích hợp LLM giả để khoá 1–4 lại; hiện suite xanh với cả 4 lỗi trên.
6. Phần còn lại theo bảng.
