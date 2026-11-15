# CONTEXT — vì sao, và đừng phá

Viết lại 08/11/2026, khi bắt đầu dựng lại tầng agent. Giữ phần **tại sao** và những gì **không được
phá**. Tài liệu cũ (spec/plan agent từ 8/2026 tới 11/2026, `REPO_AUDIT.md`, kịch bản kiểm thử, nhật
ký) đã xoá khỏi cây thư mục; xem lại bằng git: `git show 72c98fc:<đường dẫn>`.

| Cần gì | Đọc file |
|---|---|
| Đang làm gì, làm tiếp gì | Mục **Trạng thái** ngay dưới + spec [`docs/superpowers/specs/2026-11-14-guard-rebuild-design.md`](docs/superpowers/specs/2026-11-14-guard-rebuild-design.md) + plan [`docs/superpowers/plans/2026-11-14-guard-rebuild.md`](docs/superpowers/plans/2026-11-14-guard-rebuild.md) (+ sổ `.superpowers/sdd/2026-11-14-guard-rebuild/progress.md`, git-ignored, **đọc trước tiên**; chỉ xoá khi đã tổng kết) |
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

## Trạng thái — 15/11/2026 (tối)

### Nhánh và quy ước
- Nhánh **`feat/llm-orchestrator`**: chưa push, chưa merge. HEAD lúc dừng: **`b27d5a3`**. Luồng: nhánh
  việc → `henry/develop` → `main`, không commit thẳng `main`. Commit message tiếng Anh, ngắn.
- **Working tree dùng chung với một phiên khác.** Phiên làm trang demo từng chuyển working tree sang
  `feat/demo-autoplay`, làm 3 commit plan lọt vào nhánh đó (đã cherry-pick về; nhánh
  `feat/demo-autoplay` vẫn còn bản sao, chủ dự án quyết xoá hay giữ).
  - Luôn kiểm `git branch --show-current` trước khi commit.
  - Không `stash` / `checkout` / `reset`.
- **File của chủ dự án, đừng add:** `docs/superpowers/review.md`, `image.png`,
  `scripts/terminal_chat.py` (script này import gói đã xoá nên đang hỏng).
- **Test hỏng có sẵn, chưa ai tìm nguyên nhân:**
  `tests/salon/subagents/test_shop_info.py::test_parse_then_is_open_at_answers_a_relative_question`
  (`KeyError: 'iso'`). Có thể phụ thuộc ngày chạy. Thêm một test tự bỏ qua sau 3 giờ chiều trong
  `tests/test_telegram_handlers.py` (bình thường).
- Chạy pytest **từng lệnh một** (DB test dùng chung): `.venv/bin/python -m pytest -q` (~6 phút).
  Trước khi commit chạy **toàn bộ** backend.
- 8 commit của đợt 14–15/11 do agent Sonnet làm ghi trailer `Co-Authored-By: Claude Sonnet 5` thay vì
  dòng quy định. Muốn sửa thì phải rebase trước khi push (chủ dự án quyết).

### Lịch sử ngắn (đã xong)
- Rebuild tầng agent (plan `2026-11-08-subagents-rebuild`): 4 agent, gói `app/agents/salon/`.
  Log chạy thật 1–4: `.superpowers/sdd/2026-11-08-subagents-rebuild/live-{1..4}.log`.
- Chạy thật 1, 2 không đạt; chạy thật 3 **đạt** (guard cũ). Chủ dự án chốt: phát hiện giờ kín thì luôn
  báo khách; giờ hỏi kín mà không có đề xuất thì luôn là `need_info`.
- **Mức C** (11/11, câu trả lời JSON trích id): chạy thật 5 — A đạt, **B không đạt** (mời giờ kín, câu
  cứng, "08:00"). Log `.superpowers/sdd/2026-11-11-level-c/live-5-{A,B}.log`. Chủ dự án kết luận lỗi
  sinh ra từ chính lớp guard → xây lại.
- Đợt 13/11 ("câu trả lời theo kế hoạch của code", model viết lại câu mẫu, guard đọc câu chữ) dừng ở
  Task 2b, **đã bị thay** bởi đợt 14/11.

