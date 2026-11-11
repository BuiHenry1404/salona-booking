# Mức C — câu trả lời tham chiếu dữ kiện

Ngày 11/11/2026. Chủ dự án chọn làm mức C sau khi rà các lớp guard. Phạm vi và cách làm
chốt qua brainstorming và bốn bản thử trên Azure trong cùng phiên.

## Vì sao

Guard câu cuối hiện **dò chữ**: đọc giờ, số tiền, động từ "đặt / giữ / huỷ" trong câu của
bot rồi so với dữ kiện. Nó không biết bot đang **làm gì** với một dữ kiện (mời, báo kín, báo
đã đặt), nên để lọt những lỗi nói sai nghĩa. Bằng chứng từ các lần chạy thật:

- **Lần chạy 2, lượt 2:** 3 giờ chiều đã kín, bot vẫn nói *"em đã tìm được giờ 3 giờ chiều…
  chị đồng ý giờ này nhé?"*. Guard cho qua vì "3 giờ chiều" có trong dữ kiện và "tìm được"
  không phải động từ nhận vơ.
- **Lần chạy 4, lượt 8:** khách đặt cắt tóc, bot nói *"em đã giữ chỗ sơn gel"*. Guard không
  kiểm tên dịch vụ. Lỗi dữ liệu gốc đã sửa riêng (sửa G, commit `0476b75`); câu chữ thì vẫn
  không có gì kiểm.
- **Lần chạy 4, lượt 3** và **bản thử (a)**: model viết chữ Armenia "Եթե" ("nếu") giữa câu
  tiếng Việt.

Mức C buộc mỗi lần bot nhắc **giờ, dịch vụ hoặc giá** phải gắn với một dữ kiện có thật kèm
**vai trò**. Code kiểm vai trò đó bằng cấu trúc, không đoán nghĩa từ chữ. Đây là cách "trích
nguồn rồi kiểm nguồn" (grounded generation): đúng tinh thần guard bằng code (quyết định 5),
không dùng LLM giám khảo.

## Mục tiêu

1. Mỗi lần câu trả lời của Orchestrator nhắc giờ, dịch vụ hoặc giá, chỗ đó tham chiếu một dữ
   kiện của lượt (id) kèm vai trò.
2. Code kiểm:
   - id có thật;
   - vai trò khớp dữ kiện (giờ mời phải trống hoặc là lựa chọn của `need_info`; giờ báo kín
     phải là giờ kín; "đã đặt" chỉ với giờ đã ghi trong lượt; dịch vụ trong câu giữ chỗ phải
     là dịch vụ của đề xuất);
   - chữ khớp dữ kiện (đúng giờ, đúng dịch vụ, số tiền trong khoảng giá);
   - phần chữ thường không nhắc giờ, dịch vụ, giá nào ngoài tham chiếu.
3. Bot **nói gọn tự nhiên** (chủ dự án chọn): "4 giờ chiều mai", "cắt tóc", "khoảng 150
   nghìn". Không bắt đọc nguyên dạng `spoken` của tool.
4. Chặn chữ nước ngoài (mục H) và lộ prompt / lọt tiếng Anh, bằng code.

## Không làm (non-goals)

- **Không lưu câu bị guard loại vào DB.** Chủ dự án 11/11: chỉ log ra để debug (log
  `guard_rejected` / `guard_violation` đã có trường `rejected_reply`).
- Không dùng LLM giám khảo hay model NLI nhỏ để chấm (quyết định 5; NLI yếu với tiếng Việt và
  với suy luận).
- Không đổi agent con, nút bấm, câu mẫu code (`fallback.py`), REST, Telegram, frontend.
- Không tham chiếu ngày / thứ riêng lẻ ("thứ Năm", "ngày mai" không kèm giờ) và không tham
  chiếu số điện thoại tiệm. Các luật sẵn có vẫn lo phần đó.

## Thất bại dù vẫn chạy (anti-goals)

