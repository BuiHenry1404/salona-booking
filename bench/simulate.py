"""Khách hàng giả lập: sinh câu nói tiếp theo của khách cho kịch bản "simulated".

Hộp đen — không `import app`. `SimCustomer` đóng vai một khách Việt lớn tuổi
nhắn tin qua điện thoại, theo `SimGoal.persona`/`SimGoal.goal`, và dừng khi
đạt mục tiêu (model trả đúng token `<<DONE>>`), khi hết `max_turns`, hoặc khi
gọi model lỗi — mọi trường hợp đều trả `None` để runner (Task 9) kết thúc
kịch bản.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import anthropic

from bench.judge import render_transcript

if TYPE_CHECKING:
    from bench.adapters.base import TurnRecord
    from bench.scenario import SimGoal

DONE_TOKEN = "<<DONE>>"

# Tiếng Anh theo quy ước prompt của repo (xem AGENTS.md); khách phải trả lời
# bằng tiếng Việt.
_SYSTEM_PROMPT = """You are role-playing an elderly Vietnamese customer texting a \
nail/hair salon's booking chat assistant on their phone.

Persona: {persona}
Goal: {goal}

Rules:
- Write ONLY the next message the customer would type, in Vietnamese, the way
  a real elderly Vietnamese phone user types (short, simple vocabulary,
  sometimes a little informal or imprecise).
- Never reveal, hint at, or admit that you are an AI or that this is a
  simulation. Stay in character as the customer at all times, no matter what
  the assistant says.
- Read the conversation so far and continue naturally toward the goal.
- When the goal has been reached, or the customer would give up and stop
  trying, output EXACTLY the token {done_token} and nothing else — no
  punctuation, no quotes, no extra words.
- Otherwise output only the customer's next message: no explanation, no
  quotes, no labels, no stage directions."""


class SimCustomer:
    def __init__(self, client, model: str, goal: "SimGoal") -> None:
        self._client = client
        self._model = model
        self._goal = goal

    async def next_message(self, turns: "list[TurnRecord]") -> str | None:
        """Câu tiếp theo của khách, hoặc `None` khi đã `<<DONE>>`, hết
        `max_turns`, hoặc lỗi gọi model."""
        if len(turns) >= self._goal.max_turns:
            return None

        transcript = render_transcript(turns)
        user_content = (
            f"{transcript}\n(Hãy viết câu tiếp theo của khách.)"
            if transcript
            else "(Đây là tin nhắn đầu tiên — hãy bắt đầu cuộc hội thoại với khách hàng.)"
        )
        system = _SYSTEM_PROMPT.format(
            persona=self._goal.persona, goal=self._goal.goal, done_token=DONE_TOKEN
        )

        try:
            response = await self._client.messages.create(
                model=self._model,
                max_tokens=4000,
                thinking={"type": "adaptive"},
                system=system,
                messages=[{"role": "user", "content": user_content}],
            )
        except anthropic.APIError:
            return None

        if getattr(response, "stop_reason", None) == "refusal":
            return None

        text = "".join(
            block.text for block in response.content if getattr(block, "type", None) == "text"
        ).strip()

        if not text or text == DONE_TOKEN:
            return None

        return text
