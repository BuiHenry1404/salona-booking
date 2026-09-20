# Tool cấp dữ kiện, không viết sẵn câu — thiết kế

Ngày: 2026-09-20.

## Vì sao

Nguyên tắc: **tool là thứ cấp bối cảnh cho LLM, không phải chỗ viết sẵn câu trả
lời.** Hiện 5 trong 7 tool đang trả về câu tiếng Việt hoàn chỉnh, và prompt còn
bảo model chép lại (`SHOP_PROMPT`: *"Answer with what the tool returned"*).

Chạy thật 2026-09-19 cho thấy nó bị chép gần nguyên văn:

| Tool trả về | AI nói với khách |
|---|---|
| `Tiệm mở từ 8 giờ sáng đến 7 giờ tối. Mở cả tuần.` | *Tiệm mình mở từ 8 giờ sáng đến 7 giờ tối, mở cả tuần anh chị nhé.* |

Ba cái giá phải trả:

1. **Giọng chết cứng.** Câu thoại nằm trong `tools.py` nên mọi khách nghe y hệt
   nhau, và tầng `guard`/`rewrite` sinh ra để làm giọng tự nhiên không với tới
   được — nó chỉ soi câu cuối, trong khi khuôn câu đã bị tool áp đặt từ trước.
2. **Luật nằm hai nơi.** Tool nhét **mệnh lệnh** vào kênh dữ liệu:
   `"Hãy nhắc lại đầy đủ ngày giờ và hỏi khách xác nhận."`,
   `"Hãy hỏi khách chọn ngày khác."`, `"Hãy hỏi khách xác nhận trước khi hủy."`
   Đó là chỉ dẫn cho model trá hình thành kết quả tool. Chỉ dẫn thuộc system
   prompt (ổn định, được cache); để ở đây thì cùng một luật có hai bản, ở
   `prompts.py` và `tools.py`, rồi trôi lệch nhau mà không ai rà được.
3. **Câu lỗi rò ra khách.** `exc.message` của `AppError` được trả thẳng cho
   model. Những chuỗi đó viết cho **tầng REST** (router dịch sang HTTP), không
   viết cho lễ tân nói với khách.

`parse_time` đã làm đúng từ đầu, và lý do nằm sẵn trong code:

```python
# Trả JSON gọn thay vì câu tiếng Việt: agent cần chuỗi ISO nguyên vẹn
# để chuyển thẳng sang propose_appointment, không được diễn giải lại.
return parsed.model_dump_json(include={"start_at", "partial_date", "missing"})
```

Repo đã có đúng khuôn mẫu ở một chỗ và làm ngược ở năm chỗ còn lại. Đợt này
kéo cả năm về cùng một khuôn.

## Chỗ vướng phải giải trước

Không được trả ISO thô rồi để model tự viết giờ. Hai hàng rào đang dựa vào
việc tool cấp sẵn dạng đọc:

- `guard.check_clock` **cờ mọi chuỗi `HH:MM`** và mọi số viết bằng chữ
  ("mười lăm giờ"). Bắt model tự định dạng là tăng đúng thứ guard đang chặn,
  và mỗi lần cờ là một lượt `rewrite` — thêm một lời gọi LLM và ~2 giây.
- Luật "chép y giờ tool viết" trong `BOOKING_PROMPT`/`SHOP_PROMPT` giữ cho hai
  chỗ nói giờ (tool và khối bối cảnh) không lệch giọng nhau.

Nên hợp đồng mới cấp **cả hai**: giá trị máy đọc và dạng người đọc — nhưng
**không cấp câu**.

## Hợp đồng mới

Mọi tool trả **một chuỗi JSON**, không có chữ nào là lời thoại. Mốc thời gian
luôn là một object hai trường:

```json
{"iso": "2026-09-20T16:00:00+07:00", "spoken": "Chủ Nhật 20/9, 4 giờ chiều"}
```

`spoken` do `format_vi_datetime`/`format_vi_hhmm` sinh — y như hiện nay, nên
`guard.check_clock` vẫn an toàn và luật "chép y `spoken`" vẫn còn nguyên tác
dụng. Cái mất đi là **khuôn câu**, không phải dạng đọc.

### Từng tool

```jsonc
// get_shop_status
{"busy": false}
{"busy": true, "until": {"iso": "...", "spoken": "3 giờ rưỡi chiều"}}

// get_shop_hours
{"open": {"iso": "08:00", "spoken": "8 giờ sáng"},
 "close": {"iso": "19:00", "spoken": "7 giờ tối"},
 "closed_days": ["Chủ Nhật"]}        // [] = mở cả tuần

// find_free_slots
{"day": "2026-09-20", "slots": [{"iso": "...", "spoken": "..."}]}
{"day": "2026-09-20", "slots": []}                  // hết chỗ
{"error": "bad_day_format"}

// propose_appointment — thành công
{"held": {"iso": "...", "spoken": "..."}, "note": "móng bột",
 "replaces": {"id": "...", "was": {"iso": "...", "spoken": "..."}}}   // vắng nếu đặt mới
// propose_appointment — giờ đó không đặt được
{"error": "slot_unavailable",
 "nearest": [{"iso": "...", "spoken": "..."}]}       // [] khi cả ngày hết chỗ
{"error": "bad_time_format"}
{"error": "appointment_not_found"}                   // id dời lịch sai

// list_my_appointments
{"appointments": [{"id": "...", "at": {"iso": "...", "spoken": "..."}, "note": "..."}]}

// cancel_appointment
{"will_cancel": {"id": "...", "at": {"iso": "...", "spoken": "..."}}}
{"error": "appointment_not_found"}
{"error": "not_your_appointment"}

// parse_time — GIỮ NGUYÊN, đã đúng khuôn
```

