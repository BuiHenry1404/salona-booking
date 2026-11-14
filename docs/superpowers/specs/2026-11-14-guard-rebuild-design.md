# Đập đi xây lại lớp guard: Planner JSON → LLM → Validator

Ngày 14/11/2026. Chủ dự án chốt qua brainstorming 14/11. **Thay cho** spec
`2026-11-13-plan-guided-reply-design.md` và plan `2026-11-13-plan-guided-reply.md`. Từ spec cũ chỉ
mang sang những mục được nhắc tên ở đây (bộ đo độ chính xác, `asks_price`, các luật hành động).

## Vì sao

1. **Danh sách từ khó bảo trì.** Guard hiện đoán *nghĩa* câu của bot bằng danh sách từ ngữ pháp:
   - nhận vơ: `đặt/ghi/chốt`, `đã/vừa/rồi`, `chưa/không`;
   - mời giờ kín: `kín/có khách…`, `chọn/lấy/đồng ý`;
   - mệnh đề giữ/đặt/dời: `wrong_service` (b);
   - xưng hô: `cô/chú/bác`, `tôi`;
   - từ tiếng Anh.

   Tiếng Việt quá nhiều cách nói, nên mỗi lần chạy thật lại phải thêm từ hoặc ngoại lệ.
2. **`guard/` là ba lớp chồng lên nhau, chưa lớp nào được dọn** (khoảng 2.400 dòng, 7 file):
   - kiểm chữ đời đầu: `check_text`;
   - mức C: `catalog.py` (id + vai trò), `segments.py`, `RequiredRef`;
   - kế hoạch câu: `plan.py`, `check_reply`.

   Hệ quả:
   - hằng số lặp ở 2–3 file (`_RELEASED_OUTCOMES`, `_STILL_PENDING`);
   - phụ thuộc vòng vèo (`facts → plan → catalog`, `fallback → catalog`);
   - dữ kiện lượt dựng qua hai tầng (`FactCatalog` rồi `ReplyPlan`).

## Mục tiêu

1. Guard theo đúng sơ đồ chủ dự án đưa:
   ```
   Business results (DB, agent Đặt lịch / Thông tin tiệm / Dịch vụ)
     → Response Planner (Python: dữ kiện + hành động + nội dung bắt buộc + câu mẫu)
     → LLM, một lần gọi (viết tiếng Việt tự nhiên)
     → Response Validator (Python: trích dữ kiện + ràng buộc + độ phủ)
     → PASS: chữ của model · FAIL: câu mẫu
   ```
2. **Mọi thứ guard đưa cho Orchestrator đều là JSON**: kế hoạch của lượt và lời nhắc khi viết lại.
   Model đọc JSON rồi tự viết câu. Model **không** nhận câu mẫu tiếng Việt.
3. **Validator không có danh sách từ ngữ pháp.** Ràng buộc chỉ tính trên dữ kiện trích được: giờ,
   giá, tên dịch vụ trong danh mục, chữ số điện thoại, dấu `?`, loại ký tự, tool call.
4. **Giờ kín do code tự báo**; model không được nhắc giờ kín.
5. **Xưng hô do memory và context lo**, guard không kiểm. Orchestrator ghi cách xưng khách tự nói
   (tool `remember_address`), code in vào dòng `Address` của khối bối cảnh (mục "Xưng hô").
6. **Mọi lỗi được viết lại 1 lần**; vẫn sai thì gửi câu mẫu.
7. **Hỏi gộp khi thiếu nhiều thông tin** (chủ dự án 14/11): agent con hỏi một lần mọi phần còn thiếu
   (ví dụ ngày và giờ), không hỏi từng phần qua nhiều lượt.
8. **Một tin nhiều việc thì làm hết** (chủ dự án 14/11): Orchestrator gọi mọi agent mà tin nhắn cần
   trước khi trả lời, và câu trả lời phủ mọi việc khách hỏi.

## Không làm

- Không LLM giám khảo, không NLI (quyết định 5 của CONTEXT.md).
- Không thêm lần gọi model nào ngoài lần viết lại khi FAIL.
- Không đổi:
  - agent con, ngoài việc hỏi gộp (mục "Hỏi gộp"); tool của Orchestrator, ngoài `remember_address`; `asks_price` trên `service_task` (đã có từ Task 2b);
  - nút bấm (vẫn là API code, vẫn dùng câu mẫu);
  - `decide_pending`, `veto`, `ToolRulesMiddleware`;
  - REST, Telegram, frontend.
