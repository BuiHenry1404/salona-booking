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
from typing import Any, List, Optional
from uuid import uuid4

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, AIMessageChunk
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
