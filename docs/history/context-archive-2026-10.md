# CONTEXT — phần lưu trữ (tách khỏi CONTEXT.md ngày 2026-10-26)

Những mục dưới đây là **biên bản**, không phải mô tả hiện tại. Luồng chat ở đây là
bản trước bộ phân tích câu khách (Task 12b) và nút xác nhận (M1); bản hiện tại xem
`docs/research/Architech.md` và sơ đồ https://claude.ai/artifact/4z1oYjFe8hJr47rFHdBx1s.
Phép đo bằng script chat thật gọi Azure — đã dừng chạy benchmark từ 26/10 (tốn phí).

## Một lượt chat đi qua đâu

```
Khách ──socket──▶ run_turn
                    │
     load_context (code, 3 truy vấn song song): trạng thái tiệm · lịch sắp tới
     [id + iso] · summary + slots + covers_until · câu đáp trước
                    │
     pick_thread: thread <user>:<ngày VN> (nối thread hôm qua qua nửa đêm);
     lịch sử model đọc từ thread, cắt bằng model_history
                    │
     thread đang chờ (interrupt)? ──có──▶ Command(resume=câu khách) ─▶ confirm ┐
                    │ không (input mới, proposal_id=None)                      │
   START ─▶ supervisor (LLM, 1 chữ) ─▶ booking | shop | social                  │
                                          │ (booking: tool tạo đề xuất →        │
                                          │  proposal_id qua ConfirmMiddleware) │
   trong MỖI agent (middleware, M4):
     PhraseMiddleware (before_agent): có confirm_fact → câu chốt thay model, nhảy tới cuối
     model ⇄ tool (thử lại model 1 lần; timeout 20 giây mỗi lần gọi)
     GuardMiddleware (after_agent, chạy SAU CÙNG): xoá tên tool → 3 phép kiểm (0 LLM)
       ──vi phạm──▶ rewrite (1 lần) ─▶ kiểm lại (+content) ─▶ answer
                    │
     agent ──answer──▶ record (câu đáp vào thread) ─▶ confirm
                                                                                 │
   confirm: không có đề xuất ─────────────────────────────────────────────▶ END
            có đề xuất, lượt này ─▶ interrupt (câu hỏi xác nhận đã stream xong) ─▶ END
            lượt sau (resume): đọc đề xuất từ DB, lưới code → bộ phân loại
              ├─ approve ─▶ ProposalService.approve ─▶ booking + confirm_fact (PhraseMiddleware: LLM viết câu chốt; Guard kiểm)
              ├─ reject/change ─▶ booking (câu khách là tin mới) + dòng "[declined: …]" trong khối bối cảnh
              ├─ unclear lần 1 ─▶ booking + confirm_fact clarify (câu hỏi lại do code viết; Guard kiểm) ─▶ record ─▶ confirm chờ tiếp
              └─ hết hạn / không còn pending ─▶ supervisor (hết hạn: + dòng "[declined: …]" HẾT HẠN)
                    │
   stream token tag "respond" ─▶ complete ─▶ ghi conversations (nhật ký) ─▶ nén state (nền)
   (cả lượt chịu trần TURN_TIMEOUT_SECONDS = 60 giây: hết giờ / model lỗi sau 1 lần thử lại
    → `error` "Máy đang bận chút xíu, anh chị nhắn lại giúp em nhé." như A)
```

- `booking` có **7 tool, 0 tool ghi**; `shop` 2 tool; `social` 0 tool.
- `booking` / `shop` / `social` là agent `create_agent` (LangChain) bọc trong node
  (`agents.py`: `build_agent` + `make_agent_node`), trả `answer` đã qua guard
  (kèm `draft` thô để soi). Middleware (`middleware/`), ngoài → trong: Phrase,
  Guard, lịch sử, confirm (booking), bố cục message theo bẫy #9, tắt
  `parallel_tool_calls` chỉ khi có tool (Azure trả 400 nếu gửi cờ này mà không
  kèm tool), tối đa 4 lần gọi model rồi câu fallback, lỗi tool thành câu tiếng
  Việt cố định, thử lại model đúng 1 lần (`ModelRetryMiddleware`). `after_agent`
  chạy ngược danh sách nên Guard chạy sau cùng — kể cả khi Phrase hay giới hạn
  vòng nhảy thẳng tới cuối.
