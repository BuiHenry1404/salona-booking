"""Câu chốt lịch thay cho lượt agent — `before_agent`, thay node `phrase` cũ.

`confirm` vừa ghi xong (approve) hoặc cần hỏi lại MỘT câu (clarify) thì đưa
`confirm_fact` + `fallback` sang agent booking. Middleware này:
- approve/failed, gõ chữ (`confirm_source != "button"`): LLM viết câu chốt
  theo số liệu code cấp (`write_phrase`, tag `respond` — stream như A); lỗi/quá
  giờ → `fallback`;
- approve/failed, bấm nút "Đồng ý" (`confirm_source == "button"`): CODE viết
  câu chốt từ mẫu (`code_phrase`) — không gọi LLM, lượt nút phải xong nhanh
  (≤1.5s theo spec Task 3);
- clarify: câu hỏi lại do CODE viết (= `fallback`), không gọi LLM, không token
  — tới khách qua `complete` như các câu cứng của A;
rồi nhảy thẳng tới cuối (`jump_to: "end"`): model agent KHÔNG được gọi, tool
không chạy. `after_agent` vẫn chạy — GuardMiddleware kiểm số liệu, sai thì dùng
câu cứng (bẫy #21).

Không có `confirm_fact` → None: agent chạy như thường.
"""
from datetime import datetime
from typing import Any, Dict, List, Optional

from langchain.agents.middleware import AgentMiddleware, AgentState, hook_config
from langchain_core.messages import AIMessage
from typing_extensions import NotRequired

from app.agents.booking_graph import phrase


class PhraseState(AgentState):
    confirm_fact: NotRequired[Optional[Dict[str, Any]]]
    phrase_fact: NotRequired[Optional[Dict[str, Any]]]
    fallback: NotRequired[Optional[str]]
    confirm_source: NotRequired[Optional[str]]  # "button" | "text"
    previous_replies: NotRequired[List[str]]
    # `write_phrase` xếp message như agent (bẫy #9) và cắt lịch sử như agent.
    context_block: NotRequired[str]
    summary: NotRequired[str]
    history_after: NotRequired[Optional[datetime]]
    asked_at: NotRequired[Optional[datetime]]


class PhraseMiddleware(AgentMiddleware):
    state_schema = PhraseState
    tools = []

    @hook_config(can_jump_to=["end"])
    async def abefore_agent(self, state, runtime) -> Dict[str, Any] | None:
        fact = state.get("confirm_fact")
        if not fact:
            return None
        if fact.get("kind") == "clarify":
            text = state.get("fallback") or ""
        elif state.get("confirm_source") == "button":
            # Nút "Đồng ý": câu chốt CODE viết, không gọi LLM (Task 3).
            text = phrase.code_phrase(fact, state.get("previous_replies") or [])
        else:
            # Gọi qua module: test thay `phrase.write_phrase`/`phrase.build_chat_model`.
            text = await phrase.write_phrase(state)
        return {"messages": [AIMessage(content=text)], "phrase_fact": fact, "jump_to": "end"}
