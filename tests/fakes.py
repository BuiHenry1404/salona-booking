"""Model chat giả dùng chung cho test middleware/agent — không gọi Azure.

`ScriptedChatModel` trả lần lượt các phản hồi dựng sẵn và ghi lại mọi lô
message nó nhận (`calls`) cùng kwargs lúc bind (`bind_kwargs`). Phần tử của
`replies` có thể là:
- `AIMessage`: trả thẳng;
- `Exception`: ném ra (giả Azure lỗi);
- `Hang(seconds, then)`: ngủ `seconds` giây rồi trả `then` (giả Azure treo —
  Task 2.4 cần một lượt chạy chồng lên lượt đang treo).
"""
import asyncio
import json
import time
from dataclasses import dataclass
from typing import Any, Callable, List, Optional
from uuid import uuid4

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, AIMessageChunk, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatGenerationChunk, ChatResult


@dataclass
class Hang:
    """Model treo `seconds` giây rồi mới trả `then` (mặc định `AIMessage("")`)."""
    seconds: float = 3600.0
    then: Optional[Any] = None


class ScriptedStructured:
    def __init__(self, parent: "ScriptedChatModel", schema: Any):
        self.parent = parent
        self.schema = schema

    def invoke(self, messages, *args, **kwargs):
        return self._parse(self.parent._next(messages))

    async def ainvoke(self, messages, *args, **kwargs):
        return self._parse(await self.parent._anext(messages))

    def _parse(self, reply: Any):
        if isinstance(reply, self.schema):
            return reply
        if isinstance(reply, dict):
            return self.schema(**reply)
        if isinstance(reply, AIMessage):
            content = reply.content
            if hasattr(self.schema, "model_fields"):
                if "route" in self.schema.model_fields and content in ("booking", "shop", "social"):
                    return self.schema(route=content)
                if "decision" in self.schema.model_fields and content in ("approve", "reject", "change", "unclear"):
                    return self.schema(decision=content)
            try:
                return self.schema.model_validate_json(content)
            except Exception:
                pass
        return reply


class ScriptedChatModel(BaseChatModel):
    """Trả lần lượt các AIMessage dựng sẵn; ghi lại mọi lô message và kwargs bind."""
    replies: List[Any]
    calls: List[Any] = []
    bind_kwargs: List[dict] = []
    bound_tools: Any = None

    @property
    def _llm_type(self) -> str:
        return "scripted"

    def with_structured_output(self, schema, **kwargs):
        return ScriptedStructured(self, schema)

    def bind_tools(self, tools, **kwargs):
        self.bound_tools = tools
        self.bind_kwargs.append(kwargs)
        return self

    def bind(self, **kwargs):
        self.bind_kwargs.append(kwargs)
        return self

    def _pop(self, messages):
        self.calls.append(list(messages))
        return self.replies.pop(0)

    @staticmethod
    def _settle(reply):
        if isinstance(reply, Exception):
            raise reply
        return reply

    def _next(self, messages):
        reply = self._pop(messages)
        if isinstance(reply, Hang):
            time.sleep(reply.seconds)
            reply = reply.then if reply.then is not None else AIMessage(content="")
        return self._settle(reply)

    async def _anext(self, messages):
        reply = self._pop(messages)
        if isinstance(reply, Hang):
            await asyncio.sleep(reply.seconds)
            reply = reply.then if reply.then is not None else AIMessage(content="")
        return self._settle(reply)

    def _generate(self, messages, stop=None, run_manager=None, **kw):
        return ChatResult(generations=[ChatGeneration(message=self._next(messages))])

    async def _agenerate(self, messages, stop=None, run_manager=None, **kw):
        return ChatResult(generations=[ChatGeneration(message=await self._anext(messages))])

    async def _astream(self, messages, stop=None, run_manager=None, **kw):
        m = await self._anext(messages)
        chunk = ChatGenerationChunk(message=AIMessageChunk(
            content=m.content, tool_call_chunks=[
                {"name": t["name"], "args": json.dumps(t["args"]), "id": t["id"], "index": i}
                for i, t in enumerate(m.tool_calls)]))
        if run_manager:
            await run_manager.on_llm_new_token(m.content or "", chunk=chunk)
        yield chunk


