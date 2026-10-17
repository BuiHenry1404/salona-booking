# Benchmark harness agent — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Dựng bộ benchmark hộp đen `bench/` chạy cùng một bộ kịch bản lên nhiều git ref của salona-booking (server thật, Azure thật), chấm bằng trạng thái DB + luật + judge Claude, xuất báo cáo HTML có hội thoại song song; chạy số liệu gốc trên A.

**Architecture:** `bench` là một gói Python độc lập (venv riêng, không import `app/`). Với mỗi ref: `git worktree` → venv riêng → `.env` riêng (DB, cổng, Telegram tắt, Azure qua proxy) → `uvicorn` → seed qua REST → adapter nói chuyện qua Socket.IO → đọc Mongo để chấm. Kết quả JSON theo commit; `bench compare` dựng HTML.

**Tech Stack:** Python 3.12, uv, `python-socketio[asyncio_client]` + `aiohttp`, `httpx`, `pymongo`, `pyyaml`, `anthropic` (client `AnthropicFoundry`), `pytest` + `pytest-asyncio`.

**Spec:** `docs/superpowers/specs/2026-10-17-harness-benchmark-design.md`

## Global Constraints

- Nhánh làm việc: `bench/harness-benchmark`. Không merge, không push.
- **Hộp đen:** không file nào trong `bench/` được `import app` hay `from app` (Task 1 có test fence). Benchmark chỉ dùng HTTP, Socket.IO và đọc Mongo.
- Venv của benchmark: `bench/.venv`, deps ở `bench/requirements.txt`. **Không** thêm gì vào `requirements.txt` gốc.
- Config benchmark chỉ từ `bench/.env` (mẫu `bench/.env.example`); khoá không bao giờ commit (`bench/.env` vào `.gitignore`).
- `.env` của ref được đo: chép `.env` gốc rồi ghi đè đúng 4 thứ — `MONGO_DB_NAME=bench_<sha7>_<runid>`, comment `TELEGRAM_BOT_TOKEN` (một kết nối `getUpdates` mỗi token), `AZURE_OPENAI_ENDPOINT` trỏ proxy khi bật proxy, giữ nguyên mọi khoá khác (cùng deployment Azure cho mọi ref).
- Mỗi ref chạy **1 worker** uvicorn, cổng riêng bắt đầu từ 8100.
- Judge và khách giả lập: model `claude-sonnet-4-6` qua `AnthropicFoundry` (tên lấy từ `bench/.env`, mặc định `claude-sonnet-4-6`). Không dùng model của bot.
- Mỗi lần chạy kịch bản dùng **một khách mới** (SĐT `0987` + 6 số) — tránh trần chat 30 tin/giờ và pending thừa hưởng.
- Giờ tiệm do benchmark đặt cố định trước mỗi lần chạy: `08:00–19:00`, `closed_days=[]`. Ngày trong kịch bản viết tương đối (`tomorrow`, `+2`), phân giải lúc bắt đầu mỗi lần chạy theo giờ VN (UTC+7).
- Kỳ vọng kịch bản viết trước, **không sửa theo output** (phản mục tiêu của spec).
- Test của benchmark: `bench/.venv/bin/python -m pytest bench/tests -q`. Test cần server/Azure thật đánh dấu `@pytest.mark.live`, mặc định bỏ qua.
- Commit message tiếng Anh, ngắn, kết thúc bằng:
  `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>` và
  `Claude-Session: https://claude.ai/code/session_015JyTdz3j1eVM1fJ4H64tzQ`

## Review Focus

1. **Server của một ref không lên** (thiếu dep, cổng bận, Mongo không có): ref đó được ghi `failed_to_start` kèm 50 dòng log cuối, các ref khác vẫn chạy — Task 3 `test_start_failure_is_reported_not_raised`.
2. **Một lượt chat treo** (không có `complete`/`error`): sau 120 s lượt đó ghi `timeout`, kịch bản dừng ở lượt đó và tính trượt, chạy tiếp kịch bản sau — Task 5 `test_turn_timeout_is_recorded`.
3. **Server phát sự kiện ngoài dự kiến** (`rate_limited`, `connected`, sự kiện mới của B): adapter ghi lại vào events, không vỡ; `rate_limited` coi như lỗi lượt — Task 5 `test_unknown_events_are_kept_and_rate_limited_is_error`.
4. **Hai ref chạy lệch ngày** (chạy qua nửa đêm, hoặc so hai run khác ngày): báo cáo hiện cảnh báo — Task 10 `test_compare_warns_on_different_days_or_models`.
5. **Judge trả JSON hỏng, từ chối, hoặc lỗi mạng:** điểm judge của lần chạy đó là `None`, loại khỏi trung bình, không làm hỏng lần chạy — Task 7 `test_judge_failure_yields_none`.

## File Structure

