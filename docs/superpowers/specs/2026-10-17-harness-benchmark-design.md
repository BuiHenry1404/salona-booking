# Benchmark harness agent: tự xây (A) và theo LangChain (B) — thiết kế

Ngày: 2026-10-17. Nhánh: `bench/harness-benchmark`.

## Vì sao

Dự án sẽ **refactor harness của agent sang khung LangChain** (`create_agent` +
middleware + `interrupt` + checkpointer Mongo, agent vẫn không có tool ghi), làm
theo lộ trình 6 giai đoạn (0 → 5). Benchmark này tồn tại để:

1. Có **số liệu gốc** của harness hiện tại (A) trước khi refactor.
2. Sau mỗi giai đoạn của B, **chứng minh refactor không làm hệ thống tệ đi**, và
   chỉ ra chỗ tốt lên — bằng số liệu và bằng hội thoại đọc được.
3. Làm lưới an toàn cho giai đoạn 1 của B ("đổi vòng lặp, giữ nguyên hành vi").

Bối cảnh: `docs/AGENT_GRAPH_REVIEW.md` (lỗi G-xx), trang đánh giá "Tự xây hay
theo LangChain" (trace mã nguồn hai phía).

## Người dùng đã quyết

- So sánh **theo nhánh git**: cùng một bộ benchmark chạy lên nhánh A và từng
  nhánh B. Không giữ hai harness song song trong code.
- Hai đầu ra: **số liệu của harness** và **hội thoại edge case đặt cạnh nhau**.
- Gọi **Azure OpenAI thật**. Không giới hạn chi phí.
- Hai kiểu "khách":
  - **Lời khách viết sẵn** cho edge case đã biết → so song song từng lượt.
  - **LLM đóng vai khách lớn tuổi** cho kịch bản thông thường → đo tỉ lệ đạt và pass^k.
- B được dựng **đầy đủ theo lộ trình**, mỗi giai đoạn là dự án riêng (không thuộc
  spec này).
- Làm trên **nhánh mới**.

## Mục tiêu

- Một lệnh chạy trọn bộ kịch bản lên một git ref, lưu kết quả gắn với commit SHA.
- Một lệnh so hai (hoặc nhiều) kết quả đã lưu, xuất báo cáo HTML: bảng số liệu và
  hội thoại song song có đánh dấu chỗ khác nhau.
- Kết luận đáng tin: cùng model, cùng prompt hệ thống (của nhánh đó), cùng kịch
  bản, nhiều lần chạy.

## Không làm (non-goals)

- Không phải cổng CI; không chạy trong `pytest` mặc định.
- Không đo giao diện React, không đo luồng admin/REST/Telegram.
- Không load test (nhiều khách cùng lúc).
- Không dựng B ở đây.

## Phản mục tiêu (thất bại dù chạy được)

- Benchmark "thích nghi" để hệ thống qua: nới kỳ vọng theo kết quả, judge dễ dãi.
  Kỳ vọng của kịch bản viết trước, không sửa theo output.
- Kết luận từ **một** lần chạy. Mọi chỉ số đúng/sai báo kèm số lần chạy.
- Hai bên chạy khác model, khác cấu hình Azure, khác dữ liệu tiệm.
- Benchmark ghi nhầm vào DB dev, hoặc giành kết nối Telegram của server dev.

## Ràng buộc

- Mỗi nhánh được đo chạy **server thật** của chính nó (`uvicorn main:app`, 1
  worker), benchmark nói chuyện qua HTTP + Socket.IO như khách thật — **hộp đen**,
  không import code của nhánh được đo.
- Config chỉ đọc từ `.env` (AGENTS.md). Mỗi worktree được đo có `.env` riêng:
  `MONGO_DB_NAME` riêng (vd `bench_<sha7>`), cổng riêng, **Telegram tắt** (comment
  `TELEGRAM_BOT_TOKEN` — chỉ một kết nối `getUpdates` mỗi token), cùng khoá Azure.
- Ngày giờ: server dùng đồng hồ thật, không ghim được. Kịch bản phải mô tả ngày
  tương đối ("ngày mở cửa gần nhất sau hôm nay") và tự tính ra câu nói / kỳ vọng
  lúc chạy. Dữ liệu tiệm (giờ mở cửa, ngày nghỉ) được benchmark đặt cố định qua
  REST admin trước mỗi lượt chạy.

