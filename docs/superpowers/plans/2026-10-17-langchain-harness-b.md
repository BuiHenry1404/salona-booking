# Harness B (LangChain create_agent + middleware + interrupt) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Chuyển agent đặt lịch từ vòng lặp tự viết sang `create_agent` + middleware + `interrupt()` + checkpointer Mongo, theo 5 mốc đo được, để khách từ chối không bao giờ bị ghi lịch.

**Architecture:** Graph ngoài (`StateGraph`) giữ supervisor và định tuyến; mỗi agent booking/shop/social là một `create_agent` (compile `checkpointer=False`) được gọi trong một node bọc, luật nằm ở middleware. Lịch sử model đọc từ thread của checkpointer `MongoDBSaver` (thread = `<user_id>:<ngày VN>`). Chốt lịch: `ConfirmMiddleware` bắt đề xuất từ kết quả tool vào state; node `confirm` của graph ngoài gọi `interrupt(proposal)` SAU khi guard đã duyệt câu hỏi; lượt sau `run_turn` resume bằng `Command(resume=..., update=...)`, lưới code + bộ phân loại quyết, code gọi `AppointmentService`.

**Tech Stack:** Python 3.12, FastAPI, python-socketio, langchain 1.3.15, langchain-core 1.5.5, langgraph 1.2.11, langgraph-checkpoint-mongodb 0.5.1, pymongo 4.19.0, motor 3.7.1, pytest + pytest-asyncio, Mongo :27017.

**Spec:** `docs/superpowers/specs/2026-10-17-langchain-harness-b-design.md` (bản sau commit `028451c`).

---

## Lệch khỏi spec

Mỗi mục đã kiểm bằng mã nguồn thư viện đã cài hoặc chạy thử trong venv nháp; plan làm theo cột "Làm thế này".

1. **`AsyncMongoDBSaver` không có trong `langgraph-checkpoint-mongodb==0.5.1`.** Gói chỉ còn `langgraph.checkpoint.mongodb.MongoDBSaver(client: pymongo.MongoClient, db_name="checkpointing_db", checkpoint_collection_name="checkpoints", writes_collection_name="checkpoint_writes", ttl: int|None, serde=...)`. Các hàm `aget_tuple/aput/aput_writes/alist` của nó chạy hàm sync trong thread executor. TTL là `ttl` tính bằng **giây**, đặt index TTL trên `created_at`. → Dùng `MongoDBSaver` với một `MongoClient` sync riêng (tz_aware), `db_name=settings.mongo_db_name` (ràng buộc mới của spec: **không** dùng DB mặc định `checkpointing_db`), `ttl=checkpoint_ttl_days*86400`.
2. **motor 3.3.2 không chạy với pymongo 4.19** (`ImportError: cannot import name '_QUERY_OPTIONS' from 'pymongo.cursor'`). → Nâng `motor==3.7.1` (đã thử: insert/find chạy được với pymongo 4.19.0).
3. **`interrupt()` nằm ở node `confirm` của graph ngoài, không nằm trong `ConfirmMiddleware`.** Lý do: (a) câu hỏi xác nhận phải qua `guard` trước khi lượt kết thúc. Interrupt trong `after_agent` của agent sẽ dừng trước guard, nên `complete` mang câu chưa gác hoặc rỗng. (b) Trong `factory.py`, `jump_to: "end"` từ một `after_agent` quay về after_agent ĐẦU chuỗi (`end_destination=exit_node`), nên resume bên trong agent dễ thành vòng lặp. (c) Resume ở node ngoài chỉ chạy lại đúng node đó; đã thử: `Command(resume=x, update={...})` thì node resume thấy giá trị `update`; interrupt thứ hai trong cùng lượt vẫn dừng đúng. `ConfirmMiddleware` vẫn có (`awrap_tool_call`): nó bắt đề xuất từ kết quả `propose_appointment`/`cancel_appointment` vào state.
4. **M4: `phrase` là `before_agent` (nhảy `jump_to: "end"`), không phải `after_agent`.** Câu chốt phải chạy THAY vòng lặp agent; nếu là `after_agent` thì model booking đã chạy trước, vô ích. `jump_to "end"` từ before_agent đi vào chuỗi after_agent, nên guard vẫn kiểm câu chốt như A.
5. **Cắt lịch sử dùng `wrap_model_call`, không dùng `before_model`.** `before_model` trả update là sửa state đã lưu vào checkpoint; ở đây chỉ cần lọc thứ gửi model.
6. **Ca nửa đêm.** Nếu theo đúng chữ `thread_id=<user>:<ngày VN>` thì mất luật giữ tin 30 phút (khách được hỏi lúc 23:58, đáp "ừ" lúc 00:01). → Thêm luật nối thread: trước 00:30 giờ VN, nếu thread hôm nay chưa có checkpoint mà thread hôm qua có checkpoint trong 30 phút gần nhất, thì dùng tiếp thread hôm qua. Cửa sổ lịch sử vẫn cắt theo `_session_cutoff` như A, dựa trên `created_at` gắn vào từng message.
7. **`previous_replies` (guard) và `last_reply` (khối bối cảnh) vẫn đọc từ `conversations`.** Hai thứ này không phải lịch sử model, giữ nguyên để giảm rủi ro.
8. **Luật lộ tên tool (M4) là xoá tất định, không gọi rewrite.** Spec `028451c` đã hạ luật này xuống mức phòng ngừa, vì A không lộ tên tool. Xoá bằng code bảo đảm `tool_name_leak = 0` mà không tốn LLM.
9. **Bộ phân loại dùng cùng deployment** (`gpt-5.4-mini` vốn là model nhỏ). Không thêm setting model riêng.
10. **Hết vòng tool thì bỏ đề xuất.** A vẫn giữ pending. Nếu giữ, câu "dạ vâng để chị gọi" ở G-07 có thể ghi lịch, trong khi bot chưa hề hỏi xác nhận (bot nói "em chưa tra được"). Bỏ đề xuất đi đúng hướng phản mục tiêu.
11. **Lưới phủ quyết thêm hoãn kiểu "để … gọi/tính/nghĩ"** ngoài "hỏi/xem lại" (G-07: "dạ vâng để chị gọi").
12. **Mỗi thread chỉ một lượt chạy.** Có checkpointer thì hai lượt song song trên cùng thread (ca `azure-cham`: lượt 1 treo, khách gửi lượt 2) ghi đè checkpoint của nhau. → Lượt mới huỷ lượt cũ còn đang chạy của cùng user. Lượt cũ chưa phát `complete` nên khách không mất gì.
13. **`parallel_tool_calls=False` chỉ gắn khi request có tool.** `create_agent` gọi `model.bind(**model_settings)` khi không có tool, và OpenAI/Azure trả 400 nếu gửi `parallel_tool_calls` mà không kèm tools (agent `social` có 0 tool).
14. **M0 không có lượt đo** (lịch đo của spec bắt đầu từ M1). Task cuối M0 chỉ ghi lệnh smoke tuỳ chọn.

---

## Global Constraints