- Không lưu câu bị loại (chỉ log `rejected_reply`, không chữ khách).

## Thất bại dù vẫn chạy (anti-goals)

- Bot mời giờ kín, đọc sai giờ hoặc giá, nêu dịch vụ không thuộc lượt.
- Khách nhận câu mẫu ở nhiều lượt vì validator chặn nhầm.
- Câu đọc như máy: model chép nguyên JSON, ví dụ đọc "15:00" hay "120000–150000".
- Lại xuất hiện một danh sách từ ngữ pháp để vá một ca.

## Các cách đã cân nhắc

**Model nhận gì:**
- **A. JSON dữ kiện + hành động, không câu mẫu.** Chủ dự án chọn. Khớp với "mọi thứ là JSON cho
  Orchestrator", câu ít đúc khuôn. Bỏ quyết định "cách 2" (model viết lại câu mẫu) của spec 13/11.
- B. Câu mẫu tiếng Việt để model viết lại (như plan 13/11). Bị loại: câu dễ giống câu mẫu.

**Giờ kín:**
- **Code ghép câu báo kín, model không được nhắc giờ kín.** Chủ dự án chọn. Không cần đọc chữ mời
  hay chữ báo kín.
- Chấp nhận rủi ro (bỏ luật). Bị loại: lỗi lần chạy 5 B lượt 7 sẽ lọt.

**Sau khi model viết:**
- **Validator chỉ kiểm dữ kiện.** Chọn.
- Không kiểm gì. Bị loại: giờ bịa lọt thẳng tới khách.
- LLM chấm. Bị loại theo quyết định 5.

**Cách xây:**
- **Đập một lần.** Chủ dự án chọn. Xoá phần guard cũ và viết lại trong một đợt, chấp nhận test đỏ
  giữa đợt và diff lớn.
- Xây song song rồi chuyển. Bị loại theo lựa chọn của chủ dự án.
- Sửa dần trên file cũ. Bị loại: mang theo cấu trúc chồng lớp.

## Thiết kế

### Bố cục `app/agents/salon/guard/`

| File | Vai trò | Đầu vào → đầu ra |
|---|---|---|
| `results.py` | Business results: gom dữ kiện code biết của lượt | `TurnSnapshot`, `turn_facts`, `pending_outcome`, câu khách, danh mục dịch vụ (đọc DB một lần) → `TurnResults` |
| `planner.py` | Hành động + nội dung bắt buộc | `TurnResults` → `ReplyPlan` (có `to_json()`) |
| `templates.py` | Câu mẫu tiếng Việt | `ReplyPlan` → câu FAIL; `PendingOutcome` → câu nút bấm; câu báo kín |
| `validator.py` | Trích dữ kiện + ràng buộc + độ phủ | (chữ model, `ReplyPlan`, `TurnResults`) → `Verdict` |
| `clocks.py` | Giữ: đọc giờ theo giá trị trong ngày | như hiện nay |

**Xoá:**
- `catalog.py`, `facts.py`, `plan.py`, `checks.py`, `fallback.py`, `segments.py`;
- test của chúng (`tests/salon/guard/test_{catalog,checks,plan,fallback,segments}.py`).

**Mang sang:**
- **logic** cần giữ, viết lại vào file mới: `short_label`, `format_price`, `strip_tool_text`, đọc giờ
  và tiền;
- **luật** hành động (mục Planner bên dưới).

Mỗi khái niệm chỉ định nghĩa ở một chỗ, không lặp hằng số.

Hướng phụ thuộc một chiều: `results ← planner ← templates`, `results/planner ← validator`.
`templates.py` không import `validator.py`; `results.py` không import file nào khác trong `guard/`
ngoài `clocks.py`.

### Business results (`results.py`)

`TurnResults` (Pydantic, giá trị JSON thường) gồm những gì code biết, không tin chữ model:

| Nhóm | Nội dung |
|---|---|
| Giờ của lượt | mỗi giờ: `moment`, nhãn nói (`short_label`), vai trò (`taken`, `free`, `held`, `appointment`, `shop_open`, `shop_close`, `busy_until`, `asked`, `at`), `ClockKey` |
| Đề xuất | đề xuất mới của lượt (`ProposalFact`), đề xuất chờ (`PendingView`) |
| Quyết định | `pending_outcome` của lượt (gồm `nearest`, `service_ids`), các quyết định R0 chưa báo (`UnannouncedDecision`) |
| Phiên chờ | phiên vừa mở trong lượt (câu hỏi `field`, các lựa chọn) |
| Dịch vụ | **dịch vụ của lượt** (xem dưới); tên mọi dịch vụ của tiệm (để bắt tên lạ) |
| Khác | xưng hô (`address_phrase`), số điện thoại tiệm (`settings.shop_phone`, có thể trống), 2 câu trả lời trước (cho `repeat`), đề xuất chờ chưa được quyết (`pending_undecided`) |

**Dịch vụ của lượt** *(quyết định của tôi, sau builder check)* gồm:
- `ServiceFact` do agent Dịch vụ trả **trong lượt này**;
- dịch vụ của đề xuất mới, của đề xuất chờ, và của `pending_outcome`;
- dịch vụ của các lịch hẹn mà hành động `appointments` của lượt kể.

Hẹp hơn hiện nay: **không** còn `snapshot.known_services` của lượt trước, và không còn dịch vụ
của mọi lịch sắp tới. Lý do: câu sai dịch vụ của lần chạy 4 lượt 8 ("giữ chỗ sơn gel" khi đề
xuất là cắt tóc; sơn gel đặt từ lượt 4) chỉ bị bắt nếu sơn gel không thuộc lượt. Cái giá: câu nhắc
dịch vụ của một lịch cũ mà lượt này không kể sẽ bị chặn, nhưng chỉ tốn một lần viết lại.

`results.py` dựng một lần mỗi lần guard / context cần. `ContextMiddleware` và
`ReplyGuardMiddleware` gọi chung một hàm `reply_results_for(...)`, nên hai bên luôn thấy cùng dữ kiện.

### Planner (`planner.py`)

`ReplyPlan` gồm:
- `taken`: các giờ kín (code báo);
- `acts`: danh sách hành động, đúng thứ tự;
- `ask`: câu hỏi của phiên vừa mở, nếu có;
- `announce_turn_ids`.

**Các hành động** (luật mang từ spec 13/11 và các Ruling, giữ nguyên ý):

| `act` | Khi nào | Trường JSON |
|---|---|---|
| `failed` | `failed`/`not_approvable` của lượt, hoặc R0 thất bại | `at`, `nearest` (≤ 3 nhãn, có thể rỗng), `phone` (số tiệm hoặc `null`) |
| `done` | quyết định `approved` của lượt gõ chữ, hoặc R0 approved (R0 trùng quyết định của lượt thì không báo hai lần) | `kind` (`book`/`move`/`cancel`), `at`, `services` |
| `released` | `rejected`/`superseded` của lượt | `kind`, `at` |
| `proposal` | đề xuất mới của lượt, hoặc đề xuất chờ ở `clarify` (không hỏi lại khi `kept`) | `kind`, `at`, `services` |
| `offer` | lựa chọn của phiên vừa mở, giờ `free` (tối đa 3); bỏ khi có `proposal` | `times` |
| (`ask`, không phải act) | phiên vừa mở trong lượt, khi không bị `offer` thay | `{"fields": [QuestionField…], "options": [nhãn nói…]}`; `fields` là các giá trị tiếng Anh sẵn có (`day`, `time`, `part_of_day`, `service`, `which_appointment`, `week`, `confirm`), model tự viết **một** câu hỏi gồm mọi trường |
| `price` | dịch vụ có `asked_price`; hoặc lượt không có hành động đặt lịch thì các dịch vụ `identified`; mỗi dịch vụ tối đa một lần | `service`, `price` (chuỗi nói: "120–150 nghìn") |
| `appointments` | lịch agent Đặt lịch trả trong lượt (tối đa 5) | `times` |
| `hours` | giờ mở / đóng cửa | `open`, `close` |
| `busy` | tiệm bận tới | `until` |

Luật đi kèm:
- **`failed.phone`** *(quyết định của tôi)*: có số tiệm **và** `nearest` rỗng thì `phone` là số tiệm,
  ngược lại `null`. Có `nearest` thì mời giờ gần nhất, không bảo gọi tiệm (giống `button_reply` hiện
  nay; gỡ luôn chỗ lệch cũ "lượt gõ chữ vừa bảo gọi tiệm vừa bảo nhắn giờ"). Lần thất bại thứ hai
  trong cùng lượt không kèm số (giống `ALSO_FAILED_REPLY`). Mọi giờ trong `nearest` đều bắt buộc.
