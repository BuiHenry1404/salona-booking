"""Chạy những cuộc trò chuyện đặt lịch dài qua Socket.IO như khách thật, ghi
toàn bộ ra file txt để đọc lại bằng mắt.

    PYTHONPATH=. .venv/bin/python scripts/chat_e2e_transcript.py \
        [--scenario booking|doi_y|huy|thong_tin|all] [--out FILE] [--phone SDT]

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
SCENARIOS = {
    # Đặt lịch suôn sẻ — kịch bản kiểm câu xác nhận có đủ ngày/giờ/dịch vụ không.
    "booking": [
        "chào em",
        "chị muốn làm tóc",
        "mai chị rảnh buổi sáng",
        "9 giờ được không em",
        "ừ chốt luôn nha",
        "cảm ơn em nhé",
    ],
    # Đổi ý giữa chừng — đúng ca lượt 6 của transcript cũ, chỗ bot giục khách.
    "doi_y": [
        "chị muốn làm nail mai 9 giờ sáng",
        "à khoan, để chị xem lại",
        "thôi 10 giờ đi em",
        "ừ được đó",
    ],
    # Hủy lịch — kiểm list_my_appointments chạy trước cancel.
    "huy": [
        "chị có lịch nào sắp tới không em",
        "hủy giùm chị cái lịch đó",
        "ừ hủy đi em",
    ],
    # Thông tin tiệm — kịch bản MỚI, hiện đang rơi hết vào refuse.
    "thong_tin": [
        "tiệm mình mở cửa mấy giờ vậy em",
        "chủ nhật có làm không em",
        "tiệm đang bận không em",
        "cho tôi công thức nấu phở",
    ],
    # Dời lịch rồi hủy Ở LƯỢT SAU — tái hiện BUG-1 và BUG-2 của checkpoint
    # 2026-09-14. Đúng: sau lượt 4 DB còn ĐÚNG MỘT lịch (10 giờ), và lượt 6-7
    # hủy được mà không cần gọi list_my_appointments cùng lượt.
    "doi_lich": [
        "anh muốn cắt tóc mai 9 giờ sáng",
        "ừ",
        "em ơi chuyển giùm anh qua 10 giờ sáng nha, đừng để 9 giờ nữa",
        "ừ",
        "vậy anh còn mấy lịch em",
        "thôi hủy lịch đó giùm anh",
        "ừ hủy đi",
    ],
    # Hội thoại dài 16 lượt, đi qua MỌI nhánh của graph. Đây là kịch bản
    # dùng để chấm rubric, nên 16 dòng dưới đây là MỐC SO SÁNH — sửa một
    # chữ là mọi điểm cũ hết so được với điểm mới.
    "dai": [
        "chào em",
        "tiệm mình mở cửa mấy giờ vậy em",
        "chủ nhật có nghỉ không em",
        "chị muốn làm tóc",
        "mai được không em",
        "3 giờ chiều",
        "ừ chốt nha",
        "chị đặt lúc mấy giờ vậy em nhắc lại giùm",
        "khách nào đặt lúc 4 giờ vậy em",
        "chị muốn đổi sang 4 giờ chiều mai",
        "à thôi khoan để chị tính lại",
        "chủ tiệm đang bận không em",
        "cho chị xin công thức nấu phở",
        "thôi hủy giùm chị cái lịch mai đi em",
        "ừ hủy đi em",
        "cảm ơn em nhé chị đi đây",
    ],
}


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
    parser.add_argument(
        "--scenario", default="all", choices=[*SCENARIOS, "all"],
        help="chạy một kịch bản, hoặc 'all' để chạy hết vào cùng một file",
    )
    args = parser.parse_args()

    names = list(SCENARIOS) if args.scenario == "all" else [args.scenario]

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
    total = 0

    for name in names:
        lines += [f"##### KỊCH BẢN {name} #####", ""]
        for i, message in enumerate(SCENARIOS[name], 1):
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
            total += 1

            # Nhắn dồn dập sẽ đụng trần chat_max_per_hour và làm hỏng kịch bản.
            await asyncio.sleep(1.5)

        # Nghỉ dài hơn giữa hai kịch bản: bốn kịch bản chạy liền nhau dễ chạm
        # trần 30 tin/giờ mỗi khách, và kịch bản sau sẽ hỏng vì bị chặn.
        await asyncio.sleep(3)

    await sio.disconnect()

    with open(args.out, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    print(f"đã ghi {args.out} ({total} lượt, {len(names)} kịch bản)")


if __name__ == "__main__":
    asyncio.run(main())