- Đúng **1 worker** uvicorn. Không `--workers`, không chạy song song hai `pytest` (bẫy "Lặt vặt": index build failed).
- Config **chỉ từ `.env`**. Thêm setting = `app/core/config.py` + `.env.example` + `.env` (trong worktree `.env` là bản copy, không commit).
- **Mọi logic đặt lịch ở `app/services/`.** Middleware/tool/node chỉ gọi `AppointmentService.create/reschedule/cancel`.
- **Agent không có tool ghi**, kể cả bọc HITL. Không dùng `HumanInTheLoopMiddleware`. Fence `test_there_is_NO_tool_that_writes_an_appointment` phải còn.
- Ghi lịch chỉ từ `proposal` trong state. `proposal` dựng từ JSON tool, mà JSON đó do code sinh từ giá trị service; không bao giờ từ chuỗi model gõ.
- **Chỉ stream token mang tag `respond`.** Supervisor `supervisor`, bộ phân loại `classify` (+`streaming=False`), rewrite `rewrite`, time-parser `timeparse`, state `state`.
- Thứ tự message model nhận (bẫy #9): **System → [summary + slots] → lịch sử → khối bối cảnh → câu khách → (tool exchange của lượt này)**. Khối bối cảnh có ngày hôm nay ở dòng đầu (bẫy #6) và không nằm trong system prompt.
- Index unique partial của slot; hủy = đổi `status` (bẫy #1, #2). Không đụng repository.
- Giao thức Socket.IO giữ nguyên: `turn_started` / `token` / `tool_started` / `tool_finished` / `complete` / `error`, payload y như cũ.
- Câu khách thấy giữ nguyên chữ: `FALLBACK_ANSWER = "Dạ em chưa tra được, anh chị gọi trực tiếp cho tiệm giúp em nhé ạ."`, câu lỗi tool `"Tra cứu không được, thử lại giúp em ạ."`, câu lỗi lượt `"Máy đang bận chút xíu, anh chị nhắn lại giúp em nhé."`, mọi fallback của `confirm` cũ.
- Middleware **không giữ trạng thái lượt chat trong `self.*`**; mọi thứ theo lượt đi qua state/request.
- Prompt mới viết bằng **tiếng Anh**, **không có câu ví dụ tiếng Việt cho luật phân loại** (bẫy #15, #16).
- Langfuse: metadata giữ `langfuse_user_id = user_id` và `langfuse_session_id = user_id` (không phải thread_id).
- Checkpointer nằm trong **`settings.mongo_db_name`**, collections `checkpoints` / `checkpoint_writes`; TTL mặc định 7 ngày (`CHECKPOINT_TTL_DAYS`).
- Docs/comment/chuỗi giao diện tiếng Việt; commit message tiếng Anh ngắn, kết bằng hai dòng trailer `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>` và `Claude-Session: https://claude.ai/code/session_015JyTdz3j1eVM1fJ4H64tzQ`.
- Test middleware dùng **model giả** (`tests/fakes.py`), không gọi Azure. Test thật mang marker `llm`.
- Lệnh test trong worktree: `PYTHONPATH=. .venv/bin/python -m pytest -q` (Mongo ở :27017, DB `chatbot_test_db`).
- Không merge, không push.

## Review Focus

1. **Khách trả lời mơ hồ hoặc có từ đồng ý lẫn trong câu khác** ("dạ vâng để chị gọi", "phải tính trước cho chắc", "ok mà 4 giờ được không em?"): không bao giờ ghi. Test ở Task 3.1 (lưới) và Task 3.5 (đầu-cuối với bộ phân loại giả trả `approve`).
2. **Lượt lỗi giữa chừng không làm kẹt thread hay để lại đề xuất mồ côi** (A-07): lượt sau gửi input mới thì `proposal=None`, chạy lại từ START. Test ở Task 2.3 và Task 3.5.
3. **Mốc thời gian lệch mili-giây giữa checkpoint và `conversations`**: tin đã nén bị gửi lại, hoặc tin chưa nén bị bỏ. Hai nơi phải dùng cùng một `now_ms()`. Test ở Task 2.2.
4. **Hai lượt cùng user chạy chồng nhau** (Azure treo): lượt sau phải chạy được và không bị checkpoint của lượt trước ghi đè. Test ở Task 2.4.
5. **Agent không có tool (`social`) không được gửi `parallel_tool_calls`**: nếu gửi, Azure trả 400 và mọi câu xã giao thành "Máy đang bận". Test ở Task 1.2.

---

## File Structure

```
app/agents/booking_graph/
  agents.py              make_agent_node (bọc create_agent) — thay make_subagent_node
  middleware/__init__.py export các middleware
  middleware/layout.py   MessageLayoutMiddleware (thứ tự bẫy #9)
  middleware/tools.py    ToolRoundLimitMiddleware, ToolErrorMiddleware, NoParallelToolsMiddleware
  middleware/history.py  HistoryWindowMiddleware (M2)
  middleware/confirm.py  ConfirmMiddleware: bắt proposal từ kết quả tool (M3)
  middleware/guard.py    GuardMiddleware (M4: guard + rewrite 1 lần + xoá tên tool)
  middleware/phrase.py   PhraseMiddleware (M4: before_agent)
  middleware/intent.py   IntentPromptMiddleware (M5, biến thể một agent)
  history.py             model_history(): cửa sổ lịch sử thuần (M2)
  reply.py               strip_diacritics, veto_reason, classify_reply, decide (M3)
  confirm.py             execute_proposal + make_confirm_node (interrupt) — thay node regex
  events.py              run_turn: thread, resume, khoá theo user
  checkpoint.py → app/agents/checkpoint.py  make_checkpointer / get_checkpointer (M2)
tests/fakes.py           ScriptedChatModel dùng chung
```

---

# M0 — nền

### Task 0.1: Cài checkpointer Mongo, nâng motor, pin phụ thuộc

Bẫy liên quan: không bẫy nào trong 23; A-08 (pin). Giữ `bcrypt==4.0.1`.

**Files:**
- Modify: `requirements.txt` (khối `# Database`, `# Agent`)
- Create: `tests/test_requirements_pins.py`

**Interfaces:**
- Consumes: không có
- Produces: các gói import được: `langgraph.checkpoint.mongodb.MongoDBSaver`, `pymongo.MongoClient`; motor 3.7.1

- [ ] **Step 1: Dựng môi trường worktree**

```bash
cd /home/henryb1/Desktop/HenryB1/data/salona-booking-harness-b
cp /home/henryb1/Desktop/HenryB1/data/salona-booking/.env .env   # gitignored, không commit
uv venv .venv --python 3.12 && uv pip install -p .venv/bin/python -r requirements.txt
PYTHONPATH=. .venv/bin/python -m pytest -q
```
Expected: suite xanh như nhánh gốc (đây là baseline trước khi đổi gì).

- [ ] **Step 2: Viết test pin (đỏ)**

```python
# tests/test_requirements_pins.py
import re
PINNED = ("langchain", "langchain-core", "langchain-openai", "langgraph",
          "langgraph-checkpoint", "langgraph-checkpoint-mongodb", "pymongo", "motor")

def _reqs():
    out = {}
    for line in open("requirements.txt", encoding="utf-8"):
        line = line.split("#", 1)[0].strip()
        m = re.match(r"^([A-Za-z0-9_.\-\[\]]+?)(\[.*\])?\s*(==|>=|<=|~=|>|<)?\s*(.*)$", line)
        if line and m:
            out[m.group(1).lower()] = (m.group(3), m.group(4))
    return out

def test_agent_and_mongo_packages_are_pinned_exactly():
    reqs = _reqs()
    for name in PINNED:
        assert name in reqs, name
        assert reqs[name][0] == "==", f"{name} phải pin '==' (A-08)"

def test_motor_is_new_enough_for_pymongo_4_19():
    assert _reqs()["motor"][1] == "3.7.1"
    assert _reqs()["pymongo"][1] == "4.19.0"

def test_checkpointer_package_imports():
    from langgraph.checkpoint.mongodb import MongoDBSaver  # noqa: F401
```

- [ ] **Step 3: Chạy cho đỏ**

Run: `PYTHONPATH=. .venv/bin/python -m pytest tests/test_requirements_pins.py -v`
Expected: FAIL (`langchain>=0.3`, `motor==3.3.2`, thiếu gói checkpoint).

- [ ] **Step 4: Sửa `requirements.txt`**

Đặt `motor==3.7.1`, `pymongo==4.19.0`. Khối Agent pin chính xác `langgraph==1.2.11`, `langgraph-checkpoint==4.2.0`, `langgraph-checkpoint-mongodb==0.5.1`, `langchain==1.3.15`, `langchain-core==1.5.5`, `langchain-openai==1.5.1`. Giữ comment cũ về gói `langchain` (bẫy #5). Cài lại bằng `uv pip install -p .venv/bin/python -r requirements.txt`, rồi `uv pip list -p .venv/bin/python | grep -iE "lang|mongo|motor"`. Nếu resolver chọn bản khác các số trên, chỉ được đổi khi bản trên không cài được; ghi lý do vào commit message.

- [ ] **Step 5: Chạy test pin và toàn bộ suite**

Run: `PYTHONPATH=. .venv/bin/python -m pytest -q`
Expected: PASS hết. Nếu test động tới motor đỏ thì dừng lại sửa trước: đó là đúng rủi ro của mốc này.

- [ ] **Step 6: Commit**

```bash
git add requirements.txt tests/test_requirements_pins.py
git commit -m "build: pin agent deps, add mongo checkpointer, bump motor"   # + 2 dòng trailer
```

### Task 0.2: Mốc M0 — tag harness-b/m0, full pytest green

- [ ] **Step 1:** `PYTHONPATH=. .venv/bin/python -m pytest -q`. Expected: 0 failed.
- [ ] **Step 2:** `git tag harness-b/m0`
- [ ] **Step 3: Đo.** Lịch đo không có M0. Lệnh smoke tuỳ chọn để controller kiểm worktree bench dựng được venv mới (chạy từ `/home/henryb1/Desktop/HenryB1/data/salona-booking`, nhánh `bench/harness-benchmark`):

```bash
bench/.venv/bin/python -m bench run --ref docs/arch-security-review:a --ref harness-b/m0:a --tags fault --k 1
```

---

# M1 — đổi vòng lặp sang `create_agent`, giữ hành vi

### Task 1.1: Model giả dùng chung + `MessageLayoutMiddleware`

Bẫy liên quan: **#9** (thứ tự), **#6** (khối bối cảnh có ngày, đứng sát câu khách), **#20/#22** (summary/slots chỉ là chữ, đứng ngay sau system, cùng một message).

**Files:**
- Create: `tests/fakes.py`, `app/agents/booking_graph/middleware/__init__.py`, `app/agents/booking_graph/middleware/layout.py`
- Test: `tests/test_mw_layout.py`

**Interfaces:**
- Consumes: `render_slots(slots) -> str` (`context.py`), `STATE_HEADER` (`agents.py`)
- Produces:
  - `class LayoutState(AgentState)`: `context_block: NotRequired[str]`, `summary: NotRequired[list[str]]`, `slots: NotRequired[Any]`
  - `class MessageLayoutMiddleware(AgentMiddleware)`: `state_schema = LayoutState`; `async def awrap_model_call(request, handler)`
  - `def layout_messages(messages: list[AnyMessage], *, context_block: str, summary: list[str], slots) -> list[AnyMessage]` (hàm thuần, middleware gọi nó)
  - `tests/fakes.py::ScriptedChatModel`, `tool_call(name, args, id) -> AIMessage`

- [ ] **Step 1: Viết `tests/fakes.py`** (hạ tầng test; đã thử chạy được với `create_agent` + `astream_events`)

```python
import json
from typing import Any, List
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, AIMessageChunk
from langchain_core.outputs import ChatGeneration, ChatGenerationChunk, ChatResult

class ScriptedChatModel(BaseChatModel):
    """Trả lần lượt các AIMessage dựng sẵn; ghi lại mọi lô message và kwargs bind."""
    replies: List[Any]
    calls: List[Any] = []
    bind_kwargs: List[dict] = []
    bound_tools: Any = None

    @property
    def _llm_type(self) -> str:
        return "scripted"

    def bind_tools(self, tools, **kwargs):
        self.bound_tools = tools
        self.bind_kwargs.append(kwargs)
        return self

    def bind(self, **kwargs):
        self.bind_kwargs.append(kwargs)
        return self

    def _next(self, messages):
        self.calls.append(list(messages))
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply

    def _generate(self, messages, stop=None, run_manager=None, **kw):
        return ChatResult(generations=[ChatGeneration(message=self._next(messages))])

    async def _astream(self, messages, stop=None, run_manager=None, **kw):
        m = self._next(messages)
        chunk = ChatGenerationChunk(message=AIMessageChunk(
            content=m.content, tool_call_chunks=[
                {"name": t["name"], "args": json.dumps(t["args"]), "id": t["id"], "index": i}
                for i, t in enumerate(m.tool_calls)]))
        if run_manager:
            await run_manager.on_llm_new_token(m.content or "", chunk=chunk)
        yield chunk

def scripted(replies, tags=("respond",)):
    return ScriptedChatModel(replies=list(replies), calls=[], bind_kwargs=[], tags=list(tags))

def tool_call(name, args, id="c1"):
    return AIMessage(content="", tool_calls=[{"name": name, "args": args, "id": id}])
```

- [ ] **Step 2: Viết test (đỏ)**: chuyển nguyên các khẳng định thứ tự của `tests/test_subagents.py` sang `layout_messages`

```python
# tests/test_mw_layout.py
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from app.agents.booking_graph.middleware.layout import layout_messages
from app.models.conversation import ConversationSlots
CTX = "Bạn đang nói chuyện với: Cô Lan (0912345678)."

def contents(ms): return [m.content for m in ms]

def test_context_block_sits_right_before_the_question():
    out = layout_messages([HumanMessage("câu cũ"), AIMessage("đáp cũ"), HumanMessage("câu mới")],
                          context_block=CTX, summary=[], slots=None)
    assert contents(out) == ["câu cũ", "đáp cũ", CTX, "câu mới"]

def test_tool_exchange_of_this_turn_stays_after_the_question():
    tc = AIMessage("", tool_calls=[{"name": "x", "args": {}, "id": "c1"}])
    out = layout_messages([HumanMessage("q"), tc, ToolMessage("r", tool_call_id="c1")],
                          context_block=CTX, summary=[], slots=None)
    assert contents(out)[:2] == [CTX, "q"] and out[2] is tc and out[3].content == "r"

def test_summary_and_slots_ride_in_one_message_before_history():
    out = layout_messages([HumanMessage("hôm qua"), HumanMessage("còn không em")], context_block=CTX,
                          summary=["Khách muốn làm tóc."], slots=ConversationSlots(service="làm móng bột"))
    assert out[0].content.startswith("Diễn biến phần trước của cuộc trò chuyện hôm nay:")
    assert "- Khách muốn làm tóc." in out[0].content and "- Dịch vụ: làm móng bột" in out[0].content
    assert contents(out)[1:] == ["hôm qua", CTX, "còn không em"]

def test_no_state_block_when_nothing_to_say():
    out = layout_messages([HumanMessage("q")], context_block=CTX, summary=[], slots=None)
    assert contents(out) == [CTX, "q"]

def test_empty_messages_do_not_crash():
    assert contents(layout_messages([], context_block=CTX, summary=[], slots=None)) == [CTX]

async def test_middleware_keeps_the_system_message_first_and_sends_the_layout():
    from langchain.agents import create_agent
    from tests.fakes import scripted
    from app.agents.booking_graph.middleware.layout import MessageLayoutMiddleware
    model = scripted([AIMessage("Dạ.")])
    agent = create_agent(model, [], system_prompt="prompt", middleware=[MessageLayoutMiddleware()])
    await agent.ainvoke({"messages": [HumanMessage("q")], "context_block": CTX})
    assert contents(model.calls[0]) == ["prompt", CTX, "q"]
```

- [ ] **Step 3:** Run `PYTHONPATH=. .venv/bin/python -m pytest tests/test_mw_layout.py -v`. Expected: FAIL (module not found).
- [ ] **Step 4: Implement** `layout_messages` (câu khách = HumanMessage **cuối cùng**; phần trước là lịch sử, phần sau là tool exchange; khối trạng thái dựng y như `make_subagent_node`) và `MessageLayoutMiddleware.awrap_model_call` (`request.override(messages=layout_messages(request.messages, context_block=request.state.get("context_block", ""), summary=request.state.get("summary") or [], slots=request.state.get("slots")))`).
- [ ] **Step 5:** Run lại. Expected: PASS.
- [ ] **Step 6: Commit** `feat: add message layout middleware and scripted test model`

### Task 1.2: Giới hạn 4 vòng tool, lỗi tool, tắt parallel tool calls

Bẫy liên quan: **#23** (tool trả dữ kiện; câu lỗi giữ nguyên), Review Focus #5.

**Files:**
- Create: `app/agents/booking_graph/middleware/tools.py`
- Test: `tests/test_mw_tools.py`

**Interfaces:**
- Consumes: `MAX_TOOL_ROUNDS = 4`, `FALLBACK_ANSWER` (`agents.py`)
- Produces:
  - `TOOL_ERROR_TEXT = "Tra cứu không được, thử lại giúp em ạ."`
  - `class LimitState(AgentState)`: `tool_loop_exhausted: NotRequired[bool]`
  - `class ToolRoundLimitMiddleware(AgentMiddleware)`: `__init__(self, max_rounds: int = MAX_TOOL_ROUNDS)`; `@hook_config(can_jump_to=["end"]) async def abefore_model(state, runtime)`. Đếm AIMessage sau HumanMessage cuối; `>= max_rounds` thì trả `{"jump_to": "end", "messages": [AIMessage(FALLBACK_ANSWER)], "tool_loop_exhausted": True}` và log `tool_loop_exhausted`.
  - `class ToolErrorMiddleware(AgentMiddleware)`: `awrap_tool_call`. Tool `None` → `ToolMessage(f"Không có tool tên {name}.")` + log `unknown_tool_requested`, không gọi handler. Exception → `ToolMessage(TOOL_ERROR_TEXT)` + log `tool_failed`.
  - `class NoParallelToolsMiddleware(AgentMiddleware)`: `awrap_model_call`. Chỉ khi `request.tools` khác rỗng thì `model_settings={**request.model_settings, "parallel_tool_calls": False}`.

- [ ] **Step 1: Viết test (đỏ)**, mỗi test dựng `create_agent(scripted([...]), [fake_lookup], system_prompt="p", middleware=[...])`, với `fake_lookup` như `tests/test_subagents.py`:
  - `test_four_model_calls_then_fallback`: 7 lượt `tool_call("fake_lookup", {"day": "2026-08-07"}, id=f"c{i}")`. Khẳng định `len(model.calls) == 4`, tin cuối `.content == FALLBACK_ANSWER`, `out["tool_loop_exhausted"] is True`.
  - `test_answer_without_tools_does_not_set_exhausted`: `"tool_loop_exhausted" not in out`.
  - `test_rounds_are_counted_from_the_last_customer_message`: state vào có lịch sử `[Human, AI, AI, AI, AI, Human("mới")]`, model trả lời ngay. Không fallback.
  - `test_tool_exception_becomes_the_vietnamese_error_text`: tool ném `RuntimeError`. ToolMessage trong lô gọi model lần 2 có `content == TOOL_ERROR_TEXT`.
  - `test_unknown_tool_does_not_crash`: `tool_call("khong_ton_tai", {})` rồi `AIMessage("Dạ.")`. Kết quả cuối `"Dạ."`, có ToolMessage `"Không có tool tên khong_ton_tai."`.
  - `test_parallel_tool_calls_off_when_tools_exist`: `model.bind_kwargs[0]["parallel_tool_calls"] is False`.
  - `test_no_parallel_flag_without_tools`: agent không tool. `all("parallel_tool_calls" not in k for k in model.bind_kwargs)`.
- [ ] **Step 2:** Run `pytest tests/test_mw_tools.py -v`. Expected: FAIL.
- [ ] **Step 3: Implement** ba middleware theo Interfaces. Không lưu bộ đếm trong `self`.
- [ ] **Step 4:** Run lại. Expected: PASS.
- [ ] **Step 5: Commit** `feat: add tool round limit, tool error and no-parallel middleware`

### Task 1.3: `make_agent_node` thay `make_subagent_node`, nối vào graph

Bẫy liên quan: **#7** (booking 7 tool, 0 tool ghi), **#8** (tag `respond` trên model agent; supervisor không), **#21** (agent chỉ đặt `draft`, guard mới đặt `answer`).

**Files:**
- Modify: `app/agents/booking_graph/agents.py` (bỏ `make_subagent_node`; giữ `MAX_TOOL_ROUNDS`, `FALLBACK_ANSWER`, `STATE_HEADER`)
- Modify: `app/agents/booking_graph/graph.py` (ba node `shop`/`social`/`booking`)
- Modify: `tests/test_subagents.py` (viết lại trên `make_agent_node`)
- Test: `tests/test_graph_events.py` (thêm test tag)

**Interfaces:**
- Consumes: middleware Task 1.1, 1.2; `build_chat_model(tags, temperature)`
- Produces:
  - `def build_agent(prompt: str, tools: list[BaseTool], *, tag: str = RESPOND_TAG, model=None, extra_middleware: Sequence[AgentMiddleware] = ()) -> CompiledStateGraph`: `create_agent(model or build_chat_model(tags=[tag], temperature=0.2), tools, system_prompt=prompt, middleware=[*extra_middleware, MessageLayoutMiddleware(), NoParallelToolsMiddleware(), ToolRoundLimitMiddleware(), ToolErrorMiddleware()], checkpointer=False)`. `extra_middleware` đứng ngoài cùng (M2 thêm history, M3 thêm confirm).
  - `def make_agent_node(agent) -> Callable[[GraphState], Awaitable[dict]]`: gọi `agent.ainvoke({"messages": state["messages"], "context_block": ..., "summary": ..., "slots": ...})` và trả `{"draft": <content AIMessage cuối> or FALLBACK_ANSWER}`. Tool message **không** quay về `messages` ngoài.
  - `RESPOND_TAG` chuyển sang `agents.py`; `graph.py` và `events.py` import lại từ đó, giữ tên cũ.

- [ ] **Step 1: Viết lại `tests/test_subagents.py` (đỏ)** với cùng tên test và cùng khẳng định. Vì `build_agent(..., model=scripted([...]))` nhận model, test không cần monkeypatch. `test_llm_node_returns_a_draft_not_an_answer`: `out == {"draft": "Dạ."}`. `test_tools_are_bound_to_the_model`: `[t.name for t in model.bound_tools] == ["fake_lookup"]`. `test_tool_loop_stops_at_the_limit`: `out["draft"] == FALLBACK_ANSWER`. Thêm `test_empty_model_reply_falls_back`: `AIMessage("")` cho ra `FALLBACK_ANSWER`.
- [ ] **Step 2: Thêm test tag (đỏ)** vào `tests/test_graph_events.py`:

```python
async def test_agent_tokens_carry_the_respond_tag_inside_the_graph():
    from langchain_core.messages import AIMessage, HumanMessage
    from app.agents.booking_graph.agents import build_agent, make_agent_node
    from tests.fakes import scripted
    node = make_agent_node(build_agent("p", [], model=scripted([AIMessage("Dạ em chào chị.")])))
    from langgraph.graph import StateGraph, START, END
    from app.agents.booking_graph.state import GraphState
    g = StateGraph(GraphState); g.add_node("social", node); g.add_edge(START, "social"); g.add_edge("social", END)
    texts = [e.data["text"] async for raw in g.compile().astream_events(
                 {"messages": [HumanMessage("chào em")], "context_block": "x"}, version="v2")
             for e in translate_langchain_event(raw) if e.type == "token"]
    assert "".join(texts) == "Dạ em chào chị."
```

- [ ] **Step 3:** Run `pytest tests/test_subagents.py tests/test_graph_events.py -v`. Expected: FAIL.
- [ ] **Step 4: Implement** `build_agent`, `make_agent_node`; đổi `graph.py` sang `make_agent_node(build_agent(PROMPT, tools))`. Cạnh và tên node giữ nguyên (`tests/test_graph_wiring.py` không đổi).
- [ ] **Step 5:** Run `PYTHONPATH=. .venv/bin/python -m pytest -q`. Expected: PASS hết.
- [ ] **Step 6: Commit** `refactor: run subagents on create_agent with middleware`

### Task 1.4: Mốc M1 — tag harness-b/m1, full pytest green

- [ ] **Step 1:** `grep -rn "make_subagent_node" app tests scripts CONTEXT.md AGENTS.md`. Sửa chỗ còn nhắc (CONTEXT.md mục "Một lượt chat đi qua đâu" ghi `create_agent`).
- [ ] **Step 2:** `PYTHONPATH=. .venv/bin/python -m pytest -q`. Expected: 0 failed. Commit docs nếu có (`docs: note create_agent in agent flow`).
- [ ] **Step 3:** `git tag harness-b/m1`
- [ ] **Step 4: Lệnh đo controller chạy** (từ `/home/henryb1/Desktop/HenryB1/data/salona-booking`, nhánh `bench/harness-benchmark`; mọi tag trừ `slow`, k=2):

```bash
SLOW='llm/llm-32.yaml edge/pending-het-han.yaml edge/compaction-long.yaml'
FILES=$(find bench/scenarios -name '*.yaml' | \
  grep -vFf <(for s in $SLOW; do echo "bench/scenarios/$s"; done))
bench/.venv/bin/python -m bench run --ref docs/arch-security-review:a --ref harness-b/m1:a --k 2 --scenarios $FILES
```
Cổng: "không tệ đi" theo spec. Khác biệt ở kịch bản scripted phải giải thích được.

---

# M2 — lịch sử sang checkpointer

### Task 2.1: Setting TTL + `make_checkpointer` trong DB của ref

Bẫy liên quan: cấu hình chỉ từ `.env`; ràng buộc spec mục Benchmark 5 (checkpointer trong `MONGO_DB_NAME`).

**Files:**
- Modify: `app/core/config.py`, `.env.example`, `.env` (worktree, không commit)
- Create: `app/agents/checkpoint.py`
- Modify: `main.py` (lifespan khởi tạo, fail-hard như Mongo)
- Test: `tests/test_checkpoint.py`

**Interfaces:**
- Produces:
  - `Settings.checkpoint_ttl_days: int = 7` (`.env`: `CHECKPOINT_TTL_DAYS=7`)
  - `def make_checkpointer(client: MongoClient | None = None) -> MongoDBSaver`: `MongoDBSaver(client or MongoClient(str(settings.mongo_uri), tz_aware=True), db_name=settings.mongo_db_name, ttl=settings.checkpoint_ttl_days * 86400, serde=JsonPlusSerializer(allowed_msgpack_modules=[("app.models.conversation", "ConversationSlots")]))`
  - `def get_checkpointer() -> MongoDBSaver` (singleton module-level, tạo lần đầu); `def set_checkpointer(saver) -> None` (cho test/lifespan)

- [ ] **Step 1: Viết test (đỏ)**

```python
# tests/test_checkpoint.py
from pymongo import MongoClient
from app.core.config import settings
from tests.conftest import TEST_DB_NAME, TEST_MONGO_URI

async def test_checkpoints_live_in_the_settings_db_not_checkpointing_db(monkeypatch, test_db):
    from app.agents.checkpoint import make_checkpointer
    monkeypatch.setattr(settings, "mongo_db_name", TEST_DB_NAME)
    saver = make_checkpointer(MongoClient(TEST_MONGO_URI, tz_aware=True))
    assert saver.checkpoint_collection.database.name == TEST_DB_NAME
    from langgraph.graph import StateGraph, START, END
    from typing import TypedDict
    class S(TypedDict): x: int
    g = StateGraph(S); g.add_node("n", lambda s: {"x": 1}); g.add_edge(START, "n"); g.add_edge("n", END)
    await g.compile(checkpointer=saver).ainvoke({"x": 0}, {"configurable": {"thread_id": "u:2026-10-17"}})
    assert "checkpoints" in await test_db.list_collection_names()

def test_ttl_index_uses_the_setting(monkeypatch):
    from app.agents.checkpoint import make_checkpointer
    monkeypatch.setattr(settings, "mongo_db_name", TEST_DB_NAME)
    monkeypatch.setattr(settings, "checkpoint_ttl_days", 2)
    saver = make_checkpointer(MongoClient(TEST_MONGO_URI, tz_aware=True))
    ttl = [i for i in saver.checkpoint_collection.index_information().values() if "expireAfterSeconds" in i]
    assert ttl and ttl[0]["expireAfterSeconds"] == 2 * 86400

def test_ttl_default_is_seven_days():
    assert type(settings).model_fields["checkpoint_ttl_days"].default == 7
```
(Khẳng định chính là `saver.checkpoint_collection.database.name == TEST_DB_NAME`, tức không phải `checkpointing_db`.)

- [ ] **Step 2:** Run `pytest tests/test_checkpoint.py -v`. Expected: FAIL.
- [ ] **Step 3: Implement** theo Interfaces. Lifespan gọi `set_checkpointer(make_checkpointer())` ngay sau `ensure_indexes`, trong cùng khối try fail-hard; shutdown đóng client. Thêm `CHECKPOINT_TTL_DAYS=7` vào `.env.example` (có comment tiếng Việt) và `.env` của worktree.
- [ ] **Step 4:** Run lại. Expected: PASS.
- [ ] **Step 5: Commit** `feat: add mongo checkpointer in the app database`

### Task 2.2: Mốc thời gian chung + cửa sổ lịch sử thuần + `HistoryWindowMiddleware`

Bẫy liên quan: **"Phiên hội thoại: một phiên mỗi ngày, cắt lúc ĐỌC"**, luật nửa đêm 30 phút, ngân sách 1500 token, luật bỏ câu đáp đầu khi bị cắt (docstring `ConversationService.history`), **#20** (summary + `covers_until`). Review Focus #3.

**Files:**
- Modify: `app/core/clock.py` (`now_ms`), `app/services/conversation.py` (`append(..., created_at=None)`, tách `select_window`)
- Create: `app/agents/booking_graph/history.py`, `app/agents/booking_graph/middleware/history.py`
- Modify: `app/agents/booking_graph/phrase.py` (lịch sử qua `model_history`)
- Test: `tests/test_history_window.py`, `tests/test_conversation.py` (giữ xanh)

**Interfaces:**
- Produces:
  - `def now_ms() -> datetime`: `now_utc()` cắt về mili-giây (Mongo lưu ms)
  - `def select_window(items: list, *, cutoff: datetime, after: datetime | None, token_budget: int) -> list`: thuần; `items` có `.role` (`"user"|"assistant"`), `.content`, `.created_at`; đúng thuật toán đang nằm trong `history()`. `history()` gọi lại nó.
  - `ConversationService.session_cutoff() -> datetime` (đổi `_session_cutoff` thành public; giữ alias `_session_cutoff`)
  - `CREATED_AT_KEY = "created_at"`; `def stamp(message: BaseMessage, at: datetime) -> BaseMessage` (ghi ISO vào `response_metadata`); `def stamped_at(message) -> datetime | None`
  - `def model_history(messages: list[AnyMessage], *, after: datetime | None, now: datetime | None = None, token_budget: int = DEFAULT_TOKEN_BUDGET) -> list[AnyMessage]`: chỉ Human/AI có stamp; cutoff = `min(đầu ngày VN, now − 30')`
  - `class HistoryState(AgentState)`: `history_after: NotRequired[datetime | None]`
  - `class HistoryWindowMiddleware`: `awrap_model_call`; thay phần trước HumanMessage cuối bằng `model_history(...)`; giữ câu khách và tool exchange
  - `make_phrase_node` dùng `model_history(state["messages"][:-1], after=state.get("history_after"))`

- [ ] **Step 1: Viết test (đỏ)**
  - `test_select_window_matches_history_on_the_same_data`: dựng 6 `ChatMessage` qua `ConversationService.append`. So `history()` với `select_window(await _all_messages(), cutoff=session_cutoff(), after=None, token_budget=1500)` theo nội dung.
  - `test_model_history_drops_messages_up_to_covers_until`: ba cặp Q/A stamp t1<t2<t3, `after=t2`, chỉ còn tin t3.
  - `test_same_millisecond_stamp_is_treated_as_compacted`: stamp bằng `now_ms()` rồi ghi Mongo qua `append(created_at=...)`; đọc lại `covers_until` từ Mongo; `model_history(after=covers_until)` loại đúng tin đó.
  - `test_model_history_keeps_last_30_minutes_across_midnight`: `now` = 00:05 giờ VN, tin lúc 23:50 hôm trước được giữ, tin 22:00 bị bỏ.
  - `test_budget_drops_a_leading_assistant_only_when_truncated`: bản sao hai test tương ứng của `history()`.
  - `test_middleware_sends_only_the_window_and_keeps_layout_order`: agent `[HistoryWindowMiddleware(), MessageLayoutMiddleware()]`, state có 2 cặp cũ (1 cặp ≤ after). Lô gửi model là `["p", <cặp mới>, CTX, "q"]`.
  - `test_append_uses_the_given_created_at`.
- [ ] **Step 2:** Run. Expected: FAIL.
- [ ] **Step 3: Implement** theo Interfaces. Tin không có stamp (state cũ, test M1) coi như `created_at = now` để không bị cắt.
- [ ] **Step 4:** Run `pytest tests/test_history_window.py tests/test_conversation.py tests/test_phrase.py -v`. Expected: PASS.
- [ ] **Step 5: Commit** `feat: window model history from stamped thread messages`

### Task 2.3: `run_turn` chạy trên thread checkpointer

Bẫy liên quan: **#8** (lọc token không đổi), **#9**, ca nửa đêm (Lệch #6), A-07 (Review Focus #2), Langfuse **#4** (session = user_id).

**Files:**
- Modify: `app/agents/booking_graph/events.py`, `graph.py` (`build_graph(db, user, checkpointer=None)`; node `record` sau guard), `state.py` (`history_after`)
- Modify: `app/agents/booking_graph/context.py` (`load_context` trả thêm `history_after = state.covers_until`)
- Test: `tests/test_run_turn_thread.py`; sửa `tests/test_graph_events.py` (FakeGraph có `aget_state`)

**Interfaces:**
- Consumes: `get_checkpointer()`, `stamp`, `now_ms`, `HistoryWindowMiddleware` (thêm vào `extra_middleware` của cả ba agent)
- Produces:
  - `def thread_id_for(user_id: str, now: datetime) -> str` = `f"{user_id}:{to_local(now).date().isoformat()}"`
  - `async def pick_thread(graph, user_id: str, now: datetime) -> str`: luật nối nửa đêm (Lệch #6), dựa vào `StateSnapshot.created_at`
  - `def fresh_turn_fields(context: dict, question: str) -> dict`: đặt lại `answer=""`, `draft=""`, `original_draft=""`, `violations=[]`, `rewritten=False`, `route=""`, `fallback=None`, `confirm_fact=None`, `phrase_fact=None`; cùng `context_block`, `summary`, `slots`, `history_after`, `previous_replies`, `customer_text`, `user_id`. (M3 thêm `proposal=None` cho lượt mới.)
  - node `record`: `{"messages": [stamp(AIMessage(answer), now_ms())]}`; cạnh `guard --end--> record --> END`
  - `run_turn`: `asked_at = now_ms()`; input `{"messages": [stamp(HumanMessage(question), asked_at)], **fresh_turn_fields(...)}`; config `{"configurable": {"thread_id": ...}, "callbacks": ..., "metadata": get_trace_metadata(user_id=user_id, conversation_id=user_id)}`; sau stream lấy `snap = await graph.aget_state(config)`, `answer = snap.values.get("answer", "")`; ghi `conversations` bằng `created_at=asked_at` và `stamped_at(<AIMessage cuối>)`

- [ ] **Step 1: Viết test (đỏ)**. Graph thật, model giả (monkeypatch `build_chat_model` ở `agents.py` và `supervisor.py` thành `scripted`), `set_checkpointer(InMemorySaver())`:
  - `test_second_turn_sees_the_first_turn_from_the_thread`: lượt 2 không đọc `conversations` cho lịch sử. Khẳng định lô gửi model booking lượt 2 có câu khách và câu đáp lượt 1.
  - `test_thread_holds_only_question_and_answer_not_tool_messages`: `snap.values["messages"]` toàn Human/AI không tool_calls.
  - `test_answer_of_previous_turn_never_leaks_into_this_turn`: lượt 2 lỗi model (ném) thì phát `error`, và `conversations` không ghi câu đáp cũ lần hai.
  - `test_conversations_and_thread_share_timestamps`.
  - `test_langfuse_session_is_the_user_not_the_thread`: bắt config truyền vào `astream_events`; `metadata["langfuse_session_id"] == str(user.id)`.
  - `test_thread_id_changes_with_the_vn_day` và `test_pick_thread_continues_yesterday_within_30_minutes_after_midnight` (freezegun 00:10 giờ VN; thread hôm qua có checkpoint lúc 23:55 thì chọn hôm qua; checkpoint lúc 23:00 thì chọn hôm nay).
  - Sửa ba test FakeGraph trong `test_graph_events.py`: thêm `async def aget_state(self, config)` trả `SimpleNamespace(values={"answer": "Dạ.", "messages": []}, interrupts=(), created_at=None)`.
- [ ] **Step 2:** Run. Expected: FAIL.
- [ ] **Step 3: Implement** theo Interfaces. `build_graph(db, user, checkpointer=get_checkpointer())` compile graph ngoài với checkpointer; agent bên trong vẫn `checkpointer=False`.
- [ ] **Step 4:** Run `PYTHONPATH=. .venv/bin/python -m pytest -q`. Expected: PASS.
- [ ] **Step 5: Commit** `feat: read model history from the checkpointer thread`

### Task 2.4: Một thread một lượt chạy

Bẫy liên quan: đúng 1 worker (khoá trong tiến trình là đủ); Review Focus #4; fault `azure-cham`.

**Files:**
- Modify: `app/agents/booking_graph/events.py`
- Test: `tests/test_run_turn_thread.py`

**Interfaces:**
- Produces: `_ACTIVE_TURNS: dict[str, asyncio.Task]`. Đầu `run_turn`: nếu user đã có task khác chưa xong thì `task.cancel()` và chờ nó kết thúc (nuốt `CancelledError`, timeout 2s), rồi đăng ký `asyncio.current_task()`. Cuối (finally) gỡ nếu vẫn là mình. Lượt bị huỷ không phát `error`, không ghi `conversations`, log `turn_superseded`.

- [ ] **Step 1: Viết test (đỏ)**: `test_a_new_turn_supersedes_a_hung_turn`. Model giả lượt 1 `await asyncio.sleep(3600)` (tạo hai task chạy `run_turn` cho cùng user). Lượt 2 phát `complete` trong <5s; task 1 kết thúc bởi huỷ; `conversations` chỉ có câu hỏi/đáp của lượt 2. Thêm `test_turns_of_two_users_do_not_cancel_each_other`.
- [ ] **Step 2:** Run. Expected: FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4:** Run. Expected: PASS.
- [ ] **Step 5: Commit** `fix: newer chat turn supersedes a hung one on the same user`

### Task 2.5: Mốc M2 — docs, tag harness-b/m2, full pytest green

- [ ] **Step 1:** `CONTEXT.md`: bảng "Chốt cứng" dòng "Phiên hội thoại" ghi thêm "lịch sử model = thread checkpointer `<user>:<ngày VN>`, luật 30 phút qua nửa đêm giữ bằng `pick_thread` + `model_history`; `conversations` vẫn là nhật ký hiển thị". Sơ đồ "Một lượt chat" sửa `load_context`. `RUNBOOK.md`: hai collection `checkpoints`/`checkpoint_writes`, TTL `CHECKPOINT_TTL_DAYS`, lệnh dọn. Commit `docs: describe checkpointer threads`.
- [ ] **Step 2:** `PYTHONPATH=. .venv/bin/python -m pytest -q`. Expected: 0 failed.
- [ ] **Step 3:** `git tag harness-b/m2`
- [ ] **Step 4: Lệnh đo** (mọi tag trừ `slow`, k=2):

```bash
SLOW='llm/llm-32.yaml edge/pending-het-han.yaml edge/compaction-long.yaml'
FILES=$(find bench/scenarios -name '*.yaml' | \
  grep -vFf <(for s in $SLOW; do echo "bench/scenarios/$s"; done))
bench/.venv/bin/python -m bench run --ref docs/arch-security-review:a --ref harness-b/m2:a --k 2 --scenarios $FILES
```
Sau lượt đo: controller kiểm `checkpoints` nằm trong DB `bench_<sha7>_<runid>` (không có DB `checkpointing_db` mới).

---

# M3 — chốt lịch bằng `interrupt()`

### Task 3.1: Lưới phủ quyết bằng code

Bẫy liên quan: **#19** ("hủy" là đồng ý khi đang chốt hủy; bản không dấu đi song song), **#11** (không sửa/nới regex parser, chỉ dùng lại), mục "VIỆC TIẾP THEO — bug `is_affirmative`".

**Files:**
- Create: `app/agents/booking_graph/reply.py`
- Test: `tests/test_reply_veto.py`

**Interfaces:**
- Produces:
  - `def strip_diacritics(text: str) -> str` (chuyển nguyên từ `confirm.py`)
  - `def veto_reason(text: str, proposal: dict, now: datetime) -> str | None`. Trả `"negation"`, `"deferral"`, `"question"` hoặc `"other_time"`; `None` = không phủ quyết. So trên cả bản có dấu lẫn bản đã bỏ dấu, theo từ (`\b`):
    - phủ định: không/ko/k/hông/hổng/hong/chưa/thôi/khỏi/khoan/đổi/khác/hủy (riêng "hủy"/"huy" KHÔNG tính khi `proposal["kind"] == "cancel"`)
    - hoãn: `để\s+\w+(\s+\w+)?\s+(hỏi|xem|gọi|tính|nghĩ|bàn)`, "xem lại", "hỏi lại", "suy nghĩ", "tính sau"
    - câu hỏi: có `?`, hoặc kết thúc bằng "không", "hả", "chưa", "sao", "nào", "vậy"
    - giờ khác: `mentions_other_time(text, start, now)`
  - `def mentions_other_time(text: str, start: datetime, now: datetime) -> bool`. Dùng `timeparse._match_regex`, `_TIME_PATTERN`, `_to_24h`, `_DAY_OFFSETS` (import, không sửa). Giờ nhắc tới khác giờ đề xuất (so `hour % 12`), ngày tương đối hoặc thứ nhắc tới khác ngày đề xuất, hay "tuần sau/tuần tới" khi đề xuất trong 7 ngày → `True`.

- [ ] **Step 1: Viết test (đỏ)**: tham số hoá; `P_BOOK = {"kind": "book", "start_at": "<mai 15:00 +07:00>"}`, `P_CANCEL = {"kind": "cancel", ...}`.
  - Phải phủ quyết (book): `"dạ ko em"`, `"hổng em"`, `"khoan đã"`, `"để chị hỏi thằng Huy đã"`, `"dạ vâng để chị gọi"`, `"Mà làm móng bột mất bao lâu hả chị, việc gì cũng phải tính trước cho chắc"`, `"giờ đó còn chỗ không em"`, `"ừ mà 4 giờ được không"`, `"ok nhưng thứ năm nha"`, `"thôi hủy đi"`, `"chua em"`, `"k em"`.
  - Không phủ quyết (book): `"ừ"`, `"dạ đúng rồi"`, `"ok em"`, `"vâng"`, `"dung roi"`, `"ừ 3 giờ chiều mai đúng rồi"`, `"được em"`.
  - Khi `P_CANCEL`, không phủ quyết: `"ừ hủy đi"`, `"hủy đi em"`, `"u huy di"`, `"dạ hủy"`; phủ quyết: `"không hủy nữa"`, `"thôi khỏi hủy"`, `"khong huy"`.
  - `"ok"` không bị bắt vì chữ `k`; `"cho cô dùng thử"` vẫn ra `None` (lưới không quyết approve, chỉ không phủ quyết).
- [ ] **Step 2:** Run. Expected: FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4:** Run. Expected: PASS.
- [ ] **Step 5: Commit** `feat: add code veto for confirmation replies`

### Task 3.2: Bộ phân loại câu trả lời + `decide`

Bẫy liên quan: **#8** (tag `classify`, `streaming=False`), **#10** (timeout 8s), **#15/#16** (prompt tiếng Anh, không ví dụ tiếng Việt).

**Files:**
- Modify: `app/agents/booking_graph/reply.py`, `app/agents/booking_graph/prompts.py` (`CLASSIFY_PROMPT`)
- Test: `tests/test_reply_classify.py`; `tests/test_prompts.py` (fence)

**Interfaces:**
- Produces:
  - `class ReplyDecision(BaseModel)`: `decision: Literal["approve", "reject", "change", "unclear"]`
  - `CLASSIFY_TIMEOUT_SECONDS = 8`
  - `async def classify_reply(proposal: dict, bot_question: str, customer_text: str) -> str`: `build_chat_model(tags=["classify"], temperature=0.0, streaming=False).with_structured_output(ReplyDecision)` trong `asyncio.wait_for`; mọi exception/timeout → `"unclear"` + log `classify_failed`
  - `async def decide(text: str, proposal: dict, bot_question: str, now: datetime) -> str`: chạy `veto_reason` trước; có lý do phủ quyết thì trả `"reject"` (log `confirm_vetoed` kèm lý do) và **không gọi model**; ngược lại trả kết quả `classify_reply`
  - `CLASSIFY_PROMPT`: tiếng Anh; nêu proposal (kind, giờ đọc được), câu bot vừa hỏi, câu khách; định nghĩa 4 nhãn bằng mô tả; "approve only when the customer clearly agrees to exactly this proposal; anything else is not approve". Không câu ví dụ tiếng Việt.

- [ ] **Step 1: Viết test (đỏ)** (monkeypatch `reply.build_chat_model`):
  - `test_veto_short_circuits_the_model`: model giả ném nếu bị gọi; `decide("dạ ko em", ...) == "reject"`.
  - `test_model_approve_passes_through_when_no_veto`: model giả `with_structured_output` trả `ReplyDecision(decision="approve")`; `decide("ừ", ...) == "approve"`.
  - `test_timeout_is_unclear`: model `ainvoke` ngủ 9s, `CLASSIFY_TIMEOUT_SECONDS` patch còn 0.05; kết quả `"unclear"`.
  - `test_error_is_unclear`.
  - `test_classifier_model_is_tagged_classify_and_not_streaming`: bắt kwargs của `build_chat_model`.
  - Trong `tests/test_prompts.py`: `test_classify_prompt_has_no_vietnamese_examples` (không chứa "ừ", "dạ", "không", "đồng ý", "được").
- [ ] **Step 2:** Run. Expected: FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4:** Run. Expected: PASS.
- [ ] **Step 5: Commit** `feat: classify confirmation replies with a code veto first`

### Task 3.3: `ConfirmMiddleware` bắt đề xuất; tool thôi ghi pending

Bẫy liên quan: **#7** (vẫn 0 tool ghi; middleware không đăng ký tool), **#19** (`replaces_appointment_id` → `move`), **#23** (tool vẫn trả JSON dữ kiện), Lệch #10.

**Files:**
- Create: `app/agents/booking_graph/middleware/confirm.py`
- Modify: `app/agents/booking_graph/tools.py` (bỏ `set_pending` ở `propose_appointment` và `cancel_appointment`; JSON trả về không đổi)
- Modify: `app/agents/booking_graph/agents.py` (`make_agent_node` trả thêm `proposal`, bỏ đề xuất nếu `tool_loop_exhausted`), `graph.py` (booking có `ConfirmMiddleware()` trong `extra_middleware`), `state.py` (`proposal`)
- Test: `tests/test_mw_confirm.py`; sửa `tests/test_tools.py` (các test đọc `get_pending` chuyển sang khẳng định JSON trả về)

**Interfaces:**
- Produces:
  - `class ProposalState(AgentState)`: `proposal: NotRequired[dict | None]`
  - `def proposal_from_tool(name: str, content: str, now: datetime) -> dict | None`. Shape: `{"id": uuid4().hex, "kind": "book"|"move"|"cancel", "start_at": iso, "note": str|None, "appointment_id": str|None, "created_at": now.isoformat()}`.
    - `propose_appointment` có `held`: kind `book`, hoặc `move` khi JSON có `replaces`
    - `cancel_appointment` có `will_cancel`: kind `cancel`, `start_at = will_cancel.at.iso`
    - JSON có `error` → `None`
  - `class ConfirmMiddleware(AgentMiddleware)`: `state_schema = ProposalState`; `tools = []`; `awrap_tool_call` gọi handler; nếu `proposal_from_tool(...)` có giá trị thì trả `Command(update={"messages": [tool_message], "proposal": p})`, không thì trả tool_message
  - `make_agent_node` → `{"draft": ..., "proposal": None if out.get("tool_loop_exhausted") else out.get("proposal")}`

- [ ] **Step 1: Viết test (đỏ)**:
  - `test_propose_result_becomes_a_book_proposal`, `test_replaces_makes_a_move_proposal`, `test_cancel_result_becomes_a_cancel_proposal`, `test_error_result_makes_no_proposal`: unit trên `proposal_from_tool` với JSON thật sinh từ tool (gọi tool thật trên `test_db`).
  - `test_agent_returns_the_proposal_from_state`: agent + `tool_call("propose_appointment", {...})` trên `test_db`, `out["proposal"]["start_at"]` đúng ISO giờ đã giữ.
  - `test_proposal_values_come_from_the_tool_result_not_model_text`: model gõ `start_at` thiếu offset (`"2026-..T15:00:00"`); proposal mang `+07:00` do tool chuẩn hoá.
  - `test_exhausted_tool_loop_drops_the_proposal` (G-07).
  - `test_confirm_middleware_registers_no_tools` và `test_no_hitl_middleware_anywhere` (grep source `app/agents` không có `HumanInTheLoopMiddleware`).
  - `test_tools_no_longer_write_pending`: sau `propose`, document `conversations` không có `pending_confirmation`.
- [ ] **Step 2:** Run. Expected: FAIL.
- [ ] **Step 3: Implement.** Comment trong `tools.py` chỗ "Lưu vào Mongo để lượt sau đọc lại" sửa thành: đề xuất đi qua `ConfirmMiddleware` vào state.
- [ ] **Step 4:** Run `pytest tests/test_mw_confirm.py tests/test_tools.py -v`. Expected: PASS.
- [ ] **Step 5: Commit** `feat: capture booking proposals in agent state via middleware`

### Task 3.4: Node `confirm` với `interrupt`, bỏ regex và `pending_confirmation`

Bẫy liên quan: **#19** (dời = `reschedule`, đặt mới trước hủy cũ; hủy qua bước xác nhận), **#21** (câu chốt qua phrase + guard), **#22** (`test_no_code_path_books_from_slots` vẫn quét `confirm.py`), chốt cứng "Ghi lịch".

**Files:**
- Modify (viết lại): `app/agents/booking_graph/confirm.py`
- Modify: `graph.py`, `supervisor.py` (bỏ `route_from_state`), `context.py` (`load_context` bỏ `get_pending`), `app/services/conversation.py` (bỏ `set_pending`/`get_pending`, bỏ `pending_confirmation` trong `get_or_create`), `app/models/conversation.py` (giữ field `Optional` để đọc document cũ, comment "không còn ghi"), `state.py` (bỏ `pending_confirmation`; thêm `gate_next`)
- Modify tests: `tests/test_confirm.py` (viết lại), `tests/test_supervisor.py` (bỏ hai test short-circuit), `tests/test_conversation.py` (bỏ ba test pending), `tests/test_graph_wiring.py`

**Interfaces:**
- Consumes: `decide`, `proposal` trong state, `AppointmentService`, `address_phrase`, `format_vi_datetime`
- Produces:
  - `PROPOSAL_MAX_AGE_MINUTES = 10`
  - `async def execute_proposal(db, user, proposal: dict) -> dict`: trả `{"confirm_fact": {...}, "fallback": str}`, đúng các nhánh và chuỗi của `make_confirm_node` cũ (booked / moved / cancelled / failed, `_sentence_start`, `bad_pending_payload`). `kind == "move"` → `reschedule(user, appointment_id, start, note)`.
  - `def make_confirm_node(db, user)`. Node async:
    - không có `proposal` → `{"gate_next": "end"}`
    - có `proposal` → `text = interrupt(proposal)`; quá 10 phút → `{"proposal": None, "gate_next": "supervisor"}` + log `proposal_expired`
    - còn hạn → `decide(text, proposal, bot_question=<AIMessage cuối trước câu khách>, now)`; `approve` → `{**await execute_proposal(...), "proposal": None, "gate_next": "phrase"}`; khác → `{"proposal": None, "gate_next": "booking"}` + log `confirm_declined`
  - Graph: `START → supervisor`; `guard --end--> record → confirm`; `confirm --gate_next--> {end: END, phrase, booking, supervisor}`; `phrase → guard`

- [ ] **Step 1: Viết lại `tests/test_confirm.py` (đỏ)**. Giữ mọi khẳng định nghiệp vụ của bản cũ, chuyển từ "pending + câu khách" sang `execute_proposal(proposal)`: đặt lịch theo giờ trong proposal; xưng hô suy từ tên; slot đã có người → `failed` + "có người"; dời không thêm lịch thứ hai; giờ mới hụt thì giữ lịch cũ; hủy; lịch đã mất → câu thân thiện; payload hỏng → "em nhầm mất rồi". Thêm test node, chạy trong graph một node có `InMemorySaver`:
  - `test_confirm_interrupts_with_the_proposal`
  - `test_resume_with_approve_writes_once`
  - `test_resume_with_veto_writes_nothing_and_routes_booking`
  - `test_expired_proposal_routes_to_supervisor_without_writing` (freezegun +11 phút)
  - `test_no_proposal_ends`
  Sửa `test_graph_wiring.py`:
  - `test_confirm_goes_to_phrase_booking_supervisor_or_end`
  - `test_record_then_confirm_after_guard`
  - `test_there_is_no_confirm_shortcut_from_start` (không có cạnh `__start__ → confirm`)
  - `test_every_llm_node_feeds_the_guard`: giữ
- [ ] **Step 2:** Run. Expected: FAIL.
- [ ] **Step 3: Implement.** Xoá `is_affirmative` và các regex `_YES*`/`_NO*`.
- [ ] **Step 4:** `grep -rn "pending_confirmation\|set_pending\|get_pending\|is_affirmative" app tests` chỉ còn field model (đọc ngược) và comment lịch sử. Run `PYTHONPATH=. .venv/bin/python -m pytest -q`. Expected: PASS.
- [ ] **Step 5: Commit** `feat: confirm bookings via interrupt instead of regex pending flag`

### Task 3.5: `run_turn` resume + kịch bản đầu-cuối

Bẫy liên quan: **#8** (token câu hỏi xác nhận và câu chốt vẫn stream tag `respond`; classify không), Review Focus #1, #2.

**Files:**
- Modify: `app/agents/booking_graph/events.py`
- Test: `tests/test_confirm_flow.py`

**Interfaces:**
- Produces: trong `run_turn`, `snap = await graph.aget_state(config)` trước khi chạy:
  - `snap.interrupts` khác rỗng → input `Command(resume=question, update={"messages": [stamped Human], **fresh_turn_fields(...)})`. **Không** đặt lại `proposal`.
  - ngược lại → input thường, với `fresh_turn_fields(...)` + `"proposal": None`.

- [ ] **Step 1: Viết test (đỏ)**. Graph thật, `InMemorySaver`, model giả cho supervisor/booking/phrase, `decide` dùng bộ phân loại giả; DB `test_db`:
  - `test_g01_ko_em_never_books`: lượt 1 booking gọi `propose_appointment` rồi hỏi; lượt 2 `"dạ ko em"` với bộ phân loại giả LUÔN trả `approve`. Không có lịch; lượt 2 đi vào booking (model booking được gọi).
  - `test_tan_gau_with_phai_never_books`: câu dài có "phải"; bộ phân loại giả trả `approve`; không ghi; answer không chứa "Xong rồi".
  - `test_approve_books_and_phrase_streams`: `"ừ em"` + `approve`. Đúng 1 lịch, giờ = proposal; có token tag `respond`; complete ≠ rỗng.
  - `test_g07_after_tool_loop_da_vang_de_chi_goi_never_books`
  - `test_failed_turn_leaves_no_pending_proposal`: lượt 1 propose rồi model ném lỗi (phát `error`); lượt 2 `"ok đặt giúp chị"` chạy từ START (supervisor được gọi), không ghi.
  - `test_resume_turn_is_logged_in_conversations`
- [ ] **Step 2:** Run. Expected: FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4:** Run `PYTHONPATH=. .venv/bin/python -m pytest -q`. Expected: PASS.
- [ ] **Step 5: Commit** `feat: resume pending confirmation on the next chat turn`

### Task 3.6: Mốc M3 — docs, tag harness-b/m3, full pytest green

- [ ] **Step 1: Docs.**
  - `AGENTS.md`: thay "a separate `confirm` graph node" bằng "`ConfirmMiddleware` captures the proposal; the `confirm` node interrupts and, on the next turn, code (veto + classifier) decides; still no write tool".
  - `CONTEXT.md`: bảng "Chốt cứng" dòng "Ghi lịch"; sơ đồ lượt chat; bẫy #7 và #19 (cơ chế mới, lý do giữ); mục "VIỆC TIẾP THEO — bug is_affirmative" ghi đã đóng ở M3 kèm lý do.
  - Commit `docs: describe interrupt-based confirmation`.
- [ ] **Step 2:** `PYTHONPATH=. .venv/bin/python -m pytest -q`. Expected: 0 failed.
- [ ] **Step 3:** `git tag harness-b/m3`
- [ ] **Step 4: Lệnh đo** (mọi tag trừ `slow`, k=4):

```bash
SLOW='llm/llm-32.yaml edge/pending-het-han.yaml edge/compaction-long.yaml'
FILES=$(find bench/scenarios -name '*.yaml' | \
  grep -vFf <(for s in $SLOW; do echo "bench/scenarios/$s"; done))
bench/.venv/bin/python -m bench run --ref docs/arch-security-review:a --ref harness-b/m3:a --k 4 --scenarios $FILES
```
Cổng: ghi sai = 0 ở G-01/02/03 và `is-affirmative-tan-gau`; edge tăng; llm không giảm quá 5 điểm.

---

# M4 — guard và giới hạn thành middleware

### Task 4.1: `GuardMiddleware` (guard + rewrite 1 lần + luật tên tool)

Bẫy liên quan: **#21** (3 phép kiểm + content; rewrite đúng một lần; hỏng lần hai thì trả draft GỐC, trừ ca chỉ lỗi `repeat`), **#8** (rewrite tag `rewrite`, không stream), UI "Không hiện tên tool".

**Files:**
- Create: `app/agents/booking_graph/middleware/guard.py`
- Modify: `app/agents/booking_graph/guard.py` (thêm `TOOL_NAMES`, `strip_tool_names`), `rewrite.py` (tách `async def rewrite_once(draft, codes, previous_replies, context_block) -> str | None`)
- Test: `tests/test_mw_guard.py`; `tests/test_guard.py`, `tests/test_rewrite.py` (giữ test hàm thuần; test node chuyển sang middleware)

**Interfaces:**
- Produces:
  - `TOOL_NAMES = ("parse_time", "find_free_slots", "propose_appointment", "list_my_appointments", "cancel_appointment", "get_shop_hours", "get_shop_status")`
  - `def strip_tool_names(text: str) -> str`: bỏ nhóm ngoặc chứa tên tool hoặc định danh snake_case, bỏ tên trần, gộp khoảng trắng, bỏ khoảng trắng trước dấu câu
  - `class GuardState(AgentState)`: `answer`, `answer_source`, `violations`, `previous_replies`, `customer_text`, `confirm_fact`, `fallback`, `context_block` (NotRequired)
  - `class GuardMiddleware(AgentMiddleware)`: `__init__(self, user: User)`; `async def aafter_agent(state, runtime) -> dict`. `draft = strip_tool_names(<AIMessage cuối>)` (log `guard_tool_name_stripped` nếu có đổi), rồi đúng luật của `make_guard_node`/`_guard_phrase`/`make_rewrite_node`. Trả `{"answer", "answer_source", "violations"}`; mọi câu đi ra cũng qua `strip_tool_names`.

- [ ] **Step 1: Viết test (đỏ)**: chuyển nguyên 10 test node của `tests/test_rewrite.py` (lớp guard/rewrite) sang gọi `GuardMiddleware(user).aafter_agent(state, None)` với `rewrite_once` monkeypatch. Thêm:
  - `test_tool_name_in_parentheses_is_removed`: `"Dạ em xem rồi (parse_time, find_free_slots) ạ."` → `"Dạ em xem rồi ạ."`
  - `test_bare_tool_name_never_ships`: kể cả nhánh `guard_gave_up`
  - `test_phrase_fact_path_uses_fallback_on_wrong_numbers`
- [ ] **Step 2:** Run. Expected: FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4:** Run. Expected: PASS.
- [ ] **Step 5: Commit** `feat: move guard and rewrite into an after_agent middleware`

### Task 4.2: `PhraseMiddleware` + gỡ node guard/rewrite/phrase khỏi graph ngoài

Bẫy liên quan: **#21** (câu chốt LLM viết, số liệu code cấp, fallback khi sai), **#9** (phrase giữ thứ tự system → summary → lịch sử → bối cảnh → câu khách), **#8** (phrase stream tag `respond`).

**Files:**
- Create: `app/agents/booking_graph/middleware/phrase.py`
- Modify: `phrase.py` (tách `async def write_phrase(state) -> str`, dùng chung), `graph.py`, `agents.py` (`make_agent_node` đọc `answer`/`answer_source`/`violations` từ agent; truyền `confirm_fact`, `fallback`, `previous_replies`, `customer_text` vào agent)
- Test: `tests/test_mw_phrase.py`, `tests/test_graph_wiring.py`

**Interfaces:**
- Produces:
  - `class PhraseMiddleware(AgentMiddleware)`: `@hook_config(can_jump_to=["end"]) async def abefore_agent(state, runtime)`. Có `confirm_fact` → `{"messages": [AIMessage(await write_phrase(state))], "phrase_fact": fact, "jump_to": "end"}`; không có → `None`.
  - `build_agent` thứ tự middleware: `[PhraseMiddleware(), GuardMiddleware(user), *extra, MessageLayout, NoParallel, ToolRoundLimit, ToolError]` (after_agent chạy ngược danh sách nên Guard chạy cuối cùng)
  - Graph ngoài: `START → supervisor → booking|shop|social → record → confirm`; `confirm --gate_next--> {end, booking (approve: mang confirm_fact), booking, supervisor}`. Node `guard`/`rewrite`/`phrase` không còn.

- [ ] **Step 1: Viết test (đỏ)**:
  - `test_phrase_runs_instead_of_the_model_and_guard_checks_it`: model booking giả ném nếu bị gọi; câu chốt có `when` → answer = câu LLM
  - `test_phrase_missing_when_falls_back_to_code_sentence`
  - `test_phrase_timeout_ships_fallback`
  - `test_no_confirm_fact_runs_the_agent_normally`
  - wiring: `test_every_agent_feeds_record_then_confirm`, `test_no_guard_node_left_in_outer_graph`
  - chạy lại `tests/test_confirm_flow.py` không sửa khẳng định
- [ ] **Step 2:** Run. Expected: FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4:** Run `PYTHONPATH=. .venv/bin/python -m pytest -q`. Expected: PASS.
- [ ] **Step 5: Commit** `refactor: phrase as before_agent middleware, drop guard nodes`

### Task 4.3: Retry có giới hạn + timeout LLM và trần thời gian lượt (A-04)

Bẫy liên quan: **#10** (các timeout 8s riêng giữ nguyên), fault `azure-500`/`azure-cham` (câu lỗi tiếng Việt như A, không lộ lỗi kỹ thuật).

**Files:**
- Modify: `app/agents/llm.py` (`timeout`, `max_retries`), `agents.py` (thêm `ModelRetryMiddleware`), `events.py` (`asyncio.timeout`)
- Test: `tests/test_llm_limits.py`

**Interfaces:**
- Produces:
  - `LLM_TIMEOUT_SECONDS = 20`; `build_chat_model(..., timeout: float = LLM_TIMEOUT_SECONDS, max_retries: int = 0)`, truyền xuống `AzureChatOpenAI`/`ChatOpenAI` (`timeout=`, `max_retries=`)
  - `build_agent` thêm `ModelRetryMiddleware(max_retries=1, on_failure="error", initial_delay=0.5)` (`langchain.agents.middleware`)
  - `TURN_TIMEOUT_SECONDS = 60`; `run_turn` bọc phần chạy graph trong `asyncio.timeout(TURN_TIMEOUT_SECONDS)`; hết giờ → phát `error` với câu A, log `agent_turn_timeout`

- [ ] **Step 1: Viết test (đỏ)**:
  - `test_chat_model_has_a_timeout_and_no_sdk_retries` (provider azure với key giả; đọc thuộc tính `request_timeout`/`max_retries`)
  - `test_one_retry_then_vietnamese_error`: model giả ném hai lần → `error` với "Máy đang bận chút xíu, anh chị nhắn lại giúp em nhé."
  - `test_one_failure_is_retried_transparently`
  - `test_turn_timeout_emits_the_same_error` (patch `TURN_TIMEOUT_SECONDS=0.1`, model ngủ)
- [ ] **Step 2:** Run. Expected: FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4:** Run. Expected: PASS.
- [ ] **Step 5: Commit** `fix: bound llm retries and time per chat turn`

### Task 4.4: Mốc M4 — docs, tag harness-b/m4, full pytest green

- [ ] **Step 1:** `CONTEXT.md`: chốt cứng "Câu trả lời" (guard nay là `GuardMiddleware`, luật không đổi), bẫy #21 ghi nơi ở mới, thêm luật xoá tên tool. Commit `docs: guard lives in middleware`.
- [ ] **Step 2:** `PYTHONPATH=. .venv/bin/python -m pytest -q`. Expected: 0 failed.
- [ ] **Step 3:** `git tag harness-b/m4`
- [ ] **Step 4: Lệnh đo** (mọi tag trừ `slow`, k=4):

```bash
SLOW='llm/llm-32.yaml edge/pending-het-han.yaml edge/compaction-long.yaml'
FILES=$(find bench/scenarios -name '*.yaml' | \
  grep -vFf <(for s in $SLOW; do echo "bench/scenarios/$s"; done))
bench/.venv/bin/python -m bench run --ref docs/arch-security-review:a --ref harness-b/m4:a --k 4 --scenarios $FILES
```
Cổng: như M1, cộng `tool_name_leak` = 0 (A cũng 0).

---

# M5 — định tuyến: router hay một agent

### Task 5.1: Biến thể một agent sau setting `AGENT_ROUTING`

Bẫy liên quan: **#7** (agent duy nhất vẫn 7 tool, 0 tool ghi; shop/social không thấy tool lịch), **#8** (supervisor trong middleware vẫn tag `supervisor`), **#9**.

**Files:**
- Modify: `app/core/config.py`, `.env.example`, `.env` (`AGENT_ROUTING=router`)
- Create: `app/agents/booking_graph/middleware/intent.py`
- Modify: `graph.py`
- Test: `tests/test_single_agent.py`

**Interfaces:**
- Produces:
  - `Settings.agent_routing: Literal["router", "single"] = "router"`
  - `class IntentPromptMiddleware(AgentMiddleware)`:
    - `abefore_agent`: gọi `supervise(state)` một lần mỗi lượt, đặt `intent`
    - `awrap_model_call`: chọn `system_message` theo `intent` (`BOOKING_PROMPT`/`SHOP_PROMPT`/`SOCIAL_PROMPT`) và lọc `tools` (booking: 7; shop: 2 tool shop; social: không tool)
  - `build_graph` khi `single`: `START → agent → record → confirm`; một `create_agent` với `booking_tools(db, user)` và `[IntentPromptMiddleware(), Phrase, Guard, History, Confirm, ...]`

- [ ] **Step 1: Viết test (đỏ)**:
  - `test_single_mode_has_one_agent_node`
  - `test_social_intent_sends_no_tools_and_social_prompt`
  - `test_shop_intent_sees_only_shop_tools`
  - `test_single_agent_still_has_no_write_tool`
  - `test_router_is_the_default`
  - chạy `tests/test_confirm_flow.py` với `agent_routing="single"` (parametrize)
- [ ] **Step 2:** Run. Expected: FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4:** Run `PYTHONPATH=. .venv/bin/python -m pytest -q`. Expected: PASS.
- [ ] **Step 5: Commit** `feat: add single-agent routing variant behind a setting`

### Task 5.2: Tag hai biến thể, full pytest green

Bench đọc `.env` gốc, không đổi setting theo ref được, nên mỗi biến thể là một commit có default khác.

- [ ] **Step 1:** HEAD (default `router`) → `git tag harness-b/m5-router`.
- [ ] **Step 2:** Đổi default thành `"single"` trong `config.py` + `.env.example`; `pytest -q` xanh; commit `chore: default to single-agent routing for measurement`; `git tag harness-b/m5-single`.
- [ ] **Step 3: Lệnh đo** (mọi tag kể cả `slow`, k=4; chạy cả hai biến thể cùng A):

```bash
bench/.venv/bin/python -m bench run --ref docs/arch-security-review:a \
  --ref harness-b/m5-router:a --ref harness-b/m5-single:a --k 4
```

### Task 5.3: Giữ biến thể thắng — tag harness-b/m5, full pytest green

Controller quyết theo spec: ghi sai → tỉ lệ đạt edge → llm → độ trễ; hoà thì giữ router.

- [ ] **Step 1:** Router thắng → revert commit Task 5.2 Step 2. Single thắng → giữ nguyên.
- [ ] **Step 2:** `CONTEXT.md` ghi biến thể được giữ và số đo (dòng mới trong bảng nhật ký).
- [ ] **Step 3:** `PYTHONPATH=. .venv/bin/python -m pytest -q`. Expected: 0 failed. Commit `docs: record routing decision`.
- [ ] **Step 4:** `git tag harness-b/m5`. Không cần đo thêm: số liệu Task 5.2 là của commit này.
