# Harness cho mỗi lượt chat của agent — thiết kế

Ngày: 2026-10-04. Nguồn: [`docs/AGENT_GRAPH_REVIEW.md`](../../AGENT_GRAPH_REVIEW.md),
[`docs/ARCH_SECURITY_REVIEW.md`](../../ARCH_SECURITY_REVIEW.md).

## Vì sao

Review agent ngày 2026-10-04 cho thấy lỗi nặng nhất **không nằm ở LLM mà ở lớp
code tất định quanh nó**. Ba nhóm lỗi đều ghi, hủy hoặc dời **lịch thật trái ý
khách** mà không cần model sai, và suite hiện tại vẫn xanh:

| Lỗi | Tái hiện |
|---|---|
| G-01 — từ chối viết tắt bị hiểu là đồng ý | `is_affirmative("dạ ko em")` → `True` → lịch được ghi |
| G-02 — đang chốt hủy, câu hỏi có chữ "hủy"/"Huy" là đồng ý | `"hủy cái nào vậy em?"` → `True` → lịch bị hủy |
| G-03 — đồng ý kèm giờ mới, ghi giờ cũ | `"dạ 4 giờ nha em"` → `True` → ghi 3 giờ |
| Bug `is_affirmative` (CONTEXT) — câu tán gẫu có "phải/được" | ghi lịch giữa cuộc |
| G-07 — hết vòng tool sau khi giữ chỗ | khách nghe "gọi tiệm giúp em", đáp "dạ vâng" → ghi lịch |
| G-08/G-09 — nhiều lần giữ chỗ trong một lượt | chỉ cái cuối được ghi; "dạ 9 giờ" ghi 10 giờ |
| G-10 — dời lịch mà model quên id lịch cũ | khách có 2 lịch, tưởng đã dời (gốc của BUG-1) |
| G-11 — model nói "đặt xong" khi chưa ghi gì | lớp chặn duy nhất là prompt |

Nguyên nhân chung: luật quanh `pending_confirmation` nằm rải ở `tools.py`,
`agents.py`, `confirm.py`, `events.py`, và không chỗ nào biết **lượt này** đã giữ
gì, ghi gì, khách đã thật sự được hỏi gì.

## Mục tiêu

- Agent không ghi / hủy / dời lịch trái ý khách, kể cả khi khách lớn tuổi gõ tắt,
  không dấu, trả lời lửng.
- Mọi luật quanh pending nằm **một chỗ**, test được bằng hàm thuần, không cần LLM.
- Mọi câu tái hiện ở bảng trên thành test và xanh.

## Giữ nguyên (không được đổi)

- Agent **không có tool ghi**; lịch ghi từ giá trị trong Mongo, không từ chuỗi model.
- START → `confirm` đi tắt khi có pending; câu chốt **không bao giờ qua LLM**.
- Pending hết hạn sau 10 phút.
- Chỉ stream token tag `respond`; thứ tự khối prompt theo bẫy #9.
- Câu gốc của khách đi **nguyên văn** tới agent; bước chốt và regex đọc giờ chỉ dùng câu gốc.
- REST và Telegram gọi `AppointmentService` trực tiếp như cũ; harness chỉ dành cho agent.

## Thiết kế

### 1. Hai khối mới trong `app/services/`

**`consent.py`** — hàm thuần `classify_reply(text, cancelling) -> Verdict`
(`"yes" | "no" | "unclear"` kèm `reason` cho log). Không DB, không LLM. Thay hẳn
`is_affirmative` và 6 regex trong `confirm.py`. Tách khỏi harness vì đây là logic
hiểu tiếng Việt, sẽ còn chỉnh theo log; harness thì ổn định.

**`turn_harness.py`** — `TurnHarness`, một đối tượng **mỗi lượt**. Là chỗ **duy
nhất** phía agent được ghi pending và gọi `create`/`cancel`/`reschedule`.