def scripted(replies, tags=("respond",)):
    return ScriptedChatModel(replies=list(replies), calls=[], bind_kwargs=[], tags=list(tags))


def tool_call(name: str, args: dict, id: Optional[str] = None) -> AIMessage:
    """Một AIMessage với đúng một tool call. `id` mặc định duy nhất mỗi lần gọi
    (không bao giờ lặp lại giữa các lần gọi) — khác bản cũ `id="c1"` cố định."""
    if id is None:
        id = uuid4().hex
    return AIMessage(content="", tool_calls=[{"name": name, "args": args, "id": id, "type": "tool_call"}])


def final_output(**fields) -> AIMessage:
    """AIMessage với một tool call tên `SpecialistOutput` và args là `fields`
    - thay cho câu JSON chữ specialist từng trả ở lượt cuối (Task 2.6:
    `response_format=ToolStrategy(SpecialistOutput, ...)` của `create_agent`
    bắt kết quả đi qua đúng tool call này, tên tool mặc định là tên class)."""
    return tool_call("SpecialistOutput", fields)


def tool_calls(*pairs: tuple) -> AIMessage:
    """Nhiều tool call trong MỘT AIMessage — dùng để test chuỗi parallel/serial."""
    calls = [
        {"name": name, "args": args, "id": uuid4().hex, "type": "tool_call"}
        for name, args in pairs
    ]
    return AIMessage(content="", tool_calls=calls)


class FakeSpecialists:
    """Hai specialist giả dùng làm `deps.run_appointment`/`deps.run_shop` của
    Orchestrator (Task 3.4) — không chạy `create_agent` thật, không gọi Azure.

    `plan` ánh xạ `(label, action)` → một `TaskResult`/`Exception` cố định,
    hoặc một `list` các giá trị lấy LẦN LƯỢT (mỗi lần gọi đúng key lại pop
    phần tử đầu) — cho test kịch bản "lần đầu needs_info, lần hai ok" không
    cần hai entry riêng trong `plan`. `label` là "appointment" hoặc "shop" —
    đúng tên hai phương thức dưới đây, không phải `TaskSpec.agent` (vốn mang
    giá trị "shop_info", không phải "shop" — hai tầng tên khác nhau cố ý,
    tránh gõ nhầm giữa test và code tool)."""

    def __init__(self, plan: dict) -> None:
        self.plan = {key: (list(value) if isinstance(value, list) else value) for key, value in plan.items()}
        self.received: list = []

    async def _run(self, label: str, spec, ctx, question: str):
        self.received.append(spec)
        key = (label, spec.action)
        if key not in self.plan:
            raise AssertionError(f"FakeSpecialists: khong co plan cho {key}")
        value = self.plan[key]
        if isinstance(value, list):
            if not value:
                raise AssertionError(f"FakeSpecialists: het plan cho {key}")
            value = value.pop(0)
        if isinstance(value, Exception):
            raise value
        return value

    async def appointment(self, spec, ctx, question: str):
        return await self._run("appointment", spec, ctx, question)

    async def shop(self, spec, ctx, question: str):
        return await self._run("shop", spec, ctx, question)