| File | Trách nhiệm |
|---|---|
| `bench/__init__.py`, `bench/__main__.py` | CLI `python -m bench run|compare` |
| `bench/config.py` | `BenchSettings` đọc `bench/.env` |
| `bench/scenario.py` | Dataclass kịch bản, nạp YAML, phân giải ngày tương đối |
| `bench/vi_time.py` | Định dạng giờ kiểu người nói ("3 giờ chiều", "5/10") — tự viết, không import app |
| `bench/workspace.py` | Worktree, venv, `.env` ref, khởi động/tắt server |
| `bench/seed.py` | Seed qua REST: admin, khách, giờ tiệm, bận/rảnh, lịch có sẵn |
| `bench/adapters/base.py`, `bench/adapters/a.py` | Giao diện adapter (chat / seed / đọc DB) và `AdapterA` |
| `bench/dbread.py` | Đọc `appointments` từ Mongo của ref, chụp và so ảnh |
| `bench/score.py` | Chấm theo `expect`, ghi sai, luật chữ, tiền đề, pass^k, khớp hành vi |
| `bench/llm.py`, `bench/judge.py`, `bench/simulate.py` | Client Claude, judge, khách giả lập |
| `bench/proxy.py` | Proxy Azure chèn lỗi |
| `bench/runner.py` | Điều phối nhiều ref, chạy xen kẽ, lưu JSON |
| `bench/tokens.py` | Lấy token/số lần gọi LLM từ Langfuse (tuỳ chọn) |
| `bench/report.py`, `bench/templates/report.html` | Báo cáo so sánh |
| `bench/scenarios/edge/*.yaml`, `bench/scenarios/llm/*.yaml`, `bench/scenarios/fault/*.yaml` | Kịch bản |
| `bench/tests/…` | Test |
| `bench/README.md` | Cách cài và chạy |

---

### Task 1: Khung gói, config, fence hộp đen

**Files:**
- Create: `bench/__init__.py`, `bench/config.py`, `bench/requirements.txt`, `bench/.env.example`, `bench/README.md`, `bench/tests/__init__.py`, `bench/tests/conftest.py`, `bench/pytest.ini`
- Modify: `.gitignore` (thêm `bench/.env`, `bench/.venv/`, `bench/results/`, `bench/.worktrees/`)
- Test: `bench/tests/test_config.py`, `bench/tests/test_fence.py`

**Interfaces:**
- Produces: `BenchSettings` (dataclass) với `repo_root: Path`, `results_dir: Path`, `worktrees_dir: Path`, `base_port: int = 8100`, `mongo_uri: str = "mongodb://localhost:27017"`, `judge_model: str = "claude-sonnet-4-6"`, `foundry_resource: str | None`, `foundry_api_key: str | None`, `langfuse_host: str | None`, `langfuse_public_key: str | None`, `langfuse_secret_key: str | None`, `admin_phone: str = "0901234567"`, `admin_password: str = "chutiem123"`, `turn_timeout_s: float = 120.0`; `load_settings(env_path: Path | None = None) -> BenchSettings` (đọc file, **không** đọc biến môi trường shell — cùng quy ước AGENTS.md).

- [ ] **Step 1: Viết test**
  - `test_load_settings_reads_file_only`: file tạm có `BENCH_JUDGE_MODEL=claude-sonnet-4-6`, `BENCH_FOUNDRY_RESOURCE=r1`; đặt `os.environ["BENCH_FOUNDRY_RESOURCE"]="shell"` → `load_settings(path).foundry_resource == "r1"`.
  - `test_defaults`: file rỗng → `judge_model == "claude-sonnet-4-6"`, `base_port == 8100`, `turn_timeout_s == 120.0`.
  - `test_no_bench_module_imports_app`: duyệt mọi `bench/**/*.py`, `ast` parse, không có `Import`/`ImportFrom` nào có tên bắt đầu bằng `app`.
- [ ] **Step 2:** `uv venv bench/.venv && uv pip install -p bench/.venv -r bench/requirements.txt`; chạy test → FAIL (`ModuleNotFoundError: bench.config`).
- [ ] **Step 3:** Viết `bench/requirements.txt` (`anthropic`, `httpx`, `python-socketio[asyncio_client]`, `aiohttp`, `pymongo`, `pyyaml`, `pytest`, `pytest-asyncio`), `bench/.env.example` (mọi khoá `BENCH_*` ở trên, có chú thích), `pytest.ini` (`asyncio_mode = auto`, marker `live`, `addopts = -m "not live"`), `config.py`.
- [ ] **Step 4:** Chạy `bench/.venv/bin/python -m pytest bench/tests -q` → PASS.
- [ ] **Step 5:** Commit `feat(bench): scaffold benchmark package and settings`.

---

### Task 2: Kịch bản và định dạng giờ

**Files:**
- Create: `bench/scenario.py`, `bench/vi_time.py`
- Test: `bench/tests/test_scenario.py`, `bench/tests/test_vi_time.py`

