"""In bảng phân loại của supervisor cho một bộ câu mẫu.

    PYTHONPATH=. .venv/bin/python scripts/probe_supervisor.py

Mỗi câu tốn đúng một lượt LLM. Đây là lưới an toàn cho việc định tuyến:
chạy trước khi sửa để có mốc, chạy lại sau khi sửa để so.
"""
import asyncio

from langchain_core.messages import HumanMessage, SystemMessage

from app.agents.booking_graph.prompts import SUPERVISOR_PROMPT
from app.agents.llm import build_chat_model

# Cột trái là nhãn MONG ĐỢI sau khi sửa xong. Trước khi sửa sẽ lệch — đó
# chính là thứ cần chụp lại làm mốc.
PROBES = [
    ("booking", "chị muốn làm tóc"),
    ("booking", "mai 3h chiều được không em"),
    ("booking", "chị có lịch lúc nào vậy em"),
    ("booking", "hủy giùm chị lịch mai"),
    ("shop", "tiệm đang bận không em"),
    ("shop", "chừng nào chủ tiệm xong vậy"),
    ("shop", "tiệm mình mở cửa mấy giờ vậy em"),
    ("shop", "mấy giờ tiệm đóng cửa"),
    ("shop", "chủ nhật tiệm có làm không em"),
    ("social", "chào em"),
    ("social", "cảm ơn em nhé"),
    ("social", "thôi chị đi nha"),
    ("social", "cho tôi công thức nấu phở"),
    ("social", "dịch giùm tôi đoạn tiếng Anh này"),
]


async def main() -> None:
    model = build_chat_model(tags=["supervisor"], temperature=0.0)
    wrong = 0
    for expected, message in PROBES:
        reply = await model.ainvoke(
            [SystemMessage(content=SUPERVISOR_PROMPT), HumanMessage(content=message)]
        )
        actual = (reply.content or "").strip().lower()
        mark = "  " if actual == expected else "✗ "
        if actual != expected:
            wrong += 1
        print(f"{mark}{actual:10s} (mong đợi {expected:8s}) <- {message}")
    print(f"\nlệch {wrong}/{len(PROBES)}")


if __name__ == "__main__":
    asyncio.run(main())