Trạng thái lượt: `pending` (đọc lúc đầu lượt, kèm `asked_at`), `held` (lượt này
đã giữ gì), `committed` (lượt này đã ghi gì), `intent` (từ supervisor).

| Phương thức | Ai gọi | Làm gì | Luật |
|---|---|---|---|
| `hold(payload)` | tool `propose_appointment`, `cancel_appointment` | ghi pending; lần thứ 2 trong lượt → `one_at_a_time`; `intent == "reschedule"` mà payload đặt mới không có `replaces_appointment_id` → `reschedule_needs_id` | 1, 3b |
| `classify(text)` | node `confirm` | gọi `consent.classify_reply` với chế độ hủy suy từ pending | 3 |
| `commit()` | `confirm` khi `yes` | giành pending nguyên tử, ghi/hủy/dời **từ pending trong DB**, trả `confirm_fact` | 3 |
| `release()` | `confirm` khi `no` | xoá pending (so `asked_at`) | 3 |
| `check_claim(answer)` | node `guard` | câu nhận vơ đã làm mà `committed` rỗng → mã lỗi `claim` | 4 |
| `finish(answer)` | `run_turn`, trong `finally` | pending chỉ còn nếu `answer` nhắc đúng giờ đang giữ | 2 |

### 2. Bốn luật

1. **Mỗi lượt giữ tối đa một lịch.** `hold` lần 2 trong cùng lượt bị từ chối với
   `{"error": "one_at_a_time"}`; pending giữ cái đầu.
2. **Pending chỉ sống nếu khách vừa được hỏi đúng nó.** Cuối lượt, pending được
   giữ **chỉ khi** câu trả lời cuối (sau guard/rewrite) chứa **cả** phần giờ
   (`format_vi_hhmm`, vd "3 giờ chiều") **và** phần ngày ("5/10"), so trên chữ
   thường đã gộp khoảng trắng. Thiếu một trong hai, câu fallback, câu rỗng, lượt
   lỗi → xoá, log `pending_dropped_unmentioned`. Phủ G-07, G-09a, A-07, và ca
   "câu chưa rõ → booking trả lời chuyện khác mà không hỏi lại".
3. **Bước chốt ba trạng thái** (`consent`, mục 3): `yes` → `commit` → `phrase`;
   `no` → `release` → `booking`; `unclear` → **giữ** pending → `booking`.
4. **Không nhận đã làm khi chưa làm.** Chỉ bắt câu ngôi thứ nhất của bot nói đã
   hoàn tất (`em (đã) (đặt|hủy|dời|ghi) (xong|rồi)`, `xong rồi ạ`, `đặt xong`),
   không tính khi có "chưa" đứng trước, khi `committed` rỗng. Vi phạm đi qua
   `rewrite` như các mã khác (`claim`); rewrite vẫn vi phạm → câu cứng
   `"Dạ em chưa đặt lịch nào cho {address} ạ, {address} muốn em giữ giờ nào?"`.
   Không áp cho câu chốt của `phrase` (luôn có `committed`). Sửa bằng rewrite
   chứ không thay thẳng vì bot có câu đúng như "chị đã có lịch 4 giờ chiều rồi ạ".

### 3. Bộ phân loại `consent.classify_reply`

**Chuẩn hoá:** chữ thường, **bỏ dấu** (`strip_diacritics` hiện có, chuyển sang
`consent.py`), giữ `?`, bỏ dấu câu khác, tách từ. **Một bộ luật không dấu duy
nhất** — câu có dấu, không dấu, gõ trộn cho cùng kết quả. Bộ luật cũ phải tách
đôi vì "đúng"/"dùng" cùng ra "dung"; với luật "cả câu" dưới đây, "cho cô dùng
thử" có "thu" lạ nên tự rơi vào `unclear`.

Thứ tự, dừng ở bước đầu tiên khớp:

1. Rỗng → `unclear`.
2. Câu hỏi → `unclear`: có `?` hoặc từ hỏi *nao, sao, ha, gi, may, bao nhieu, vay*.
3. Từ phủ định ở bất kỳ đâu → `no`: *khong, ko, k, hong, hem, thoi, khoi, chua,
   khoan, doi* (đổi/đợi/dời), *khac, lui, chuyen, de sau*; thêm *huy* khi **không**
   ở chế độ hủy.
4. Cả câu chỉ gồm từ đồng ý + từ đệm, có ≥ 1 từ đồng ý, ≤ 8 từ → `yes`.
   - Đồng ý: *u, ua, o, uh, uhm, ok, oke, okie, vang, da, dung, phai, duoc, dc,
     chuan, chot, dat, nhat tri, dong y, chinh xac*, 👍; chế độ hủy thêm *huy*.
   - Đệm: *em, chi, anh, co, chu, bac, con, minh, roi, nhe, nha, a, oi, di, luon,
     giup, cho, the*.
5. Còn lại → `unclear`.

Đánh đổi chấp nhận: "dạ 3 giờ nha em" (nhắc lại **đúng** giờ đang giữ) cũng
`unclear` → hỏi lại một câu. Có thể bổ sung "giờ khớp pending là đồng ý" sau,
khi log cho thấy cần.

`confirm` log `consent_verdict` (verdict + reason) để mở rộng bộ từ theo câu thật.

### 3b. Supervisor trả ý định

Model trả **một từ là `intent`**; `route` do code suy ra — một nguồn sự thật,
không có cặp route/intent mâu thuẫn. Cùng một lần gọi LLM như hiện nay.

| intent | Nghĩa | route |
|---|---|---|
| `book` | đặt mới, hoặc chỉ nói tên dịch vụ | booking |
| `reschedule` | dời lịch đã có | booking |
| `cancel` | hủy lịch | booking |
| `my_appointments` | hỏi lịch của mình | booking |
| `availability` | hỏi giờ trống | booking |
| `shop_info` | giờ mở cửa, chủ tiệm bận/rảnh | shop |
| `social` | chào hỏi, cảm ơn, ngoài lề | social |
| `unknown` | trả lạ / sai định dạng | booking |

- Chuẩn hoá trước khi so: bỏ dấu câu và markdown (`"shop."`, `"**shop**"` → `shop`).
  Không dùng JSON để khỏi thêm một kiểu lỗi phân tích.
- **Người dùng `intent`:**
  1. Khối bối cảnh thêm dòng `"Lượt này khách muốn: dời lịch"` (khối bối cảnh,
     không đụng prefix cache). Prompt booking: dòng này thắng `slots.intent` khi
     khác nhau (slots có thể trễ — G-23).
  2. `hold` từ chối `reschedule_needs_id` (luật 3b ở bảng mục 1) — lưới tất định
     cho G-10. Supervisor đoán nhầm thì khách chỉ bị hỏi "dời lịch nào ạ?", không
     ghi sai.
  3. Log và metadata Langfuse.
- Nhánh `confirm → booking` không qua supervisor nên không có `intent`; dòng ý
  định không hiện.

### 4. Luồng một lượt

`run_turn`: tạo `TurnHarness(db, user, pending)` (intent gắn vào sau khi supervisor chạy) → `build_graph(db, user, harness)`
→ chạy graph → `harness.finish(answer)` trong `finally` (lỗi thì `answer` rỗng) →
lưu lịch sử → `complete` / `error`.

Thay đổi theo file:

- `tools.py`: `propose_appointment`, `cancel_appointment` giữ nguyên mọi kiểm tra
  (trống, `already_yours`, sở hữu); bước cuối gọi `harness.hold(...)` thay
  `set_pending`. Docstring thêm cách xử lý `one_at_a_time`, `reschedule_needs_id`.
- `confirm.py`: còn ba nhánh theo `classify`; `is_affirmative` bị xoá.
- `guard.py`: gọi `check_claim` trước mỗi chỗ trả `answer` (trừ nhánh `phrase`).
- `agents.py`: trả `FALLBACK_ANSWER` kèm `gave_up: True` (log; luật 2 tự xử lý vì
  câu fallback không nhắc giờ).
