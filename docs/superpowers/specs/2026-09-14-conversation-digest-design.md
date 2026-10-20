# Tầng digest trong phiên — thiết kế

Ngày: 2026-09-14. Trạng thái: đã duyệt thiết kế, chờ plan.

## Vì sao làm bây giờ

Spec `2026-09-13-natural-conversation-design.md` QĐ-6 hoãn digest với điều kiện:
*"làm hết phần rẻ rồi đo; còn lặp thì mới xây, lúc đó có bằng chứng."* Ngày
2026-09-14 phần rẻ đã làm hết — luật `_NO_REPEAT` phủ cả diễn đạt khác, rule 4
viết hoa, trích câu đáp trước vào khối bối cảnh, thử temperature 0.6 — và trục
`lap_y` của rubric vẫn 2/5. Điều kiện mở đã thoả.

Mục tiêu (đã chốt với chủ dự án): **bớt lặp ý qua nhiều lượt và tiết kiệm
token của phiên dài**. KHÔNG nhắm chữa ca "hai câu từ chối liên tiếp y nhau" —
câu bị chép nằm ở lượt vừa rồi, vẫn được giữ nguyên văn theo thiết kế này.
Ca đó ghi ở `CONTEXT.md` (phần "Còn để ngỏ"), quyết định riêng.

Digest **không phải tầng 3** đã bỏ (ký ức ngữ nghĩa xuyên phiên, Mem0 +
pgvector). Nó là bản nén của tầng 2, sống trong phiên, cắt theo ngày, lưu trong
chính document `conversations`.

## Mẫu tham chiếu

Thiết kế theo mẫu chung của LangMem `SummarizationNode`, OpenAI Agents SDK
`CompactionSession`, Mastra `lastMessages`/`summarizeThread`, và bài
*Effective context engineering* của Anthropic:

- Giữ N lượt gần nhất nguyên văn; phần cũ hơn nén thành MỘT tin tổng hợp đặt
  trước lịch sử.
- Kích hoạt theo ngưỡng token, kiểm sau mỗi lượt, nén ngoài đường trả lời.
- Digest có cấu trúc: giữ quyết định và việc chưa xong, bỏ đầu ra tool thừa.
- Log gốc không đụng; biến đổi lúc đọc (đúng nguyên tắc "cắt lúc ĐỌC" sẵn có).
- Cầu chì khi nén hỏng liên tiếp.

Cảnh báo của ngành mà thiết kế phải tôn trọng: tóm tắt là **lossy khó đoán**
(Anthropic). Vì thế digest chỉ **bổ sung** khối bối cảnh, không thay: mọi dữ
kiện bền (danh tính, lịch sắp tới, ngày giờ, trạng thái tiệm) vẫn lấy từ DB
mỗi lượt. Digest chỉ mang **diễn biến hội thoại**.

## Mục 1 — Dữ liệu

Thêm trường vào `Conversation` (`app/models/conversation.py`). Document thiếu
trường = chưa có digest. Không migrate.

```python
class Digest(BaseModel):
    day: date                 # ngày VN digest thuộc về; sang ngày là bỏ
    covers_until: datetime    # created_at của tin CUỐI đã nén
    bullets: List[str]        # 3–8 dòng dữ kiện, mỗi dòng ≤ 120 ký tự, qua single_line
    updated_at: datetime
    failures: int = 0         # cầu chì

class Conversation(BaseDocument):
    ...
    digest: Optional[Digest] = None
```

Hằng số, một chỗ ở `app/services/digest.py`:

