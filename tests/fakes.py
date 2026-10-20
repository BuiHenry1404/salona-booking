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

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, AIMessageChunk
from langchain_core.outputs import ChatGeneration, ChatGenerationChunk, ChatResult


@dataclass
class Hang:
    """Model treo `seconds` giây rồi mới trả `then` (mặc định `AIMessage("")`)."""
    seconds: float = 3600.0
    then: Optional[Any] = None


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


def tool_call(name, args, id="c1"):
    return AIMessage(content="", tool_calls=[{"name": name, "args": args, "id": id}])
