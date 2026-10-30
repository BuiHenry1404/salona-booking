# M0 — Nền móng (không đổi hành vi chat)

Đọc `README.md` cùng thư mục trước: Global Constraints áp cho mọi task dưới đây.

**Giao:** nhánh làm việc; settings `orch_*`; `app/core/vi_format.py` để `telegram/`, `repositories/` thôi import từ `app/agents`; tool đọc `is_open_at`; `ConversationState.goal/queued` + hai phương thức ghi; một lần kiểm `strict=True` trên Azure (có phép). Sau M0, gói cũ chạy y hệt, suite xanh.

---

### Task 0.1: Nhánh

**Files:** không sửa code.

- [ ] **Step 1: Commit spec đang sửa dở (nếu còn)**

```bash
git status --short            # mong đợi: M docs/superpowers/specs/2026-10-29-llm-orchestrator-design.md (uv.lock KHÔNG add)
git add docs/superpowers/specs/2026-10-29-llm-orchestrator-design.md docs/superpowers/plans/2026-10-30-llm-orchestrator/
git commit -m "docs(spec): apply review fixes; add orchestrator implementation plan"
```

- [ ] **Step 2: Tách nhánh**

```bash
git switch -c feat/llm-orchestrator
docker compose up -d mongo && pytest -q    # mốc: 281 passed
```

---

### Task 0.2: Settings `orch_*`

**Files:**
- Modify: `app/core/config.py` (sau `checkpoint_ttl_days`), `.env.example`, `.env`
- Test: `tests/test_config.py`

**Interfaces — Produces:** `settings.orch_max_tool_rounds: int = 8`, `orch_max_calls_per_round: int = 3`, `orch_no_progress_streak: int = 2`, `orch_goal_turns_limit: int = 3`, `orch_turn_timeout_seconds: int = 150`, `orch_orchestrate_timeout_seconds: int = 110`, `orch_task_timeout_seconds: int = 40`, `orch_llm_calls_per_user_per_hour: int = 150`, `orch_ledger_ttl_days: int = 7`. Tất cả **có mặc định** (như `chat_max_per_hour`: thêm biến bắt buộc làm hỏng `.env` đang chạy).

`orch_orchestrate_timeout_seconds` và `orch_task_timeout_seconds` là **quyết định của plan** (spec chỉ nêu trần lượt 150): vòng Orchestrator phải dừng sớm hơn trần lượt để còn chỗ cho Conversation (20s) + classify (8s) + ghi DB; test ngân sách ở M5 Task 5.6 ghim bất đẳng thức.

- [ ] **Step 1: Test đỏ** — thêm vào `tests/test_config.py`:

```python
def test_orchestrator_settings_have_defaults():
    # Kiểm mặc định của TRƯỜNG (như test_optional_integrations_default_to_none), không đọc .env máy đang chạy
    from app.core.config import Settings
    d = {name: f.default for name, f in Settings.model_fields.items() if name.startswith("orch_")}
    assert d == {"orch_max_tool_rounds": 8, "orch_max_calls_per_round": 3, "orch_no_progress_streak": 2,
                 "orch_goal_turns_limit": 3, "orch_turn_timeout_seconds": 150, "orch_orchestrate_timeout_seconds": 110,
                 "orch_task_timeout_seconds": 40, "orch_llm_calls_per_user_per_hour": 150, "orch_ledger_ttl_days": 7}
    assert d["orch_orchestrate_timeout_seconds"] + 30 <= d["orch_turn_timeout_seconds"]
```

- [ ] **Step 2: Chạy** `pytest tests/test_config.py -q -k orchestrator` → FAIL (`AttributeError`).
- [ ] **Step 3: Thêm 9 trường vào `Settings`** với comment tiếng Việt một dòng mỗi nhóm; thêm block `# --- Orchestrator (M0) ---` vào `.env.example` dạng comment `# ORCH_MAX_TOOL_ROUNDS=8` (tuỳ chọn → để comment, theo quy ước "KEY= rỗng ≠ tắt"); `.env` thêm cùng block.
- [ ] **Step 4: Chạy** → PASS.
- [ ] **Step 5: Commit** `feat(config): add orchestrator limits`

---

### Task 0.3: `app/core/vi_format.py`

**Files:**
- Create: `app/core/vi_format.py`
- Modify: `app/agents/booking_graph/context.py:22-90` (xoá định nghĩa, import lại từ `app.core.vi_format` để mọi import cũ `from app.agents.booking_graph.context import format_vi_datetime` vẫn chạy), `app/telegram/handlers.py:14`, `app/telegram/notify.py:5`, `app/repositories/user.py:93`
- Test: `tests/test_vi_format.py`

**Interfaces — Produces:** `format_vi_datetime(dt) -> str`, `format_vi_hhmm(hhmm: str) -> str`, `_clock_phrase(hour: int, minute: int) -> str`, `_WEEKDAYS: list[str]`, `derive_address(full_name: str | None) -> tuple[str, str]` — chữ ký và thân **y nguyên** bản ở `context.py`.