### Đợt 14–15/11 — xây lại guard: planner JSON → LLM → validator (code xong, chưa merge)
- **Spec:** `docs/superpowers/specs/2026-11-14-guard-rebuild-design.md` (thắng khi lệch).
- **Plan:** `docs/superpowers/plans/2026-11-14-guard-rebuild.md` — 11 task, **đã xong cả 11**.
- **Sổ thực thi** (git-ignored, **chưa xoá**, đọc trước tiên khi làm tiếp):
  `.superpowers/sdd/2026-11-14-guard-rebuild/progress.md` — mọi task, commit, dòng `Ruling:`,
  `minor (deferred)`, kết quả review, các lần thử live. Cùng thư mục: `final-fix-wave.md`,
  `residual-fix*.md`, `final-fix-report.md` (có nguyên văn prompt mới).
- **Đã xây** (`app/agents/salon/guard/`):
  1. `results.py`: gom dữ kiện code biết của lượt thành `TurnResults` (giờ, đề xuất, quyết định, phiên
     chờ, **dịch vụ của lượt** — chỉ dịch vụ agent trả trong lượt + của đề xuất/quyết định + của lịch
     được kể; giờ hợp lệ = giờ của lượt + mọi giờ trong khối bối cảnh + giờ khách gõ).
  2. `planner.py`: `ReplyPlan` = `taken` (giờ kín, code báo) + `acts` (`failed` / `done` / `released` /
     `proposal` / `offer` / `price` / `appointments` / `hours` / `busy`) + `ask` (câu hỏi phiên chờ,
     `fields` + `options`). Giờ là nhãn nói, giá là chuỗi nói ("120 đến 150 nghìn").
     Phiên chờ mở ở lượt trước được **hỏi lại chỉ khi trong lượt đã có agent khác chạy** và plan chưa
     có câu hỏi / câu báo đặt xong / thất bại kèm giờ gần nhất.
  3. Khối bối cảnh: `<context>…</context>` (dòng chữ "Nhãn: giá trị", lọc `<` `>` trong chữ khách) +
     `<reply_plan>JSON</reply_plan>`. Model viết **chữ thường một lần**; mức C đã xoá hẳn.
  4. `validator.py`: chỉ kiểm dữ kiện, **không từ điển ngữ pháp**. Mã: `bad_tool_call`,
     `pending_undecided`, `plan_missing`, `unknown_time`, `unknown_price`, `wrong_service`,
     `taken_mentioned`, `machine_time`, `missing_question`, `missing_phone`/`phone_not_allowed`,
     `empty`, `foreign_script`, `repeat` (**chỉ khi plan rỗng**).
  5. Mọi lỗi: viết lại **1 lần** với lời nhắc JSON; sai lần hai → câu mẫu `templates.py`. Câu báo giờ
     kín do code ghép **sau** khi kiểm.
  6. Agent con hỏi gộp (`question_fields`). Agent Dịch vụ: tìm ra **đúng một** dịch vụ (tên hoặc tên
     gọi tắt) là xác định; nhiều dịch vụ cùng khớp thì hỏi lại. Danh mục mock có thêm **"Cắt tóc
     nam"** (DB nào cũng phải chạy lại `scripts/seed_services.py`).
  7. Xưng hô: tool `remember_address(form)` (anh, chị, cô, chú, bác, ông, bà) — khách tự xưng thì lời
     khách thắng dòng `Address`. Tiền tố tên admin gõ (Cô/Chú/Bác/Ông/Bà) cho đúng cách xưng đó. Một
     lần chuyển dữ liệu lúc khởi động (dấu ở collection `migrations`) đã đổi các "Cô X"/"Chú X" đang
     lưu "chị"/"anh". `detect_self_address` (regex) đã xoá.
  8. `scan_money` đọc đúng "1 triệu 2", "1,2 triệu", "1 triệu rưỡi", không nối qua dấu phẩy / giờ.
     Sổ lượt: quyết định chưa báo tìm trong 24 giờ, không tính lượt hiện tại.
  9. Prompt Orchestrator viết lại theo lượt: *What you receive* → *How a turn works* (gọi đủ agent
     trước, viết câu trả lời theo **plan cuối cùng**) → *Reply plan* → tool, dịch vụ, đề xuất chờ,
     phiên chờ → cách viết.