- Có `offer` từ lựa chọn của phiên vừa mở thì `ask` của phiên đó bỏ (`asked_session` theo R12).
- Lượt không có hành động và không có giờ kín: kế hoạch rỗng, model viết tự do (vẫn qua validator).

**Mọi giờ trong JSON là nhãn nói** (`short_label`: "3 giờ chiều mai"; ngày khác hôm nay / mai giữ
`spoken`), không bao giờ là "15:00" hay ISO. Giá là chuỗi nói *(quyết định của tôi)*: dưới 1 triệu viết "120 đến 150 nghìn" (dùng chữ "đến",
không dùng gạch: `scan_money` đọc "120–150 nghìn" chỉ ra 150.000); từ 1 triệu trở lên viết "1 triệu 2
đến 1 triệu 5". Câu mẫu cũng đổi sang cách nói này, thay `format_price` dạng "120.000đ-180.000đ".
Hiện `scan_money` đọc sai "1,2 triệu" (ra 12 triệu) và "1 triệu 200 nghìn" (ra 21 triệu): đợt này
**sửa `scan_money`** cho các cách nói triệu mà câu mẫu và model dùng, có test (ngoại lệ duy nhất của
"không đụng `text_scan.py`"). Bên trong `ReplyPlan`, mỗi hành động
vẫn giữ `ClockKey` và khoảng giá số để validator so.

**JSON đưa cho model** nằm trong khối bối cảnh, dưới một nhãn tiếng Anh, ví dụ:
```json
{"taken_told_by_code": ["3 giờ chiều mai"],
 "acts": [{"act": "proposal", "kind": "book", "at": "4 giờ chiều mai", "services": ["Sơn gel"]},
          {"act": "price", "service": "Sơn gel", "price": "120–150 nghìn"}],
 "ask": null}
```

### LLM (Orchestrator)

- Gỡ mức C:
  - `response_format`, `reply_format`, `REPLY_SCHEMA`;
  - đoạn bắt `StructuredOutputValidationError` trong `context.py`;
  - khối "Reply facts (cite by id)".

  Câu cuối là chữ thường.
- Prompt (tiếng Anh, không chữ Việt) nói đại ý:
  - **một tin nhắn có nhiều việc** (đặt lịch, hỏi giá, hỏi giờ mở cửa…) thì gọi mọi agent cần
    thiết, các agent độc lập gọi cùng một vòng, rồi mới viết câu trả lời; câu trả lời phủ mọi việc;
    thông tin đã có trong khối bối cảnh (giờ mở cửa, lịch sắp tới) thì được dùng thẳng, không bắt
    gọi agent;
  - khi có reply plan thì nói mọi hành động trong `acts` và `ask`, với mọi giờ, giá, tên dịch vụ
    trong đó; không thêm giờ, giá hay dịch vụ nào khác;
  - **không nhắc các giờ trong `taken_told_by_code`**: code đã báo ngay trước câu của model, viết tiếp
    từ sau đó;
  - đọc giờ, giá theo đúng cách viết trong JSON; tự do diễn đạt, gộp câu, trả lời thêm việc khác;
  - **xưng hô theo khối bối cảnh** (address).
- Một lần gọi model cho mỗi câu trả lời, cộng tối đa một lần viết lại.

### Validator (`validator.py`)

Validator **chỉ kiểm phần chữ của model** (hoặc phần câu mẫu sau câu báo kín); câu báo kín do code
ghép vào **sau** khi kiểm, nên không bao giờ dính `taken_mentioned` hay `unknown_time`.

Trước khi kiểm, `strip_tool_text` xoá tên tool, JSON và Markdown lọt vào câu. Sau đó áp các mã sau,
tất cả **viết lại 1 lần**:

| Mã | Cách tính (không từ ngữ pháp) |
|---|---|
| `bad_tool_call` | AIMessage có tool call không hợp lệ |
| `pending_undecided` | có đề xuất chờ mà model chưa gọi `decide_pending` |
| `plan_missing` | mỗi `ClockKey` của mỗi hành động có trong câu; mỗi khoảng giá có ít nhất một số tiền nằm trong khoảng; mỗi tên dịch vụ của `proposal`/`done`/`price` có trong câu |
| `unknown_time` | một giờ đọc được trong câu không thuộc **giờ hợp lệ**: giờ của lượt, cộng các giờ code đưa trong khối bối cảnh (giờ mở / đóng cửa, lịch sắp tới của khách, đề xuất chờ, lựa chọn của phiên chờ), vì model được dùng thẳng khối bối cảnh |
| `unknown_price` | một số tiền ngoài mọi khoảng giá của lượt |
| `wrong_service` | tên một dịch vụ của tiệm có trong câu nhưng không thuộc dịch vụ của lượt (so theo dãy từ; tên nằm trong tên dài hơn của lượt thì không tính) |
| `taken_mentioned` | câu nêu một giờ kín: giờ:phút thuộc `taken` và **không thuộc giờ của bất kỳ hành động nào** trong `acts` (chỉ so giờ không so ngày; giờ kín trùng giờ:phút với một giờ phải nói thì không cấm) |
| `missing_question` | kế hoạch có `proposal`, `offer` hoặc `ask` mà câu không có `?` |
| `missing_phone` / `phone_not_allowed` | chữ số điện thoại tiệm có khi có `failed.phone`, không có khi không |
| `empty` | câu rỗng sau khi xoá |
| `foreign_script` | ký tự ngoài bảng chữ Latin–Việt |
| `repeat` | trùng câu trả lời trước (tỉ lệ ≥ 0.85, ≥ 6 từ, nhìn lại 2 câu) |

**Từ vựng con số được phép:** đọc giờ và tiền (`text_scan.py`, `clocks.py`) dùng bộ từ cố định
`sáng/trưa/chiều/tối/đêm`, `k/nghìn/ngàn/triệu`, `tới/đến`, cùng các từ chỉ ngày và thứ (dùng cho
`veto`). Đây là trích dữ kiện, không phải đoán nghĩa. `text_scan.py` không thuộc đợt này.

**Bỏ hẳn:**
- `claim` cùng R8/R9 "bảo lãnh";
- `wrong_service` (b);
- `taken_offered`, `TAKEN_MARKERS`, `OFFER_WORDS`;
- `pronoun`;
- `english`, `ENGLISH_FUNCTION_WORDS`.

**`Verdict`** gồm `text` (chữ đã xoá), `codes` (thứ tự cố định), và `missing`: các hành động còn thiếu,
ở dạng JSON giống hành động trong plan.

### Chính sách trong `ReplyGuardMiddleware`

1. **PASS:** câu gửi khách = câu báo kín (`templates`, một câu cho các giờ trong `taken`, nếu có) +
   chữ model. Ghi `announced_turn_ids = plan.announce_turn_ids`.
2. **FAIL lần đầu** (`guard_rewrites == 0`):
   - xoá câu bị loại khỏi state;
   - thêm lời nhắc **JSON** tiếng Anh, ví dụ
     `{"rejected": ["plan_missing"], "missing": [{"act": "price", ...}], "hint": "..."}`,
     với `hint` lấy từ bảng câu nhắc ngắn theo mã;
   - `jump_to: "model"`;
   - lần viết lại ẩn mọi tool, trừ `decide_pending` khi có `pending_undecided`.
3. **FAIL lần hai:**
   - gửi câu mẫu `templates.compose(plan)` (đã gồm câu báo kín), hoặc `GENERIC_FALLBACK` khi kế
     hoạch rỗng;
   - ghi `announced_turn_ids = plan.announce_turn_ids`;
   - đóng các phiên mở trong lượt, trừ phiên mà câu mẫu hỏi (R12);
   - `jump_to: "end"`.
4. `run.py`:
   - câu mẫu của lượt lỗi hoặc chạm trần dùng cùng `templates.compose`;
   - `_mark_announced` dùng `plan.announce_turn_ids` cho cả câu model lẫn câu mẫu, bỏ so khớp
     `spoken` và nhãn ngắn;
   - nút bấm dùng `templates.button_reply` như hiện nay (giọng câu giữ nguyên).

### Xưng hô (chủ dự án chọn 14/11: Orchestrator ghi)

