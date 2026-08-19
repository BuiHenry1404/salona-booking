"""Diễn lại vài cuộc trò chuyện thật của khách, đánh vào server đang chạy.

    docker compose up -d
    PYTHONPATH=. .venv/bin/python scripts/chat_scenarios.py

Mỗi kịch bản dùng MỘT tài khoản riêng: hội thoại và cờ `pending_confirmation`
lưu theo `user_id`, dùng chung một tài khoản thì kịch bản sau thừa hưởng ngữ
cảnh của kịch bản trước và không còn đọc được nữa. Tài khoản và lịch do script
tạo đều được dọn ở cuối.
"""
import asyncio
import sys
from datetime import datetime, timedelta

import httpx
import socketio

BASE = "http://localhost:8000"
API = f"{BASE}/api/v1"
ADMIN = {"phone": "0901234567", "password": "chutiem123"}
PASSWORD = "khachhang123"

RESET = "\033[0m"; DIM = "\033[2m"; CUS = "\033[36m"; AI = "\033[32m"; TOOL = "\033[33m"


async def login(c, phone, password):
    r = await c.post(f"{API}/auth/login", json={"phone": phone, "password": password})
    r.raise_for_status()
    return r.json()["access_token"]


def hdr(t):
    return {"Authorization": f"Bearer {t}"}


async def ensure_customer(c, admin_token, phone, name):
    await c.post(f"{API}/auth/users", headers=hdr(admin_token),
                 json={"phone": phone, "password": PASSWORD, "full_name": name})
    return await login(c, phone, PASSWORD)


class Chat:
    """Một phiên Socket.IO giữ nguyên qua nhiều lượt, như khách mở app rồi nhắn."""

    def __init__(self, token):
        self.token = token
        self.sio = socketio.AsyncClient()
        self.tools = []
        self.answer = ""
        self.done = asyncio.Event()
        self.sio.on("tool_started", lambda d: self.tools.append(d["name"]))
        self.sio.on("complete", self._complete)
        self.sio.on("error", self._error)

    async def _complete(self, data):
        self.answer = data.get("answer", ""); self.done.set()

    async def _error(self, data):
        self.answer = f"[LỖI] {data}"; self.done.set()

    async def __aenter__(self):
        await self.sio.connect(BASE, auth={"token": self.token}, wait_timeout=10)
        return self

    async def __aexit__(self, *exc):
        await self.sio.disconnect()

    async def say(self, message):
        self.tools, self.answer, self.done = [], "", asyncio.Event()
        print(f"  {CUS}Khách:{RESET} {message}")
        await self.sio.emit("send_message", {"message": message})
        await asyncio.wait_for(self.done.wait(), timeout=90)
        if self.tools:
            print(f"         {DIM}{TOOL}⚙ {' → '.join(self.tools)}{RESET}")
        print(f"  {AI}AI   :{RESET} {self.answer}\n")
        return self.answer


def local(day_offset, hour, minute=0):
    return (datetime.now() + timedelta(days=day_offset)).replace(
        hour=hour, minute=minute, second=0, microsecond=0)


async def scenario(title, note, token, lines, c=None, before=None):
    print(f"\n{'─' * 74}\n▸ {title}\n  {DIM}{note}{RESET}\n")
    if before:
        await before()
    async with Chat(token) as chat:
        for line in lines:
            await chat.say(line)
    if c is not None:
        mine = (await c.get(f"{API}/appointments/mine", headers=hdr(token))).json()
        print(f"  {DIM}→ lịch trong DB sau kịch bản: "
              f"{[a['start_at'] + ' ' + str(a.get('note')) for a in mine] or 'chưa có'}{RESET}")
        return mine
    return []