## Thiết kế

### 1. Chỗ để code và cách chạy một ref

- Code benchmark nằm ở thư mục `bench/` trên nhánh `bench/harness-benchmark`.
  Đây là nhánh "công cụ", không phải nhánh được đo.
- Chạy một ref: tạo `git worktree` của ref đó ra thư mục tạm, chép `.env` gốc,
  đổi các khoá ở mục Ràng buộc, khởi động server của worktree trên cổng riêng, chờ
  `/api/v1/health/ready`, seed dữ liệu (admin, khách, giờ tiệm), chạy kịch bản,
  tắt server, giữ worktree cho tới khi xoá thủ công hoặc theo cờ `--cleanup`.
- Kết quả lưu ở `bench/results/<sha7>-<ref-tên>/<timestamp>/` (JSON + transcript).

### 2. Kịch bản

Một kịch bản là một file dữ liệu (định dạng do builder chọn: YAML hoặc Python
dataclass), gồm:

- `id`, `title`, `kind`: `scripted` | `simulated`, `tags` (vd `edge`, `G-01`).
- `setup`: lịch có sẵn của khách (ngày tương đối + giờ), lịch của khách khác
  chiếm chỗ, trạng thái chủ tiệm.
- **scripted**: danh sách lượt khách. Mỗi lượt là câu chữ, hoặc một **ý** cần lớp
  chuyển dịch (`agree`, `decline`) — xem mục 3.
