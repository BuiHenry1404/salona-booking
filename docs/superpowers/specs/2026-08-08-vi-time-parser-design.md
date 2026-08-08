# Parser thời gian tiếng Việt

Bổ sung cho [spec app đặt lịch nail–tóc](2026-08-06-booking-nail-toc/README.md), phần [Agent](2026-08-06-booking-nail-toc/04-agent.md).

## Vì sao cần

Tool đặt lịch nhận `day` dạng `YYYY-MM-DD` và `start_at` dạng ISO 8601, nhưng khách nói "mai 3h chiều". Hiện tại BookingAgent vừa phải quy ngày, vừa phải tra lịch, vừa phải soạn câu trả lời trong cùng một lượt. Việc quy ngày vì thế không có chỗ nào kiểm tra được và không có chỗ nào nhìn thấy được.

Kiểu hỏng nguy hiểm nhất không phải lệch một giờ mà là **lệch cả năm**: model không chắc hôm nay là ngày nào thì nó suy từ mốc thời gian trong dữ liệu huấn luyện, và `2025-08-08` là một chuỗi hoàn toàn hợp lệ. Tool không có cách nào biết đó là ngày sai.

Tách việc quy ngày ra một đơn vị riêng để nó test được, log được, và có một lớp kiểm tra bằng code đứng sau.

## Phạm vi

Trong phạm vi: quy câu nói về thời gian của khách ra một thời điểm chuẩn, hoặc nói rõ còn thiếu thông tin gì.

Ngoài phạm vi: trích việc làm ("làm tóc"), hỏi khách, đọc lịch sử chat, đọc memory, kiểm giờ mở cửa.

## Đường đi

```
text ─→ [1] regex khớp trọn vẹn? ──có──→ ┐
              │không                     │
              ↓                          │
        [2] LLM structured output ──────→ ┤
                                         ↓
                                   [3] lớp chốt bằng code
                                         ↓
                                     ParsedTime
```

Regex và LLM là hai nguồn đề xuất. Lớp chốt là chỗ **duy nhất** được phép nói kết quả dùng được.

## Hợp đồng

File mới `app/agents/booking_graph/timeparse.py`:

```python
class ParsedTime(BaseModel):
    start_at: Optional[datetime] = None   # luôn có offset khi khác None
    missing: List[str] = []               # thiếu gì, viết bằng tiếng Việt
    partial_date: Optional[date] = None   # biết ngày, chưa biết giờ
    source: Literal["regex", "llm"] = "llm"   # để đọc log, agent không thấy


async def parse_vi_time(text: str, now: datetime) -> ParsedTime
```

`now` là **tham số**, không gọi `now_utc()` bên trong. Không truyền mốc vào thì không test được "mai" — và thiếu mốc thời gian đúng là lỗi đã có sẵn trong khối bối cảnh trước khi sửa.

## Tầng 1 — regex

Luật, ghi thành docstring để không ai nới ra sau này:

> **Chỉ trả kết quả khi khớp trọn vẹn: có từ chỉ ngày VÀ có giờ xác định. Khớp một phần coi như không khớp.** Không đoán, không điền thiếu, không trả `missing`. Thiếu bất cứ thứ gì thì trả `None` và nhường cho LLM.

```
Ngày:  hôm nay | mai | ngày mai | ngày kia | mốt
Giờ:   <số> h|g|giờ  +  sáng|trưa|chiều|tối
       hoặc <số>h dạng 24 giờ (13h–23h)
```

| Câu | Kết quả |
|---|---|
| "mai 3h chiều" | `2026-08-08T15:00+07:00` |
| "hôm nay 10 giờ sáng" | `2026-08-07T10:00+07:00` |
| "ngày kia 15h" | `2026-08-09T15:00+07:00` |
| "3h chiều" | nhường — thiếu ngày |
| "sáng mai" | nhường — thiếu giờ |
| "thứ Năm" | nhường — tuần này hay tuần sau là phán đoán |
| "mùng 2", "cuối tuần" | nhường |

Tập mẫu đóng và nhỏ là điều kiện để luật trên giữ được. Regex mà bắt đầu suy luận thì nó thành parser thứ hai làm được một nửa: bắt "mai 3h chiều" đúng, rồi ai đó thêm mẫu "thứ Năm", rồi mẫu đó nuốt luôn "thứ Năm tuần sau" và trả sai ngày — mà LLM không bao giờ được gọi để sửa. Hỏng kiểu này im lặng.

## Tầng 2 — LLM

Một lượt gọi model riêng, `with_structured_output(ParsedTime)`. Prompt chỉ có mốc thời gian hiện tại, giờ mở cửa, và vài ví dụ tiếng Việt. Không lịch sử chat, không memory, `temperature=0`.

Model dựng với `tags=["timeparse"]` và `streaming=False`.

**Đây là chỗ dễ sai đáng nói riêng.** Bộ phát sự kiện chỉ chuyển tiếp token mang tag `respond`. Nếu model của parser lỡ mang tag đó, khách sẽ thấy `{"start_at": "2026-08-...` chạy ngang giữa cuộc trò chuyện. Việc này cần thêm tham số `streaming` cho `build_chat_model` — hiện đang hardcode `True`.

Câu mơ hồ thì trả về thiếu gì, không đoán:

```json
{"start_at": null, "partial_date": "2026-08-08", "missing": ["giờ cụ thể"]}
{"start_at": null, "missing": ["sáng hay chiều"]}
```

Parser **không** tự hỏi khách. Hỏi bằng câu gì là việc của BookingAgent — nó mới biết đang xưng hô với ai.