async def main():
    async with httpx.AsyncClient(timeout=60) as c:
        admin = await login(c, ADMIN["phone"], ADMIN["password"])
        people = {
            "lan":  ("0987000011", "Cô Lan"),
            "hoa":  ("0987000012", "Cô Hoa"),
            "bay":  ("0987000013", "Bác Bảy"),
            "tam":  ("0987000014", "Cô Tám"),
            "chin": ("0987000015", "Bác Chín"),
            "muoi": ("0987000016", "Cô Mười"),
        }
        tok = {k: await ensure_customer(c, admin, p, n) for k, (p, n) in people.items()}
        created = []

        # 1 — đường thẳng, khách nói đủ ngày và giờ ngay từ đầu
        created += await scenario(
            "Đặt lịch suôn sẻ", "khách nói đủ ngày + giờ ngay lượt đầu",
            tok["lan"], ["mai 3h chiều làm tóc được không con",
                         "ừ con"], c)

        # 2 — thiếu giờ, AI phải hỏi lại chứ không được đoán
        created += await scenario(
            "Khách nói mơ hồ", "thiếu giờ — AI phải hỏi lại, tuyệt đối không đoán",
            tok["hoa"], ["sáng mai cô ghé làm móng được không con",
                         "9 giờ nhé",
                         "ừ đúng rồi"], c)

        # 3 — hỏi trạng thái, không phải đặt lịch
        await scenario(
            "Hỏi chủ tiệm có rảnh không", "định tuyến sang StatusAgent, không đụng tới lịch",
            tok["bay"], ["chú ơi giờ tiệm có đông không con"])

        # 4 — giờ đã có người: phải gợi ý giờ khác chứ không bỏ lửng
        async def make_it_taken():
            await c.post(f"{API}/appointments", headers=hdr(tok["lan"]),
                         json={"start_at": local(2, 10).isoformat(), "note": "giữ chỗ sẵn"})
        created += await scenario(
            "Giờ khách muốn đã có người", "phải gợi ý giờ trống gần nhất",
            tok["tam"], ["ngày kia 10 giờ sáng làm tóc nha con"], c, before=make_it_taken)

        # 5 — đổi ý ở bước xác nhận: không được ghi lịch
        created += await scenario(
            "Khách đổi ý lúc xác nhận", "nói 'thôi' thì KHÔNG được ghi lịch",
            tok["chin"], ["mai 4h chiều cắt tóc cho bác",
                          "thôi khỏi con, để hôm khác"], c)

        # 6 — hủy lịch đã có
        async def book_first():
            await c.post(f"{API}/appointments", headers=hdr(tok["muoi"]),
                         json={"start_at": local(3, 14).isoformat(), "note": "nhuộm tóc"})
        created += await scenario(
            "Hủy lịch", "phải tra danh sách trước rồi mới hủy đúng lịch",
            tok["muoi"], ["cô muốn hủy cái lịch nhuộm tóc của cô",
                          "ừ hủy giúp cô"], c, before=book_first)

        # 7 — ngoài phạm vi
        await scenario(
            "Câu ngoài chủ đề", "phải từ chối lịch sự, không gọi tool nào",
            tok["bay"], ["con ơi bán bảo hiểm nhân thọ không",
                         "vậy chỉ giúp cô xem bói được không"])

        # 8 — giờ tiệm đóng cửa
        await scenario(
            "Giờ tiệm không mở cửa", "3 giờ sáng — phải từ chối, không giữ chỗ",
            tok["tam"], ["mai 3 giờ sáng làm tóc nha con"])

        print(f"\n{'─' * 74}\n▸ Dọn dẹp")
        for key, token in tok.items():
            for a in (await c.get(f"{API}/appointments/mine", headers=hdr(token))).json():
                await c.delete(f"{API}/appointments/{a['id']}", headers=hdr(token))
        print("  đã hủy mọi lịch do kịch bản tạo")
        phones = [p for p, _ in people.values()]
        print(f"  {DIM}tài khoản kịch bản vẫn còn: {', '.join(phones)}")
        print("  xoá cả tài khoản lẫn hội thoại của chúng:")
        print("    docker compose exec -T mongo mongosh salon_booking --quiet --eval \\")
        print(f"      'const p={phones}; const u=db.users.find({{phone:{{$in:p}}}})"
              ".toArray().map(x=>String(x._id));"
              " db.conversations.deleteMany({user_id:{$in:u}});"
              f" db.users.deleteMany({{phone:{{$in:p}}}})'{RESET}")


asyncio.run(main())