- **Kiểm:** toàn bộ suite **963 đạt**, chỉ hỏng test có sẵn. Bộ đo câu thật
  (`tests/salon/guard/accuracy/cases.json`): **0/30 câu đúng bị chặn nhầm, 0/18 câu sai lọt**,
  2/2 lọt chấp nhận (báo "đã đặt" khi mới đề xuất; xưng hô sai).
- **Review:** từng task có review; review cả nhánh bằng Fable ("merge được sau khi sửa") → một đợt
  sửa (F1–F11 + 4 lỗi lần thử live) → 3 vòng sửa nhỏ do chủ dự án duyệt. Mọi vòng đã review lại sạch.
- **Quyết định chủ dự án 14–15/11** (đừng mở lại): planner JSON cho Orchestrator, bọc thẻ; validator
  chỉ dữ kiện, bỏ mọi từ điển ngữ pháp (`claim`, `pronoun`, `english`, `taken_offered`…); giờ kín do
  code báo; mọi lỗi viết lại 1 lần rồi câu mẫu; hỏi gộp; một tin nhiều việc thì làm hết; xưng hô qua
  `remember_address` + memory/context; chuyển dữ liệu `self_address`; thêm "Cắt tóc nam"; không dùng
  embedding cho memory (mẫu "profile", Mongo không có vector search).

### ĐANG DỪNG Ở ĐÂY — lần thử live cuối 15/11 (server dev, Azure thật)
Lần thử với code mới nhất: không lượt nào bị guard chặn; đúng: làm hết việc trong một tin, giờ kín do
code báo, xưng hô "cô/chú/bác", "hấp dầu" không bị hỏi lại, khách "thôi" không bị hỏi lại. Còn **2 lỗi**:
1. **P10 chưa có tác dụng:** "cô muốn cắt tóc thứ 4 lúc 2h chiều, giá bao nhiêu?" → bot hỏi nữ hay
   nam (đúng) → "cắt tóc nữ" → bot chỉ báo giá, **quên đặt 2 giờ chiều thứ 4**. Luật prompt đã thêm
   (`b27d5a3`) nhưng model không làm theo.
2. **Giá viết dạng số máy** khi bot liệt kê nhiều dịch vụ: "100000-150000". Validator cho qua (giá vẫn
   trong khoảng).

**Chủ dự án cần chọn một** (đã hỏi, chưa trả lời):
- **(1)** sửa P10 bằng code: lượt 1 vẫn gọi agent Đặt lịch, lỗi `service_unclear` thì code mở phiên
  chờ ghi giờ khách xin; lượt sau dịch vụ rõ thì plan có hành động "tiếp phiên đặt lịch" (guard kiểm
  được) — đụng `dispatch.py`, agent Đặt lịch, planner (~40–60 phút + review); **cộng** mã
  `machine_price` (số ≥ 5 chữ số không đơn vị → viết lại) và luật đọc giá trong prompt;
- **(2)** chỉ `machine_price` + luật đọc giá, P10 để sau;
- **(3)** dừng sửa, tổng kết, mở PR, chạy thật A/B.

### Làm tiếp
1. Đọc sổ `.superpowers/sdd/2026-11-14-guard-rebuild/progress.md`, `git log --oneline -15`. Hỏi chủ
   dự án chọn (1)/(2)/(3) ở trên; làm theo `superpowers:subagent-driven-development` (agent làm +
   agent review lại; dùng `implementer-rules.md`, `global-constraints.md` trong thư mục sổ).