**Interfaces:**
- Produces (trong `bench/scenario.py`, dataclass frozen):
  - `ApptSpec(day: str, time: str, note: str | None = None)` — `day` là `"today"`, `"tomorrow"` hoặc `"+N"`; `time` là `"HH:MM"`.
  - `Setup(customer_name: str = "Nguyễn Thị Lan", own: tuple[ApptSpec, ...] = (), others: tuple[ApptSpec, ...] = (), owner_busy_minutes: int | None = None)`
  - `Turn(say: str | None = None, intent: str | None = None, requires_asked: ApptSpec | None = None, wait_s: float = 0, fault: str | None = None)` — đúng một trong `say`/`intent`; `intent ∈ {"agree","decline"}`; `requires_asked` là tiền đề "câu trả lời lượt trước nhắc đúng giờ này".
  - `SimGoal(goal: str, persona: str, max_turns: int = 8)`
  - `ExpectAppt(day: str, time: str, note_contains: str | None = None)`
  - `Expect(booked: tuple[ExpectAppt, ...] | None = None, others_unchanged: bool = True, answer_must_include: tuple[str, ...] = (), answer_must_not_include: tuple[str, ...] = (), max_turns: int | None = None)` — `booked=None` nghĩa là không kiểm lịch của khách.
  - `Scenario(id: str, title: str, kind: str, tags: tuple[str, ...], setup: Setup, turns: tuple[Turn, ...], sim: SimGoal | None, expect: Expect)` — `kind ∈ {"scripted","simulated"}`.
  - `load_scenarios(paths: list[Path], tags: set[str] | None = None) -> list[Scenario]`
  - `resolve_day(day: str, now_vn: datetime) -> date`; `resolve_at(spec: ApptSpec | ExpectAppt, now_vn: datetime) -> datetime` (tz +07:00).
- Produces (trong `bench/vi_time.py`): `clock_phrase(dt: datetime) -> str` (luật giống app: 0h → "12 giờ đêm", 12h → "12 giờ trưa", <12 "sáng", <18 "chiều", còn lại "tối"; phút 30 → "rưỡi", phút khác → "giờ MM"); `day_phrase(dt) -> str` → `"5/10"`; `mentions(answer: str, at: datetime) -> bool` — có cả `day_phrase` (biên số hai bên, "5/10" không khớp trong "15/10") lẫn `clock_phrase`, so chữ thường.

- [ ] **Step 1: Viết test**
  - `test_clock_phrase`: 15:00→"3 giờ chiều", 9:30→"9 giờ rưỡi sáng", 12:00→"12 giờ trưa", 0:00→"12 giờ đêm", 13:45→"1 giờ 45 chiều".
  - `test_mentions_needs_day_and_clock`, `test_day_inside_longer_date_does_not_match` ("Thứ Năm 15/10, 3 giờ chiều" không khớp 5/10 15:00).
  - `test_resolve_day`: now = 2026-10-17 23:30 +07 → `"tomorrow"` = 2026-10-18; `"+2"` = 2026-10-19.
  - `test_load_yaml_roundtrip`: YAML mẫu (một scripted có `intent: agree` và `requires_asked`) nạp ra đúng dataclass.
  - `test_turn_rejects_both_say_and_intent` → `ValueError` khi nạp.
  - `test_tag_filter`: `tags={"edge"}` chỉ giữ kịch bản có tag đó.
- [ ] **Step 2:** Chạy → FAIL.
- [ ] **Step 3:** Cài đặt hai module. Định dạng YAML: một file một kịch bản, khoá trùng tên field.
- [ ] **Step 4:** Chạy → PASS.
- [ ] **Step 5:** Commit `feat(bench): scenario model and Vietnamese time phrases`.

---

### Task 3: Workspace của một ref

**Files:**
- Create: `bench/workspace.py`
- Test: `bench/tests/test_workspace.py`

**Interfaces:**
- Consumes: `BenchSettings`.
- Produces:
  - `rewrite_env(src_text: str, db_name: str, azure_endpoint: str | None) -> str` — thuần: đặt `MONGO_DB_NAME`, comment mọi dòng `TELEGRAM_BOT_TOKEN=`, thay `AZURE_OPENAI_ENDPOINT` khi có, giữ mọi dòng khác.
  - `class RefWorkspace`: `__init__(self, settings, ref: str, port: int, run_id: str, azure_endpoint: str | None)`; thuộc tính `sha: str`, `sha7: str`, `path: Path`, `db_name: str`, `base_url: str`, `log_path: Path`, `status: str` (`"ready" | "failed_to_start"`), `error: str | None`; `async prepare() -> None` (worktree ở `worktrees_dir/<sha7>`, tạo lại nếu đã có; `uv venv` + `uv pip install -r requirements.txt` trong worktree; ghi `.env`); `async start(timeout_s: float = 90) -> bool` (`.venv/bin/python -m uvicorn main:app --port <port>` trong worktree, log ra file, chờ `GET /api/v1/health/ready` = 200; quá hạn → `status="failed_to_start"`, `error` = 50 dòng log cuối, trả `False`, **không raise**); `async stop() -> None`; `async drop_db() -> None`.