| Hằng | Giá trị | Ý |
|---|---|---|
| `KEEP_RECENT_TURNS` | 4 | 8 tin gần nhất luôn nguyên văn |
| `COMPACT_THRESHOLD_TOKENS` | 800 | phần ngoài cửa sổ và chưa phủ vượt mức này mới nén (đếm bằng `CHARS_PER_TOKEN`) |
| `MAX_BULLETS` | 8 | |
| `BULLET_MAX_CHARS` | 120 | |
| `MAX_FAILURES` | 3 | ngừng nén tới hết ngày |
| `DIGEST_TIMEOUT_SECONDS` | 8 | cùng mốc với parser (bẫy #10) |

## Mục 2 — Luồng nén (ghi)

Chạy **sau** khi `run_turn` đã phát `complete` và đã `append` hai tin của
lượt. Trong `events.py`:

```python
asyncio.create_task(DigestService(db).maybe_compact(user_id))
```

Không `await`. Mọi exception bên trong `maybe_compact` được bắt và log
(`digest_failed`); không bao giờ chạm sự kiện gửi cho khách.

`DigestService.maybe_compact(user_id)`:

1. Nạp tin **hôm nay** theo đúng luật cắt của `history()` (ngày VN + 30 phút
   gần nhất). Nạp digest hiện có; `day` khác hôm nay → coi như không có.
2. Tách: `recent` = 8 tin cuối; `older` = tin trước đó có
   `created_at > covers_until` (hoặc tất cả nếu chưa có digest).
3. Thoát sớm, không gọi LLM, nếu: `older` rỗng; hoặc token ước lượng của
   `older` < `COMPACT_THRESHOLD_TOKENS`; hoặc `failures >= MAX_FAILURES`.
4. Gọi LLM đúng một lần:
   `build_chat_model(tags=["digest"], temperature=0.0, streaming=False)`,
   timeout 8 giây. Prompt tiếng Anh. Đầu vào: bullets cũ + `older` (role và
   nội dung). Đầu ra: danh sách dữ kiện **gộp** cũ và mới, ≤ 8 dòng, tiếng
   Việt, không trích câu thoại, mỗi dòng một dữ kiện thuộc các loại:
   - dịch vụ khách muốn;
   - giờ/ngày đã đề nghị và khách đồng ý, từ chối hay còn lưỡng lự;
   - điều tiệm đã trả lời (giờ mở cửa, ngày nghỉ, bận/rảnh);
   - yêu cầu đã từ chối vì ngoài phạm vi hoặc về người khác;
   - việc còn dở.
   KHÔNG ghi trạng thái lịch (đã đặt/đã hủy) — thứ đó khối bối cảnh nói và
   thắng.
   Schema đóng (Pydantic `list[str]`). Validator **ép**: cắt số dòng về 8,
   mỗi dòng qua `single_line(…, 120)`. Ép, không từ chối (bài học
   `ParsedTime`: lớp dưới fail-soft thì validator nghiêm là cách đắt nhất
   để mất dữ liệu tốt).
5. Ghi `digest = {day, covers_until = created_at tin cuối của older, bullets,
   updated_at, failures=0}` bằng một `update_one` toàn trường.
   Lỗi ở bước 4 → `failures += 1`, giữ digest cũ nguyên.

Tag `digest` ≠ `respond` → token không bao giờ stream ra màn hình (bẫy #8).
Trace Langfuse mang tag riêng để đo chi phí.

## Mục 3 — Luồng đọc

`ConversationService` thêm `context_window(user_id) -> tuple[list[str], list[ChatMessage]]`:

- `history()` giữ nguyên chữ ký và hành vi.
- `context_window` = digest hôm nay (bullets, hoặc `[]`) + kết quả `history()`
  **lọc bỏ tin có `created_at <= covers_until`** khi digest tồn tại. Tin đã
  nén không gửi nguyên văn nữa — đây là chỗ tiết kiệm token.
- Ngân sách 1500 token vẫn áp cho phần nguyên văn; digest ngoài ngân sách đó
  (tối đa 8 × 120 ký tự ≈ 320 token).

`load_context` trả thêm `digest`; `GraphState` thêm `digest: List[str]`.

`agents.py` — thứ tự tin nhắn:

```
System prompt (tĩnh, cache)
→ [nếu digest khác rỗng] HumanMessage:
    "Diễn biến phần trước của cuộc trò chuyện hôm nay:\n- …\n- …"
→ tin nguyên văn gần nhất
→ khối bối cảnh
→ câu khách
```

Digest đứng ngay sau system prompt, **không** nhét vào khối bối cảnh: khối
bối cảnh đổi mỗi lượt, digest đổi vài lượt một lần — tách ra để phần đầu
prompt ổn định lâu hơn cho cache. Dòng tiêu đề là chuỗi cố định do code sinh
(tiếng Việt vì là dữ liệu, cùng lối với khối bối cảnh).

`_NO_REPEAT` thêm một vế tiếng Anh: *"Facts listed under the conversation
digest count as already said."*

Node `confirm` không đọc digest (không có LLM).

## Mục 4 — Lỗi và biên

| Tình huống | Xử lý |
|---|---|
| LLM nén lỗi/timeout | Giữ digest cũ, `failures += 1`, log. Lượt chat không bị ảnh hưởng |
| 3 lần hỏng liên tiếp | Ngừng nén tới hết ngày. Lịch sử nguyên văn vẫn theo ngân sách 1500 token — hệ **suy giảm về đúng hành vi hiện tại**, không tệ hơn |
| Sang ngày | `day` khác → không digest; tin cũ đã bị `history()` cắt |
| Nửa đêm 23:58 → 00:01 | Luật 30 phút của `history()` giữ tin; digest hôm qua bỏ. 8 tin gần nhất còn — đủ cho "ừ" |
| Digest mâu thuẫn khối bối cảnh | Dòng "tin phần trên" của khối bối cảnh thắng; prompt nén cấm ghi trạng thái lịch |
| Hai lượt chat song song (REL-01) | Nén là `update_one` toàn trường; lần sau ghi đè lần trước. Chấp nhận, không tệ hơn REL-01 |
| Tiêm prompt qua tin khách vào digest | Bullets qua `single_line` + cắt 120; prompt nén dặn ghi dữ kiện **về** khách, không chép mệnh lệnh. Bán kính vẫn chỉ là chính khách đó |
| Nén đang chạy khi tiến trình tắt | Task nền mất; lượt sau nén lại từ `covers_until` cũ. Không mất gì |

## Mục 5 — Test và đo

**pytest (không LLM, mock `build_chat_model`):**

- `maybe_compact` không gọi LLM khi: dưới ngưỡng; `failures >= 3`; không có
  tin ngoài cửa sổ; digest hôm nay đã phủ hết.
- Gọi đúng một lần khi vượt ngưỡng; `covers_until` = tin cuối của `older`;
  8 tin gần nhất KHÔNG nằm trong đầu vào nén.
- Bullets bị ép số dòng và độ dài, không ném lỗi; bullets cũ được đưa vào
  đầu vào lần nén sau.
- `context_window` bỏ tin `<= covers_until`, giữ tin sau; ngày khác → không
  digest và trả đủ tin.
- Thứ tự tin nhắn trong `agents.py`: digest ngay sau system, trước lịch sử —
  khoá cứng như bẫy #9. Không digest → thứ tự y như hiện tại.
- Nén hỏng → `failures` tăng, digest cũ giữ, `run_turn` vẫn phát `complete`
  với câu trả lời đầy đủ.
- `_NO_REPEAT` có vế digest ở cả ba prompt.

**Chạy thật (bẫy #18 — pytest không bắt được nhóm lỗi này):**

- Kịch bản `dai` 16 lượt là mốc. Sau lượt ~10 phải thấy digest (log
  `digest_compacted` + trace Langfuse tag `digest`).
- Chấm `score_transcript.py` so với `baseline-dai.txt`; trục cần nhìn là
  `lap_y`.
- Đọc tay lượt 7 *"chị đặt lúc mấy giờ vậy em nhắc lại giùm"*: khi câu đặt
  đã bị nén, bot phải trả lời từ khối bối cảnh (lịch sắp tới), không từ digest.
- Đo token đầu vào trung bình/lượt trước–sau trên Langfuse.

## Ngoài phạm vi

- Digest gấp dần nhiều tầng (memorycore): trần 30 tin/giờ và phiên cắt theo
  ngày không bao giờ tới mức cần.
- Digest xuyên ngày / xuyên phiên: đó là tầng 3, đã bỏ.
- Chữa lặp nguyên văn ở lượt kế tiếp (rule 4): cần can thiệp khác, ghi ở
  `CONTEXT.md`.