2. Thử lại bằng server thật nếu sửa (cách chạy dưới).
3. **Hỏi chủ dự án** rồi chạy thật kịch bản A và B (cần 2 lần đạt; đọc transcript theo anti-goals;
   thêm câu mới vào `cases.json`):
   - `PYTHONPATH=. .venv/bin/python scripts/test_live_10_turns.py` (**xoá sạch DB dev**);
   - `PYTHONPATH=. .venv/bin/python scripts/test_live_10_turns.py --keep-db`.
4. Tổng kết: liệt kê mọi dòng `Ruling:` trong sổ cho chủ dự án (kèm cái giá nếu sai), rồi mới xoá thư
   mục sổ. `superpowers:finishing-a-development-branch` → **một** PR `feat/llm-orchestrator` →
   `henry/develop`.

### Cách chạy thử bằng server thật
- `PYTHONPATH=. .venv/bin/python scripts/seed_services.py` (nạp danh mục mới), rồi
  `.venv/bin/python -m uvicorn main:app --port 8000` (không `--reload`: sửa code thì khởi động lại).
- Chat qua Socket.IO: `static/socketio_test.html`, hoặc hàm `chat()` trong `scripts/live_e2e.py`
  (đăng nhập `POST /api/v1/auth/login`, emit `send_message`, đợi `complete`). Tài khoản: RUNBOOK mục 5.
- Log guard: tìm `guard_rejected` / `guard_violation` trong log server (có `codes`, câu bot bị loại).

### Để sau
- Đặt nhiều lịch một lần + giữ chỗ (đợt B). Spec cũ ở `git show
  2a26e28:docs/superpowers/specs/2026-11-08-multi-booking-holds-design.md`, phải viết lại.
- Các điểm nhỏ đã hoãn: xem dòng `minor (deferred)` trong sổ (Fable đã phân loại: không điểm nào chặn
  merge). Đáng nhớ: một câu hỏi phiên chờ khách đã bỏ có thể bị hỏi lại một lần trong 10 phút nếu có
  agent khác chạy; câu mẫu dự phòng có thể ra "Dạ … ạ. Dạ …"; giờ kín trùng giờ mở cửa thì không trả
  lời được câu hỏi giờ mở cửa từ bối cảnh.
- Ý tưởng chưa duyệt: guard "chỉ ghi log" để đo model tự đúng bao nhiêu; LLM đọc transcript offline để
  đánh dấu câu nghi sai.

### Đã xây khác plan (controller quyết khi chạy plan, chi tiết trong sổ)

- **R5** agent Đặt lịch có thêm tool `remember_next_request(iso)` để ghi rõ yêu cầu lịch kế ("3h và
  4h"), iso phải trùng kết quả `parse_time`; code không đoán "A hoặc B" thành hai lịch.
- ~~**R8/R9** guard "bảo lãnh" lịch sắp tới cho câu "đã đặt X"~~ — **đã xoá** cùng `claim` trong đợt
  dựng lại guard 14/11 (validator chỉ dùng dữ kiện, không đọc nghĩa câu).
- **R11** nén lịch sử: trigger AND `{tokens ≥ salon_summary_trigger_tokens, messages ≥ keep + 20}` để
  không tóm tắt lại ở mỗi lần gọi model; config kiểm `keep ≥ 2*tool + model + 2`.
- **R12** câu mẫu hỏi câu của phiên nào thì giữ phiên đó mở (không đóng như các phiên khác của lượt).
- ~~**R13** `run_turn` ghi `users.self_address` từ câu khách tự xưng~~ — **đã thay** (14/11): chỉ tool
  Orchestrator `remember_address` ghi `self_address`; code không quét chữ khách.
- **R14** lượt bấm nút có lịch kế bị cắt ngang thì bỏ đánh dấu "đã báo" để R0 báo lại.
- **R16** dịch vụ: chỉ dịch vụ khách **thật sự chọn** (`ServiceFact.identified`) mới là "đã biết"; luật
  `service_unclear` / `service_required` kiểm trong `propose_appointment` **chỉ cho lịch mới** — dời /
  huỷ / xem lịch không bị chặn, dời không kèm dịch vụ giữ thời lượng cũ.
- Khác: R1 `AppointmentDetail` (REST) tách tên khỏi `AppointmentView`; R4 `LedgerReader` Protocol ở
  `shared/`; R6/R7 câu mẫu khi từ chối đề xuất huỷ / `not_approvable`; R10 hàng rào prompt chỉ bắt
  "Address" ở prompt Orchestrator; R15 câu mẫu nút bấm kèm câu hỏi đề xuất mới khi Orchestrator lỗi.

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
  phụ thuộc `tokens`, `part_of_day` thành enum) và các hàm quét dữ kiện của `tokens.py` vào
  `text_scan.py` (`words`, `digits_in`, `split_sentences`, `scan_spoken_clocks`,
  `scan_times`, `scan_day_offsets`, `scan_weekdays`, cộng `scan_money` thêm sau) — chúng là code
  **kiểm lại** LLM (veto, guard, lọc memory), Orchestrator suy luận không thay được. Gói mới 0 regex.
  (`split_clauses` mang sang cùng đợt nhưng hết người gọi trong `app/` sau Task 8 của đợt guard
  14/11 nên đã xoá — quyết định 21 "không dùng thì xoá".)