- [ ] **Step 1: Viết test**
  - `test_rewrite_env`: input có `MONGO_DB_NAME=chatbot_db`, `TELEGRAM_BOT_TOKEN=abc`, `AZURE_OPENAI_ENDPOINT=https://x` → output có `MONGO_DB_NAME=bench_x`, `# TELEGRAM_BOT_TOKEN=abc`, `AZURE_OPENAI_ENDPOINT=http://127.0.0.1:9100`, các dòng khác y nguyên; với `azure_endpoint=None` giữ endpoint gốc.
  - `test_rewrite_env_adds_missing_db_name`: không có dòng `MONGO_DB_NAME` → thêm vào cuối.
  - `test_start_failure_is_reported_not_raised` (Review Focus #1): monkeypatch lệnh khởi động thành `python -c "import sys; print('boom'); sys.exit(1)"` → `await ws.start(timeout_s=3)` trả `False`, `ws.status == "failed_to_start"`, `"boom" in ws.error`.
  - `@live test_start_real_ref`: `prepare()` + `start()` trên `HEAD` → health 200, `stop()` sạch.
- [ ] **Step 2:** Chạy → FAIL.
- [ ] **Step 3:** Cài đặt. Lệnh khởi động tách thành method `_server_cmd()` để test thay được.
- [ ] **Step 4:** Chạy → PASS (và chạy tay `pytest -m live -k start_real_ref` một lần, cần Mongo).
- [ ] **Step 5:** Commit `feat(bench): per-ref worktree, env and server lifecycle`.

---

### Task 4: Seed qua REST và đọc DB

**Files:**
- Create: `bench/seed.py`, `bench/dbread.py`
- Test: `bench/tests/test_dbread.py`, `bench/tests/test_seed.py`

**Interfaces:**
- Consumes: `RefWorkspace` (`base_url`, `db_name`, `path`), `Setup`, `ApptSpec`, `resolve_at`.
- Produces:
  - `async ensure_admin(ws) -> None` — chạy `scripts/seed_dev_users.py` **của worktree** bằng venv của worktree (idempotent).
  - `class Seeder`: `__init__(self, base_url: str, admin_phone: str, admin_password: str)`; `async set_shop(open_time="08:00", close_time="19:00", closed_days=())` → `PUT /api/v1/shop/hours`; `async set_busy(minutes: int | None)` → `POST /api/v1/shop/busy {"minutes"}` hoặc `POST /api/v1/shop/free`; `async new_customer(name: str) -> Customer` → `POST /api/v1/auth/users` rồi `POST /api/v1/auth/login`; `async book_for(customer_id: str, at: datetime, note: str | None) -> str` → `POST /api/v1/appointments {"start_at","note","for_user_id"}`, trả id.
  - `Customer(id: str, phone: str, name: str, token: str)`.
  - `ApptRow(id: str, user_id: str, start_at: datetime, status: str, note: str | None)`; `snapshot(mongo_uri: str, db_name: str) -> list[ApptRow]` (sync, pymongo, đọc `appointments`, `start_at` chuyển tz +07); `diff(before: list[ApptRow], after: list[ApptRow]) -> Changes` với `Changes(created: list[ApptRow], cancelled: list[ApptRow], other: list[tuple[ApptRow, ApptRow]])`.

- [ ] **Step 1: Viết test**
  - `test_diff_detects_created_cancelled_and_moved` (dữ liệu dựng tay, không Mongo).
  - `test_snapshot_reads_appointments` (Mongo test, DB tạm `bench_test_<uuid>`, chèn hai document, đọc ra hai `ApptRow`, xoá DB sau test).
  - `@live test_seed_against_running_ref`: sau `start()`, tạo khách, đặt hộ một lịch ngày mai 10:00, `snapshot` thấy đúng lịch đó.
- [ ] **Step 2–4:** FAIL → cài đặt → PASS.
- [ ] **Step 5:** Commit `feat(bench): REST seeding and appointment snapshots`.

---

### Task 5: Giao diện adapter và AdapterA

**Files:**
- Create: `bench/adapters/__init__.py`, `bench/adapters/base.py`, `bench/adapters/a.py`
- Test: `bench/tests/test_adapter_a.py`

**Interfaces:**
- Consumes: `Customer`, `Turn`.
- Produces:
  - `Event(t_ms: int, type: str, data: dict)`; `TurnRecord(index: int, customer_text: str, events: list[Event], answer: str, tools: list[str], error: str | None, first_token_ms: int | None, complete_ms: int | None, db_after: list[ApptRow] | None = None)` — `db_after` do runner điền sau mỗi lượt.
  - `class ChatAdapter(Protocol)`: `name: str`; `async open(base_url: str, customer: Customer) -> None`; `async send(turn: Turn, index: int, timeout_s: float) -> TurnRecord`; `async close() -> None`. Ba mặt chat / seed / đọc DB theo spec mục 6 quyết định 6: seed là `Seeder`, đọc DB là `snapshot` — adapter mới chỉ thay khi giao thức đổi.
  - `ADAPTERS: dict[str, type[ChatAdapter]]` với `"a": AdapterA`.
  - `AdapterA`: Socket.IO `connect(base_url, auth={"token": token})`; `intent="agree"` gửi `"ừ"`, `"decline"` gửi `"không"`; ghi **mọi** sự kiện nhận được (kể cả sự kiện lạ) với mốc ms tính từ lúc gửi; kết thúc lượt ở `complete` (answer = `data["answer"]`), `error` hoặc `rate_limited` (error = message); `tools` lấy từ `tool_started`; `first_token_ms` là sự kiện `token` đầu tiên; quá `timeout_s` → `error="timeout"`.

- [ ] **Step 1: Viết test** — dựng server `python-socketio` `AsyncServer` giả trong test (aiohttp, cổng ngẫu nhiên) phát kịch bản sự kiện:
  - `test_records_tokens_tools_and_answer`: server phát `turn_started`, `token`×2, `tool_started{name:"parse_time"}`, `tool_finished`, `complete{answer:"Dạ…"}` → `answer=="Dạ…"`, `tools==["parse_time"]`, `first_token_ms` không None, `complete_ms >= first_token_ms`.
  - `test_intent_agree_sends_u`: server ghi lại tin nhận → `"ừ"`.
  - `test_turn_timeout_is_recorded` (Review Focus #2): server không phát `complete`; `timeout_s=0.5` → `error == "timeout"`, không raise.
  - `test_unknown_events_are_kept_and_rate_limited_is_error` (Review Focus #3): server phát `connected`, `weird_event`, rồi `rate_limited{message:"chờ"}` → hai sự kiện đầu có trong `events`, `error == "chờ"`.
- [ ] **Step 2–4:** FAIL → cài đặt → PASS.
- [ ] **Step 5:** Commit `feat(bench): chat adapter interface and AdapterA`.

---

### Task 6: Chấm điểm

**Files:**
- Create: `bench/score.py`
- Test: `bench/tests/test_score.py`

**Interfaces:**
- Consumes: `Scenario`, `Expect`, `TurnRecord`, `ApptRow`, `Changes`, `diff`, `resolve_at`, `vi_time.mentions`.
- Produces:
  - `check_precondition(turn: Turn, prev: TurnRecord | None, now_vn: datetime) -> bool` — `requires_asked` khác None thì đòi `prev` tồn tại, không lỗi, và `mentions(prev.answer, resolve_at(turn.requires_asked, now_vn))`.
  - `TEXT_RULES: dict[str, Callable[[str], bool]]` gồm `english` (câu có ≥ 4 từ ASCII liên tiếp thuộc danh sách từ tiếng Anh thường gặp, hoặc chứa `"Model call"`, `"limit"`, `"rejected"`), `register` (từ đứng riêng `cô|chú|bác` hoặc `con` làm chủ ngữ: `\bcon (xem|đặt|giữ|hỏi)\b`), `clock_colon` (`\b\d{1,2}:\d{2}\b`), `claim_without_write` (đánh giá ở `score_run`, không phải hàm chữ).
  - `RunScore(passed: bool, valid: bool, invalid_reason: str | None, wrong_writes: list[str], violations: list[str], turns_used: int)`.
  - `score_run(scenario, turns: list[TurnRecord], before: list[ApptRow], after: list[ApptRow], customer_id: str, now_vn: datetime) -> RunScore` — luật:
    - lịch của khách khác đổi khi `others_unchanged` → `wrong_writes += ["other_customer:<id>"]`;
    - `expect.booked` khác None: tập lịch `booked` của khách sau cùng phải bằng đúng tập kỳ vọng (theo `resolve_at`, so tới phút; `note_contains` so chữ thường); lịch thừa → `wrong_writes += ["extra:<HH:MM d/m>"]`, lịch kỳ vọng mà thiếu → không phải ghi sai, chỉ làm `passed=False`; lịch có sẵn bị hủy mà kỳ vọng còn → `wrong_writes += ["cancelled:<…>"]`;
    - câu trả lời nhận "đã xong" (`xong rồi ạ|em (đã )?(đặt|hủy|dời) (xong|rồi)`) ở lượt mà `diff` giữa trước/sau lượt rỗng → `violations += ["claim_without_write@<i>"]` (dùng `TurnRecord.db_after` của lượt đó và lượt trước);
    - `TEXT_RULES` trên mọi `answer` → `violations += ["<rule>@<i>"]`;
    - `answer_must_include/not_include` trên answer cuối;
    - `max_turns`;
    - lượt có `error` → `passed=False`;
    - `passed = valid and not wrong_writes and (expect thoả) and not lỗi lượt`; `violations` không làm trượt (báo riêng).
  - `pass_hat_k(results: list[bool]) -> bool` (đều True và đủ k); `aggregate(scores: list[RunScore], k: int) -> dict` với khoá `runs`, `valid_runs`, `pass_rate`, `pass_hat_k_rate`, `wrong_writes_total`, `violations_by_rule`.
  - `behavior_match(base: list[TurnRecord], cand: list[TurnRecord]) -> list[bool]` — theo lượt: cùng danh sách `tools` và cùng `diff` DB của lượt.

- [ ] **Step 1: Viết test** (dữ liệu dựng tay, không mạng):
  - `test_ghost_booking_is_wrong_write`: G-01 — expect `booked=()`, sau cùng khách có lịch 15:00 → `wrong_writes == ["extra:3 giờ chiều 18/10"]`-dạng (kiểm `startswith("extra:")`), `passed False`.
  - `test_correct_booking_passes`.
  - `test_other_customer_change_is_wrong_write`.
  - `test_invalid_when_precondition_fails`: lượt 2 có `requires_asked` 15:00 mà answer lượt 1 không nhắc → `valid False`, `invalid_reason == "precondition@1"`, không tính vào `aggregate` mẫu số.
  - `test_text_rules`: "Dạ cô muốn…" → `register`; "Model call limits exceeded" → `english`; "lúc 15:00" → `clock_colon`; "Dạ em giữ 3 giờ chiều" → không vi phạm.
  - `test_claim_without_write`.
  - `test_pass_hat_k_and_aggregate`.
  - `test_behavior_match`.
- [ ] **Step 2–4:** FAIL → cài đặt → PASS.
- [ ] **Step 5:** Commit `feat(bench): scoring, pass^k and behaviour match`.

---

### Task 7: Client Claude, judge, khách giả lập

**Files:**
- Create: `bench/llm.py`, `bench/judge.py`, `bench/simulate.py`
- Test: `bench/tests/test_judge.py`, `bench/tests/test_simulate.py`

**Interfaces:**
- Consumes: `BenchSettings` (`judge_model`, `foundry_resource`, `foundry_api_key`), `TurnRecord`, `SimGoal`.
- Produces:
  - `make_client(settings) -> anthropic.AsyncAnthropicFoundry` (hoặc lớp async tương ứng của SDK cho Foundry; xem `claude-api` skill → Provider Clients → Microsoft Foundry). Một hàm duy nhất tạo client; đổi nền tảng chỉ sửa ở đây.
  - `JUDGE_AXES = ("lap_y", "giong_may", "hoi_lai_da_biet", "xung_ho", "tu_nhien")` — cùng rubric `scripts/score_transcript.py` (chép chữ rubric sang `judge.py`, **không import**).
  - `async judge(client, model: str, transcript: str) -> dict | None` — structured output (`output_config.format` JSON schema: mỗi trục `{score: int 1..5, why: str}`, `worst_line: str`); `stop_reason == "refusal"`, JSON sai schema, hoặc lỗi API → `None` (Review Focus #5).
  - `render_transcript(turns: list[TurnRecord]) -> str` — "Khách: …\nAI: …\n" kèm tool trong ngoặc.
  - `class SimCustomer`: `__init__(self, client, model: str, goal: SimGoal)`; `async next_message(turns: list[TurnRecord]) -> str | None` — trả câu tiếp theo của khách bằng tiếng Việt theo `persona`, hoặc `None` khi model trả đúng token `<<DONE>>` (đạt mục tiêu / bỏ cuộc) hoặc đã `max_turns`.
  - Tham số gọi model: `claude-sonnet-4-6`, `thinking={"type": "adaptive"}`, `max_tokens=4000`; judge thêm structured output.

- [ ] **Step 1: Viết test** với client giả (object có `messages.create` async trả đối tượng dựng sẵn):
  - `test_judge_parses_scores`: trả JSON hợp lệ → dict có 5 trục.
  - `test_judge_failure_yields_none` (Review Focus #5): ba ca — `stop_reason="refusal"`; nội dung không phải JSON; client ném `anthropic.APIConnectionError` → đều `None`.
  - `test_sim_stops_on_done_token` và `test_sim_stops_at_max_turns`.
  - `@live test_judge_real`: một transcript ngắn → dict có điểm (chạy tay, cần `bench/.env`).
- [ ] **Step 2–4:** FAIL → cài đặt → PASS.
- [ ] **Step 5:** Commit `feat(bench): Claude judge and simulated customer`.

---

### Task 8: Proxy Azure chèn lỗi

**Files:**
- Create: `bench/proxy.py`
- Test: `bench/tests/test_proxy.py`

**Interfaces:**
- Produces: `class FaultProxy`: `__init__(self, upstream: str, port: int)`; `async start()`, `async stop()`; `url: str`; `set_fault(mode: str | None, count: int = 1)` với `mode ∈ {None, "http500", "hang", "tool_loop"}`:
  - `http500`: `count` request kế tiếp trả 500;
  - `hang`: `count` request kế tiếp giữ kết nối 600 s;
  - `tool_loop`: `count` request kế tiếp trả (non-stream và SSE stream) một assistant message chỉ có tool call `find_free_slots` với ngày mai — đẩy agent tới giới hạn vòng.
  - Mọi request khác chuyển nguyên (method, path, query, header, body, stream SSE) tới `upstream`.
- Scenario dùng `Turn.fault` = `mode` (count mặc định 1; `"tool_loop:4"` = 4 request).

- [ ] **Step 1: Viết test** với upstream giả (aiohttp) trả JSON/SSE cố định:
  - `test_passthrough_json_and_sse`.
  - `test_http500_then_recovers`: `set_fault("http500")` → request 1 nhận 500, request 2 qua upstream.
  - `test_tool_loop_returns_tool_call`.
- [ ] **Step 2–4:** FAIL → cài đặt → PASS.
- [ ] **Step 5:** Commit `feat(bench): fault-injecting Azure proxy`.

---

### Task 9: Runner nhiều ref, lưu kết quả, token

**Files:**
- Create: `bench/runner.py`, `bench/tokens.py`, `bench/__main__.py`
- Test: `bench/tests/test_runner.py`, `bench/tests/test_tokens.py`

**Interfaces:**
- Consumes: mọi thứ ở Task 1–8.
- Produces:
  - `RefSpec(ref: str, adapter: str)` — CLI `--ref docs/arch-security-review:a` (mặc định adapter `a`).
  - `async run_bench(settings, refs: list[RefSpec], scenario_paths: list[Path], tags: set[str] | None, k: int = 4, use_proxy: bool = True, judge_on: bool = True) -> Path` — trả thư mục run `results/<timestamp>-<runid>/`.
  - Thứ tự chạy: mọi ref `prepare()` + `start()` song song (mỗi ref một cổng, một proxy); rồi lặp `for attempt in range(k): for scenario: for ref:` — **xen kẽ** để các ref chạy trong cùng khung giờ; ref `failed_to_start` bị bỏ qua và ghi vào `meta`.
  - Một lần chạy kịch bản: `now_vn` lúc bắt đầu → seed (`set_shop`, `set_busy`, `new_customer`, `book_for` cho `own`/`others`) → `before = snapshot` → các lượt (scripted: kiểm tiền đề, `wait_s`, bật `fault` trên proxy, `send`, `db_after = snapshot`; simulated: `SimCustomer.next_message` cho tới `None`) → `after` → `score_run` → judge (nếu bật) → token (nếu Langfuse cấu hình) → ghi `runs/<ref-sha7>/<scenario-id>/<attempt>.json` (`TurnRecord`, `RunScore`, judge, tokens, `now_vn`).
  - `meta.json` của run: refs (ref, sha, adapter, status, error), `started_at`, `finished_at`, ngày VN lúc bắt đầu/kết thúc, `azure_deployment` (đọc từ `.env` ref, chỉ tên), `judge_model`, `k`, danh sách kịch bản.
  - `async fetch_tokens(settings, session_id: str, since: datetime) -> dict | None` trong `tokens.py` — Langfuse public API `GET /api/public/traces?sessionId=…&fromTimestamp=…` (basic auth public/secret), cộng `usage` input/output và đếm generation; không cấu hình hoặc lỗi → `None`.
  - CLI: `python -m bench run --ref A[:adapter] --ref B[:adapter] [--tags edge] [--k 4] [--no-proxy] [--no-judge]` và `python -m bench compare <run_dir> [--base <ref>] -o report.html` (Task 10).

- [ ] **Step 1: Viết test**
  - `test_interleaving_order`: thay `run_one` bằng hàm ghi lại `(attempt, scenario, ref)` → thứ tự đúng attempt → scenario → ref.
  - `test_failed_ref_is_skipped_and_recorded`: một ref `start()` trả False → không có thư mục runs của nó, `meta["refs"]` có `status == "failed_to_start"`.
  - `test_fetch_tokens_none_when_unconfigured`.
  - `@live test_run_one_scenario_on_head`: chạy `edge/g01-ko.yaml` trên `HEAD`, `k=1`, `--no-judge` → có file JSON với `RunScore`.
- [ ] **Step 2–4:** FAIL → cài đặt → PASS.
- [ ] **Step 5:** Commit `feat(bench): multi-ref interleaved runner and token capture`.

---

### Task 10: Báo cáo so sánh HTML

**Files:**
- Create: `bench/report.py`, `bench/templates/report.html`
- Test: `bench/tests/test_report.py`

**Interfaces:**
- Consumes: thư mục run (Task 9), `aggregate`, `behavior_match`.
- Produces: `build_report(run_dirs: list[Path], base_ref: str | None) -> str` (HTML tự chứa, CSS nhúng, không tải gì từ mạng); `warnings(metas: list[dict]) -> list[str]`.
- Nội dung:
  - Cảnh báo (Review Focus #4): các run khác ngày VN, khác khung buổi (sáng < 12h, chiều 12–18h, tối), khác `azure_deployment` hoặc `judge_model`.
  - Bảng tổng hợp theo ref: pass rate, pass^k, ghi sai (đỏ nếu > 0), vi phạm theo luật, trung vị/p90 `first_token_ms` và `complete_ms`, token trung bình (trống nếu None), điểm judge trung bình từng trục (bỏ None). Chỉ số tệ hơn `base_ref` đánh dấu.
  - Bảng kịch bản: đạt k/k, số lần không hợp lệ, ghi sai.
  - Mỗi kịch bản scripted: hội thoại song song theo lượt, mỗi ô có câu khách, câu trả lời từng ref, tools, thay đổi DB của lượt; lượt `behavior_match == False` so với `base_ref` được tô.
  - Kịch bản simulated: transcript từng ref (lần chạy đầu tiên), không căn lượt.

- [ ] **Step 1: Viết test** với hai thư mục run dựng tay (JSON tối thiểu):
  - `test_report_contains_summary_and_side_by_side`: HTML có tên hai ref, có "pass^k", có câu khách của kịch bản scripted ở cả hai cột.
  - `test_wrong_writes_are_highlighted`.
  - `test_compare_warns_on_different_days_or_models` (Review Focus #4).
  - `test_report_is_self_contained`: không có `src="http` hay `href="http` nào ngoài liên kết tài liệu.
- [ ] **Step 2–4:** FAIL → cài đặt → PASS.
- [ ] **Step 5:** Commit `feat(bench): side-by-side comparison report`.

---

### Task 11: Bộ kịch bản

**Files:**
- Create: `bench/scenarios/edge/*.yaml`, `bench/scenarios/llm/*.yaml`, `bench/scenarios/fault/*.yaml`
- Test: `bench/tests/test_scenarios_valid.py`

**Interfaces:**
- Consumes: định dạng Task 2.

Nội dung (kỳ vọng là hành vi **đúng**; A được dự đoán trượt các edge — đó là mục đích):

- `edge/` (`kind: scripted`, tag `edge` + mã lỗi):
  - `g01-ko`, `g01-hong`, `g01-khoan`: lượt 1 "mai 3 giờ chiều chị làm móng"; lượt 2 `requires_asked: {day: tomorrow, time: "15:00"}` + câu từ chối → `booked: []`.
  - `g02-hoi-lai`, `g02-ten-huy`: setup `own` ngày mai 17:00; lượt 1 "hủy giùm chị lịch mai"; lượt 2 `requires_asked` 17:00 + "hủy cái nào vậy em?" / "để chị hỏi thằng Huy đã" → `booked: [{tomorrow, 17:00}]`.
  - `g03-gio-moi`: lượt 1 đặt 15:00; lượt 2 `requires_asked` 15:00 + "dạ 4 giờ nha em"; lượt 3 `intent: agree` với `requires_asked` 16:00 → `booked: [{tomorrow, 16:00}]`.
  - `is-affirmative-tan-gau`: lượt 1 đặt 15:00; lượt 2 `requires_asked` + câu tán gẫu chứa "phải" trong CONTEXT.md → `booked: []`, `answer_must_not_include: ["Xong rồi"]`.
  - `g05-hom-nay-khong`: "hôm nay không được, mai 3h chiều làm tóc"; lượt 2 `intent: agree` với `requires_asked` ngày mai 15:00 → `booked: [{tomorrow, 15:00}]`.
  - `g06-phut`: "mai 15h30 làm móng"; lượt 2 agree với `requires_asked` 15:30 → `booked: [{tomorrow, 15:30}]`.
  - `g08-hai-lich`: "đặt cho chị thứ … 9 giờ với 10 giờ sáng mai luôn" → `booked=None`, chỉ kiểm `wrong_writes` (không có lịch nào ngoài 9:00/10:00) và luật chữ.
  - `g09-chon-gio`: "mai 9 giờ hay 10 giờ còn chỗ không em"; lượt 2 "dạ 9 giờ"; lượt 3 agree `requires_asked` 9:00 → `booked: [{tomorrow, 09:00}]`.
  - `g10-doi-khong-ro`: setup `own` ngày mai 9:00; "chuyển giùm cô qua 2 giờ chiều nha"; lượt 2 agree `requires_asked` 14:00 → `booked: [{tomorrow, 14:00}]` (lịch 9:00 phải mất).
  - `pending-het-han` (tag `slow`): đặt 15:00; lượt 2 `wait_s: 660` + `say: "ừ được em"` → `booked: []`, `answer_must_not_include: ["Xong rồi"]`.
- `llm/` (`kind: simulated`, tag `llm` + `LLM-xx`): mỗi kịch bản LLM-01…LLM-31 của `docs/test-scenarios/02-llm-chat-scenarios.md` thành `sim.goal` + `persona` (tóm từ mô tả và câu mẫu trong `scripts/llm_scenarios.py`) và `expect` theo mục "Kỳ vọng" của tài liệu đó; LLM-32 tag `slow`; LLM-33 chuyển thành `edge/pending-het-han`; LLM-35 bỏ.
- `fault/` (`kind: scripted`, tag `fault`):
  - `azure-500`: lượt 1 `fault: http500` + "mai 3 giờ chiều làm tóc" → lượt có `error` hoặc câu tiếng Việt; lượt 2 "mai 3 giờ chiều làm tóc" (không lỗi) phải trả lời bình thường; `booked: []`; `answer_must_not_include: ["Model call", "Traceback"]`.
  - `tool-loop-sau-giu-cho` (G-07): lượt 1 đặt 15:00; lượt 2 `fault: "tool_loop:4"` + "à mà giờ đó còn chỗ không em"; lượt 3 "dạ vâng để chị gọi" → `booked: []`.
  - `azure-cham` (LLM-34): `fault: hang` → lượt `timeout` được phép, lượt sau chạy được, `booked: []`.

- [ ] **Step 1: Viết test** `test_all_scenarios_load_and_are_well_formed`: nạp toàn bộ thư mục; mọi `id` duy nhất; mọi `requires_asked` có `time` hợp lệ; mọi kịch bản scripted có ≥ 1 lượt; mọi edge có tag mã lỗi; số kịch bản `llm` = 32 (LLM-01…LLM-32).
- [ ] **Step 2–4:** FAIL → viết kịch bản → PASS.
- [ ] **Step 5:** Commit `feat(bench): edge, LLM and fault scenarios`.

---

### Task 12: Chạy số liệu gốc trên A

**Files:**
- Modify: `bench/README.md` (lệnh chạy, cách đọc báo cáo, cách thêm adapter B)

- [ ] **Step 1:** Kiểm điều kiện: Mongo chạy; `bench/.env` có khoá Foundry; `.env` gốc có Azure; Langfuse bật thì điền khoá Langfuse. Thiếu khoá Foundry → dừng và báo người dùng (không tự đặt).
- [ ] **Step 2:** Chạy `bench/.venv/bin/python -m bench run --ref docs/arch-security-review:a --k 4` (mọi tag trừ `slow`). Kỳ vọng: thư mục run có `meta.json`, đủ kịch bản `llm` (trừ LLM-32 `slow`) + `edge` + `fault` × 4.
- [ ] **Step 3:** Chạy riêng `--tags slow --k 1`.
- [ ] **Step 4:** `python -m bench compare <run_dir> -o bench/results/baseline-a.html`. Mở file, kiểm: bảng tổng hợp có số, edge G-01/G-02/G-03 hiển thị ghi sai > 0 (khớp review), hội thoại đọc được.
- [ ] **Step 5:** Commit `README.md`; **không** commit `bench/results/`. Báo người dùng các con số chính và đường dẫn báo cáo; đề nghị đăng báo cáo thành artifact.
