"""Judge LLM: chấm một transcript hội thoại theo rubric cố định (Review Focus #5).

Hộp đen — không `import app`. Rubric chép lại từ `scripts/score_transcript.py`
(script đó nằm ngoài `bench/`, không được import ở đây — xem brief Task 7).

Judge trả `None` bất cứ khi nào không tin được kết quả: model từ chối trả
lời (`stop_reason == "refusal"`), nội dung không phải JSON đúng schema, hoặc
lỗi gọi API/mạng. Loại khỏi trung bình ở lớp gọi, không làm hỏng lần chạy.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

import anthropic

if TYPE_CHECKING:
    from bench.adapters.base import TurnRecord

JUDGE_AXES = ("lap_y", "giong_may", "hoi_lai_da_biet", "xung_ho", "tu_nhien")

# Rubric viết bằng tiếng Anh (quy ước prompt của repo, xem AGENTS.md), nhưng
# transcript đưa vào là tiếng Việt và model phải chấm trên tiếng Việt. Chép
# từ `scripts/score_transcript.py` — cùng tiêu chí, cùng 5 trục.
RUBRIC = """You are judging a transcript from a Vietnamese nail-and-hair salon
booking chatbot. The customer is often elderly and uses a phone.

Score each axis from 1 to 5, where 5 is best. Judge ONLY what the bot said —
the customer's lines are fixed input.

- lap_y: Does the bot restate information it already gave earlier in the same
  conversation? Repeating a fact in new words counts as repeating. 5 = never.
- giong_may: Does the bot sound like a form or a status report rather than a
  person? Software words, listing, stiff register. 5 = sounds human throughout.
- hoi_lai_da_biet: Does the bot ask for something the customer already told it,
  or that it could read from context? 5 = never.
- xung_ho: Is the form of address consistent and correct across every turn?
  The receptionist says "em" and addresses the customer as "anh"/"chị" plus
  their name. Saying "cô", "chú", "bác" or "con" is wrong. 5 = perfect.
- tu_nhien: Overall, would a real salon receptionist have said these things?

Also point out the single worst bot line in the transcript, quoted verbatim,
as "worst_line". Write every "why" in Vietnamese."""


def _axis_schema() -> dict:
    return {
        "type": "object",
        "properties": {
            "score": {"type": "integer", "minimum": 1, "maximum": 5},
            "why": {"type": "string"},
        },
        "required": ["score", "why"],
        "additionalProperties": False,
    }


def _output_schema() -> dict:
    properties = {axis: _axis_schema() for axis in JUDGE_AXES}
    properties["worst_line"] = {"type": "string"}
    return {
        "type": "object",
        "properties": properties,
        "required": [*JUDGE_AXES, "worst_line"],
        "additionalProperties": False,
    }


def render_transcript(turns: "list[TurnRecord]") -> str:
    """"Khách: …\\nAI: …\\n" cho mỗi lượt, kèm danh sách tool đã gọi trong ngoặc."""
    lines: list[str] = []
    for turn in turns:
        lines.append(f"Khách: {turn.customer_text}")
        suffix = f" ({', '.join(turn.tools)})" if turn.tools else ""
        lines.append(f"AI: {turn.answer}{suffix}")
    return "\n".join(lines) + "\n" if lines else ""


def _valid_score(score: object) -> bool:
    # bool is a subclass of int in Python — reject it explicitly.
    return isinstance(score, int) and not isinstance(score, bool) and 1 <= score <= 5


def _valid_result(data: object) -> bool:
    if not isinstance(data, dict):
        return False
    for axis in JUDGE_AXES:
        item = data.get(axis)
        if not isinstance(item, dict):
            return False
        if "score" not in item or "why" not in item:
            return False
        if not _valid_score(item["score"]):
            return False
        if not isinstance(item["why"], str):
            return False
    return isinstance(data.get("worst_line"), str)


async def judge(client, model: str, transcript: str) -> dict | None:
    """Gọi judge model trên `transcript`, trả dict 5 trục + `worst_line`, hoặc
    `None` nếu từ chối / JSON sai schema / lỗi API (Review Focus #5)."""
    try:
        response = await client.messages.create(
            model=model,
            max_tokens=4000,
            thinking={"type": "adaptive"},
            output_config={"format": {"type": "json_schema", "schema": _output_schema()}},
            messages=[{"role": "user", "content": f"{RUBRIC}\n\n{transcript}"}],
        )
    except anthropic.APIError:
        return None

    if getattr(response, "stop_reason", None) == "refusal":
        return None

    text = "".join(
        block.text for block in response.content if getattr(block, "type", None) == "text"
    ).strip()
    if not text:
        return None

    try:
        data = json.loads(text)
    except (ValueError, TypeError):
        return None

    if not _valid_result(data):
        return None

    return data