- [ ] **Step 1: Test đỏ**

```python
import ast, pathlib
def test_vi_format_matches_legacy_context_helpers():
    from app.core import vi_format
    from app.agents.booking_graph import context
    assert vi_format.format_vi_datetime is context.format_vi_datetime
    assert vi_format.derive_address is context.derive_address
    assert vi_format._WEEKDAYS[0] == "Thứ Hai"

def test_only_agents_and_the_three_known_services_import_app_agents():
    allowed = {"app/services/socketio_service.py", "app/services/conversation_state.py", "main.py"}
    root = pathlib.Path(__file__).resolve().parents[1]
    hits = set()
    for p in list((root / "app").rglob("*.py")) + [root / "main.py"]:
        rel = str(p.relative_to(root))
        if rel.startswith("app/agents/"): continue
        for node in ast.walk(ast.parse(p.read_text(encoding="utf-8"))):
            if isinstance(node, ast.ImportFrom) and (node.module or "").startswith("app.agents"):
                hits.add(rel)
    assert hits == allowed
```

- [ ] **Step 2: Chạy** `pytest tests/test_vi_format.py -q` → FAIL (`ModuleNotFoundError`; test 2 thấy `telegram/*`, `repositories/user.py`).
- [ ] **Step 3: Tạo module**, dời 5 định nghĩa (giữ docstring), `context.py` thêm `from app.core.vi_format import _WEEKDAYS, _clock_phrase, derive_address, format_vi_datetime, format_vi_hhmm  # noqa: F401 (re-export)`; sửa 3 import ngoài agents sang `app.core.vi_format`. `repositories/user.py:93` là import trễ trong hàm — giữ vị trí, đổi module.
- [ ] **Step 4: Chạy** `pytest tests/test_vi_format.py tests/test_context_block.py tests/test_telegram_handlers.py tests/test_telegram_notifier.py tests/test_user_repository.py -q` → PASS.
- [ ] **Step 5: Commit** `refactor(core): move Vietnamese time formatting out of the agent layer`

---

### Task 0.4: Tool đọc `is_open_at`

**Files:**
- Modify: `app/agents/booking_graph/tools.py:166-199` (`make_shop_tools` trả 3 tool), `app/agents/booking_graph/guard.py:52` (`TOOL_NAMES` thêm `"is_open_at"` để `strip_tool_names` xoá được tên)
- Test: `tests/test_tools.py`

**Interfaces — Produces:** tool `is_open_at(at: str) -> str` (JSON). `at` là ISO 8601 giờ VN (như `start_at` của `propose_appointment`, parse bằng `_parse_local`). Kết quả:
`{"at": _moment(dt), "open": bool, "reason": "closed_day"|"before_open"|"after_close"|"owner_busy"|"open", "next_change": _moment(...) | None}`.
Thứ tự kiểm: `closed_day` (`(to_local(dt).weekday()+1) % 7 in hours.closed_days`, đúng quy ước 0 = Chủ Nhật của `app/models/shop.py`) → `before_open`/`after_close` (so phút `_to_minutes` với `open_time`/`close_time`; `next_change` = mốc `open_time` cùng ngày khi `before_open`, `None` khi `after_close`) → `owner_busy` khi `status.is_busy and status.busy_until and dt < status.busy_until` (`next_change` = `busy_until`) → `open`. Lỗi định dạng → `{"error": "bad_time_format"}`.

- [ ] **Step 1: Test đỏ** — trong `tests/test_tools.py` (dùng fixture/helper dựng `ShopService.set_hours`/`set_busy` sẵn có trong file):

```python
async def test_is_open_at_reports_closed_day(test_db, user): ...   # closed_days chứa ngày đó → open False, reason "closed_day"
async def test_is_open_at_reports_before_open_with_next_change(test_db, user): ...  # 07:00 với open 08:00 → reason "before_open", next_change["iso"] kết thúc "T08:00:00+07:00"
async def test_is_open_at_reports_owner_busy_until(test_db, user): ...   # set_busy(60), at = now+10' → "owner_busy", next_change == busy_until
async def test_is_open_at_reports_open(test_db, user): ...
async def test_is_open_at_rejects_bad_format(test_db, user): ...   # "mai" → {"error": "bad_time_format"}
def test_make_shop_tools_returns_three_tools(): ...   # names == {"get_shop_status","get_shop_hours","is_open_at"}
```

- [ ] **Step 2: Chạy** `pytest tests/test_tools.py -q -k is_open_at` → FAIL.
- [ ] **Step 3: Viết tool** trong `make_shop_tools`, docstring tiếng Anh theo mẫu `get_shop_hours`; `tests/test_prompts.py` có test đếm tool/đối chiếu prompt — chạy để biết có cần cập nhật `SHOP_PROMPT` nhắc tool mới không (chỉ thêm một dòng mô tả nếu test đó đỏ).
- [ ] **Step 4: Chạy** `pytest tests/test_tools.py tests/test_prompts.py tests/test_mw_guard.py -q` → PASS.
- [ ] **Step 5: Commit** `feat(tools): add read-only is_open_at`

