"""Chạy một cuộc trò chuyện đặt lịch dài qua Socket.IO như khách thật, ghi
toàn bộ ra file txt để đọc lại bằng mắt.

    .venv/bin/python scripts/chat_e2e_transcript.py [--out FILE] [--phone SDT]

Đây là công cụ kiểm tay, KHÔNG phải test tự động: nó không assert gì cả. Mục
đích là đọc xem câu trả lời của AI có tự nhiên không, nên nó ghi cả sự kiện
tool để biết câu nào do LLM sinh và câu nào do code trả thẳng.
"""
import argparse
import asyncio
import json
import time
from datetime import datetime
from urllib.request import Request, urlopen

import socketio

API = "http://localhost:8000"

# Kịch bản cố tình đi vòng vèo: hỏi bâng quơ, đổi ý, hỏi lại giờ, rồi mới
# chốt. Hội thoại thẳng tuột không lộ ra chỗ nào bị trả lời cứng.
SCRIPT = [
    "chào em",
    "tiệm mình mở cửa mấy giờ vậy em",
    "chị muốn làm tóc",
    "mai chị rảnh buổi sáng",
    "9 giờ được không em",
    "à khoan, để chị xem lại",
    "thôi 10 giờ đi em",
    "ừ chốt luôn nha",
    "chị đặt lúc mấy giờ vậy em nhắc lại giùm",
    "cảm ơn em nhé",
]


def login(phone: str, password: str) -> str:
    req = Request(
        f"{API}/api/v1/auth/login",
        data=json.dumps({"phone": phone, "password": password}).encode(),
        headers={"Content-Type": "application/json"},
    )
    with urlopen(req) as resp:
        return json.load(resp)["access_token"]


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default="chat_transcript.txt")
    parser.add_argument("--phone", default="0923456789")
    parser.add_argument("--password", default="matkhau123")
    args = parser.parse_args()

    token = login(args.phone, args.password)
    sio = socketio.AsyncClient()

    # Mỗi lượt gom vào đây rồi mới ghi, để token stream nối lại thành câu.
    turn: dict = {}
    done = asyncio.Event()

    @sio.on("token")
    async def _token(data):
        turn.setdefault("tokens", []).append(data.get("text", ""))

    @sio.on("tool_started")
    async def _tool_started(data):
        turn.setdefault("tools", []).append(f"-> {data.get('name')}")

    @sio.on("tool_finished")
    async def _tool_finished(data):
        ok = "ok" if data.get("ok") else "LỖI"
        turn.setdefault("tools", []).append(f"<- {data.get('name')} [{ok}]")

    @sio.on("complete")
    async def _complete(data):
        turn["answer"] = data.get("answer", "")
        done.set()

    @sio.on("error")
    async def _error(data):
        turn["error"] = data.get("message", "")
        done.set()

    await sio.connect(API, auth={"token": token}, transports=["websocket"])

    lines = [
        f"# Transcript chat E2E — {datetime.now().isoformat(timespec='seconds')}",
        f"# tài khoản: {args.phone}",
        "",
    ]

    for i, message in enumerate(SCRIPT, 1):
        turn.clear()
        done.clear()
        started = time.monotonic()
        await sio.emit("send_message", {"message": message})
        try:
            await asyncio.wait_for(done.wait(), timeout=90)
        except asyncio.TimeoutError:
            turn["error"] = "(quá 90 giây không thấy trả lời)"
        elapsed = time.monotonic() - started

        streamed = "".join(turn.get("tokens", []))
        answer = turn.get("answer", "")
        lines += [
            f"===== LƯỢT {i} ({elapsed:.1f}s) =====",
            f"KHÁCH : {message}",
        ]
        if turn.get("tools"):
            lines.append("TOOL  : " + " | ".join(turn["tools"]))
        else:
            lines.append("TOOL  : (không gọi tool nào)")
        # Streamed rỗng mà answer có chữ = câu này KHÔNG do LLM stream ra,
        # tức là code trả thẳng. Đây là thứ cần soi.
        lines.append(f"STREAM: {'(rỗng — câu trả lời không đi qua LLM)' if not streamed else repr(streamed[:200])}")
        lines.append(f"BOT   : {answer or turn.get('error', '(không có)')}")
        lines.append("")

        # Nhắn dồn dập sẽ đụng trần chat_max_per_hour và làm hỏng kịch bản.
        await asyncio.sleep(1.5)

    await sio.disconnect()

    with open(args.out, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    print(f"đã ghi {args.out} ({len(SCRIPT)} lượt)")


if __name__ == "__main__":
    asyncio.run(main())