- **Tool mới của Orchestrator `remember_address(form)`**, `form` thuộc danh sách cố định
  `anh, chị, cô, chú, bác, ông, bà` *(danh sách của tôi; bot luôn tự xưng "em", nên không có "em",
  "con", "cháu")*. Schema cố định, strict.
- Prompt: chỉ gọi khi **khách tự nói** cách xưng của mình (tự xưng "cô muốn đặt…", hay dặn "gọi bác
  là được"). Không bao giờ đoán từ tên hay giọng văn (quyết định 8: chưa biết thì gọi "anh chị").
  Đổi cách xưng thì gọi lại.
- Tool ghi `users.self_address` (cho **ghi đè**, bỏ luật "chỉ ghi một lần" của `set_self_address`),
  rồi `mark_stale` để lần gọi model sau thấy dòng `Address` mới. `SelfAddress` mở rộng theo danh sách
  trên. `address_phrase` giữ thứ tự: `self_address` → tiền tố tên admin gõ → "anh chị".
- Đó là **memory**: `users.self_address` sống qua mọi cuộc trò chuyện; dòng `Address` là **context**
  ở mọi lần gọi model của mọi lượt.
- **Xoá** `detect_self_address` (`app/core/text.py`, regex quét chữ khách), `_seed_self_address`
  trong `run.py`, và test của chúng (quyết định 21).
- Tool không có hiệu lực tới đề xuất / lịch; `ToolRulesMiddleware` chỉ kiểm `bad_args`. Không tính là
  agent con, không hiện bước cho khách.
- Lượt bấm nút không gọi model nên không đổi xưng hô.

### Hỏi gộp (agent con)

- `SubagentResult.question_field` (một trường) đổi thành **`question_fields: list[QuestionField]`**
  (ít nhất một). `need_info` phải có ít nhất một trường và `question_text`.
- Prompt agent Đặt lịch: `need_info` hỏi **mọi** phần còn thiếu của yêu cầu trong một lần (ví dụ
  khách chưa nói gì về thời gian thì hỏi `day` + `time`; nói "chiều mai" thì chỉ hỏi `time`). Agent
  Dịch vụ giữ một trường `service`. `which_appointment` và `confirm` đứng riêng, không gộp với trường
  khác *(quyết định của tôi: hỏi "lịch nào" cùng lúc với giờ mới dễ rối cho khách lớn tuổi)*.
- `SessionQuestion.field: str` đổi thành `fields: list[str]` (Mongo `waiting_sessions`). Phiên chỉ
  sống 10 phút nên không cần chuyển dữ liệu; theo quyết định 21, phiên cũ dạng `field` bị xoá khi
  khởi động hoặc đọc không được thì coi như hết hạn *(người làm chọn một)*.
- Khách trả lời một phần (chỉ nói ngày): agent được gọi tiếp `continue_session=true` và hỏi phần còn
  lại, như hiện nay.
- Khối bối cảnh (`waiting for: …`), `dispatch.py`, Planner `ask` và câu mẫu `FIELD_QUESTION` đổi theo:
  câu mẫu ghép các trường thành một câu hỏi ("ngày nào, mấy giờ").

### Câu mẫu (`templates.py`)

- Giữ nguyên giọng các câu hiện có trong `fallback.py` (`TAKEN_REPLY`, `PROPOSAL_REPLY`,
  `DONE_REPLY`, `FAILED_*`, `REJECTED_*`, `TIME_OPTIONS_QUESTION`, `FIELD_QUESTION`, `PRICE_REPLY`,
  `APPOINTMENTS_REPLY`, `HOURS_REPLY`, `BUSY_REPLY`, `GENERIC_FALLBACK`, `BUTTON_STALE_REPLY`).
- `{at}` là nhãn nói.
- `compose(plan)` nối câu báo kín + `body(plan)`; `body(plan)` là câu của từng hành động + câu hỏi
  của `ask` (`FIELD_QUESTION`, `TIME_OPTIONS_QUESTION`). Test: `body(plan)` luôn qua validator với
  mọi kế hoạch trong test (câu báo kín không qua validator, như câu của model).

### Rủi ro chấp nhận

- Model nói "em đã đặt 3h" khi 3h mới là **đề xuất**: không bắt được, vì giờ hợp lệ. Đỡ bằng:
  - JSON ghi rõ `act` (`proposal` khác `done`);
  - prompt;
  - thẻ đề xuất có nút trên giao diện.
- Xưng hô sai, câu lẫn tiếng Anh không chứa tên tool: không bắt được.
- Chỉ so giờ, không so ngày (giữ quyết định cũ).

## Bộ đo độ chính xác (mang từ spec 13/11)

- Dữ liệu và cách dựng như spec 13/11 (câu bot thật từ log các lần chạy 2–5, cộng các câu sai đã
  biết).
- Mỗi mẫu gồm: câu, `TurnResults` + `ReplyPlan` tối thiểu, và nhãn `ok` / `bad` kèm mã mong đợi.
  **Thêm nhãn `accepted_leak`** cho các loại câu sai thuộc rủi ro chấp nhận (nhận vơ đề xuất, xưng
  hô): validator cho qua, test không hỏng, nhưng in ra số đếm.
- Test hỏng khi một câu `bad` lọt, hoặc khi số câu `ok` bị chặn vượt ngưỡng (ban đầu là 0).
- Câu bắt buộc chặn:
  - lần chạy 5 B lượt 7 (mời giờ kín): `taken_mentioned`;
  - lần chạy 2 lượt 2: `taken_mentioned`;
  - lần chạy 4 lượt 8 (sai dịch vụ): `wrong_service` (theo tập "dịch vụ của lượt"), kèm
    `plan_missing` vì thiếu "cắt tóc";
  - câu có chữ Armenia: `foreign_script`.

## Ràng buộc (CONTEXT.md)

- `create_agent` + middleware, không stream; câu tới khách chỉ qua guard.
- Không regex trong `app/agents/salon/` (`tests/test_regex_budget.py`).
- Prompt tiếng Anh, không chữ Việt (`tests/salon/test_prompts.py`). Chữ Việt chỉ ở `templates.py` và
  dữ liệu của lượt (nhãn nói, tên dịch vụ trong JSON).
- Giá trị state là JSON thường.
- Không chạy Azure khi chưa hỏi chủ dự án. Log không chứa chữ khách.
- Chạy pytest từng lệnh một; trước mỗi commit chạy **toàn bộ** backend. Test hỏng sẵn:
  `tests/salon/subagents/test_shop_info.py::test_parse_then_is_open_at_answers_a_relative_question`.
- Commit tiếng Anh, trên `feat/llm-orchestrator`, kiểm `git branch --show-current` trước khi commit;
  không stash / checkout / reset; không add `docs/superpowers/review.md`, `image.png`,
  `scripts/terminal_chat.py`.
- "Không dùng thì xoá" (quyết định 21): mọi code, test, helper chỉ phục vụ guard cũ hoặc mức C bị
  xoá, kể cả `reply`/`cite`/`Cite` trong `tests/fakes.py` nếu không còn ai dùng.

## Để người làm quyết

- Tên kiểu dữ liệu, tên hàm, chia task.
- Lời nhắc ngắn theo mã (`hint`), nhãn tiếng Anh của khối JSON trong bối cảnh.
- Cách gộp nhiều giờ kín thành một câu báo.
- Đập một lần: giữa đợt, test của phần cũ được phép đỏ. Cuối đợt toàn bộ backend phải xanh
  (trừ test hỏng sẵn).

## Tiêu chí xong

- Không còn tham chiếu nào tới các symbol của guard cũ và mức C: `guard.catalog`, `guard.segments`,
  `guard.facts`, `guard.checks`, `guard.plan`, `guard.fallback`, `FactCatalog`, `build_catalog`,
  `check_structured`, `check_reply`, `reply_format`, `REPLY_SCHEMA`, `TAKEN_MARKERS`, `OFFER_WORDS`,
  `ENGLISH_FUNCTION_WORDS`, `_BOOKING_VERBS`. (Không đổi tên `ServiceCatalogService`,
  `previous_segments`… của phần ngoài guard.)
- Không còn tập từ ngữ pháp nào trong `guard/`.
- Bộ đo độ chính xác chạy và đạt ngưỡng.
- `templates.compose(plan)` qua validator với mọi kế hoạch trong test.
- Toàn bộ pytest xanh (trừ test hỏng sẵn).
- Review cả nhánh bằng model mạnh nhất.
- Sau khi hỏi chủ dự án: chạy thật kịch bản A và B, cần 2 lần đạt. Đọc transcript theo anti-goals,
  đếm số lượt rơi xuống câu mẫu và lý do.