- **Tool per agent** (`appointment_task`, `shop_info_task`, `service_task`, cộng `decide_pending`),
  ba tool giao việc dùng chung một hàm điều phối nội bộ. Không single dispatch, **không
  `create_deep_agent`** (kiến trúc = `create_agent` + mẫu Subagents + memory của LangGraph).
- **Bộ nhớ** theo LangGraph "Add memory": ngắn hạn = checkpointer theo thread (cắt khi gửi model,
  `RemoveMessage`, `SummarizationMiddleware` thay `digest`/`slots`); dài hạn = `MongoDBStore`
  namespace `("memories", user_id)`, 3 mục `preferences` / `notes` / `last_visit` (thay dòng "Lần
  trước" đã mất), do một **bước gom** ghi ở lượt đầu của cuộc trò chuyện mới (mỗi ngày VN một cuộc), in vào khối bối cảnh dưới nhãn dữ
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
4. Giữ **transaction Mongo** (replica set 1 node `rs0`); duyệt đề xuất và ghi `salon_turns` trong
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
21. **Không dùng thì xoá** (10/11): code, setting, helper, test không còn ai gọi thì xoá, kể cả dữ liệu
    Mongo cũ. Đã xoá: `orch_turn_timeout_seconds` (gói mới không có trần cả lượt, chỉ trần Orchestrator
    `orch_orchestrate_timeout_seconds`), provider Anthropic/Gemini (luồng chat chưa từng hỗ trợ),
    `api_key` / `verify_api_key`, các helper không ai gọi.

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
`users.self_address` (khách tự xưng) → tiền tố tên admin gõ (`derive_address`, từ 15/11: tiền tố
anh/chị/cô/chú/bác/ông/bà giữ đúng cách gọi đó, "Cô Lan" → "cô Lan"; lúc khởi động seed các khách
chưa có `self_address` và một migration một lần đổi "anh"/"chị" cũ của tên "Cô …", "Chú …"… sang
đúng tiền tố) → **"anh chị"**. Không hỏi model, không đoán từ tên đầy đủ ("Nguyễn Thị Lan").
Từ 14/11, `users.self_address` được ghi bằng tool Orchestrator `remember_address(form)`, chỉ gọi khi
**khách tự nói** cách xưng của mình (`detect_self_address` quét regex cũ đã xoá) — mô hình vẫn không
được đoán, chỉ ghi lại lời khách tự xưng. Khách tự xưng khác dòng `Address` thì lời khách thắng
(prompt bảo gọi `remember_address`).

**Không viết sẵn "con", "cô", "chú", "bác" ở bất kỳ đâu khách thấy** — prompt, câu lỗi, câu hết
phiên, chuỗi frontend. "con" chỉ đi với cô/chú/bác; ghép với anh/chị là sai tiếng Việt. Cô/chú/bác/
ông/bà chỉ tới khách qua dòng `Address` (khách tự xưng hoặc tiền tố admin gõ), không bao giờ là chữ
cố định.

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
