# CONTEXT — vì sao, và đừng phá

Viết lại 08/11/2026, khi bắt đầu dựng lại tầng agent. Giữ phần **tại sao** và những gì **không được
phá**. Tài liệu cũ (spec/plan agent từ 8/2026 tới 11/2026, `REPO_AUDIT.md`, kịch bản kiểm thử, nhật
ký) đã xoá khỏi cây thư mục; xem lại bằng git: `git show 72c98fc:<đường dẫn>`.

| Cần gì | Đọc file |
|---|---|
| Đang làm gì, làm tiếp gì | Mục **Trạng thái** ngay dưới + spec [`docs/superpowers/specs/2026-11-08-subagents-rebuild-design.md`](docs/superpowers/specs/2026-11-08-subagents-rebuild-design.md) |
| Kiến trúc đích của tầng agent | [`docs/architech-agent.md`](docs/architech-agent.md) — thắng spec / plan khi lệch; `AGENTS.md` đã xoá (09/11) |
| Gõ lệnh gì | [`RUNBOOK.md`](RUNBOOK.md) |
| Lên prod cần gì | [`PROD_CHECKLIST.md`](PROD_CHECKLIST.md) |
| Rà kiến trúc / bảo mật | [`docs/ARCH_SECURITY_REVIEW.md`](docs/ARCH_SECURITY_REVIEW.md) |
| Spec sản phẩm gốc | [`docs/superpowers/specs/2026-08-06-booking-nail-toc/`](docs/superpowers/specs/2026-08-06-booking-nail-toc/README.md) — biên bản 8/2026, phần agent trong đó đã cũ |

## Sản phẩm

Đặt lịch cho **một tiệm nail–tóc nhỏ ở Việt Nam**. Khách phần lớn lớn tuổi, bấm điện thoại.

| Ai | Qua đâu |
|---|---|
| Khách | Web React — chat với AI đặt lịch, xem lịch của mình |
| Chủ tiệm | Cùng web, giao diện admin — bận/rảnh, xem lịch, quản khách, đặt hộ |
| Chủ tiệm | Bot Telegram — báo lịch mới, tra lịch, đổi bận/rảnh bằng 4 nút |

## Trạng thái — 10/11/2026

- Nhánh **`feat/llm-orchestrator`**, chưa push, chưa merge. Quy ước: nhánh việc → `henry/develop` →
  `main`, không commit thẳng `main`. Commit message tiếng Anh, ngắn.
- **Đã đập đi xây lại xong (Task 1–17 của plan).** Toàn bộ chat thật chạy qua gói mới
  `app/agents/salon/` (`run_turn` / `ButtonReply`, xem mục kiến trúc bên dưới và
  `docs/architech-agent.md`). Gói cũ `app/agents/booking/` (pipeline cố định `resolve →
  orchestrate → brief → respond → record`) đã **xoá hẳn** ở Task 16 — không còn trong cây thư mục,
  xem lại bằng git (`git show c98413b:app/agents/booking/...`) chỉ để tham khảo lịch sử. Dữ liệu
  Mongo của gói cũ (`conversation_sessions`, `turn_ledger`, `conversations.state`/
  `pending_confirmation`, checkpoint cũ dạng `thread_id = <user_id>:<ngày VN>`) đã dọn trên DB dev
  bằng `scripts/drop_legacy_agent_data.py --yes` (chạy 10/11; script vẫn còn, mặc định dry-run).
- **Chốt kiến trúc đích**: spec `docs/superpowers/specs/2026-11-08-subagents-rebuild-design.md` +
  `docs/architech-agent.md` (thắng khi spec/plan lệch). Plan thực thi:
  `docs/superpowers/plans/2026-11-08-subagents-rebuild.md`, chạy bằng
  `superpowers:subagent-driven-development`, sổ theo dõi
  `.superpowers/sdd/2026-11-08-subagents-rebuild/progress.md` (có các "Ruling" của controller khi
  plan lệch thực tế — xem file đó để biết hành vi đã XÂY khác plan ở đâu).