- Giới hạn LLM (M4, sửa A-04): mọi model có `timeout=20` giây; chỉ model agent
  tắt retry của SDK (`max_retries=0`) vì `ModelRetryMiddleware` đã thử lại —
  rewrite/phrase/timeparse/classify giữ retry SDK như A (F11) nhưng đều bọc
  `wait_for` 8 giây. Supervisor có ngân sách RIÊNG: `wait_for` 8 giây, SDK thử
  lại tối đa 1 lần, treo/lỗi → route `booking` + log `supervisor_failed` (lỗi
  live M4: 20 giây × 3 lần của supervisor > trần 60 giây, mất cả lượt). Trần
  lượt phải lớn hơn tổng xấu nhất của một đường: phân loại 8 + agent 20 × 2 +
  0,5 + rewrite 8 ≈ 56,5 giây — có test chặn. Model agent chỉ thử lại lỗi
  TẠM THỜI (`agents.is_transient`: timeout, mất kết nối, 429, 5xx);
  400/401/403/404/content filter nổ ngay. Trần lượt 60 giây bọc từng lần KÉO sự kiện từ graph (`asyncio.timeout_at` quanh
  `anext`), KHÔNG bọc `yield`: bọc cả vòng `async for` thì hết giờ lúc người
  nhận đang `sio.emit` nổ CancelledError phía người nhận và khách không nhận
  `error` nào (F12).