- `supervisor.py`, `prompts.py`: intent như mục 3b; prompt booking thêm hai mã lỗi.
- `events.py`: tạo harness, truyền vào graph, `finish` trong `finally`.
- `context.py`: dòng luôn hiện `"Đang chờ khách chốt: <spoken>, <note>"` hoặc
  `"Không có giờ nào đang chờ khách chốt."`; dòng ý định khi có `intent`.

Kịch bản:

1. "mai 3 giờ chiều làm móng" → booking → `hold(15h)` → hỏi chốt có "3 giờ chiều",
   "5/10" → giữ. "dạ ko em" → `confirm` → `no` → `release` → booking. DB trống.
2. Pending 15h, "dạ 4 giờ nha em" → `unclear` → giữ → booking thấy dòng "Đang chờ
   khách chốt: 3 giờ chiều…" → `propose(16h)` → `hold` (lần đầu trong lượt) → hỏi
   chốt 16h → giữ.
3. Vòng 4 `propose(16h30)` giữ được → hết vòng → câu fallback không nhắc 16h30 →
   `finish` xoá. "Dạ vâng để chị gọi" → không pending → supervisor. Không lịch ma.
4. Pending hết hạn, "ừ được em" → booking viết "Dạ em đặt xong rồi ạ" →
   `check_claim` → `claim` → rewrite; vẫn vi phạm → câu cứng.

### 5. Lỗi và ca biên

- **Hai lượt song song (REL-01):** không khoá cả lượt trong spec này, nhưng:
  `commit` giành pending bằng `find_one_and_update` có điều kiện `asked_at` bằng
  giá trị đã đọc → cùng một pending không bao giờ ghi hai lần; giành hụt → booking.
  `release`/`finish` chỉ xoá pending có `asked_at` khớp cái lượt này đọc hoặc giữ.
- **`commit` thất bại:** giờ bị lấy / hạn mức → `failed` (như cũ); hủy mà lịch đã
  mất → kind mới `cancel_failed`, câu chốt "lịch này không còn nữa", không mời
  chọn giờ khác (G-17); payload hỏng → `bad_pending_payload` + `failed` (như cũ).
- **`commit` dời lịch dựa vào `AppointmentService.reschedule`**, đang có lỗi A-01
  (chồng giờ → mất lịch cũ). Sửa A-01 ở nhánh riêng; spec này không đổi service.
- Supervisor trả lạ → `unknown` → booking, không dòng ý định.
- Mất mạng giữa lượt: `finish` vẫn chạy (server, `finally`).
- Pending cũ trong DB giữ định dạng — không migration.

## Test

Không gọi LLM thật (trừ `-m llm` chạy tay). Không dựng harness test cho cả graph
(đã quyết: chỉ làm harness chạy thật).

**Tầng 1 — `tests/test_consent.py`:** một test tham số hoá `(câu, cancelling, kỳ vọng)`
chứa mọi câu đã tái hiện:

- `no`: "dạ ko em", "dạ hông", "dạ khoan", "dc ko", "ừ dời qua thứ Sáu", "ừ không đổi gì đâu"
- `unclear`: "dạ 4 giờ nha em", "dạ 9 giờ", "dạ sơn gel", (hủy) "hủy cái nào vậy em?",
  (hủy) "để chị hỏi thằng Huy đã", câu tán gẫu chứa "phải" trong CONTEXT, "cho cô dùng thử"
- `yes`: "ừ", "dạ được em", "ok chị chốt", "u dc", "👍", (hủy) "ừ hủy đi em"
- Có dấu / không dấu / trộn → cùng kết quả.

**Tầng 2 — `tests/test_turn_harness.py`** (`test_db` thật, không LLM):