class EchoTool(BaseChatModel):
    """Model giả một bước cho SPECIALIST THẬT: gọi đúng MỘT tool với args cho
    trước, rồi CHÉP JSON ToolMessage vào `SpecialistOutput` cuối qua
    `build_output` — mô phỏng việc model "chỉ chép" mà không cần gọi Azure
    thật (dùng chung giữa `tests/test_specialist_appointment.py` và
    `tests/test_scenarios.py`)."""

    tool_name: str
    tool_args: dict
    build_output: Callable[[dict], dict]
    # Nội dung (chữ, không phải tool call) của bước gọi tool — mặc định rỗng
    # (hành vi cũ); một test cần chứng minh việc lọc token theo tag vẫn đúng
    # dù model THẬT có trả chữ kèm tool call (không chỉ tool call rỗng chữ)
    # truyền giá trị khác rỗng ở đây.
    content: str = ""

    @property
    def _llm_type(self) -> str:
        return "echo_tool"

    def bind_tools(self, tools, **kwargs):
        return self

    def bind(self, **kwargs):
        return self

    def _reply(self, messages) -> AIMessage:
        if messages and isinstance(messages[-1], ToolMessage):
            tool_json = json.loads(messages[-1].content)
            return final_output(**self.build_output(tool_json))
        return AIMessage(content=self.content, tool_calls=[{
            "name": self.tool_name, "args": self.tool_args, "id": uuid4().hex, "type": "tool_call",
        }])

    def _generate(self, messages, stop=None, run_manager=None, **kw):
        return ChatResult(generations=[ChatGeneration(message=self._reply(messages))])

    async def _agenerate(self, messages, stop=None, run_manager=None, **kw):
        return ChatResult(generations=[ChatGeneration(message=self._reply(messages))])

    async def _astream(self, messages, stop=None, run_manager=None, **kw):
        """Như `ScriptedChatModel._astream` — một model KHÔNG override hàm
        này vẫn stream được (LangChain tự chạy `_agenerate` rồi phát một
        chunk), nhưng khai báo tường minh ở đây để chắc chắn `content` (khác
        rỗng) của bước gọi tool thật sự đi qua `on_chat_model_stream` — nửa
        "specialist" của C-1 (`test_only_respond_tokens_reach_the_stream`)
        cần đúng điều đó, không chỉ suy luận từ hành vi mặc định."""
        m = self._reply(messages)
        chunk = ChatGenerationChunk(message=AIMessageChunk(
            content=m.content, tool_call_chunks=[
                {"name": t["name"], "args": json.dumps(t["args"]), "id": t["id"], "index": i}
                for i, t in enumerate(m.tool_calls)]))
        if run_manager:
            await run_manager.on_llm_new_token(m.content or "", chunk=chunk)
        yield chunk


class TwoProposeAttempts(BaseChatModel):
    """Model giả cho SPECIALIST THẬT: cố tạo đề xuất HAI lần trong cùng task
    (lần hai bị `OneProposalPerTaskMiddleware` chặn), rồi báo lại đề xuất ĐẦU
    TIÊN đã tạo thành công — mô phỏng model thử lại sau khi bị tool báo lỗi."""

    first_args: dict
    second_args: dict

    @property
    def _llm_type(self) -> str:
        return "two_propose_attempts"

    def bind_tools(self, tools, **kwargs):
        return self

    def bind(self, **kwargs):
        return self

    def _reply(self, messages) -> AIMessage:
        ai_rounds = sum(1 for m in messages if isinstance(m, AIMessage))
        if ai_rounds == 0:
            return AIMessage(content="", tool_calls=[{
                "name": "propose_appointment", "args": self.first_args,
                "id": uuid4().hex, "type": "tool_call",
            }])
        if ai_rounds == 1:
            return AIMessage(content="", tool_calls=[{
                "name": "propose_appointment", "args": self.second_args,
                "id": uuid4().hex, "type": "tool_call",
            }])
        created = None
        for m in messages:
            if isinstance(m, ToolMessage) and m.name == "propose_appointment":
                data = json.loads(m.content)
                if "proposal_id" in data:
                    created = data
                    break
        output = {
            "status": "ok",
            "data": {
                "proposal_id": created["proposal_id"], "kind": "book",
                "start_at": created["held"], "note": created.get("note"),
                "replaces": None, "queued": [],
            },
            "missing": [], "error_code": None,
        }
        return final_output(**output)

    def _generate(self, messages, stop=None, run_manager=None, **kw):
        return ChatResult(generations=[ChatGeneration(message=self._reply(messages))])

    async def _agenerate(self, messages, stop=None, run_manager=None, **kw):
        return ChatResult(generations=[ChatGeneration(message=self._reply(messages))])


def book_output(tool_json: dict) -> dict:
    """`build_output` cho `EchoTool` khi tool là `propose_appointment` — chép
    JSON ToolMessage của `propose_appointment` vào `SpecialistOutput`."""
    return {
        "status": "ok",
        "data": {
            "proposal_id": tool_json["proposal_id"], "kind": "book",
            "start_at": tool_json["held"], "note": tool_json.get("note"),
            "replaces": None, "queued": [],
        },
        "missing": [], "error_code": None,
    }