- Câu cứng, lặp, đọc như máy: mọi giờ đều "Thứ Năm 12/11, …", tên dịch vụ viết hoa giữa câu.
- Tỉ lệ lượt rơi xuống câu mẫu tăng rõ so với hiện nay, vì model viết sai cấu trúc.
- Guard chặn nhầm câu đúng nhiều hơn guard dò chữ hiện nay.

## Bằng chứng từ hai bản thử (11/11, Azure `gpt-5.4-mini`, 5 tình huống × 3 lần)

| | (a) đánh dấu `[chữ](vai:id)` trong văn bản | **(b) JSON strict, các đoạn kèm id + vai trò** |
|---|---|---|
| Sai cú pháp / thiếu vai trò | 5/15 | 0/15 |
| Quên đánh dấu giờ, dịch vụ, giá | 3/15 | 0/15 |
| Id không có thật | có thể | không thể (id là enum trong schema của bản thử; spec chọn schema cố định, code kiểm id, xem mục 1) |
| Lỗi nói sai nghĩa bị bắt được | 3/15 | 6/15 (đều là "em đã đặt…" trong khi đề xuất còn chờ) |
| Qua hết các bước kiểm | 5/15 | 9/15 |

Script và log thử (không commit) ở `.superpowers/sdd/2026-11-11-level-c/spike/`. Kết luận:
**chọn (b)**.