### Mã lỗi, không phải câu lỗi

`AppError.message` **không đổi** — nó phục vụ tầng REST và frontend. Tầng tool
ánh xạ sang mã:

| Exception | mã |
|---|---|
| `NotFoundError` | `appointment_not_found` |
| `ForbiddenError` | `not_your_appointment` |
| còn lại | `unavailable` |

Ánh xạ theo **lớp exception**, không theo chuỗi `message` — chuỗi đó đổi lúc
nào không ai báo.

### Mệnh lệnh chuyển về prompt

Ba câu chỉ dẫn đang nằm trong `tools.py` chuyển thành luật trong
`BOOKING_PROMPT`, nói theo hợp đồng JSON mới. Chúng **không** được ở lại dưới
bất kỳ dạng nào trong `tools.py`.

## Cái KHÔNG đụng

- **`parse_time`** — đã đúng khuôn từ đầu.
- **`confirm.py` và `FALLBACK_ANSWER`** — câu cứng do code viết, đi thẳng tới
  khách, `answer_source: "code"`. Đó là **có chủ ý**: chúng là đường lùi khi
  LLM hỏng, và là chốt "câu chốt lịch nói bằng số liệu code cấp". Khác hẳn
  chuyện tool viết sẵn câu cho LLM chép.
- **Khối bối cảnh** (`build_context_block`) — nó là dữ liệu code dựng, không
  phải tool, và đã đúng vai.
- **`AppError.message`** — của tầng REST.
- **`guard.check_clock`** và luật viết giờ — giữ nguyên, hợp đồng mới cố ý
  thiết kế để chúng còn hiệu lực.

## Rủi ro và cách chặn

| Rủi ro | Chặn bằng |
|---|---|
| Model tự viết giờ sai dạng khi không còn câu mẫu | `spoken` vẫn được cấp; luật "chép y `spoken`" giữ nguyên; `guard.check_clock` là lưới cuối |
| Model đọc nhầm JSON, nói lẫn khoá `iso` ra cho khách | Test hàng rào: không tool nào trả chuỗi có dấu `:` kiểu giờ ngoài `spoken`; và chạy thật soi transcript |
| Giọng xấu đi vì mất câu mẫu | Đây là **mục tiêu**, không phải rủi ro — nhưng phải đo bằng chạy thật, không bằng pytest |
| Prompt và tool trôi lệch lần nữa | Test đọc `tools.py` và khẳng định không còn chuỗi mệnh lệnh (`"Hãy "`) |

## Kiểm thử

| Loại | Nội dung |
|---|---|
| Từng tool | Trả về parse được bằng `json.loads`; đúng khoá; `spoken` khớp `format_vi_datetime` |
| Mã lỗi | `NotFoundError` → `appointment_not_found`; `ForbiddenError` → `not_your_appointment`; ánh xạ theo LỚP, có test cho việc đổi `message` không làm đổi mã |
| Hàng rào | `tools.py` không còn chuỗi chứa `"Hãy "`; không tool nào (trừ `spoken`) trả chuỗi tiếng Việt thành câu |
| Prompt | Ba luật vừa chuyển từ tool sang phải có mặt trong `BOOKING_PROMPT` |
| Hồi quy | `test_tools.py` (505 dòng) sửa theo hợp đồng mới — **đổi assert, không nới assert** |
| Chạy thật | `LLM-32` chạy lại, đối chiếu trước/sau. **Bắt buộc**: đây là đổi thứ model đọc mỗi lượt, pytest không chấm được giọng |

Chạy thật là hàng rào thật của đợt này. Ba lỗi nặng nhất của dự án đều không
làm test nào đỏ, và riêng đợt trước kịch bản `LLM-32` đã hai lần trở thành bảo
đảm giả.

## Nhánh

`refactor/tools-return-data`, rẽ từ `fix/live-run-findings`.

Xếp chồng ba nhánh chưa merge: `feat/conversation-state` (PR #7) →
`fix/live-run-findings` → nhánh này. Chấp nhận vì đợt này đọc và sửa chính
những câu prompt mà hai nhánh kia vừa đổi; rẽ từ `henry/develop` sẽ xung đột.
Người dùng quyết cách gộp lúc merge.
