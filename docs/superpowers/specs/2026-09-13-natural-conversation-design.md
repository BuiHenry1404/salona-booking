# Làm hội thoại tự nhiên hơn — Design

**Ngày:** 2026-09-13
**Trạng thái:** đã duyệt thiết kế, chờ plan
**Nhánh dự kiến:** `feat/natural-conversation`

## Vấn đề

Sau khi định tuyến lại agent (PR #5), hội thoại hết đọc thuộc lòng nhưng vẫn
nghe như **nhân viên đọc từ màn hình**. Bằng chứng lấy từ một cuộc 16 lượt chạy
thật ngày 2026-09-13 với tài khoản "Cô Thắm" (`gpt-5.4-mini` qua Azure):

**Lỗi 1 — nói lại thứ vừa nói.** Lượt 2 và lượt 3 đều vào node `shop`:

```
L2  BOT: Dạ chị Thắm, tiệm mình mở từ 8 giờ sáng đến 7 giờ tối ạ. Mở cả tuần
         luôn chị nhé.
L3  BOT: Dạ chị Thắm, chủ nhật tiệm vẫn mở bình thường ạ. Tiệm mở từ 8 giờ
         sáng đến 7 giờ tối ạ.
```

**Lỗi 2 — giọng biểu mẫu.** *"em giữ chỗ … **cho dịch vụ làm tóc**"*. Không ai
trong tiệm nói "dịch vụ".

Đã kiểm: ở lượt 3 lịch sử **không hề bị cắt** (dùng ~150 trên ngân sách 1500
token), nên model có nhìn thấy câu nó vừa nói. Đây không phải lỗi thiếu dữ liệu.

## Chẩn đoán

`docs/superpowers/specs/2026-08-06-booking-nail-toc/04-agent.md:103` đã chốt sẵn
thứ tự chẩn đoán lỗi lặp:

> Khi gặp lỗi lặp, kiểm theo thứ tự: lịch sử có đủ và đúng cặp chưa → prompt đã
> dặn chưa → model có quá nhỏ không.

Chạy hết ba bước:

| Bước | Kết quả |
|---|---|
| Lịch sử đủ chưa | Đủ ở ca lỗi. Nhưng `history()` **không giữ trọn cặp hỏi–đáp** như spec dòng 99 đòi — lỗi tiềm ẩn, chưa gây ra ca này |
| Prompt đã dặn chưa | **Chưa.** Luật hiện tại cấm lặp `verbatim` (nguyên văn); L3 lặp **ý**, khác chữ, nên luật không chạm tới |
| Model có nhỏ không | `gpt-5.4-mini`. Không phải tầng nhỏ nhất |

Thêm một nguyên nhân thứ tư, tìm được khi đọc lại cách ghép prompt.

### Khối bối cảnh đóng vai khách và đứng cuối

`agents.py:31-35` ghép ra thứ tự này (`events.py:68` đưa câu hỏi mới vào cuối
`state["messages"]`):

```
[SYSTEM]  SHOP_PROMPT
[KHÁCH]   … lịch sử …
[KHÁCH]   chủ nhật có nghỉ không em          ← câu thật
[KHÁCH]   Bây giờ là Chủ Nhật 13/9, …        ← khối bối cảnh
          Gọi khách là: chị Thắm.
          Chủ tiệm: đang rảnh.
          Nếu có gì trong cuộc trò chuyện mâu thuẫn với phần trên, hãy tin
          phần trên.
```

Thứ **cuối cùng** model đọc trước khi sinh chữ là một bản báo cáo trạng thái,
lại mang vai `HumanMessage`. Model bắt chước giọng người đối thoại, nên nó sinh
ra giọng biểu mẫu. Đây là nguồn của lỗi 2.

**Đây là một chỗ code trôi khỏi thiết kế, không phải chủ ý.** `CONTEXT.md` bẫy #9
chốt thứ tự là *"System → lịch sử → **bối cảnh → tin mới**"*, và test tên
`test_context_block_is_the_last_message_before_the_question` cũng nói vậy —
nhưng assertion của nó (`contents[-1]`) khoá chặt đúng hành vi đã trôi.

Lý do "prompt caching" trong docstring **không biện minh được cho vị trí cuối**:
lịch sử vốn đổi mỗi lượt, nên tiền tố cache được chỉ có `SystemMessage`. Dời
khối bối cảnh lên trước câu hỏi không mất gì.

## Mục tiêu

Hội thoại nghe **tự nhiên như người**, đo được bằng rubric chứ không bằng cảm
tính.

Không nằm trong phạm vi: luồng đổi lịch, xác nhận trước khi hủy (hai vấn đề
nghiệp vụ ghi nhận ở đánh giá 2026-09-13, để riêng).

## Quyết định

### QĐ-1: Sửa thứ tự tin nhắn

```python
history, question = state["messages"][:-1], state["messages"][-1:]
messages = [
    SystemMessage(content=prompt),
    *history,
    HumanMessage(content=state.get("context_block", "")),
    *question,
]
```

Cắt lát chịu được `messages` rỗng. Lời khách là thứ cuối model đọc.

Test `test_context_block_is_the_last_message_before_the_question` sửa assertion
cho **khớp đúng tên nó**: bối cảnh ở `[-2]`, câu khách ở `[-1]`.

### QĐ-2: Luật chống lặp dùng chung, không ví dụ tiếng Việt

Thêm hằng dùng chung cạnh `_VIETNAMESE_ONLY`, đưa vào cả ba prompt sinh câu cho
khách (`SHOP_PROMPT`, `BOOKING_PROMPT`, `SOCIAL_PROMPT`):

```python
_NO_REPEAT = """DO NOT REPEAT YOURSELF:
Never tell the customer something you already told them earlier in this
conversation — not in the same words, and not the same fact reworded.
Answer only what they just asked; what you already said still stands.
When they ask a follow-up about a topic you have already covered — for
instance asking about one particular day after you have already given the
full opening hours — answer ONLY the new part. Do not restate the facts
from your earlier reply."""
```

Gỡ chữ `verbatim` khỏi luật cũ trong `BOOKING_PROMPT`.

**Luật phải vào `SHOP_PROMPT`** — ca lỗi thật nằm ở node `shop`, không phải
`booking`.

### QĐ-3: `history()` giữ trọn cặp hỏi–đáp

Vòng lặp hiện tại đi từ tin mới nhất lùi về và `break` khi hết ngân sách, nên
có thể **giữ câu trả lời mà bỏ mất câu hỏi sinh ra nó**. Spec dòng 99 đòi giữ
trọn cặp.

Sửa: sau khi gom xong, nếu tin cũ nhất còn giữ có `role == "assistant"` thì bỏ
luôn nó.

### QĐ-4: Lọc `full_name`

`user.full_name` không có `max_length`, không lọc xuống dòng
(`app/models/user.py:28`), mà nó đi thẳng vào khối bối cảnh — một tin vai
`HumanMessage` mà dòng cuối của nó tự nói *"hãy tin phần trên"*. Khách đăng ký
tên có xuống dòng là ghi được luật của riêng họ vào đó.

Đặt validator **trên model `User`**, không phải trên schema tạo user: như vậy
dữ liệu đã nằm trong DB cũng được làm sạch lúc đọc lên. Gộp mọi khoảng trắng
(kể cả `\n`, `\r`, tab) thành một dấu cách, cắt còn 60 ký tự.

agentbox chống đúng thứ này bằng cách dán nhãn `untrustedMemoryLabel` lên mọi
mục sinh từ ký ức (`memorycore/runtime.go:537`). Ta chọn cách rẻ hơn là làm
sạch tại nguồn, vì khối bối cảnh của ta **hoàn toàn do code dựng** — `full_name`
là lối vào duy nhất.

### QĐ-5: Bỏ câu mẫu tiếng Việt khỏi prompt

**Đây là quyết định của người dùng ngày 2026-09-13, đi ngược ba ghi chép cũ.**
Ghi lại đầy đủ để lần sau đọc không tưởng là sơ suất:

- `prompts.py` docstring: *"Giọng 'em — anh/chị' giữ được là nhờ mấy câu mẫu
  này, không phải nhờ dòng mô tả."*
- `CONTEXT.md` bẫy #15: *"câu mẫu giữ tiếng Việt"*
- `CONTEXT.md` bẫy #16: *"Luật prompt chung chung không ăn, phải kèm ví dụ"* —
  đo được ở đợt rà 2026-08-23

Rủi ro đã nêu trước khi quyết: luật bị phớt lờ, giọng trôi khỏi "em — anh/chị",
model đáp bằng tiếng Anh. Vì vậy QĐ-5 được **tách thành bước đo riêng** (xem
"Cách đo").

**Ranh giới.** Từ tiếng Việt **là chủ thể của luật** thì giữ — `em`, `anh`,
`chị`, `con`, `cô`, `chú`, `bác` trong khối VOICE không minh hoạ cho gì cả,
chúng chính là thứ luật nói tới. Bỏ là **câu mẫu**: câu tiếng Việt hoàn chỉnh
dựng sẵn cho model chép theo.

| Chỗ | Bỏ | Thay bằng |
|---|---|---|
| `SHOP_PROMPT` r2 | `Say: "xong lúc 3 giờ rưỡi chiều ạ"` | mô tả: state the absolute finish time |
| `SHOP_PROMPT` r3, `BOOKING` r6 | `"3 giờ chiều"…`, `Never write "15:00"` | mô tả: spoken words, hour plus part of day, never digits with a colon |
| `BOOKING` r1 | `("mai", "thứ Năm tuần sau")` | any time expression |
| `BOOKING` r3 | `("lúc nào vắng thì xếp em"…)` | says the salon may choose |
| `BOOKING` r4 | `("khách đặt lúc 3 giờ là ai"…)` | asks about another customer |
| `BOOKING` r5 | `"Dạ anh chị chọn giờ nào ạ — …"` | a shorter sentence covering only the choice |
| `SOCIAL` r2 | `- "chào em" -> …`, `- "cảm ơn em nhé" -> …` | a greeting … / a thank-you or goodbye … |
| `SOCIAL` r3 | câu từ chối mẫu | phạm vi nói bằng tiếng Anh: you only handle hair and nail appointments |
| `SUPERVISOR` | `("chị muốn làm tóc", "em làm nail nha")` | wanting a service IS wanting an appointment |

**Giữ nguyên văn:** câu bảo mật ở `BOOKING` r4 —
*"Dạ em chỉ xem và đặt lịch cho chính anh chị thôi ạ. Anh chị cần đặt lịch hay
xem lịch của mình không ạ?"*. Prompt bắt model đáp **đúng chuỗi đó**; nó là đầu
ra bắt buộc, không phải ví dụ. Bỏ nó là phá luật bảo mật và test đang canh.

**Docstring của tool giữ nguyên ví dụ tiếng Việt** — người dùng chốt phạm vi
chỉ gồm prompt.

**Rủi ro đã lượng hoá được:** ví dụ trong `SUPERVISOR_PROMPT` chính là thứ đưa
probe từ 1/14 về 0/14 ở PR #5. Bỏ nó nhiều khả năng làm probe lệch lại. Plan
**phải** chạy `probe_supervisor.py` sau QĐ-5 và báo cáo con số.

### QĐ-6: KHÔNG làm digest ở giai đoạn này

Đã cân nhắc một tầng digest gấp dần như `memorycore` của agentbox, và **hoãn**:
spec 04-agent.md dòng 103 chốt chống lặp là việc của tầng 2, và bước "prompt đã
dặn chưa" chưa từng được thử (luật chỉ cấm lặp `verbatim`). Làm hết phần rẻ rồi
đo; còn lặp thì mới xây digest, lúc đó có bằng chứng.

Ghi rõ để khỏi hiểu nhầm: **digest không phải "tầng 3"** mà `CONTEXT.md` đã bỏ.
Tầng 3 bị bỏ là ký ức **ngữ nghĩa xuyên phiên** (Mem0 + pgvector, nhớ sở thích
khách). Digest trong phiên, cắt theo ngày, lưu trong chính document
`conversations` thì không dính lý do nào trong ba lý do bỏ tầng 3 (kéo theo
Postgres / lộ ký ức chéo khách / không tool nào cần sở thích khách). Nó là
**bản nén của tầng 2**.

## Cách đo

`CONTEXT.md` bẫy #18: pytest không bắt được nhóm lỗi này — từng có 413 test
xanh trong khi bot đang lặp câu với khách thật.

### Công cụ

**Kịch bản `dai`** thêm vào `SCENARIOS` của `scripts/chat_e2e_transcript.py`:
16 lượt **nguyên văn** cuộc đã chạy ngày 2026-09-13, để lần chạy sau so được
với lần trước. Kịch bản đi qua mọi nhánh: social chào, shop giờ mở cửa, shop
ngày nghỉ, booking hỏi ngày, booking ghép ngữ cảnh, confirm đồng ý, tra lịch,
chặn bảo mật, đổi giờ, confirm rút lui, shop bận/rảnh, social từ chối, hủy
lịch, social tạm biệt.

**`scripts/score_transcript.py`**: đọc file transcript, đưa cho LLM chấm theo
rubric, in điểm. Prompt chấm viết bằng tiếng Anh. Năm trục, mỗi trục 1–5 kèm
một câu lý do, cộng một điểm tổng:

| Trục | Bắt cái gì |
|---|---|
| `lap_y` | Nói lại thông tin đã nói ở lượt trước |
| `giong_may` | "dịch vụ", liệt kê, giọng biểu mẫu |
| `hoi_lai_da_biet` | Hỏi thứ khách đã trả lời |
| `xung_ho` | Nhất quán, không lọt cô/chú/bác |
| `tu_nhien` | Cảm nhận tổng thể |

### Trình tự đo

QĐ-5 có rủi ro đi ngược mục tiêu, nên nó được đo tách khỏi phần còn lại:

```
mốc "trước"  →  QĐ-1 + QĐ-3 + QĐ-4  →  ĐO lần 1  →  QĐ-5  →  ĐO lần 2
```

Mốc "trước" đã có sẵn: transcript 16 lượt chạy ngày 2026-09-13. Chấm nó là ra
baseline, không cần chạy lại.

QĐ-2 đi cùng lần đo 1 (nó không bỏ ví dụ nào — hằng `_NO_REPEAT` vốn không có
câu mẫu tiếng Việt).

Nếu ĐO lần 2 tụt so với lần 1, báo cáo số liệu để người dùng quyết giữ hay bỏ
QĐ-5 — không tự ý lùi.

### Chốt bằng pytest những gì tất định được

- QĐ-1: thứ tự tin nhắn (`FakeModel`, đã có sẵn)
- QĐ-3: cắt lịch sử không để lại `assistant` mồ côi
- QĐ-4: tên có xuống dòng / quá dài bị làm sạch
- QĐ-2: hằng `_NO_REPEAT` có mặt trong cả ba prompt
- QĐ-5: prompt **không** chứa câu mẫu tiếng Việt, **trừ** câu bảo mật

Hiệu quả thật của QĐ-2 và QĐ-5 do rubric đo, không do pytest.

## Tài liệu phải sửa theo

Không sửa thì người sau sẽ khôi phục lại đúng thứ ta vừa bỏ:

| File | Sửa gì |
|---|---|
| `app/agents/booking_graph/prompts.py` | Docstring module đang nói "câu mẫu phải giữ tiếng Việt" — viết lại theo QĐ-5, kèm ngày |
| `CONTEXT.md` bẫy #9 | Ghi rõ thứ tự đã được sửa cho khớp tài liệu |
| `CONTEXT.md` bẫy #15, #16 | Ghi QĐ-5 và kết quả đo |
| `tests/test_prompts.py` | `TestExamplesStayVietnamese` đổi sang canh chiều ngược lại |

## Rủi ro

| Rủi ro | Mức | Giảm thiểu |
|---|---|---|
| Bỏ ví dụ làm luật hết tác dụng (bẫy #16) | Cao | Đo tách riêng ở lần 2; có số liệu thì người dùng quyết |
| Bỏ ví dụ làm probe supervisor lệch lại | Trung bình | Chạy `probe_supervisor.py` sau QĐ-5, báo con số |
| Bỏ ví dụ làm model đáp tiếng Anh | Trung bình | `_VIETNAMESE_ONLY` vẫn nằm ở đầu và cuối mỗi prompt; rubric có trục `tu_nhien` |
| Đổi thứ tự làm hỏng prompt caching | Thấp | Tiền tố cache được chỉ là `SystemMessage`, không đổi |
| QĐ-3 làm mất ngữ cảnh ở hội thoại dài | Thấp | Chỉ bỏ thêm tối đa một tin; ngân sách 1500 token chưa từng chạm ở 16 lượt |