- Đề xuất khách KHÔNG chốt (lỗi live M4 3/5/6): reject/change/unclear lần 2/hết
  hạn → confirm chèn vào khối bối cảnh MỘT dòng code viết từ hàng `proposals`
  ("Khách vừa KHÔNG chốt đề xuất … — chưa ghi gì, … [declined: <kind>, iso: …]",
  `context.declined_line`), trước dòng chốt. `load_context` dựng lại đúng dòng
  đó ở lượt KẾ (đề xuất mới nhất đã rejected/superseded/expired, ≤ 10 phút, khách
  chưa nhắn câu nào sau lúc đổi trạng thái — `proposals.updated_at`), rồi thôi.
  BOOKING_PROMPT rule 8: không đề xuất/hủy lại trừ khi CHÍNH tin này xin; câu
  không nêu ngày thì dùng iso của dòng đó làm neo. `parse_vi_time`: có neo mà
  câu nêu giờ cụ thể và KHÔNG nêu ngày thì ngày của neo thắng ngày LLM tự điền
  (`put_on_anchor_day`; regex parser không đổi — bẫy #11).
- Nhiều lịch trong một tin → đặt LẦN LƯỢT (quyết định của chủ dự án, lỗi live
  B2 #2: "9h cho chú, 2h cho thằng cháu" bị gộp, ghi chú nhầm người, rồi hai
  đề xuất huỷ qua lại). Agent đề xuất lịch ĐẦU; các lịch sau đi vào tham số
  `queued` của `propose_appointment` (mỗi mục giờ + ghi chú riêng; tool kiểm
  giờ, bỏ mục hỏng/đã qua). `ConfirmMiddleware.next_queue` ghi hàng chờ vào
  state graph (`queued`, sống trong thread): tool trả `queued` thì THAY, không
  trả thì giữ; mục trùng giờ vừa giữ rời hàng. Mỗi lượt `make_agent_node` chèn
  MỘT dòng code viết `[queued, iso: …]` vào khối bối cảnh (`context.queued_line`);
  hết hiệu lực khi giờ đã qua hoặc sau `QUEUE_MAX_AGE_MINUTES` = 30. Chốt xong
  một lịch mà còn hàng chờ → câu chốt + câu mời lịch kế do CODE viết
  (`confirm_fact.then`, guard nối sau câu chốt) — chưa giữ chỗ gì, lượt sau agent
  đề xuất. Còn hàng chờ thì supervisor đưa thẳng vào booking (không gọi model).
  Lưới phủ quyết: với hàng chờ, mệnh đề CHỈ nói về một lịch đang chờ (có giờ,
  khớp trọn lịch chờ, khác giờ đề xuất) được bỏ khỏi phép kiểm `question` /
  `other_time`; phủ định/hoãn vẫn xét cả câu; còn lại rỗng → xét cả câu
  (fail-closed). Bộ phân loại được báo giờ các lịch đang chờ (không kèm ghi chú
  khách — tiêm lệnh). Ghi chú KHÔNG tự chép từ hàng chờ sang đề xuất: model chép
  từ dòng `[queued …]` (rule 10).
- Giục dời/hủy bằng chính động từ (lỗi live B2 #1, "u doi di em" vẫn bị model
  thật đọc là `change`): với đề xuất `move`/`cancel`, câu CHỈ gồm tiểu từ đồng ý +
  đúng động từ của việc đó (dời/đổi/doi; hủy/huỷ/huy) + tiểu từ đệm (đi, nha,
  em, giùm, cho cô…) → `reply.is_go_ahead` quyết approve bằng CODE, không gọi bộ
  phân loại. Chạy SAU lưới phủ quyết; thêm bất kỳ chữ nào khác (giờ, ngày, "nữa",
  "qua"…) là trả cho bộ phân loại như cũ. Chữ không dấu chỉ tính khi gõ không dấu
  ("đợi" ≠ "doi"). Đặt mới không áp.
- Tối đa 3 giờ trống (lỗi live B2 #3, bot đọc 12 khung giờ): `find_free_slots`
  chỉ đưa model ≤ 3 mốc — gần `around` nhất, rải đều trong `part_of_day`
  (sáng/trưa/chiều/tối), hoặc rải đều cả ngày; ưu tiên mốc chẵn giờ khi đủ 3;
  `more` báo còn mốc khác. `nearest` của `propose_appointment` dùng chung
  `nearest_slots`. Luật prompt (rule 2) chỉ là lớp thứ hai: model đọc hết cái nó có.
- Không nhận vơ "đã đặt/dời/hủy" khi mới giữ (lỗi live B2 #4): lượt có đề xuất
  MỚI (`proposal_id` trong state agent) mà không có câu chốt thì
  `guard.unclaim` sửa TẤT ĐỊNH "em đã/vừa đặt|chốt|ghi (lịch)" → "em giữ chỗ",
  "em đã dời/hủy … rồi" → "em sẽ dời/hủy …" (không gọi LLM). Chỉ chủ ngữ "em"
  hoặc câu không chủ ngữ — "chị đã đặt…" (`already_yours`) không đụng. Lượt
  không có đề xuất mới thì không sửa: "em đã hủy rồi ạ" có thể đúng về lượt
  trước. Kèm luật prompt ở rule 1.
- Nhắc tên người thân không phải ca bảo mật (lỗi live B2 #5, "để cô hỏi thằng
  Huy đã" nhận câu "Em chỉ xem và đặt lịch của chị Lan thôi"): câu đó do
  BOOKING_PROMPT rule 4 sinh (lượt hoãn bị lưới `deferral` chặn rồi đi vào agent
  booking). Rule 4 thêm vế loại trừ: đặt hộ người thân bằng tài khoản mình, hay
  nói sẽ hỏi ai đó trước khi quyết, vẫn là lịch CỦA khách — lượt hoãn thì đáp
  một câu ngắn kiểu lễ tân, không gọi tool, không đọc giờ trống.
- Lời "đã giữ/đã đặt" phải khớp DB của lượt (lỗi live B3 #1, câu chốt bịa "Còn
  2 giờ chiều em cũng đã giữ cho cháu rồi ạ" khi 2 giờ chỉ đang chờ). MỌI lượt,
  `guard.claim_facts` lấy các giờ có thật từ chuỗi CODE viết: dòng `[id: …]`
  (lịch sắp tới), `held`/`will_cancel` của tool lượt này, `confirm_fact.when`.
  Mệnh đề "đã/vừa/đang giữ|đặt|ghi|chốt" hay "giữ … rồi" nói về giờ khác (hoặc
  không giờ khi chẳng có gì) → câu chốt: câu cứng; câu thường: rewrite một lần,
  vẫn sai thì bỏ hẳn câu đó. Dời/hủy không xét ở đây ("đã hủy" đúng mà giờ không
  còn trong lịch); "ghi nhận/ghi lại" không phải ghi lịch. Phrase được báo các
  giờ đang chờ là NOT held (`confirm_fact.queued_when`).
- Người nhà rảnh không ≠ chủ tiệm (lỗi live B3 #2): "nó có rảnh không" từng ra
  get_shop_status, "thằng cháu có rảnh không … để chú hỏi rồi nhắn lại" ra câu từ
  chối quyền riêng tư. `relatives.py` nhận ra bằng code (người nhà + "rảnh";
  câu hoãn/nhắn lại sau) → supervisor vào booking không gọi model, agent đọc dòng
  `[relative]`/`[deferral]`; rule 1 cấm tool tiệm cho câu đó, rule 4 coi nó là
  vế loại trừ.
- Báo trước "đặt từng lịch một" (lỗi live B3 #3): lượt đề xuất mà còn hàng chờ,
  guard chèn câu CODE viết "Em giữ <giờ> trước, xong em giữ tiếp lịch <giờ chờ>
  <ghi chú> nhé." trước câu hỏi xác nhận. Live B4 #1: vế "trừ khi câu đã nhắc đủ
  giờ chờ" làm câu báo biến mất khi model tự viết "Lịch cho thằng cháu là … 2 giờ
  chiều", rồi "xác nhận giúp em nhé?" đọc như chốt cả hai. Giờ câu báo LUÔN có;
  câu model nào nhắc giờ chờ bị bỏ; câu hỏi phải gọi đúng giờ đang giữ và không
  nhắc giờ chờ, không thì code viết "<Khách> xác nhận lịch <giờ giữ> giúp em nhé?".
- Câu hoãn trần do CODE đáp (lỗi live B4 #2): dù có dòng [relative]/[deferral],
  model vẫn chọn câu từ chối rule 4 cho "thoi de chu hoi no roi nhan lai em sau
  nha". `relatives.deferral_reply`: câu hoãn KHÔNG kèm yêu cầu (giờ, đặt/giữ/dời/
  hủy, hỏi tiệm) → node agent trả một trong ba câu mời nhắn lại, không gọi model,
  không lặp câu vừa nói. Kèm yêu cầu thì vẫn qua model.
- Hợp đồng stream (review M4 M-2, KHÔNG đổi giao thức Socket.IO): `token` chỉ
  là bản nháp hiện dần; `complete.answer` THAY bubble đang stream bằng câu đã
  qua guard, `error` THAY nó bằng câu lỗi (`frontend/src/hooks/useAgentStream.ts`,
  `onComplete`/`onError`). Model agent nổ giữa stream rồi thử lại thì stream
  lại từ đầu — token hai lần nối nhau trên màn hình cho tới khi `complete`/`error`
  tới và thay cả bubble. Client mới (Telegram ở Plan 3, client khác) phải giữ
  đúng luật này: không bao giờ coi chuỗi token là câu cuối.
  Cố ý giữ (review M4 M-3): `strip_tool_names` của guard chỉ áp lên câu cuối
  (`answer`), KHÔNG lên token `respond` đang stream — tên tool lỡ lọt sẽ nháy
  trên màn hình tới khi `complete` thay bubble. Chỉ là phòng ngừa (A chưa lộ
  lần nào; metric bench `tool_name_leak` đọc câu cuối); lọc token thì phải
  đệm token qua ranh giới chữ, không đáng.
- Lịch sử model (từ M2) nằm trong thread checkpointer `MongoDBSaver`
  (`app/agents/checkpoint.py`, cùng DB `MONGO_DB_NAME`, collection `checkpoints` /
  `checkpoint_writes`, TTL `CHECKPOINT_TTL_DAYS`). Thread chỉ giữ câu khách + câu
  đáp đã qua guard, mỗi tin mang stamp `created_at`; tool exchange ở lại trong
  agent con. `HistoryWindowMiddleware` (agent), `write_phrase` và `supervisor` đều đọc
  qua `model_history` → model thấy đúng cửa sổ A gửi; câu khách của lượt lỗi
  (không có câu đáp) bị bỏ như A. Mọi khoá theo lượt (kể cả `answer`,
  `confirm_fact`, `gate_next`) đặt lại mỗi lượt bằng `fresh_turn_fields` — không thì
  checkpoint mang giá trị lượt trước sang. `proposal_id` thì KHÔNG nằm trong đó:
  lượt resume phải giữ đề xuất đang chờ; input mới tự đặt `proposal_id=None`
  (lượt lỗi giữa chừng không để lại đề xuất mồ côi trong state).
- Resume = `aupdate_state(fields, as_node="record")` rồi `Command(resume=câu khách)`
  KHÔNG kèm `update`: lượt resume bị huỷ (lượt mới thay) để lại `update` của nó
  thành pending write, và lần resume sau ghi `answer` hai lần trong một bước →
  `InvalidUpdateError` mãi. "Đang chờ" = có interrupt HOẶC `confirm` là bước kế
  (lượt resume bị huỷ trước khi confirm interrupt lại). `ProposalService.approve`
  chạy trong `asyncio.shield`: huỷ lượt giữa lúc ghi thì việc duyệt vẫn xong;
  lượt sau thấy `approving` thì chờ tối đa 3 giây, thấy `approved` thì đi thẳng
  câu chốt (duyệt lại idempotent) — khách luôn nghe xác nhận cho lịch có thật.
  Lượt bị huỷ sau `record` khi ĐÃ có đề xuất nhưng confirm chưa kịp interrupt:
  tin sau được coi là câu trả lời đề xuất — lưới code + bộ phân loại vẫn gác
  việc ghi. Không có đề xuất thì next=confirm không tính là đang chờ: tin sau là
  tin mới từ supervisor. Mỗi khách một lượt chạy: lượt mới huỷ
  lượt cũ còn treo (`_ACTIVE_TURNS`, log `turn_superseded`).
- Giới hạn đã biết của thread theo ngày (chấp nhận, không sửa):
  - **Qua hai nửa đêm liền nhau** (nối thread hôm qua lúc 00:10, rồi lại nhắn
    23:58 và 00:05 đêm sau): `pick_thread` chỉ nhìn thread mang tên hôm qua,
    thread đang dùng mang tên hôm kia nên đêm thứ hai mất phần giữ 30 phút.
  - **Cặp hỏi/đáp vắt qua mốc cắt**: câu khách stamp lúc HỎI, câu đáp lúc
    GHI. Lượt chạy ngang mốc cắt (nửa đêm, hay mốc 30 phút) có thể giữ câu đáp
    mà bỏ câu hỏi (vd. hỏi 23:59:58, đáp 00:00:03, đọc lúc 00:40). A ghi cả
    cặp sau khi trả lời nên hai mốc sát nhau, không tách. Luật bỏ câu đáp mồ
    côi đầu cửa sổ chỉ chạy khi ngân sách token cắt, nên không cứu ca này.
    Hiếm (lượt phải chạy đúng qua mốc) và chỉ mất một câu hỏi.
- **Mọi tool trả JSON dữ kiện, không trả câu** (bẫy #23). Mốc thời gian là
  `{"iso", "spoken"}`; model chép `spoken` nguyên văn, câu thì tự viết.
- Guard (`GuardMiddleware` → `guard.apply_guard`) là chỗ **duy nhất** đặt `answer`.
  `rewrite` mang tag riêng, không stream. Câu hỏi xác nhận đi qua guard TRONG
  agent, rồi `record`, rồi mới tới `confirm` interrupt — interrupt nằm ở node
  ngoài chính vì thứ tự này (câu hỏi phải được kiểm trước khi chờ khách).
- Khối bối cảnh dựng bằng code mỗi lượt và **thắng** lịch sử lẫn `ConversationState` (summary + slots).

## Cách chạy lại phép đo

```bash
docker compose up -d mongo
docker compose exec -T mongo mongosh salon_booking --quiet --eval 'db.rate_limits.deleteMany({})'
PYTHONPATH=. nohup .venv/bin/python -m uvicorn main:app --port 8000 > /tmp/uvicorn.log 2>&1 &

# tài khoản mới qua API admin (0901234567 / chutiem123) — tên PHẢI có tiền tố Cô/Chú
curl -s -X POST localhost:8000/api/v1/auth/users -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"phone":"0986000401","password":"khachhang123","full_name":"Chú Tám","role":"user"}'

PYTHONPATH=. .venv/bin/python scripts/probe_supervisor.py     # định tuyến, 18 mẫu, kỳ vọng 0 lệch
PYTHONPATH=. .venv/bin/python scripts/probe_repeats.py *.txt  # ngưỡng lặp trên transcript cũ
PYTHONPATH=. .venv/bin/python scripts/chat_e2e_transcript.py --scenario dai \
    --phone 0986000401 --password khachhang123 --out after.txt
PYTHONPATH=. .venv/bin/python scripts/score_transcript.py baseline-dai.txt after.txt

grep -o "guard_violation\|guard_rewritten\|guard_gave_up\|phrase_fallback\|anchor_ignored" /tmp/uvicorn.log | sort | uniq -c
fuser -k -TERM 8000/tcp        # KHÔNG dùng pkill -f "uvicorn main:app" — khớp cả shell của chính mình
```

Kịch bản có sẵn: `booking`, `doi_y`, `huy`, `thong_tin`, `dai` (16 lượt, **mốc
rubric — sửa một chữ là mọi điểm cũ hết so được**), `doi_lich`, `tu_nhien`.

Trần chat **30 tin/giờ mỗi khách** → **mỗi lần chạy phải tạo tài khoản mới**.
Dọn: `db.users.deleteMany({phone: /^0986/})` kèm appointments và conversations
của họ (lệnh đầy đủ ở `RUNBOOK.md`).

**Đừng chỉ tin rubric** (bẫy #18). Ba lỗi nặng nhất từng lọt qua rubric và chỉ
lộ ra khi **trò chuyện thật, không kịch bản, đọc từng câu rồi mới nghĩ câu sau**.
Cách tốt nhất đang có: tạo tài khoản qua API rồi tự chat như khách.

---

# Nhật ký các đợt việc

**Bốn nhánh cuối bảng CHƯA merge** — xem mục Trạng thái.

Mỗi nhánh một merge commit `--no-ff` nên vẫn tra ngược được từng đơn vị công việc.
Cột test là số test backend sau khi merge nhánh đó.

| Nhánh | Làm gì | Test |
|---|---|---|
| `feat/agent-routing-redesign` (PR #5) | `status`→`shop`, `refuse`→`social`; xưng hô suy bằng code; `BOOKING_PROMPT` 4718→2975 ký tự | 461 → 499 |
| `feat/natural-conversation` | Thứ tự tin nhắn; `_NO_REPEAT`; `history()` giữ ca nửa đêm; lọc `full_name`/`note` | 499 → 530 |
| `fix/timeparse-closed-schema` | Đóng schema `ParsedTime`; docstring tool tiếng Anh | 530 → 545 |
| `fix/reschedule-and-cancel` | BUG-1/2/3 (dưới) | 546 → 568 |
| `fix/cancel-confirm-and-prompts` | Hủy qua `confirm`; rule 4 hết bắn nhầm khi hết lịch; `note` là dịch vụ | 568 → 588 |
| `fix/rule4-english` | Bỏ câu thoại sẵn cuối cùng khỏi prompt | 588 → 590 |
| `fix/no-repeat-wording`, `fix/context-last-reply` | Hai lần thử chữa lặp bằng prompt/bối cảnh | 590 → 596 |
| `feat/conversation-digest` | Tầng digest trong phiên | 596 → 633 |
| `fix/live-chat-2026-09-14` | 3 lỗi từ cuộc chat thật không kịch bản | 633 → 639 |
| `feat/natural-voice-guard` | `guard` + `rewrite` + `phrase` + neo thời gian + 7 tool | 639 → **738** |
| `feat/conversation-state` | `Digest`→`ConversationState`; thêm `slots` (ý định/dịch vụ/ngày/giờ/giờ đã từ chối) do chính lượt nén sinh — 0 lời gọi LLM thêm | 738 → **776** |
| `fix/live-run-findings` | 4 lỗi từ chạy thật 19/9: giá/dịch vụ bị đáp bằng câu quyền riêng tư; lặp nguyên văn; AI đoán giới tính khách; liệt kê thiếu giờ đã từ chối | 776 → **785** |
| `refactor/tools-return-data` | 6 tool trả JSON thay vì câu tiếng Việt; câu lỗi thành mã lỗi; mệnh lệnh chuyển từ tool về prompt | 785 → **803** |
| `fix/clock-and-dead-data` | 12 giờ đọc là "trưa" không phải "chiều"; bỏ `target_appointment_id` (dữ liệu chết tốn một truy vấn); tách mã `already_yours` | 803 → **805** |

## Ba bug nghiệp vụ (tìm 13–14/9, sửa xong)

Giữ lại vì cách tái hiện và bài học còn dùng được.

**BUG-1 — "dời lịch" tạo ra lịch thứ hai** (nặng nhất, tái hiện 2 lần).
`"em ơi chuyển giùm anh qua 10 giờ nha"` → `"ừ"` → DB có **cả** 09:00 và 10:00
booked, bot vẫn nói "Xong rồi ạ". Gốc: `AppointmentService.create` chỉ chống
trùng **cùng một mốc giờ**, và hệ thống **không có luồng đổi lịch**.
→ Sửa: `reschedule` + `replaces_appointment_id` + nhánh dời trong `confirm` (bẫy #19).

**BUG-2 — hủy lịch hay thất bại giữa chừng.** `cancel_appointment` cần
`appointment_id`, mà lịch sử chỉ lưu **câu hỏi và câu trả lời**, không lưu kết
quả tool → lượt sau model bịa mã. Có lần khách phải nói 4 lượt mới hủy được.
→ Sửa: khối bối cảnh in `[id: ...]` mỗi dòng lịch sắp tới. **Không** lưu kết quả
tool vào lịch sử — lịch sử là thứ khách đọc lại được.

**BUG-3 — LLM viết số bằng chữ.** *"thứ bảy ngày mười chín tháng chín, lúc chín
giờ sáng"* trong khi câu do code sinh viết đúng `"Thứ Bảy 19/9, 9 giờ sáng"`.
Hệ quả của việc bỏ ví dụ khỏi luật định dạng.
→ Sửa: luật tham chiếu **đầu ra của tool**; `guard` có phép kiểm `clock`.

## Ba lỗi từ cuộc chat thật không kịch bản (14/9)

- Khách xin hủy → model **tự hỏi xác nhận trước khi gọi tool** → không có
  pending → "ừ hủy đi" rơi về booking → hỏi xác nhận lần hai; khách chào về,
  lịch còn nguyên. Sửa: gọi tool TRƯỚC, hỏi SAU (cùng bài học `propose_appointment`).
- "tiệm có nhuộm tóc bạc không, giá bao nhiêu" → dính câu bảo mật rule 4.
  Sửa: vế loại trừ — giá/dịch vụ là chuyện chủ tiệm trả lời.
- "tiệm còn làm không" → bot "còn làm ạ" **không tra gì**. Dò supervisor: định
  tuyến đúng vào `shop`; lỗi ở `SHOP_PROMPT` thiếu luật "đang mở không".
  Sửa: gọi `get_shop_hours` rồi so với giờ trong khối bối cảnh.

## Tầng digest — kết quả đo (14/9)

6 task: `Digest` model → `DigestService.maybe_compact` (ngưỡng 800 token ngoài
cửa sổ 4 lượt gần nhất, cầu chì 3 lần hỏng, schema đóng) → `context_window` →
`schedule_compaction` chạy nền sau `complete` → đo.

- Ở ngưỡng mặc định **800**, kịch bản `dai` (16 lượt câu ngắn) **không** đủ token
  để kích nén. Không phải lỗi — 800 token cần phiên dài hơn.
- Hạ tạm xuống **300** để xác nhận luồng đầu-cuối: `digest_compacted` lên log sau
  lượt 10 (`bullets: 7, compacted: 22`), bullets đúng ràng buộc (tiếng Việt, ≤8
  dòng, không trích câu thoại, không ghi "đã đặt/đã hủy"). Ngưỡng **đã khôi phục
  về 800** ngay sau đó — không đổi hằng số để "qua bài đo".
- Đọc tay lượt 8 ("chị đặt lúc mấy giờ vậy em nhắc lại giùm"): bot trả lời bằng
  `list_my_appointments`/khối bối cảnh, **không** lấy từ digest — đúng bẫy #20.
- Rubric 2.4 → 2.6 (trục `hoi_lai_da_biet` 2→3). Nhích nhẹ, đừng đọc quá tay.

## Tầng gác — kết quả đo (14/9)

6 task + một đợt sửa sau review. Phép kiểm cuối cùng là **3** (`repeat`,
`pronoun`, `clock`) + `content`; `address`/`register` gộp làm một, `language`
bỏ hẳn — quyết định của chủ dự án, khác con số "5 phép kiểm" ở spec gốc.

- `tu_nhien` (10 lượt, gieo sẵn lịch 9 giờ sáng mai): **8/8 điểm kiểm đúng** —
  trả lời cả hai vế câu kép; từ chối xem lịch người khác không gọi tool; hỏi giá
  không dính câu bảo mật; **"chuyển qua 10 giờ" không hỏi lại "sáng hay chiều"**
  (neo từ lịch 9 giờ sáng); câu chốt do LLM viết có đủ ngày giờ và tên; hủy chỉ
  hỏi xác nhận một lần; không câu nào lặp nguyên văn.
- Cuộc thứ hai, khách nữ, 11 lượt tự nghĩ (18/9): kết quả như trên; "con gái
  chị" không bị bắt nhầm là sai xưng hô.
- **Log tầng gác** trên 26 lượt: `guard_violation` **1 lượt** (`repeat`), theo sau
  là `guard_gave_up` — rewrite vẫn phạm nên guard trả draft gốc (đúng bẫy #21).
  Không có `rewrite_failed`, `phrase_fallback`, `anchor_ignored` nào.
- `probe_supervisor.py` **0/18**. `probe_repeats.py` **12/161**, không ca oan.
- Rubric `dai` 2.2 (chấm lại baseline) → 2.4. Giám khảo LLM nhiễu giữa các lần
  chấm **cùng một baseline** — đừng coi là cải thiện chắc chắn.

**Đợt sửa sau review (F1–F10):** chặn `apply_anchor` đoán bừa khi `missing` có
phần tử ngoài `{"ngày nào", "sáng hay chiều"}` (VD "tuần này hay tuần sau" —
đang khiến "thứ Năm 10 giờ" bị gán vào ngày của neo); bỏ `re.I` sai chỗ khiến
"bác sĩ", "cô gái", "anh Tám giờ nào" bị bắt oan; `check_pronoun` bỏ tiểu từ mở
đầu ("Dạ chị…") và dấu câu dính token; `REWRITE_PROMPT` nhúng `_VIETNAMESE_ONLY`;
`phrase` mang theo digest.