## Tầng 3 — lớp chốt

`_guard(candidate, now) -> ParsedTime`, hàm thuần, không mạng, không I/O.

| Tình huống | Xử lý |
|---|---|
| `start_at` không có offset | Gắn giờ Việt Nam |
| `start_at` đã qua | Bỏ, `missing: ["ngày khác — giờ đó qua mất rồi"]` |
| `start_at` ngoài `(now, now+90 ngày]` | Bỏ, `missing: ["ngày gần hơn"]` |
| Còn lại | Cho qua |

Cửa sổ 90 ngày là thứ bắt được lỗi lệch năm. Một tiệm nail không ai đặt trước ba tháng.

**Cố ý không kiểm giờ mở cửa ở đây.** `AppointmentService` đã từ chối giờ ngoài `shop_hours` và `find_free_slots` đã lọc. Kiểm thêm ở parser nghĩa là giờ mở cửa nằm ở hai chỗ, đổi một chỗ quên chỗ kia.

## Tool

Trong `make_booking_tools`:

```python
@tool
async def parse_time(text: str) -> str:
    """Quy câu nói về thời gian của khách ra ngày giờ chuẩn.
    Gọi tool này TRƯỚC find_free_slots và create_appointment,
    mỗi khi khách nhắc tới thời gian. Không tự tính ngày."""
```

Trả JSON gọn:

```json
{"start_at": "2026-08-08T15:00:00+07:00"}
{"start_at": null, "partial_date": "2026-08-08", "missing": ["giờ cụ thể"]}
```

Không cấp cho StatusAgent — "chủ tiệm rảnh không" chẳng có gì để parse.

Ánh xạ ở frontend:

| Tool | Đang chạy | Xong |
|---|---|---|
| `parse_time` | Đang xem lịch… | Đã xem lịch |

Không dịch thành "Đang phân tích thời gian": với một cụ 70 tuổi câu đó vô nghĩa, mà thực chất khách chỉ cần biết máy đang làm việc.

## Xử lý lỗi

Cùng khuôn fail-soft với `recall()`:

```
LLM lỗi hoặc quá 2 giây
  → ParsedTime(start_at=None, missing=["giờ cụ thể"])
  → BookingAgent hỏi lại "Dạ cô chú muốn ngày nào, mấy giờ ạ?"
```

Không bao giờ ném, không bao giờ đoán. Chậm hay hỏng thì khách mất thêm một lượt hỏi đáp — chấp nhận được. Đặt nhầm giờ thì không.

## Kiểm thử

| Tầng | Cách test | Chạy khi nào |
|---|---|---|
| `_guard` | Hàm thuần, bảng ca kiểm | Luôn |
| Regex | Bảng ca kiểm, không gọi LLM | Luôn |
| LLM | Model giả trả JSON dựng sẵn | Luôn |
| LLM thật | Bảng câu tiếng Việt thật, `@pytest.mark.llm` | Chỉ khi gọi tay |

Phần quan trọng nhất của bảng regex là **ca âm** — những câu regex phải nhường. Ai nới regex ra sau này thì chính mấy ca đó đỏ lên trước.

Bảng LLM thật mặc định bị loại khỏi `pytest` (`-m "not llm"` trong `pytest.ini`). Nó tốn tiền và phụ thuộc mạng; để trong CI là có ngày build đỏ vì Azure nghẽn chứ không phải vì code sai.

## Ảnh hưởng tới chi phí và độ trễ

Câu không nhắc tới thời gian: **không đổi**, vẫn 2 lượt LLM. Parser là tool, chỉ gọi khi cần.

Câu có nhắc thời gian và khớp regex: **không đổi** — không gọi LLM nào thêm.

Câu có nhắc thời gian, không khớp regex: thêm một lượt LLM prompt ngắn, nằm trong vòng lặp tool sẵn có của subagent (`MAX_TOOL_ROUNDS = 4`).

## Không đổi

- Khối bối cảnh vẫn giữ dòng "Bây giờ là…". Parser là đường riêng, nhưng agent vẫn cần biết hôm nay để nhắc lại ngày cho khách nghe.
- `AppointmentService` không đổi một dòng nào.
- `_parse_local` trong `tools.py` **giữ lại**. Lớp chốt đã gắn múi giờ, nhưng `create_appointment` vẫn nhận chuỗi từ model — nó có thể bỏ qua `parse_time` rồi tự dựng ISO. Ba dòng để chặn một `TypeError` không nằm trong nhánh bắt lỗi nào.

## Những file phải sửa

| File | Sửa gì |
|---|---|
| Plan 2 · `task-01-llm-and-langfuse.md` | `build_chat_model(..., streaming: bool = True)` |
| Plan 2 · **file mới** `task-04b-timeparse.md` | Toàn bộ parser |
| Plan 2 · `task-05-tools.md` | Thêm tool `parse_time` |
| Plan 2 · `task-06-supervisor.md` | `BOOKING_PROMPT` quy tắc 5 |
| Plan 2 · `README.md` | Chèn task mới vào bảng thứ tự |
| Plan 4 · `task-03-agent-stream.md` | Bảng ánh xạ thêm `parse_time`; test đang chốt đúng 5 tool phải thành 6 |
| Spec · `04-agent.md` | Bảng tool thêm một dòng |
| Spec · `05-frontend.md` | Bảng ánh xạ thêm một dòng |

Đánh số `04b` thay vì chèn `05` rồi dồn cả loạt: task 5–10 được tham chiếu chéo nhiều chỗ, đổi số là phải sửa hết và dễ sót.