---

### Task 0.5: `ConversationState.goal` / `queued` và hai phương thức ghi

**Files:**
- Modify: `app/models/conversation.py:111-135` (thêm `GoalProgress`, `QueuedRequest` **trước** `ConversationState`; thêm 2 trường), `app/services/conversation_state.py` (thêm 2 phương thức sau `record_tool_facts`; `_compact` không chạm — kiểm bằng test)
- Test: `tests/test_conversation_state_service.py`

**Interfaces — Produces:**

```python
class GoalProgress(BaseModel):      # app/models/conversation.py
    goal_id: Optional[str] = None
    turns_without_progress: int = 0
    last_marker: Optional[str] = None
    service: Optional[str] = None           # dùng để nhận "đổi mục tiêu" (§7.2)
    appointment_id: Optional[str] = None

class QueuedRequest(BaseModel):
    queue_id: str
    start_at: datetime                      # UTC aware (validator như Proposal)
    spoken: str
    note: Optional[str] = None
    queued_at: datetime

# ConversationState thêm (code-only, _compact không chạm, như tool_day):
    goal: Optional[GoalProgress] = None
    queued: List[QueuedRequest] = Field(default_factory=list)

# ConversationStateService
async def record_goal_progress(self, user_id: str, goal: GoalProgress) -> None   # $set state.goal qua _set_today_fields
async def set_queued(self, user_id: str, queued: List[QueuedRequest]) -> None     # $set state.queued
```

- [ ] **Step 1: Test đỏ**

```python
async def test_record_goal_progress_roundtrips(test_db): ...        # set → get_state().goal == goal
async def test_set_queued_roundtrips_and_keeps_tz(test_db): ...     # start_at trả về tz-aware UTC
async def test_compact_does_not_touch_goal_or_queued(test_db, monkeypatch): ...  # theo mẫu test tool_day hiện có trong file: sau _compact, goal/queued còn nguyên
```

- [ ] **Step 2: Chạy** → FAIL. **Step 3: Viết** theo mẫu `record_tool_facts`/`_set_today_fields`. **Step 4:** `pytest tests/test_conversation_state_service.py tests/test_conversation_state_model.py -q` → PASS.
- [ ] **Step 5: Commit** `feat(state): persist goal progress and queued requests`

---

### Task 0.6: Kiểm `strict=True` trên Azure (một lần, có phép)

**Files:**
- Create: `tests/test_strict_schema_llm.py` (marker `llm`, bị loại khỏi lần chạy mặc định)

**Interfaces — Produces:** hàm `strict_probe_tools() -> list[BaseTool]` trả **một** tool `probe(action: Literal["a","b"], hint: ProbeHint)` với `ProbeHint(day: date | None, hour: int | None, part: Literal["sang","chieu"] | None, items: list[str] | None)` — mô phỏng hình dạng §5.1 (Literal, `| None`, model lồng, list, `date`). M3 Task 3.6 thay bằng 5 tool thật.

- [ ] **Step 1: Viết test**

```python
pytestmark = [pytest.mark.llm, pytest.mark.asyncio]

async def test_strict_tool_schema_is_accepted_by_the_deployment():
    model = build_chat_model(tags=["probe"], streaming=False, max_retries=0).bind_tools(
        strict_probe_tools(), strict=True, tool_choice="required")
    reply = await model.ainvoke([HumanMessage("Call the tool with action a for tomorrow morning at 9.")])
    assert reply.tool_calls and reply.tool_calls[0]["name"] == "probe"
```

- [ ] **Step 2: Kiểm thuần không tốn tiền** (chạy mặc định được): `convert_to_openai_tool(tool, strict=True)` không chứa khoá `default`/`minimum`/`maximum` ở bất kỳ độ sâu nào — thêm `test_probe_schema_is_strict_safe()` không marker.
- [ ] **Step 3: DỪNG — hỏi chủ dự án** đúng một câu: "Cho phép chạy `pytest tests/test_strict_schema_llm.py -m llm` một lần (1 lời gọi Azure) để xác nhận `strict=True` không 400?" Không chạy khi chưa có đồng ý. Có đồng ý → chạy, ghi kết quả (PASS hay mã lỗi) vào checkbox này.
- [ ] **Step 4: Nếu 400** → ghi rõ thông điệp Azure vào spec §14 mục 4 và đổi quyết định: bỏ `strict` (plan M3 Task 3.4 dùng `strict=False`, giữ kiểm khoảng ở middleware). Nếu PASS → không đổi gì.
- [ ] **Step 5: Commit** `test(llm): probe strict tool schemas on the deployment`
