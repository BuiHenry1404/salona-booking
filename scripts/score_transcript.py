"""Chấm một transcript hội thoại theo rubric, bằng LLM.

    PYTHONPATH=. .venv/bin/python scripts/score_transcript.py FILE [FILE...]

Sinh ra vì `CONTEXT.md` bẫy #18: pytest không bắt được lỗi lặp ý và giọng
máy — từng có 413 test xanh trong khi bot đang lặp câu với khách thật. Đây
là cách duy nhất trong repo cho ra một CON SỐ để so trước/sau.

Mỗi file tốn đúng một lượt LLM. Chấm cùng một kịch bản ở hai thời điểm thì
mới so được; đổi lời thoại trong kịch bản là mọi điểm cũ hết giá trị.
"""
import asyncio
import json
import sys

from langchain_core.messages import HumanMessage, SystemMessage

from app.agents.llm import build_chat_model

# Rubric viết bằng tiếng Anh (quy ước prompt của repo), nhưng transcript đưa
# vào là tiếng Việt và model phải chấm trên tiếng Việt.
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

Reply with ONLY a JSON object, no prose and no code fence:
{"lap_y": {"score": N, "why": "..."},
 "giong_may": {"score": N, "why": "..."},
 "hoi_lai_da_biet": {"score": N, "why": "..."},
 "xung_ho": {"score": N, "why": "..."},
 "tu_nhien": {"score": N, "why": "..."},
 "tong": N.N,
 "te_nhat": "the single worst bot line, quoted verbatim"}

"tong" is the mean of the five scores. Write every "why" in Vietnamese."""

AXES = ["lap_y", "giong_may", "hoi_lai_da_biet", "xung_ho", "tu_nhien"]


def parse_json(raw: str) -> dict:
    """Model hay bọc JSON trong ```json dù đã dặn đừng. Gỡ rào rồi mới đọc."""
    text = raw.strip()
    if text.startswith("```"):
        text = text.split("```")[1]
        if text.startswith("json"):
            text = text[4:]
    return json.loads(text.strip())


async def score(model, path: str) -> None:
    with open(path, encoding="utf-8") as f:
        transcript = f.read()

    reply = await model.ainvoke([
        SystemMessage(content=RUBRIC),
        HumanMessage(content=transcript),
    ])
    try:
        result = parse_json(reply.content or "")
    except (ValueError, IndexError):
        print(f"{path}: KHÔNG ĐỌC ĐƯỢC JSON\n{(reply.content or '')[:400]}")
        return

    print(f"\n=== {path} ===")
    for axis in AXES:
        item = result.get(axis, {})
        print(f"  {axis:18s} {item.get('score', '?')}/5  {item.get('why', '')}")
    print(f"  {'TỔNG':18s} {result.get('tong', '?')}/5")
    print(f"  câu tệ nhất: {result.get('te_nhat', '')}")


async def main() -> None:
    paths = sys.argv[1:]
    if not paths:
        print(__doc__)
        raise SystemExit(2)
    # streaming=False: đây là lượt sinh JSON, không có gì để hiện dần.
    model = build_chat_model(tags=["judge"], temperature=0.0, streaming=False)
    for path in paths:
        await score(model, path)


if __name__ == "__main__":
    asyncio.run(main())