| Luật | Test |
|---|---|
| 1 | `hold` lần 2 → `one_at_a_time`, pending là cái đầu |
| 3b | `intent=reschedule`, `hold` không `replaces` → `reschedule_needs_id` |
| 2 | `finish` đủ giờ + ngày → giữ; thiếu một / fallback / rỗng → xoá |
| 2 | pending khác `asked_at` (lượt khác giữ) không bị xoá |
| 3 | `commit` ghi đúng giờ trong DB |
| 3 | hai `commit` cùng pending → đúng một lịch |
| 3 | hủy thất bại → `cancel_failed` |
| 4 | `check_claim`: "em đặt xong rồi ạ" chưa ghi → bắt; "chị đã có lịch 4 giờ chiều rồi ạ", "em chưa đặt" → qua |

**Tầng 3 — node/wiring** (model giả, theo lối test hiện có):

- `confirm`: ba nhánh, đúng route và pending; `unclear` giữ pending.
- `agents.py`: model giả đòi tool cả 4 vòng, vòng 4 `propose` → sau `finish` pending rỗng (G-07).
- Supervisor: chuẩn hoá (`"Reschedule."`, `"**shop**"`, `" BOOK "`), bảng intent→route, chuỗi lạ → `unknown`.
- `run_turn` (FakeGraph): `finish` chạy ở cả `complete` và `error`; harness được truyền vào `build_graph`.
- Khối bối cảnh: dòng pending luôn có; dòng ý định khi có `intent`.
- Fence (lớp phụ): ngoài `turn_harness.py`, `app/agents/` không gọi `set_pending`,
  `create`, `cancel`, `reschedule`.

**Sửa test cũ:** test `is_affirmative` trong `test_confirm.py` chuyển sang
`test_consent.py` (đổi kỳ vọng ở chỗ cố ý đổi: nhánh hủy, câu trộn dấu);
`test_tools.py` kiểm `hold`; `test_supervisor.py` đổi nhãn sang intent;
`test_prompts.py` quét hai mã lỗi mới.

**Chạy tay:** mở rộng `scripts/probe_supervisor.py` và test `-m llm` của supervisor
với câu thật; chạy `scripts/chat_scenarios.py` sau khi xong.

**Xong khi:** `pytest` xanh; mọi câu tái hiện G-01/02/03/07/08/09 + bug
`is_affirmative` có test; grep `is_affirmative` không còn kết quả.

## Tài liệu đi kèm

- `CONTEXT.md`: xoá mục "VIỆC TIẾP THEO — bug `is_affirmative`" (đã thay bằng
  `consent`); bỏ câu "nhánh hủy đang đúng, giữ nguyên"; bẫy #19 viết lại: "hủy" là
  đồng ý **chỉ khi** cả câu chỉ có "hủy" + từ đệm; thêm bẫy mới: pending chỉ sống
  khi câu trả lời cuối nhắc đúng giờ đang giữ — đừng "nới" luật này.
- `AGENTS.md`: "`propose_appointment` holds a slot" → giữ **ý định** (pending), không
  giữ chỗ trong DB; mọi ghi lịch của agent đi qua `TurnHarness`.

## Ngoài phạm vi

- G-04 (đánh số ngày nghỉ lệch frontend/backend) — bug riêng, sửa bounded.
- G-05/G-06 (regex đọc giờ), G-12/G-19/G-20 (guard xưng hô, đồng hồ) — spec riêng.
- S-07 (giới hạn độ dài tin), Azure Content Safety — spec riêng.
- A-01 (dời lịch chồng giờ mất lịch cũ) — sửa bounded trong `AppointmentService`.
- REL-01 (khoá theo user cho cả lượt) — spec riêng; ở đây chỉ chặn ghi trùng.
- LLM viết lại câu của khách — đã cân nhắc và **bỏ**: thêm chỗ đổi nghĩa khó phát
  hiện, đi ngược bước chốt tất định, chậm thêm 1–2 giây. Thay bằng intent của
  supervisor (mục 3b).
- Harness test chạy cả graph với LLM giả (G-27).