Cả 6 câu trượt của (b) là lỗi thật, và bị bắt bằng vai trò, không cần đọc chữ. Khối dữ kiện
trong bản thử đơn giản hơn hệ thống thật (lần chạy 3 và 4 bot thật nói đúng "em đã giữ chỗ…
chị đồng ý không?"), nên tỉ lệ này có thể cao hơn thực tế.

Điểm cần xử lý, thấy trong bản thử (b):
- các đoạn ghép lại bị dính chữ (*"3 giờ chiềuđã có khách"*);
- model lặp nguyên "Thứ Năm 12/11, …" cho mọi giờ và viết hoa tên dịch vụ giữa câu;
- một đoạn tham chiếu ôm thừa chữ (*"hoặc 4 giờ chiều nhé."* gắn với một giờ).

### Bản thử (c): gắn đủ tool như Orchestrator thật (11/11, 16 lần gọi)
Bản thử (b) gọi model không gắn tool. Bản thử (c) gắn **cả 4 tool thật** của Orchestrator và
schema JSON trong **một** lần gọi, đúng cách `create_agent` 1.4.6 gắn `ProviderStrategy`
(`bind_tools(tools, strict=True, response_format=json_schema)`). **Lưu ý (review Fable 11/11):**
bản thử tạo `ProviderStrategy` không truyền `strict=True`, mà `langchain_openai` mặc định
`strict=False` (`_convert_to_openai_response_format`). Vậy tool là strict nhưng **JSON không
strict**; kết quả dưới đây có được mà không có ràng buộc giải mã của schema. Có 4 tình
huống (giờ kín, chọn giờ trong phiên chờ, hỏi giá, đồng ý đề xuất), mỗi tình huống 2 lần, mỗi
lần 2 bước:
- **Bước 1** (chưa có kết quả tool, model phải gọi tool): **8/8** gọi đúng tool với đúng tham
  số chính (`service_task`; `appointment_task` với `continue_session=true`; `decide_pending`
  với `approve`). Không lần nào trả JSON thay cho việc gọi tool.
- **Bước 2** (đã có kết quả tool, model phải trả lời): **6/8** trả JSON hợp lệ và qua mọi bước
  kiểm; không lần nào sai cú pháp. Hai lần trượt đều là model gọi thêm `decide_pending`. Lỗi
  nằm ở bản thử: nó ghi đề xuất vừa tạo trong lượt vào dòng "Pending proposal", còn code thật
  (`_pending_view`) giấu đề xuất đó. Nếu model vẫn gọi, luật `no_pending` trả lỗi về cho nó.
- Azure nhận tool strict cùng `response_format` json_schema (không strict) trong một lần gọi,
  không có lỗi 400. Tool strict **và** JSON strict cùng lúc thì chưa kiểm.

Kết luận: **không cần lần gọi riêng để định dạng câu cuối.** Một lần gọi vừa chọn tool vừa trả
JSON chạy được. Câu thật của bước 2 đã gọn hơn (b), ví dụ *"sơn gel lúc 3 giờ chiều mai đã có
khách đặt rồi ạ. Chị chọn giúp em 1 giờ chiều, 2 giờ chiều hoặc 4 giờ chiều nhé."* Vẫn còn tên
dịch vụ viết hoa giữa câu (*"Dạ chị Lan, Nhuộm giá khoảng…"*), việc của mục 7.

### Bản thử (d): strict cả tool lẫn JSON, schema cố định (11/11, 4 lần gọi)
Chủ dự án duyệt sau review Fable. Bản thử dùng `ProviderStrategy(schema_dict, strict=True)` với
**schema cố định** chủ dự án chọn (mục 1), và in cấu hình gửi đi trước khi gọi:
`response_format` strict = True, 4 tool strict = True.
- Azure nhận cả hai strict trong cùng một lần gọi, không lỗi.
- 2/2 lần ở bước 1 gọi đúng tool (`service_task`; `decide_pending` với `approve`).
- 2/2 lần ở bước 2 trả JSON hợp lệ, qua mọi bước kiểm, id đều có thật dù `ref` là chữ tự do.
  Một trong hai là tình huống giữ chỗ với bối cảnh đúng như code thật (đề xuất vừa tạo không
  hiện là "Pending proposal"). Model trả lời ngay, không gọi thêm `decide_pending`. Điều đó xác
  nhận hai lần trượt của bản thử (c) là do bản thử dựng sai bối cảnh.

## Cách làm (đã chốt)

### 1. Câu trả lời cuối là JSON có schema
- Câu trả lời cuối của Orchestrator (lời gọi model không có tool call) trả theo schema
  strict, dùng structured output của Azure. Schema là một danh sách **đoạn** có thứ tự. Mỗi
  đoạn gồm `text`, `ref` (id dữ kiện hoặc null), `role` (vai trò hoặc null).
- **Schema cố định, giống hệt nhau ở mọi lần gọi** (chủ dự án chọn 11/11, review Fable I2):
  `ref` là chữ hoặc null, **không** phải enum id theo lần gọi; `role` là enum cố định gồm mọi
  vai trò ở mục 3. Lý do: schema của structured output nằm ở đầu prompt (tài liệu prompt
  caching của OpenAI và Azure), nên schema đổi theo lần gọi thì mất prompt cache của cả prompt.
  Dự án đã giữ schema tool cố định vì đúng lý do này (`orchestrator/tools.py:9-12`).
- Vì `ref` là chữ tự do, **code kiểm id**: id không có trong dữ kiện được phép nhắc của lượt
  thì sinh mã guard mới (vd `unknown_ref`), viết lại 1 lần rồi câu mẫu như mọi mã khác. Lời
  nhắc viết lại nêu id sai và danh sách id hợp lệ.
- Không giới hạn enum vai trò theo từng lần gọi (đề xuất I4 của Fable), vì làm vậy cũng đổi
  schema. Lỗi "em đã đặt" trên giờ đang giữ do `wrong_role` bắt và viết lại.
- **Dùng `ProviderStrategy(schema, strict=True)` tường minh.** Không dùng `ToolStrategy`,
  không truyền schema trần (factory tự chọn chiến lược qua `_supports_provider_strategy`, có
  thể ra `ToolStrategy` với tên deployment Azure). Với `ToolStrategy`, câu cuối là một tool
  call kèm ToolMessage giả: `final_reply` (`middleware/turns.py:66`) bỏ qua nó, `_final_answer`
  báo `turn_ended_without_reply`, và guard xoá câu đó sẽ để ToolMessage treo (bẫy 13).
  *(Quyết định của tôi, theo review Fable I1.)*
- Hình dạng schema (bản thử d): một **dict** JSON schema
  `{"segments": [{"text": str, "ref": str | null, "role": <enum vai trò> | null}]}`, mọi trường
  bắt buộc (nullable), `additionalProperties: false`, `strict=True`. Script thử được giữ ở
  `.superpowers/sdd/2026-11-11-level-c/spike/` (git-ignored) để tham khảo
  (`spike_level_c_strict.py`).
- **Cấu trúc hỏng không được đi đường lỗi của lượt.** JSON không parse được, `ref` với
  `role` lệch nhau (một bên null), hay model trả chữ thường thay vì JSON: tính là một mã
  guard (vd `bad_structure`), viết lại 1 lần rồi câu mẫu như mọi mã khác. Không để lỗi
  validate của thư viện bay lên `run_turn` và thành `TURN_ERROR_MESSAGE`. Nếu cần, người làm
  tự parse thay vì để factory của `create_agent` parse. *(Quyết định của tôi, theo ý "xử lý
  như các mã khác".)*
- Lần viết lại của guard dùng cùng schema.
- **Thứ tự trong guard:** parse JSON → ghép chữ → `strip_tool_text` → các luật. Không chạy
  `strip_tool_text` trên JSON thô: `_drop_json` (`guard/checks.py:360`) xoá mọi khối `{…}`,
  câu thành rỗng. *(Quyết định của tôi, review Fable I3.)*
- Câu khách đọc = các `text` ghép theo thứ tự. **Code tự chèn khoảng trắng** khi hai đoạn
  liền nhau đều là chữ / số ở chỗ nối, và chuẩn hoá khoảng trắng thừa.
- Câu lưu trong state / checkpoint là chữ đã ghép (lượt sau, luật `repeat` và lịch sử gửi
  model đều đọc chữ thường). Cấu trúc các đoạn chỉ sống trong lần kiểm của guard.

### 2. Id dữ kiện
- Code gắn id ổn định trong một lượt cho mọi dữ kiện câu trả lời được phép nhắc. Giờ có tiền
  tố `t` (`t1`, `t2`…); dịch vụ có tiền tố `s`, và giá tham chiếu chính id dịch vụ.
- Nguồn dữ kiện:
  - `turn_facts`: giờ khách hỏi, giờ trống, giờ kín, lịch của khách, đề xuất, quyết định,
    dịch vụ;
  - từ khối bối cảnh: đề xuất đang chờ, lịch sắp tới, lựa chọn của phiên chờ, giờ mở / đóng
    cửa, dịch vụ đã biết trong cuộc trò chuyện.
  - Dịch vụ của đề xuất đang chờ và của lịch sắp tới cũng có id `s*`. `PendingView` chỉ có
    id dịch vụ, nên code tra tên từ id. *(Quyết định của tôi, review Fable.)*
- Id hiện trong khối bối cảnh, cạnh từng dữ kiện (vd `- t3: time free Thứ Năm 12/11, 2 giờ
  chiều`). Model chép id từ đó, không tự đặt; id lạ bị `unknown_ref` bắt (mục 1).
- Cùng một mốc giờ có hai vai trò dữ kiện (vd giờ khách hỏi cũng là giờ kín) thì dùng một id;
  vai trò cho phép là hợp của các vai trò đó.
- **Nhóm nhiều dịch vụ** (đề xuất chờ, đề xuất mới, phiên chờ, lịch sắp tới có từ 2 dịch vụ):
  code gắn thêm id nhóm `g*`. `price` được tham chiếu id nhóm, với khoảng tổng như luật giá
  hiện nay. *(Quyết định của tôi: giữ đúng những gì guard đang cho nói.)*
- **Giờ trong câu khách** (để hỏi rõ "3 giờ sáng hay 3 giờ chiều ạ?"): code gắn id `c*` cho
  mỗi giờ quét được từ câu khách của lượt (đã có `_customer_clocks`), hiện trong khối bối cảnh
  dạng `- c1: customer said 3 giờ (03:00 or 15:00)`. Chỉ dùng được với vai trò `asked`; kiểm
  chữ chấp nhận mọi cách đọc của giờ đó. Lượt nút bấm không có câu khách nên không có `c*`.
  Chuỗi hiện ra là giờ code đọc lại, không phải chữ khách. *(Quyết định của tôi.)*

### 3. Vai trò và luật kiểm vai trò
| Vai trò trong câu | Hợp lệ khi dữ kiện của id là |
|---|---|
| `offer`: giờ bot gợi ý | giờ trống, hoặc lựa chọn của phiên chờ / `need_info` |
| `taken`: giờ đã có khách khác | giờ kín |
| `held`: giờ đang giữ chờ khách đồng ý | đề xuất mới của lượt, hoặc đề xuất chờ chưa được quyết |
| `booked` / `moved` / `cancelled`: việc đã ghi | quyết định đã duyệt **trong lượt** đúng loại, hoặc dòng Unannounced (R0) |
| `appointment`: lịch khách đang có | lịch sắp tới |
| `asked`: nhắc lại giờ khách vừa nói, để hỏi rõ | giờ khách hỏi / giờ trong câu khách |
| `hours`: giờ mở / đóng cửa | giờ mở / đóng cửa |
| `service`: tên dịch vụ | dịch vụ; nếu câu có `held`, dịch vụ phải thuộc đề xuất đó |
| `price`: giá hoặc khoảng giá | dịch vụ, hoặc nhóm `g*` (một con số trong khoảng giá là hợp lệ, quyết định 13) |
| `failed`: ghi lịch thất bại | quyết định `failed` / `not_approvable` của lượt, hoặc R0 thất bại |
| `released`: lịch / đề xuất khách vừa bỏ | quyết định `rejected` hoặc `superseded` của lượt |
| `busy`: tiệm bận tới mấy giờ | `TimeFact` `busy_until` của agent Thông tin tiệm |

Nguyên tắc *(quyết định của tôi)*: **mọi giờ guard hiện nay cho nói đều phải có vai trò**,
không giờ nào hôm nay nói được mà sau mức C thành không nói được. Cụ thể:
- `pending_outcome.nearest` (giờ trống sau khi duyệt thất bại) và các lựa chọn của phiên chờ
  dùng `offer`;
- `TimeFact` `at` (khách hỏi tiệm có mở lúc X không) dùng `asked`;
- `shop_open` / `shop_close` dùng `hours`;
- quyết định `kept` / `clarify` (đề xuất vẫn chờ) dùng `held`.

Danh sách cuối do người làm đối chiếu với `TimeRole`, `PendingOutcomeName` và R0. Chỗ nào
lệch nguyên tắc trên thì ghi vào sổ.

Sai vai trò sinh mã guard mới (vd `wrong_role`). Lời nhắc viết lại phải nêu chính xác đoạn
sai, vai trò dữ kiện thật và vai trò được phép, ví dụ *"t1 is a proposal waiting for the
customer, not booked"*.

### 4. Luật kiểm chữ
- Đoạn **giờ**: giờ đọc từ `text` (hàm đọc giờ sẵn có của guard) phải khớp **giờ** (giờ:phút)
  của id. **Chỉ so giờ, không so ngày**, giống R8/R9 hiện nay. "mai", "thứ Năm", "12/11"
  trong đoạn không bị kiểm, để tránh chặn nhầm (anti-goal 3). *(Quyết định của tôi.)* Buổi
  ("chiều") dùng để chọn cách đọc như hàm đọc giờ đang làm.
- Đoạn **dịch vụ**: chữ phải chỉ đúng dịch vụ của id. Luật so khớp cụ thể là việc của người
  làm, tối thiểu: có ít nhất một từ của tên dịch vụ, và không chứa tên một dịch vụ khác của
  lượt.
- Đoạn **giá**: mọi số tiền đọc được nằm trong khoảng giá của id.
- Đoạn tham chiếu **chỉ chứa đúng mention** (giờ / tên dịch vụ / giá, kèm từ đi liền như
  "khoảng", "từ … đến"). Không chứa dấu kết câu hay câu hỏi.
- Luật cụ thể cho đoạn **giờ**: bỏ giờ, phần ngày ("mai", "hôm nay", thứ, dd/mm, đọc bằng các
  hàm quét sẵn có) và vài từ nối ngắn ("lúc", "vào", "ngày") đi, thì không còn chữ nào. Phần
  ngày không bị so với dữ kiện (vẫn chỉ so giờ). Ví dụ đoạn *"hoặc 4 giờ chiều nhé."* bị chặn.
  *(Quyết định của tôi, review Fable I5.)*
- **Lỗ hổng đã biết, chấp nhận:** nếu model gắn đúng vai trò (`taken`) cho một giờ kín, nhưng
  phần chữ thường lại mời giờ đó (*"em tìm được … chị đồng ý nhé?"*), thì vai trò không bắt
  được, vì vai trò kiểm nhãn chứ không đọc nghĩa câu. Câu sai của lần chạy 2, lượt 2 bị chặn
  khi model gắn nó là `offer`; test dùng đúng cách gắn đó.
- Đoạn **chữ thường** không được có giờ, số tiền, hoặc tên dịch vụ nào của lượt (dịch vụ của
  lượt, dịch vụ đã biết trong cuộc trò chuyện).

### 5. Các luật guard hiện có
| Luật | Sau mức C |
|---|---|
| `unknown_time`, `unknown_price` | Bỏ. Mục 3 và 4 đã phủ (chữ thường không được có giờ / giá; giờ / giá trong tham chiếu phải khớp id) |
| `missing_fact` (so nguyên văn `spoken`) | Đổi thành **phải tham chiếu**: giờ đề xuất mới (`held`), giờ quyết định phải báo (`booked` / `moved` / `cancelled` …), giờ R0, giờ kín của lượt (`taken`, mục F2 của spec 2026-11-11 live-run-2-fixes). Hết cảnh chặn câu chỉ vì viết gọn ngày |
| `claim`, `pronoun` | Giữ làm lớp phụ (vai trò không phủ hết cách nói) |
| `empty`, `missing_question`, `missing_phone`, `phone_not_allowed`, `pending_undecided`, `repeat`, `bad_tool_call` | Giữ nguyên |
| `strip_tool_text` | Giữ, chạy trên chữ đã ghép |

Câu mẫu code, viết lại tối đa 1 lần, `exit_behavior="error"` của trần: không đổi.

Phải đổi kèm *(quyết định của tôi, review Fable C1 và các điểm nhỏ)*:
- **Đánh dấu "đã báo"** (`turn/run.py::_mark_announced`, dòng 505) đang so nguyên văn `spoken`
  trong câu đã gửi. Câu nói gọn ("9 giờ sáng mai") sẽ không bao giờ được đánh dấu, nên dòng
  Unannounced (R0) hiện lại mỗi lượt và ép bot báo lại mãi. Đổi sang: guard trả về các id
  quyết định / R0 đã được tham chiếu với vai trò `booked` / `moved` / `cancelled` / `failed`
  trong câu đã nhận, và `_mark_announced` dùng danh sách đó. Câu mẫu code không có đoạn tham
  chiếu nhưng luôn chứa nguyên `spoken`, nên giữ cách so nguyên văn cho câu mẫu.
- Lời nhắc viết lại `_CODE_HINTS` (`middleware/reply_guard.py:52`): `unknown_time` /
  `missing_fact` đang bảo "copied exactly as their spoken value", phải đổi theo luật tham
  chiếu.
- Prompt dòng "Never mention tools, agents, ids…" đổi thành "never write ids **in the text**"
  (id nằm trong `ref`). `tests/salon/test_prompts.py` đang ghim câu chữ luật E, sửa theo.
- `CONTEXT.md` ghi F2 "so nguyên văn … cố ý, đừng nới"; mức C nới luật này theo quyết định
  của chủ dự án, nên cập nhật `CONTEXT.md` trong cùng thay đổi.

### 6. Chặn chữ nước ngoài (H) và lộ prompt / lọt tiếng Anh
- **`foreign_script`:** câu có chữ cái không thuộc bảng chữ Latin thì chặn. Chữ tiếng Việt có
  dấu thuộc Latin. Chuẩn hoá NFC trước, chỉ xét ký tự `isalpha()` (dấu rời có tên "COMBINING
  …", không phải "LATIN"), kiểm bằng tên Unicode của ký tự, không regex. Bắt được Armenia,
  Cyrillic, CJK, Thái…
- **`english`:** câu có một dãy dài từ tiếng Anh liền nhau thì chặn. Không định nghĩa "từ
  tiếng Anh" là "toàn chữ ASCII": rất nhiều từ tiếng Việt đúng là ASCII (em, anh, cho, mai,
  hay, con…), nên dễ chặn nhầm. Dùng một danh sách nhỏ từ chức năng tiếng Anh (the, is, you,
  to, of, and, for, with, this, that…): dãy liền nhau phải chứa đủ số từ trong danh sách đó.
  Người làm chọn ngưỡng; ngưỡng phải được test bằng các câu thật của các lần chạy.
  *(Quyết định của tôi, review Fable.)*
- **Lộ prompt** được `english` phủ: mọi prompt của dự án viết tiếng Anh (`test_prompts.py`),
  nên câu chép lại prompt là một dãy tiếng Anh. Không làm mã `prompt_leak` riêng; thay vào đó
  có test "chép một dòng prompt Orchestrator / agent con vào câu thì bị chặn". *(Quyết định của
  tôi.)*
- Các luật này kiểm trên chữ đã ghép, xử lý như các mã khác (viết lại 1 lần → câu mẫu).

### 7. Prompt Orchestrator
- Thay luật "Copy every time and date exactly as the `spoken` value…" bằng luật đoạn / tham
  chiếu. Nói gọn tự nhiên, tên dịch vụ viết thường như trong câu, đoạn tham chiếu chỉ chứa
  mention.
- Prompt viết tiếng Anh, không câu mẫu tiếng Việt (bẫy 17–19). Luật này là luật **định
  dạng**, tham chiếu đầu ra (id trong khối bối cảnh).
- Luật E (giờ `taken`), luật C (plain text) giữ nguyên ý.

### 8. Kịch bản test chạy thật
- Kịch bản B (`scripts/test_live_10_turns.py --keep-db`) **tự chọn giờ** để chạy nhiều lần
  trên DB cũ:
  - giờ "phải kín" lấy từ một lịch đang có của ngày mai;
  - lượt khách "chọn giờ" dùng một lựa chọn trong phiên chờ bot vừa mở (đọc từ
    `waiting_sessions`).
  - Kiểm điều kiện trước khi chạy vẫn giữ. Cần **hai** lịch khác nhau của ngày mai (lượt 2 và
    lượt 7 hỏi hai giờ kín khác nhau), không đủ thì dừng; không tự tạo lịch cho khách khác.
    *(Quyết định của tôi: chủ dự án muốn giữ DB cũ, chỉ thêm khách mới.)*
  - Giờ kín chỉ lấy từ lịch của **khách khác**. Lịch khách mới vừa đặt ở lượt 5 là
    `already_yours`, không phải `taken`. Giờ khách "gõ" viết như người thật gõ ("4 giờ chiều"),
    không chép `spoken`. Phiên chờ vừa mở mà không có lựa chọn nào thì script dừng và báo,
    không đoán giờ. Các giờ cố định hiện nay (`KEEP_DB_TURNS`, `check_keep_db_scenario`) bỏ.
    *(Quyết định của tôi, review Fable I6.)*

## Phương án dự phòng

Nếu chạy thật cho thấy lần viết lại **không sửa được** lỗi sai vai trò (như 6 câu "em đã
đặt" của bản thử), mà lượt rơi xuống câu mẫu vì `wrong_role`, thì chuyển sang **"lên kế hoạch
rồi mới nói"** (dialogue act → realization):
- lần gọi 1: model trả danh sách hành động (`báo_kín(t1)`, `mời(t2,t3)`, `xin_đồng_ý(p1)`);
  code kiểm danh sách này;
- lần gọi 2: model viết câu từ đúng danh sách; code kiểm như mục 4.

Ngưỡng chuyển do chủ dự án quyết khi xem transcript. Không làm trong đợt này.

## Ràng buộc (CONTEXT.md)
- Kiến trúc: `create_agent` + middleware, không `create_deep_agent`, không `interrupt()`,
  không stream; câu tới khách chỉ qua `complete` sau guard.
- Gói `salon/` không có regex (`tests/test_regex_budget.py`). Đọc JSON và chuỗi không cần
  regex.
- Không chạy Azure khi chưa hỏi chủ dự án. Đổi prompt hoặc luồng thì phải đọc transcript chạy
  thật (bẫy 20).
- Mọi ToolMessage và tool call vẫn phải hợp lệ với Azure (bẫy 13). Structured output không
  được để lại tool call treo trong thread.
- Commit message tiếng Anh; không add `docs/superpowers/review.md`, `image.png`,
  `scripts/terminal_chat.py`.

## Lưu ý kỹ thuật cho người làm
- `create_agent` lưu `structured_response` vào state; với model pydantic dựng động, serde của
  checkpointer ghi cảnh báo "Blocked deserialization" mỗi lần đọc (Fable đã thử), và nó không
  bao giờ bị xoá khi `create_agent` dựng không có `response_format` (factory dòng 1534). Dùng
  **schema dạng dict**. Guard parse `reply.text`, không đọc `state["structured_response"]`.
- `ProviderStrategy` ép `strict=True` cho cả schema của tool Orchestrator (factory 1.4.6,
  khoảng dòng 1452). **Đã kiểm** (bản thử c, cùng `convert_to_openai_tool(strict=True)`):
  schema 4 tool hiện có hợp strict. Tool thêm sau này cũng phải hợp: mọi trường bắt buộc,
  tuỳ chọn viết `X | None`, không giá trị mặc định.
- Khi lời gọi không có tool call, factory parse JSON trong `_handle_model_output`; parse hỏng
  thì nó ném `StructuredOutputValidationError`, lỗi này mang `ai_message`. Bắt nó ở
  `awrap_model_call` ngoài cùng (`ContextMiddleware`) và trả `ModelResponse(result=[exc.ai_message])`
  để guard ra `bad_structure`, không phải tự parse thay factory.

## Để người làm quyết
- Chỗ gắn `ProviderStrategy` (tham số `response_format` của `create_agent`, hay
  `request.override` trong middleware). Điều kiện bắt buộc: guard đọc được các đoạn, lời gọi có
  tool call vẫn chạy như cũ, và thread không bao giờ chứa tool call thiếu ToolMessage. Không
  thêm lần gọi riêng để định dạng câu cuối.
- Cách đánh số id cụ thể, và dữ kiện nào hiện ở đâu trong khối bối cảnh.
- Luật so khớp tên dịch vụ, và danh sách "từ đi liền" cho đoạn giá.
- Ngưỡng của `prompt_leak` và `english`.
- Tên mã guard mới.

## Tiêu chí xong
- Test viết trước (TDD): mỗi vai trò đúng / sai; chữ khớp / không khớp; chữ thường có giờ /
  giá / dịch vụ; ghép khoảng trắng; các luật mục 6, kèm câu thật "Եթե" của lần chạy 4; câu sai
  của lần chạy 2 lượt 2 và lần chạy 4 lượt 8 phải bị chặn; quyết định được báo bằng câu nói
  gọn vẫn được đánh dấu "đã báo"; câu mẫu vẫn được đánh dấu như cũ; JSON hỏng ra
  `bad_structure`, không ra câu báo lỗi của lượt. Toàn bộ pytest + vitest xanh.
- Chạy thật (hỏi chủ dự án trước): kịch bản A, và kịch bản B tự chọn giờ trên DB cũ. Đọc tay
  transcript theo các anti-goal ở trên.
- Ghi lại số lượt rơi xuống câu mẫu và số lần viết lại vì `wrong_role`. Số liệu này dùng để
  quyết có cần phương án dự phòng không.
