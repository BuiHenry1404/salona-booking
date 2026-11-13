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
from typing import Any, Callable, List, Optional, Union
from uuid import uuid4

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, AIMessageChunk, SystemMessage
from langchain_core.outputs import ChatGeneration, ChatGenerationChunk, ChatResult

from app.agents.salon.guard.catalog import CATALOG_HEADER
from app.core.text import normalize_for_match


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
            try:
                return self.schema.model_validate_json(reply.content)
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
        if callable(reply) and not isinstance(reply, AIMessage):
            reply = reply(messages)
        return self._settle(reply)

    async def _anext(self, messages):
        reply = self._pop(messages)
        if isinstance(reply, Hang):
            await asyncio.sleep(reply.seconds)
            reply = reply.then if reply.then is not None else AIMessage(content="")
        if callable(reply) and not isinstance(reply, AIMessage):
            reply = reply(messages)
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


def tool_calls(*pairs: tuple) -> AIMessage:
    """Nhiều tool call trong MỘT AIMessage — dùng để test chuỗi parallel/serial."""
    calls = [
        {"name": name, "args": args, "id": uuid4().hex, "type": "tool_call"}
        for name, args in pairs
    ]
    return AIMessage(content="", tool_calls=calls)


@dataclass(frozen=True)
class Cite:
    """Một đoạn trích dẫn trong `reply(...)`: `role` + `text` tra trong khối
    danh mục (`FactCatalog.render()`) để tìm id. `find` tìm theo chữ khác
    `text` (vd `text` là câu đầy đủ, `find` là mẩu ngắn có trong label).
    `ref` cho sẵn thì bỏ qua tra cứu — dùng để test id không tồn tại."""
    role: str
    text: str
    find: Optional[str] = None
    ref: Optional[str] = None


def cite(role: str, text: str, *, find: Optional[str] = None, ref: Optional[str] = None) -> Cite:
    return Cite(role=role, text=text, find=find, ref=ref)


def _catalog_lines(messages: list) -> List[str]:
    """Các dòng `- {id} [{roles}]: {label}` sau `CATALOG_HEADER`, gộp từ mọi
    SystemMessage của lô (chỉ khối bối cảnh của Orchestrator mang khối này)."""
    lines: List[str] = []
    for message in messages:
        if not isinstance(message, SystemMessage):
            continue
        content = message.content
        if CATALOG_HEADER not in content:
            continue
        after_header = content.split(CATALOG_HEADER, 1)[1]
        lines.extend(line.strip() for line in after_header.splitlines() if line.strip())
    return lines


def _parse_catalog_line(line: str) -> Optional[tuple]:
    """`- {id} [{role1, role2}]: {label}` -> (id, [roles], label), hoặc
    `None` nếu dòng không đúng hình dạng đó."""
    if not line.startswith("- "):
        return None
    rest = line[2:]
    if " [" not in rest or "]: " not in rest:
        return None
    entry_id, remainder = rest.split(" [", 1)
    roles_part, label = remainder.split("]: ", 1)
    roles = [role.strip() for role in roles_part.split(",")]
    return entry_id.strip(), roles, label


def _resolve_ref(cited: Cite, messages: list) -> str:
    if cited.ref is not None:
        return cited.ref
    needle = normalize_for_match(cited.find or cited.text)
    wanted_role = normalize_for_match(cited.role)
    for line in _catalog_lines(messages):
        parsed = _parse_catalog_line(line)
        if parsed is None:
            continue
        entry_id, roles, label = parsed
        if wanted_role not in {normalize_for_match(role) for role in roles}:
            continue
        if needle in normalize_for_match(label):
            return entry_id
    raise AssertionError(f"no reply fact for {cited.role} {cited.find or cited.text!r}")


def reply(*parts: Union[str, Cite]) -> Callable[[list], AIMessage]:
    """Kịch bản một câu trả lời cuối Level C (`REPLY_SCHEMA`): mỗi `part` là
    chữ thường (một đoạn `ref`/`role` null) hoặc `Cite` (tra id trong khối
    danh mục của lô message). Trả một callable — `ScriptedChatModel` gọi nó
    với lô message để biết nội dung lúc đó mới có khối danh mục."""
    def build(messages: list) -> AIMessage:
        segments = []
        for part in parts:
            if isinstance(part, Cite):
                segments.append({"text": part.text, "ref": _resolve_ref(part, messages), "role": part.role})
            else:
                segments.append({"text": part, "ref": None, "role": None})
        return AIMessage(content=json.dumps({"segments": segments}, ensure_ascii=False))
    return build


def plain(text: str) -> AIMessage:
    """Một AIMessage với đúng một đoạn chữ thường — không cần khối bối cảnh."""
    return AIMessage(content=json.dumps({"segments": [{"text": text, "ref": None, "role": None}]},
                                        ensure_ascii=False))