- **Việc còn lại trước PR**: Task 18 (tài liệu này + kịch bản chạy thật
  `scripts/test_live_10_turns.py`) và Task 19 (hai lần chạy thật trên Azure, chủ dự án duyệt từng
  lần trước khi chạy — xem CONTEXT.md quyết định 7). Sau đó mới mở **một** PR vào `henry/develop`.
- **Để sau đợt rebuild này**: đặt nhiều lịch một lần + giữ chỗ (đợt B). Spec cũ ở `git show
  2a26e28:docs/superpowers/specs/2026-11-08-multi-booking-holds-design.md`, phải viết lại cho kiến
  trúc mới.

### Kiến trúc đã dựng (tóm tắt spec — xem chi tiết ở `docs/architech-agent.md`)

- **4 agent**: Orchestrator (`create_agent` + checkpointer Mongo, thread `conversation_id`) gọi 3
  agent con như tool: Đặt lịch, Thông tin tiệm, **Dịch vụ & giá** (mới). Agent con không trạng thái,
  dựng lại mỗi lần gọi; Orchestrator gọi chúng nhiều lần trong một lượt (vòng lặp).
- **Phiên chờ**: agent con thiếu thông tin thì trả câu hỏi; phiên (việc đang dở + dữ kiện code trích
  từ tool) nằm trong collection Mongo `waiting_sessions` qua `WaitingSessionService`, không trong
  state graph; lượt sau làm tiếp. Hỏi chuyện khác → phiên vẫn chờ; đổi ý →
  phiên bị thay; hết hạn 10 phút (tính lại mỗi lần hỏi câu mới).
- **Không dùng `interrupt()`**, không stream. Câu trả lời = AIMessage cuối của Orchestrator, qua guard
  (code, `after_model`) rồi mới tới khách.
- **Lịch dài theo dịch vụ** (`service_ids` → thời lượng do `ProposalService` tự tính từ collection
  `services`; không có dịch vụ thì dùng `booking_slot_minutes`). Danh mục dịch vụ tạm là mock; file
  Excel thật chủ dự án gửi sau.
- **Gói mới `app/agents/salon/`**, chia theo vai trò: `turn/` (một lượt: `run_turn`, event, ledger),
  `orchestrator/` (agent, prompt, state, tool, `decide.py` = cổng ghi, `middleware/`), `subagents/`
  (`dispatch.py` + mỗi agent một thư mục `agent.py · tools.py · prompt.py`), `guard/`, `memory/`,
  `shared/` (không import thư mục nào khác); test xếp theo đúng cây `tests/salon/…`. Không import gì
  từ gói cũ. Mang sang duy nhất: `timeparse.py` (bỏ
  phụ thuộc `tokens`, `part_of_day` thành enum) và 8 hàm quét dữ kiện của `tokens.py` vào
  `text_scan.py` (`words`, `digits_in`, `split_sentences`, `split_clauses`, `scan_spoken_clocks`,
  `scan_times`, `scan_day_offsets`, `scan_weekdays`) — chúng là code **kiểm lại** LLM (veto, guard,
  lọc memory), Orchestrator suy luận không thay được. Gói mới 0 regex.
- **Tool per agent** (`appointment_task`, `shop_info_task`, `service_task`, cộng `decide_pending`),
  ba tool giao việc dùng chung một hàm điều phối nội bộ. Không single dispatch, **không
  `create_deep_agent`** (kiến trúc = `create_agent` + mẫu Subagents + memory của LangGraph).