- **simulated**: mục tiêu và tính cách khách cho LLM đóng vai (vd "đặt làm móng
  chiều mai, gõ không dấu, hay đổi ý"), giới hạn số lượt.
- `expect`:
  - trạng thái DB cuối: lịch nào phải tồn tại (giờ, dịch vụ), lịch nào phải còn
    nguyên / đã hủy, **không có lịch nào khác** được tạo hoặc hủy;
  - (tuỳ chọn) chuỗi phải có / không được có trong câu trả lời cuối;
  - (tuỳ chọn) số lượt tối đa.

**Bộ kịch bản ban đầu** (builder viết đủ, có thể thêm):

- Edge case **scripted**, lấy từ review đã tái hiện:
  - G-01: từ chối viết tắt / miền Nam — "dạ ko em", "dạ hông", "dạ khoan".
  - G-02: đang chốt hủy mà hỏi lại — "hủy cái nào vậy em?", "để chị hỏi thằng Huy đã".
  - G-03: đồng ý kèm giờ mới — "dạ 4 giờ nha em".
  - Bug `is_affirmative` cũ: câu tán gẫu có "phải"/"được" khi đang có pending.
  - G-05/G-06: "hôm nay không được, mai 3h chiều", "mai 15h30".
  - G-07: hết vòng tool sau khi đã giữ chỗ, rồi "dạ vâng để chị gọi" (khó gây ra
    chắc chắn bằng model thật — xem mục 5, dùng proxy lỗi nếu cần).
  - G-08/G-09: hai lịch trong một câu; "mai 9 giờ hay 10 giờ còn chỗ không" rồi "dạ 9 giờ".
  - G-10: dời lịch mà không nói rõ lịch nào.
  - Pending hết hạn rồi mới "ừ" (cần chờ > 10 phút — đánh dấu `slow`).
  - Riêng B (khi có nút): khách gõ chữ thay vì bấm nút.
- Kịch bản **simulated**: chuyển 20 kịch bản LLM-xx trong
  `docs/test-scenarios/02-llm-chat-scenarios.md` thành mục tiêu cho khách giả lập,
  cộng các luồng đặt / dời / hủy cơ bản.

### 3. Lớp chuyển theo phiên bản (adapter)

Benchmark nói chuyện với server qua một **adapter**, mỗi phiên bản giao thức một
adapter:

- `AdapterA` (giao thức hiện tại): đăng nhập REST, Socket.IO `send_message`, nhận
  `turn_started` / `token` / `tool_started` / `tool_finished` / `complete` / `error`.
  Ý `agree` → gõ "ừ", `decline` → gõ "không".
- `AdapterB` (viết khi giai đoạn 2 của B định nghĩa giao thức nút bấm): nhận thẻ
  xác nhận, gửi quyết định. Ý `agree` có **hai biến thể**, chạy cả hai: bấm nút, và
  gõ chữ thay vì bấm.
- Chọn adapter theo ref (khai báo khi chạy, hoặc dò bằng một sự kiện/endpoint mà
  B công bố). Spec này chỉ yêu cầu **giao diện adapter** ổn định để `AdapterB` cắm
  vào sau.

Adapter ghi lại cho mỗi lượt: câu khách, các sự kiện kèm mốc thời gian, câu trả
lời cuối (`complete.answer`), tool đã gọi.

### 4. Chấm điểm

Theo từng lần chạy kịch bản:

- **Đúng/sai (chính):** so trạng thái DB cuối (đọc thẳng Mongo của worktree) với
  `expect`.
- **Ghi sai (nghiêm trọng):** bất kỳ lịch nào được tạo / hủy / dời ngoài tập kỳ
  vọng — lịch ma, ghi khi chưa đồng ý, sai giờ, hủy nhầm. Đếm riêng, báo riêng.
- **Chất lượng hội thoại:**
  - luật tất định: câu tiếng Anh lọt ra khách, "cô/chú/bác/con", giờ dạng `HH:MM`,
    câu nhận vơ "đã đặt xong" khi DB không đổi;
  - LLM judge: dùng lại rubric của `scripts/score_transcript.py` (5 trục, 1–5);
  - số lượt để hoàn thành; số lần bot hỏi lại điều khách đã nói (từ judge).
- **Hiệu năng:** thời gian từ gửi tin tới token đầu, tới `complete`, theo lượt.
- **Chi phí:** số lần gọi LLM và token mỗi lượt, lấy từ Langfuse local theo
  `session_id` (= user_id) nếu Langfuse bật; không có thì để trống chỉ số này,
  không ước đoán.

Tổng hợp mỗi ref: tỉ lệ đạt, **pass^k** (đạt cả k lần, mặc định k = 4), tổng ghi
sai, trung vị / p90 độ trễ, token trung bình, điểm judge trung bình theo trục.

### 5. Chịu lỗi (proxy Azure)

Một **proxy HTTP tương thích Azure OpenAI** chạy trong benchmark. `.env` của
worktree trỏ `AZURE_OPENAI_ENDPOINT` vào proxy; proxy chuyển tiếp tới Azure thật,
và theo kịch bản thì chèn lỗi: trả 500, treo quá N giây, trả tool call lặp lại để
đẩy agent tới giới hạn vòng. Kịch bản chịu lỗi khai báo kiểu lỗi và lượt áp dụng.
Kỳ vọng: không ghi sai, khách nhận câu tiếng Việt, lượt sau vẫn chạy được.

### 6. Báo cáo

- `bench compare <run-a> <run-b> [...]` xuất một file HTML tự chứa:
  - bảng tổng hợp theo ref, chỉ số nào tệ đi thì đánh dấu;
  - danh sách kịch bản: đạt/không theo ref, số ghi sai;
  - với mỗi kịch bản scripted: **hội thoại song song** theo lượt — câu khách, câu
    trả lời mỗi bên, tool đã gọi, thay đổi DB sau lượt; lượt nào khác nhau thì
    đánh dấu;
  - với kịch bản simulated: transcript từng bên (không căn theo lượt).
- File HTML có thể đăng thành artifact để đọc trên claude.ai.

### 7. Thứ tự làm

1. Benchmark + `AdapterA`, chạy trên nhánh A hiện tại (`docs/arch-security-review`
   HEAD) → **số liệu gốc**.
2. Khi từng giai đoạn của B xong: chạy lại, so với gốc. `AdapterB` viết cùng giai
   đoạn 2 của B.

## Quyết định bổ sung (sau vòng đọc thử của builder)

Các điểm dưới đây là **quyết định của người thiết kế**, chưa qua người dùng duyệt
riêng — đánh dấu để người dùng kiểm khi đọc.

1. **Số liệu gốc = A ở HEAD hiện tại của nhánh này** (code giống
   `docs/arch-security-review`). Spec/plan `2026-10-04-agent-turn-harness` **không
   làm nữa** (người dùng đã chuyển hướng sang framework), nên A không đổi và không
   có ứng viên thứ ba.
2. **Hai loại "không tệ đi":** báo cáo tách riêng
   - *đúng so với kỳ vọng* (A được dự đoán trượt các edge case G-xx — đó là lỗi đã
     biết, không phải lỗi của benchmark);
   - *khớp hành vi so với một ref gốc*: từng lượt cùng tool được gọi và cùng thay
     đổi DB. Dùng để kiểm giai đoạn 1 của B ("đổi vòng lặp, giữ nguyên hành vi").
3. **Chạy lại A cạnh mỗi lần đo B**, không dùng lại số liệu cũ. Lệnh chạy nhận
   nhiều ref và chạy xen kẽ trong cùng khung giờ (mỗi ref một worktree, cổng, DB).
   Báo cáo cảnh báo khi hai kết quả được so khác ngày, khác khung giờ (sáng/chiều,
   ngày trong tuần) hoặc khác deployment Azure.
4. **Kịch bản scripted đi lệch:** mỗi lượt quan trọng có *điều kiện tiền đề* suy
   từ tín hiệu hộp đen (vd "lượt trước bot đã gọi `propose_appointment` hoặc
   `cancel_appointment` thành công" — đọc từ sự kiện `tool_started/tool_finished`
   hoặc thẻ xác nhận ở B). Không đạt tiền đề → lần chạy đó **không hợp lệ**, báo
   riêng, không tính vào mẫu số. Benchmark **không** đọc trạng thái nội bộ như
   `conversations.pending_confirmation` hay checkpoint.
5. **Kịch bản LLM-xx:** file có 35 kịch bản (LLM-01…35), không phải 20. Chuyển
   **tất cả kịch bản chuyển được**. LLM-32 (qua nửa đêm) và LLM-33 (chờ > 10 phút)
   gắn tag `slow`/`clock`, chỉ chạy khi bật cờ. LLM-34 (Azure lỗi/chậm) vào nhóm
   chịu lỗi qua proxy. LLM-35 (Mongo đứt) bỏ.
6. **Hợp đồng coi là cố định qua các giai đoạn B:** REST admin dùng để seed
   (`/auth/users`, `/shop/hours`, `/shop/busy`, `/appointments` với `for_user_id`,
   `/health/ready`) và schema collection `appointments` (`start_at`, `status`,
   `note`, `user_id`). Refactor chỉ đụng harness của agent. Nếu giai đoạn nào đổi
   các thứ này, adapter mở rộng để bao cả lớp seed và lớp đọc DB — giao diện
   adapter nên tách ba phần: chat, seed, đọc DB.
7. **Client LLM riêng của benchmark:** judge và khách giả lập dùng client do
   benchmark tự tạo từ khoá trong `.env` của benchmark, **không** import
   `build_chat_model` của nhánh được đo (giữ hộp đen).
8. **Model cho judge và khách giả lập: `claude-sonnet-4-6`** (người dùng đã có
   deployment). Khác họ model với bot (`gpt-5.4-mini`), nên không còn cảnh model tự
   chấm mình. Gọi qua Anthropic Python SDK, client `AnthropicFoundry` (Microsoft
   Foundry); cấu hình trong `bench/.env`: tên resource, khoá, tên model/deployment
   (mặc định `claude-sonnet-4-6`). Nếu deployment thực ra không nằm trên Foundry
   (vd Anthropic API trực tiếp), chỉ đổi lớp tạo client — phần còn lại giữ nguyên.
   Judge trả JSON theo schema (structured output), không bóc JSON từ văn bản tự do.

## Để builder tự quyết

- Định dạng file kịch bản, cấu trúc module trong `bench/`, CLI (argparse/typer).
- Cách LLM đóng vai khách (prompt, model — dùng cùng Azure deployment hay khác),
  điều kiện dừng hội thoại simulated.
- Cách sinh HTML (template thuần hay thư viện), miễn file tự chứa.
- Chi tiết proxy lỗi.
- Có tái dùng code của `scripts/llm_scenarios.py` / `chat_e2e_transcript.py` hay
  viết lại gọn hơn.
