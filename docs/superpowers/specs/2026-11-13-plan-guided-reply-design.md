# Xây lại lớp guard: câu trả lời theo kế hoạch của code

Ngày 13/11/2026. Thay cho "mức C" (spec `2026-11-11-level-c-grounded-reply-design.md`). Chủ dự
án chốt qua brainstorming 13/11 sau lần chạy thật 5 và một vòng nghiên cứu kỹ thuật guardrail.

## Vì sao

Mức C bắt model trả JSON gồm các đoạn, tự gắn id và vai trò cho từng giờ, dịch vụ và giá; code
chỉ kiểm nhãn. Lần chạy thật 5 cho thấy phần lớn lỗi mới **sinh ra từ chính lớp này**
(log: `.superpowers/sdd/2026-11-11-level-c/live-5-{A,B}.log`):
- **B lượt 7:** bot mời khách chọn đúng giờ đã kín. Lời nhắc viết lại "cite as taken" đẩy model chèn
  giờ kín vào câu mời; guard cho qua vì nhãn đúng. Kiểm nhãn do model tự khai chỉ kiểm được model
  có nhất quán với chính nó.
- **Câu cứng, câu cụt:** model chép nguyên nhãn dài ("Thứ Bảy 14/11, …"), bỏ động từ ("tiệm mình
  7 giờ tối ạ"). Nghiên cứu *Let Me Speak Freely?* (EMNLP 2024) cũng thấy ép định dạng làm chất
  lượng câu giảm.
- Lần chạy 3 (trước mức C, guard đơn giản hơn) **đạt**.

Bài học từ nghiên cứu (Rasa CALM, Google T2G2, tau²-bench, OpenAI agents guide): hệ thống đặt lịch
làm tốt thì để **code quyết định câu phải nói gì**, model chỉ diễn đạt; viết lại chỉ có ích khi
phản hồi chính xác; lớp kiểm cuối là lớp yếu nhất, không nên gánh hết.

## Mục tiêu

1. Ở mọi lượt có nhắc **giờ hoặc giá**, code lập **kế hoạch câu** (các hành động phải nói) kèm
   **câu mẫu** tiếng Việt. *(Chủ dự án chọn: phủ cả lượt chỉ báo thông tin, như bảng giá, lịch
   hẹn, giờ mở cửa.)*
2. Model **viết lại câu mẫu bằng chữ thường**, không JSON. Model được trả lời thêm phần khách hỏi việc
   khác trong cùng lượt. *(Chủ dự án chọn "cách 2": model viết lại, không gửi nguyên câu mẫu.)*
3. Guard kiểm bằng code trên chữ thường. Câu sai nghĩa thì **khách nhận câu mẫu ngay**.
4. Vẫn **một lần gọi model** cho mỗi câu trả lời.

## Không làm

- Không LLM giám khảo, không NLI (quyết định 5).
- Không lần gọi model riêng để viết lại (loại cách C).
- Không đổi agent con, nút bấm, REST, Telegram, frontend, các cổng chặn hành động (`decide_pending`,
  veto, `ToolRulesMiddleware`).
- Không lưu câu bị guard loại vào DB (chỉ log `rejected_reply`).

## Thất bại dù vẫn chạy (anti-goals)

- Câu đọc như máy, giống hệt câu mẫu ở mọi lượt (mất lợi ích của "cách 2").
- Tỉ lệ rơi xuống câu mẫu cao vì guard chặn nhầm câu viết lại đúng.
- Bot mời giờ kín, báo đã đặt khi chưa đặt, đọc sai giờ hoặc giá. Đây là lỗi không được phép lọt.

## Cách làm (đã chốt: cách A)

### Các cách đã cân nhắc
- **A — chủ dự án chọn.** Giữ `guard/catalog.py` làm nguồn dữ kiện nội bộ (đã có vai trò kín, trống,
  đang giữ, đã đặt, thất bại, lịch sắp tới, giờ mở cửa, dịch vụ, giá; đã có test và đã chạy thật),
  nhưng không hiện id cho model nữa. Thêm `guard/plan.py` biến dữ kiện thành kế hoạch và câu mẫu.
  Gỡ phần JSON và nhãn tự khai.
- **B.** Revert toàn bộ mức C về guard của lần chạy 3, rồi thêm lớp kế hoạch. Bị loại vì mất các sửa
  tốt làm sau đó (`parse_time` lấy buổi khách gõ, chặn chữ nước ngoài và tiếng Anh, nhãn giờ ngắn,
  `pending_outcome.nearest`), và guard cũ dựa vào luật so nguyên văn `spoken`.
- **C.** Lần gọi riêng để viết lại sau khi model trả lời. Bị loại vì thêm một lần gọi model mỗi lượt.

### 1. Kế hoạch câu (`guard/plan.py`, mới)
- Dựng từ `FactCatalog` (của `build_catalog`) cùng `ReplyFacts` của lượt. Chạy ở cả hai chỗ
  `ContextMiddleware` (để đưa vào bối cảnh) và guard (để kiểm), từ cùng đầu vào như hiện nay, nên
  hai bên luôn khớp.
- Các hành động *(danh sách của tôi; người làm đối chiếu với vai trò trong `catalog.py`)*:

  | Hành động | Khi nào | Câu mẫu dựa trên |
  |---|---|---|
  | báo giờ kín | có giờ vai trò `taken` | `TAKEN_REPLY` |
  | mời các giờ trống | lựa chọn của phiên chờ vừa mở, giờ `free`, `nearest` | `TIME_OPTIONS_QUESTION`, `FAILED_NEAREST_REPLY` |
  | hỏi đồng ý đề xuất | đề xuất mới của lượt, hoặc đề xuất chờ còn giữ (`kept`/`clarify`) | `PROPOSAL_REPLY`, `CANCEL_PROPOSAL_REPLY` |
  | báo đã đặt / dời / huỷ | quyết định `approved` của lượt gõ chữ, hoặc R0 approved | `DONE_REPLY` |
  | báo ghi thất bại (kèm số tiệm) | `failed`/`not_approvable` của lượt, hoặc R0 thất bại | `FAILED_REPLY` |
  | báo đã bỏ lịch hoặc đề xuất | `rejected`/`superseded` của lượt | `REJECTED_REPLY`, `REJECTED_CANCEL_REPLY` |
  | hỏi câu hỏi của phiên chờ vừa mở | phiên mở trong lượt (`field`) | `FIELD_QUESTION` |
  | báo giá | dịch vụ hoặc nhóm dịch vụ có trong dữ kiện của lượt (do agent Dịch vụ trả) | **mới** |
  | kể lịch hẹn | giờ `appointment` do agent Đặt lịch trả trong lượt | **mới** |
  | báo giờ mở / đóng cửa, tiệm bận tới mấy giờ | giờ `shop_open`/`shop_close`/`busy_until` của agent Thông tin tiệm | **mới** |

- Lượt không có hành động nào (chào hỏi, cảm ơn) thì không có kế hoạch; model viết tự do.
- **Câu mẫu dùng nhãn giờ ngắn** (`short_label`: "… hôm nay", "… mai", ngày khác giữ `spoken`).
  Câu mẫu của kế hoạch **cũng là câu dự phòng**, dùng một nguồn duy nhất, tránh câu cứng.
  `fallback.py` đổi theo: `{at}` lấy nhãn ngắn thay cho nguyên văn `spoken`. *(Quyết định của tôi;
  đổi quy ước cũ "`{at}` luôn là `Moment.spoken`".)* Câu báo của nút bấm (`button_reply`) cũng
  dùng nhãn ngắn.
- Mỗi hành động mang các mốc giờ và khoảng giá **phải có trong câu** (giờ:phút, khoảng giá), cùng
  `announce_turn_ids` của quyết định hoặc R0 mà nó báo.

### 2. Bối cảnh và prompt
- `ContextMiddleware` thêm khối `Reply plan:` vào khối bối cảnh, gồm câu mẫu tiếng Việt của kế
  hoạch. Lượt không có kế hoạch thì không có khối này. Khối "Reply facts (cite by id)" và mọi id
  bị gỡ.
- Prompt Orchestrator (tiếng Anh, không câu mẫu tiếng Việt) thay các luật đoạn và id bằng luật kế
  hoạch, đại ý:
  - khi có Reply plan, câu trả lời phải nói đúng điều các câu trong plan nói, gồm mọi giờ và giá
    trong đó, không thêm giờ hay giá nào khác;
  - được đổi cách nói, nói ngày gọn, gộp câu, và trả lời thêm việc khác khách hỏi.

  Luật E (giờ kín) và "no Markdown" giữ ý.
- Gỡ `response_format` / `ProviderStrategy` / `REPLY_SCHEMA` khỏi `create_agent`. Câu cuối lại là
  chữ thường.

### 3. Guard (trên chữ thường)
Các mã sau *(quyết định của tôi về tên và nhóm; người làm có thể đổi tên)*. Nhóm "nghĩa" thì gửi
câu mẫu ngay; nhóm "câu chữ" thì cho viết lại 1 lần rồi mới gửi câu mẫu.

| Mã | Kiểm gì | Nhóm |
|---|---|---|
| `plan_missing` | Mỗi giờ của kế hoạch có trong câu (so giờ:phút, `clocks_in_text`/`is_known`); mỗi giá của kế hoạch có ít nhất một số tiền nằm trong khoảng | nghĩa |
| `unknown_time` | Câu không có giờ nào ngoài dữ kiện của lượt (lấy từ catalog) | nghĩa |
| `unknown_price` | Câu không có số tiền nào ngoài các khoảng giá của lượt | nghĩa |
| `taken_offered` | Mệnh đề chứa một giờ kín phải có chữ báo kín, và không có chữ mời. Đây là luật `taken_not_told` cũ, chuyển sang chữ thường (`clocks_in_text` theo mệnh đề) | nghĩa |
| `claim` | Như hiện nay | nghĩa |
| `missing_question` | Có đề xuất mới mà câu không có dấu hỏi | nghĩa |
| `missing_phone` / `phone_not_allowed` | Như hiện nay | nghĩa |
| `pending_undecided` | Như hiện nay (cần gọi `decide_pending`, nên cho viết lại có tool) | câu chữ |
| `empty`, `pronoun`, `repeat`, `foreign_script`, `english`, `bad_tool_call` | Như hiện nay | câu chữ |

- `strip_tool_text` và việc bỏ Markdown giữ nguyên.
- **Lượt không có kế hoạch mà vi phạm nhóm nghĩa** (ví dụ chào hỏi lại bịa ra một giờ): cho viết lại
  1 lần, rồi câu chung như hiện nay, vì không có câu mẫu đúng để gửi.
- **Đánh dấu "đã báo"** (`_mark_announced`): câu model được nhận thì đánh dấu các
  `announce_turn_ids` của kế hoạch, vì guard đã đảm bảo các giờ đó có trong câu. Câu mẫu đánh dấu
  như hiện nay. Giữ cơ chế key `announced_turn_ids` trong state.

### 4. Gỡ phần mức C
- Gỡ:
  - `guard/segments.py`, `check_structured`, các mã `bad_structure`, `unknown_ref`, `wrong_role`,
    `ref_mismatch`, `unreferenced`;
  - render id của catalog trong bối cảnh;
  - xoá `structured_response` trong guard;
  - `reply`/`cite` trong `tests/fakes.py`;
  - các lời nhắc của các mã trên.
- Giữ:
  - `catalog.py` (dữ kiện, vai trò, nhãn ngắn);
  - `parse_time` lấy buổi khách gõ;
  - `foreign_script`, `english`;
  - luật báo kín (chuyển sang chữ thường);
  - `TAKEN_MARKERS`/`OFFER_WORDS`;
  - kịch bản B tự chọn giờ.
- Test: các câu trả lời giả lập dạng đoạn chuyển về `AIMessage` chữ thường. Câu của test giữ nguyên
  để các assert về chữ vẫn đúng.

## Ràng buộc (CONTEXT.md)
- `create_agent` + middleware, không stream; câu tới khách chỉ qua `complete` sau guard.
- Không regex trong `app/agents/salon/`.
- Prompt tiếng Anh, không câu mẫu tiếng Việt. Câu mẫu tiếng Việt là chữ cố định trong code
  (`fallback.py`, `plan.py`), không phải prompt; khối `Reply plan` là dữ liệu của lượt, cùng loại
  với `spoken`.
- Không chạy Azure khi chưa hỏi chủ dự án. Log không chứa chữ khách.
- Commit tiếng Anh; không add `docs/superpowers/review.md`, `image.png`, `scripts/terminal_chat.py`.

## Để người làm quyết
- Tên kiểu dữ liệu và hàm của kế hoạch, cách gộp nhiều hành động thành câu mẫu.
- Lời chữ câu mẫu mới (báo giá, kể lịch, giờ mở cửa, tiệm bận), theo giọng các câu trong
  `fallback.py`.
- Lời nhắc viết lại cho các mã nhóm câu chữ.

## Tiêu chí xong
- Test viết trước. Các câu thật của các lần chạy 2–5 làm dữ liệu:
  - câu đúng phải qua;
  - câu lượt 7 lần chạy 5, câu "em đã tìm được giờ 3 giờ chiều … đồng ý nhé?" của lần chạy 2, và câu
    sai dịch vụ của lần chạy 4 phải bị chặn hoặc không thể sinh ra;
  - câu sai nghĩa thì ra câu mẫu, không viết lại.
- Toàn bộ pytest xanh (trừ `test_shop_info.py::test_parse_then_is_open_at_answers_a_relative_question`
  hỏng sẵn).
- Chạy thật A và B (hỏi chủ dự án trước). Đọc transcript theo anti-goals. Đếm số lượt rơi xuống câu
  mẫu và lý do.

## Quyết định sau builder check (13/11)

Các điểm dưới đây tôi tự quyết, dựa trên những gì chủ dự án đã chốt trước đó. Mỗi điểm đánh dấu
*(quyết định của tôi)*.

1. **Tên dịch vụ phải đúng.** Chủ dự án đã chọn phạm vi "giờ, dịch vụ và giá" từ mức C, nên tên dịch
   vụ vẫn được kiểm.
   - Câu mẫu đề xuất và câu báo đã đặt **ghi tên dịch vụ** của đề xuất, ví dụ "em giữ chỗ cắt tóc nữ
     2 giờ chiều mai…".
   - Thêm mã nhóm nghĩa **`wrong_service`**, dùng lại bộ so tên theo từ của `segments.py` (chuyển
     sang `plan.py`/`checks.py` trước khi xoá `segments.py`):
     - câu có tên một dịch vụ không thuộc dữ kiện của lượt thì vi phạm;
     - mệnh đề có động từ giữ, đặt hoặc dời mà nhắc tên dịch vụ khác dịch vụ của đề xuất hoặc quyết
       định thì vi phạm.
   - Câu sai dịch vụ của lần chạy 4 ("em đã giữ chỗ sơn gel" khi khách đặt cắt tóc) phải bị chặn.
   *(quyết định của tôi)*
2. **Thứ tự và loại trừ giữa các hành động.** Kế hoạch có thể có nhiều hành động, theo thứ tự:
   1. ghi thất bại, R0 thất bại;
   2. đã đặt, dời hoặc huỷ (của lượt, hoặc R0);
   3. **báo giờ kín (luôn có khi có giờ kín**, chủ dự án 11/11: phát hiện kín thì luôn báo khách);
   4. hỏi đồng ý đề xuất;
   5. mời giờ trống;
   6. hỏi câu của phiên chờ vừa mở;
   7. báo giá, kể lịch, giờ mở cửa.

   Luật loại trừ:
   - **có đề xuất mới thì bỏ "mời giờ trống"** (giờ trống do agent tự tìm khi tạo đề xuất không mời
     lại);
   - có "mời giờ trống" từ lựa chọn của phiên vừa mở thì không lặp thêm "hỏi câu của phiên" (hai
     hành động gộp thành một câu);
   - đã có câu thất bại hoặc đã đặt thì vẫn nối câu đề xuất mới nếu có, giống `compose_fallback_asking`
     hiện nay.

   `asked_session` (Ruling R12) được tính từ việc kế hoạch có câu hỏi của phiên vừa mở hay không.
   *(quyết định của tôi)*
3. **Hành động dạng danh sách.**
   - **Báo giá:** chỉ các dịch vụ khách đã chọn hoặc đã nêu (`ServiceFact.identified` của lượt, hoặc
     dịch vụ của đề xuất). Các dịch vụ chỉ được liệt kê (bảng giá, ứng viên) không bắt buộc phải
     nhắc; nếu nhắc thì giá vẫn phải nằm trong khoảng (`unknown_price`).
   - **Kể lịch:** mọi lịch agent Đặt lịch trả trong lượt (tối đa 5) đều phải có.
   - **Mời giờ trống:** mọi lựa chọn đều phải có.
   - Model được nói một số tiền nằm trong khoảng ("khoảng 150 nghìn"); không được nói "từ 100
     nghìn" nếu số đó nằm ngoài khoảng.

   *(quyết định của tôi)*
4. **Đề xuất chờ chỉ hỏi lại khi `clarify`**, giống hiện nay. Với `kept` (khách nói chuyện khác),
   kế hoạch không bắt nhắc lại đề xuất, để tránh lặp và tránh dính luật `repeat`. Model vẫn được nhắc
   nếu hợp ngữ cảnh. *(quyết định của tôi)*
5. **Chỉ so giờ, không so ngày**, như quyết định đã có (R8/R9, mức C). Sai ngày là lỗ hổng đã biết,
   chấp nhận. Trùng giờ khác ngày: luật `taken_offered` chỉ áp cho giờ kín **không** đồng thời là
   giờ được mời trong kế hoạch. *(quyết định của tôi)*
6. **`pending_undecided` đi trước:** câu vi phạm `pending_undecided` luôn được viết lại 1 lần, có tool
   `decide_pending`, kể cả khi đồng thời có lỗi nghĩa. Nếu không, lời đồng ý của khách bị mất. Lần
   viết lại đó vẫn sai nghĩa thì mới gửi câu mẫu. *(quyết định của tôi)*
7. **Đánh dấu "đã báo" cho câu mẫu** dùng `announce_turn_ids` của kế hoạch, không tìm `spoken` trong
   chữ nữa, vì câu mẫu giờ dùng nhãn ngắn. Câu của model đã qua guard cũng dùng các id này.
   *(quyết định của tôi)*