- **Bộ nhớ** theo LangGraph "Add memory": ngắn hạn = checkpointer theo thread (cắt khi gửi model,
  `RemoveMessage`, `SummarizationMiddleware` thay `digest`/`slots`); dài hạn = `MongoDBStore`
  namespace `("memories", user_id)`, 3 mục `preferences` / `notes` / `last_visit` (thay dòng "Lần
  trước" đã mất), do một **bước gom** ghi ở lượt đầu của ngày mới, in vào khối bối cảnh dưới nhãn dữ
  liệu. Không tìm kiếm ngữ nghĩa (Mongo không có `mongot`).
- **Guard câu cuối** (code, `after_model`): nhận vơ, giờ bịa, giá ngoài khoảng, thiếu điều bắt buộc,
  xưng hô, lặp câu, lọt tên tool. Sai → xoá câu khỏi state, viết lại 1 lần → câu mẫu code. Trần dùng
  `exit_behavior="error"` để câu tiếng Anh của thư viện không lọt ra khách.

### Quyết định chủ dự án — đừng mở lại

1. **Subagents** (một agent chính điều phối, agent con là tool), không Handoffs (agent con sẽ tự nói
   với khách), không Router làm lõi (không trạng thái, docs không khuyên cho hội thoại nhiều lượt).
   Router bằng code chỉ ở cửa vào: nút bấm đi thẳng `apply_decision`.
2. Bám sát docs LangChain; chỉ tự viết phần docs không lo.
3. Thêm agent **Dịch vụ & giá**; Hồ sơ khách / Hỏi đáp chính sách / Chuyển cho chủ tiệm để sau, và
   nếu làm thì là tool của agent đã có, không phải agent mới.
4. Giữ **transaction Mongo** (replica set 1 node `rs0`); duyệt đề xuất và ghi `turn_ledger` trong
   một transaction.
5. Guard sự thật **bằng code**, không LLM giám khảo.
6. Bỏ cơ chế "3 lượt không tiến triển → mời gọi tiệm"; câu "gọi tiệm" chỉ dùng khi ghi lịch thất bại.
7. **Không chạy Azure thật khi chưa hỏi** (benchmark, live run, probe). Kiểm bằng test case, model giả.
8. Gọi khách **"anh chị"** là chủ ý (mục Xưng hô).
9. **Đập đi xây lại** tầng agent, không dùng lại code agent sẵn có, xoá tuỳ ý (08/11). Giữ `app/services`,
   models, repositories, REST, Telegram, frontend, `app/core`.
10. **Tool per agent**, không single dispatch; **không `create_deep_agent`**.
11. Nén lịch sử bằng **`SummarizationMiddleware`**; bộ nhớ theo **LangGraph "Add memory"** (checkpointer
    + `MongoDBStore`), không theo Deep Agents memory.
12. Tiệm phục vụ **từng khách một** (lịch nhuộm 2 tiếng chặn cả khách làm móng).
13. Bot **được nói một con số nằm trong khoảng giá** của danh mục.
14. Khách đã nhắc dịch vụ mà chưa rõ loại → **hỏi rõ, không đề xuất** (`service_unclear`).
15. **Không có tiêu chí độ trễ**.
16. **Hiện các bước của agent con** cho khách, nhãn tiếng Việt, không tên tool kỹ thuật.
17. **Giữ `veto`** (4 luật code chỉ hạ nhãn `decide_pending`, quét giờ / ngày / thứ trong câu khách)
    dù Orchestrator có reasoning (08/11): reasoning không đúng 100% (chính lần chạy thật 08/11 còn sai),
    không chống được chữ khách cố tình lừa, và veto sai thì chỉ hỏi lại một câu, còn nhãn `approve`
    sai thì ghi nhầm lịch. Vẫn cho khách xác nhận bằng chữ, không chỉ bằng nút.
18. **`docs/architech-agent.md` là kiến trúc đích** (08/11); `AGENTS.md` đã xoá (09/11), đừng tạo lại.
    Theo đó: một đường `apply_decision` cho nút và chữ (chữ qua Decision Gate = `veto` + kiểm đề xuất);
    guard từ chối câu cuối khi đề xuất chờ chưa được quyết (`pending_undecided`); lần viết lại không có
    tool; tool huỷ tên `propose_cancel`; human-in-the-loop = xác nhận đề xuất qua hai lượt, không `interrupt()`.
19. **Phiên chờ ở Mongo, thread là cuộc trò chuyện** (09/11, sau spike `interrupt()`): phiên chờ là
    collection `waiting_sessions` cạnh `proposals`; agent con không bao giờ `checkpointer=True`.
    `thread_id = conversation_id` (collection `conversation_threads`), mỗi ngày VN một cuộc mới.
20. **Nút bấm là API bằng code** (09/11): bấm Đồng ý / Không thì code duyệt và trả câu báo, không gọi
    model. Chỉ khi khách nói hai lịch trong một tin, bấm Đồng ý lịch đầu thì Orchestrator đề xuất lịch kế
    và khách bấm nút lần nữa.

## Chốt cứng — đừng mở lại

| Chủ đề | Chốt | Vì sao |
|---|---|---|
| Khung agent | **LangChain v1 `create_agent` + LangGraph**, không AutoGen | Middleware, checkpointer, subagent tường minh |
| Ghi lịch | **Agent không có tool ghi, cũng không huỷ ngay** (kể cả bọc HITL). `propose_appointment` / `cancel_appointment` chỉ tạo **đề xuất** trong `proposals`. Chỉ `ProposalService.approve` ghi, và trong `app/agents/` chỉ `orchestrator/decide.py::apply_decision` gọi nó (nút bấm cũng qua đó). `approve` kiểm: đúng chủ, còn hạn 10 phút, tạo trước lượt duyệt, một lần duy nhất, lịch đích của dời/huỷ chưa đổi. | Khách luôn có một bước đồng ý; giá trị lấy từ DB, không từ chữ model gõ lại. `tests/test_write_fence.py` canh |
| Câu trả lời | **Mọi câu qua guard code trước khi tới khách**; viết lại tối đa 1 lần; vẫn sai thì câu mẫu code | Prompt không chặn được nhận vơ / bịa giờ |
| Memory | **Theo LangGraph "Add memory"** (chủ dự án 08/11): ngắn hạn = checkpointer theo thread + `SummarizationMiddleware`; dài hạn = `MongoDBStore` namespace `("memories", user_id)`, 3 mục cố định, do bước gom (code + một lời gọi model nhỏ có schema) ghi ở lượt đầu của cuộc trò chuyện mới. **Không vector / embedding** | Mỗi khách chỉ vài mục nên đọc hết namespace; vector cần `mongot` / Atlas và tốn gọi embedding. Không Postgres, namespace theo khách nên không lộ chéo. Memory không chứa giờ / giá / id / mệnh lệnh; hồ sơ do code dựng (`self_address`, `staff_note`) vẫn nằm ngoài memory |
| Phiên hội thoại | **Một cuộc trò chuyện mỗi ngày VN** (chủ dự án 09/11). `thread_id = conversation_id`, bản ghi trong `conversation_threads` do `ConversationThreadService.current` chọn: tiếp cuộc cũ nếu cùng ngày VN hoặc lượt trước cách chưa tới 30 phút (đáp lúc 00:01 câu hỏi lúc 23:58), không thì mở cuộc mới trỏ `previous_id` về cuộc cũ. `conversations` vẫn là nhật ký hiển thị. | Id riêng cho mỗi cuộc: Langfuse nhóm trace theo cuộc, bước gom memory biết chắc cuộc trước là cuộc nào |
| Phiên chờ | **Collection Mongo `waiting_sessions`** qua `WaitingSessionService`, mỗi khách một phiên mỗi agent con, hết hạn 10 phút kiểm lúc đọc. **Không** `interrupt()`, không trong state graph | Spike 09/11: resume bằng câu chuyện khác làm sai câu trả lời và mất câu hỏi; tin thường lúc graph treo làm Azure trả 400 và hỏng thread; câu hỏi của `interrupt()` không qua guard |
| Checkpoint | **Không phải nguồn sự thật** cho lịch / đề xuất (TTL xoá được) | Lịch và đề xuất luôn đọc lại từ DB |
| Kênh chủ tiệm | **Telegram**, không Zalo OA, không SMS | Bot API miễn phí, không khung 48h |
| Bot Telegram | **Không có AI**, 4 nút | Tất định, không tốn token |
| Số worker | **Đúng 1** | Telegram chỉ cho một `getUpdates` mỗi token |
| Cấu hình | **Chỉ đọc file `.env`** | Biến môi trường từ dự án khác âm thầm đè lên |
| Redis | **Tuỳ chọn, tắt mặc định**, không bao giờ fail-hard; Mongo là nguồn sự thật | Chỉ dùng chống tin trùng + cache trạng thái tiệm |
| Quan sát | **Langfuse tự dựng**, compose riêng | Dữ liệu khách không ra khỏi máy |
| Tool | **Chỉ trả dữ kiện JSON, không viết câu, không ra lệnh** | Câu là việc của model; mốc giờ trả cả `iso` lẫn `spoken` |
| Ghi Mongo | **Không gộp** các `update_one` mỗi lượt bằng closure | Ghi qua state phải tường minh (giá trị trả về / `Command(update=...)`) |

## Xưng hô — chốt 2026-08-23, siết 2026-09-19

**Lễ tân xưng "em", gọi khách "anh"/"chị".** Cách gọi do **code** quyết, theo thứ tự:
`users.self_address` (khách tự xưng) → tiền tố tên admin gõ (`derive_address`: cô/chị/bà→chị,
chú/anh/ông→anh) → **"anh chị"**. Không hỏi model, không đoán từ tên đầy đủ ("Nguyễn Thị Lan").

**Không dùng "con", "cô", "chú", "bác" ở bất kỳ đâu khách thấy** — prompt, câu lỗi, câu hết phiên,
chuỗi frontend. "con" chỉ đi với cô/chú/bác; ghép với anh/chị là sai tiếng Việt.

**Tên không cho biết giới tính thì nói đúng "anh chị" — cấm đoán.** Từng chạy thật: cùng một khách bị
gọi "Chị ơi" hai lượt đầu rồi "Anh chị ơi" từ lượt 3. Hàng rào: `tests/test_prompts.py`.

## Bẫy — đã trả giá, đừng "sửa cho gọn"

**Dữ liệu**

1. Unique index trên `slot_keys` phải **partial** (`{status: "booked"}`), không sparse — Mongo đánh
   mảng rỗng thành `undefined`, huỷ lịch thứ hai là `E11000`. Huỷ chỉ lật `status`.
2. `DuplicateKeyError` là **lớp con** của `PyMongoError` — repository đổi thành `SlotTakenError`
   trước, không thì khách trùng giờ thấy "máy đang hỏng".
3. Mongo chạy replica set 1 node (cần cho transaction); **mọi URI cần `?directConnection=true`**
   (`.env`, test, scripts), thiếu là treo ở chọn server.
4. TTL index của Mongo chạy mỗi 60 giây và có thể trễ hơn — **hết hạn phải kiểm bằng code lúc đọc**
   (đề xuất, phiên chờ); TTL chỉ để dọn rác.

**Langfuse**

5. v3+ đặt credential ở `Langfuse(...)`, **không** ở `CallbackHandler()` — truyền vào handler là
   `TypeError` bị nuốt, mất sạch trace.
6. Danh tính qua metadata `langfuse_user_id` / `langfuse_session_id`.
7. Cần gói `langchain` đầy đủ (không chỉ `langchain-core`) và model definition khớp tên model.

**Agent** — *nhiều lỗi dưới đây không làm test nào đỏ vì fail-soft nuốt bằng chứng.*

8. Khối bối cảnh **phải nói hôm nay là ngày nào, ở dòng đầu** — thiếu thì model đặt lệch cả năm mà
   chuỗi ISO vẫn hợp lệ.
9. Khối bối cảnh **không nằm trong system prompt** (đổi mỗi lượt, cache không bao giờ trúng); đặt
   ngay sau system prompt. Thứ tự này từng bị code làm ngược và có test khoá cái sai lại.
10. Gói cũ chỉ cho tag `respond` stream tới khách — lọt tag khác là JSON chạy ngang màn hình. Gói
    `salon/` **không stream gì cả**: Socket.IO không còn sự kiện `token`, câu tới khách chỉ đi qua
    `complete` sau khi guard duyệt.
11. Cạnh tools→END của `create_agent` xét theo **tên** tool, bỏ qua `ToolMessage.status` — một tool
    `return_direct` bị guard từ chối vẫn kết thúc lượt. Đừng dùng `return_direct` cho tool kết thúc.
12. Nhiều tool call cùng vòng chạy trong **cùng một superstep**, đọc cùng snapshot; khoá state nào
    nhiều call có thể ghi thì **phải có reducer**, không thì `InvalidUpdateError` hoặc đếm thiếu.
13. Mỗi tool call phải có **đúng một** ToolMessage đi sau trước lần gọi model kế tiếp, không thì API
    trả 400 (`INVALID_TOOL_RESULTS`). Cẩn thận khi tự dựng / gọi lại lịch sử.
14. Structured output của agent con: **schema hẹp theo việc** (`ToolStrategy`). Union rộng làm model
    chọn sai kiểu (đo Azure: sai 5/8 lần).
15. `interrupt()` khi resume **chạy lại cả node từ đầu**; agent `checkpointer=False` thì không có
    interrupt. Gọi agent con bên trong tool thì interrupt dừng cả lượt ngay tại agent con. Spike 09/11
    (Azure thật) thấy thêm: tin thường gửi lúc graph đang treo để lại tool call không có ToolMessage,
    Azure trả 400 và thread hỏng (`docs/architech-agent.md` luật 7).
16. Agent con báo `proposal_id` / giờ / giá thì **code đối chiếu với ToolMessage và DB**, không tin
    chữ model.

**Prompt**

17. **Prompt tự bảo model lặp thì model sẽ lặp.** Prompt viết bằng **tiếng Anh**.
18. **Không câu mẫu tiếng Việt trong prompt và docstring tool.** Luật *định dạng* (viết giờ thế nào)
    có lợi; ví dụ cho luật *phân loại* có hại — biến ranh giới ngữ nghĩa thành so khớp từ. Luật định
    dạng tham chiếu đầu ra của tool.
19. **Prompt chặt hơn khuếch đại cả luật viết ẩu** — "gọi tool NGAY" từng thành lỗ hổng quyền riêng
    tư khi model tuân thủ sát chữ.
20. **`pytest` không bắt được lỗi hội thoại.** Đổi prompt / luồng thì phải đọc transcript chạy thật
    (hỏi chủ dự án trước khi chạy Azure). Rubric chấm điểm bão hoà, đọc tay mới là bằng chứng.
21. **Không từ điển từ khoá ý định.** Từng có bộ từ phủ định khiến khách không bao giờ huỷ được ("ừ
    huỷ đi" là đồng ý). Code chỉ được quét *dữ kiện* (giờ, ngày, thứ), không quét ý định. Regex có
    ngân sách, `tests/test_regex_budget.py` canh.

**Nghiệp vụ**

22. **Dời lịch không phải đặt thêm.** `propose_appointment` nhận `replaces_appointment_id`; duyệt thì
    `AppointmentService.reschedule` **đặt mới trước, huỷ cũ sau** (đặt hụt thì khách vẫn còn lịch
    cũ). Ngoại lệ: giờ mới chồng lên chính lịch cũ thì nhả cũ trước, sau khi đã kiểm quá khứ / giờ mở
    cửa. Đề xuất dời / huỷ chụp lịch đích (`target_snapshot`); lúc duyệt mà lịch đã đổi qua REST /
    Telegram thì không duyệt.
23. **Id để dời / huỷ lấy từ khối bối cảnh** (dòng "Lịch sắp tới", dựng từ DB mỗi lượt), enum id dựng
    lại mỗi lượt — model không nhắm được lịch không phải của khách.
24. **Tóm tắt hội thoại chỉ ghi diễn biến, không ghi trạng thái lịch — khối bối cảnh thắng.** Slots do
    LLM sinh luôn thua khối bối cảnh; không đường code nào đặt / dời / huỷ dựa trên chúng.
25. Mọi chữ tự do của khách chảy vào khối bối cảnh (`full_name`, `note`) đi qua
    `app/core/text.py::single_line`, và câu khách luôn nằm **sau nhãn untrusted**.

**Telegram**

26. Trả `answerCallbackQuery` **trước** khi làm việc — callback hết hạn 10 giây, chậm là chủ tiệm
    bấm lại và đặt bận hai lần.
27. `get_updates` phải **ném lỗi ra ngoài** — nuốt thì mạng đứt trông y hệt "không có tin mới".

**Frontend**

28. Hết giờ bận **không có sự kiện nào từ server** — `useShopStatus` đặt `setTimeout` theo
    `minutes_left`. `socket.io-client` **gộp kết nối theo URL** — phải `acquireSocket()` /
    `releaseSocket()` đếm tham chiếu.

## Ràng buộc giao diện — không được phá

Khách bấm điện thoại ngoài đường hoặc trong tiệm ồn. Đây không phải thẩm mỹ.

- Cỡ chữ nền **19px**, nút cao tối thiểu **56px**, rộng hết chiều ngang.
- Tương phản tối thiểu **4.5:1**. Dùng `#0369A1`, `#047857`, `#B91C1C`. **Không** dùng `#0284C7`
  (4.10) hay `#059669` (3.77) cho chữ.
- Font **Be Vietnam Pro** (Atkinson Hyperlegible không có bộ ký tự tiếng Việt).
- Mọi trạng thái diễn đạt **bằng chữ**, không chỉ bằng màu.
- **Không hiện tên tool kỹ thuật** cho khách: `find_free_slots` → "Đang xem lịch trống…".
- Nút Đồng ý / Không: xếp dọc, rộng hết ngang, khoá khi mất mạng, chỉ hiện cho đề xuất đang chờ.
- Huỷ lịch phải qua một bước xác nhận **tại chỗ**, không modal.
- Thời gian hiển thị bằng **mốc**, không bằng khoảng: "xong lúc 3 giờ rưỡi chiều", không "còn 30
  phút" — kể cả câu của AI, vì câu đó nằm lại trong lịch sử chat.
- Access token giữ **trong bộ nhớ**, không `localStorage`. Gọi `/api/v1/auth/*` phải
  `credentials: "include"`.
- `complete.answer` **thay** chữ đang hiện (không append) — tầng gác dựa vào điều này.

## Bảo mật — đã rà 2026-08-15 và trong `docs/ARCH_SECURITY_REVIEW.md`

- **Kiểm hạn mức đăng nhập TRƯỚC khi so mật khẩu** — `RateLimitService` tách `check()` và `hit()`.
- **`token_version` là số đếm, không phải mốc thời gian.** Đổi mật khẩu phải xoá **mọi** refresh
  token.
- **Refresh token đi bằng cookie `HttpOnly`**, chuỗi ngẫu nhiên (không JWT), chỉ lưu hash SHA-256, có
  **ân hạn 30 giây**.
- **`reset-password` phân biệt 204/404 là CỐ Ý** — endpoint đổi mật khẩu ngay, luôn-204 là nói dối.
- Rủi ro đã chấp nhận: quên mật khẩu của **khách** chỉ cần SĐT. **Admin thì không** — khôi phục qua
  Telegram, spec `docs/superpowers/specs/2026-08-15-admin-password-reset-telegram-design.md`.
- Tiêm prompt qua tên / ghi chú: bán kính thiệt hại chỉ là câu trả lời cho **chính khách đó**; vẫn giữ
  `single_line` + nhãn untrusted.

## Việc còn dở ngoài spec dựng lại

- **Trước khi mở cho khách thật**: SEC-01 (Socket.IO chưa kiểm `token_version`), tách compose prod,
  sao lưu Mongo, HTTPS, CI — ở `PROD_CHECKLIST.md`.
- Lỗi giờ "15h30" bị cắt về 15:00 (g06) chưa có task.
- `slot_unavailable` vẫn gộp ba nguyên nhân (tiệm nghỉ, ngoài giờ, giờ quá khứ); `already_yours` đã
  tách riêng.
- Test DB không chịu được hai `pytest` song song (chung `chatbot_test_db`) — chạy một lần một.
- `scripts/terminal_chat.py` (untracked, của chủ dự án, không được sửa thay) — vẫn import gói
  `booking_graph` đã xoá nên đang hỏng; Task 16 (chuyển Socket.IO + xoá `app/agents/booking/`) đã
  xong, chủ dự án tự đổi sang `app.agents.salon` khi cần dùng lại script này.
