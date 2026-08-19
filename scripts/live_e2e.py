"""Kiểm đầu-cuối trên server ĐANG CHẠY, qua HTTP và Socket.IO thật.

    docker compose up -d                    # hoặc uvicorn main:app
    PYTHONPATH=. .venv/bin/python scripts/live_e2e.py

Khác với pytest: không import app, không mock gì cả, gọi Azure thật và ghi vào
database dev (`salon_booking`). Lịch do script tạo được dọn ở bước cuối.
Cần hai tài khoản của `scripts/seed_dev_users.py` và gói `aiohttp` (client
Socket.IO cần, server thì không).
"""
import asyncio, sys
from datetime import datetime, timedelta
import httpx, socketio

BASE = "http://localhost:8000"
API = f"{BASE}/api/v1"
ADMIN = {"phone": "0901234567", "password": "chutiem123"}
USER = {"phone": "0912345678", "password": "matkhau123"}

ok = fail = 0
def check(name, cond, detail=""):
    global ok, fail
    if cond:
        ok += 1; print(f"  PASS  {name}")
    else:
        fail += 1; print(f"  FAIL  {name}  {detail}")

async def login(c, cred):
    r = await c.post(f"{API}/auth/login", json=cred)
    assert r.status_code == 200, r.text
    return r.json()["access_token"]

def hdr(t): return {"Authorization": f"Bearer {t}"}

async def chat(token, message):
    """Một lượt chat qua Socket.IO, trả về (danh sách sự kiện, câu trả lời)."""
    sio = socketio.AsyncClient()
    events, answer, done = [], {"text": ""}, asyncio.Event()

    for name in ("turn_started", "tool_started", "tool_finished", "token"):
        sio.on(name, lambda data=None, n=name: events.append(n))
    @sio.on("complete")
    async def _(data): events.append("complete"); answer["text"] = data.get("answer", ""); done.set()
    @sio.on("error")
    async def _(data): events.append("error"); answer["text"] = str(data); done.set()

    await sio.connect(BASE, auth={"token": token}, wait_timeout=10)
    await sio.emit("send_message", {"message": message})
    try:
        await asyncio.wait_for(done.wait(), timeout=90)
    finally:
        await sio.disconnect()
    return events, answer["text"]

async def main():
    async with httpx.AsyncClient(timeout=30) as c:
        print("\n[1] Xác thực")
        ut, at = await login(c, USER), await login(c, ADMIN)
        check("khách đăng nhập", bool(ut))
        check("admin đăng nhập", bool(at))
        r = await c.post(f"{API}/auth/login", json={**USER, "password": "sai"})
        check("mật khẩu sai → 401", r.status_code == 401, r.status_code)
        r = await c.get(f"{API}/appointments/mine")
        check("không token → 403", r.status_code == 403, r.status_code)
        me = await c.get(f"{API}/auth/me", headers=hdr(ut))
        check("/auth/me trả đúng khách", me.json().get("phone") == "0912345678", me.text[:80])

        print("\n[2] Phân quyền")
        r = await c.post(f"{API}/auth/users", headers=hdr(ut),
                         json={"phone": "0900000001", "password": "x123456", "full_name": "X"})
        check("khách không tạo được tài khoản → 403", r.status_code == 403, r.status_code)
        r = await c.post(f"{API}/shop/busy", headers=hdr(ut), json={"minutes": 30})
        check("khách không đổi được bận/rảnh → 403", r.status_code == 403, r.status_code)

        print("\n[3] Trạng thái tiệm")
        r = await c.post(f"{API}/shop/busy", headers=hdr(at), json={"minutes": 30})
        check("admin đặt bận → 200", r.status_code == 200, r.text[:100])
        r = await c.get(f"{API}/shop/status", headers=hdr(ut))
        check("khách thấy đang bận", r.json().get("is_busy") is True, r.text[:100])
        check("bận có mốc giờ xong", bool(r.json().get("busy_until")), r.text[:100])
        await c.post(f"{API}/shop/free", headers=hdr(at))
        r = await c.get(f"{API}/shop/status", headers=hdr(ut))
        check("admin gỡ bận → rảnh", r.json().get("is_busy") is False, r.text[:100])

        print("\n[4] Đặt lịch qua REST")
        for a in (await c.get(f"{API}/appointments/mine", headers=hdr(ut))).json():
            await c.delete(f"{API}/appointments/{a['id']}", headers=hdr(ut))
        tmr = (datetime.now() + timedelta(days=1)).replace(hour=9, minute=0, second=0, microsecond=0)
        r = await c.post(f"{API}/appointments", headers=hdr(ut),
                         json={"start_at": tmr.isoformat(), "note": "làm nail"})
        check("đặt lịch → 201", r.status_code == 201, r.text[:120])
        appt_id = r.json().get("id")
        check("giữ nguyên offset UTC", str(r.json().get("start_at", "")).endswith(("+00:00", "Z")), r.text[:80])
        r2 = await c.post(f"{API}/appointments", headers=hdr(at),
                          json={"start_at": tmr.isoformat(), "note": "trùng"})
        check("trùng giờ → 409", r2.status_code == 409, r2.status_code)
        r = await c.post(f"{API}/appointments", headers=hdr(ut),
                         json={"start_at": tmr.replace(hour=3).isoformat(), "note": "3 giờ sáng"})
        check("ngoài giờ mở cửa → 400", r.status_code == 400, r.status_code)
        r = await c.delete(f"{API}/appointments/{appt_id}", headers=hdr(ut))
        check("hủy lịch → 204", r.status_code == 204, r.status_code)
        check("hủy xong danh sách rỗng",
              (await c.get(f"{API}/appointments/mine", headers=hdr(ut))).json() == [])

        print("\n[5] Chat qua Socket.IO — lượt 1: xin đặt lịch")
        ev, ans = await chat(ut, "mai 3h chiều làm tóc được không con")
        check("có turn_started", "turn_started" in ev, ev)
        check("gọi tool", "tool_started" in ev, ev)
        check("có stream token", "token" in ev, ev)
        check("kết thúc bằng complete", ev[-1] == "complete", ev)
        check("CHƯA ghi lịch sau lượt 1",
              (await c.get(f"{API}/appointments/mine", headers=hdr(ut))).json() == [])
        print(f"        AI: {ans}")

        print("\n[6] Chat — lượt 2: xác nhận")
        ev, ans = await chat(ut, "ừ")
        check("không gọi tool nào", "tool_started" not in ev, ev)
        check("không stream token (không gọi LLM)", "token" not in ev, ev)
        mine = (await c.get(f"{API}/appointments/mine", headers=hdr(ut))).json()
        check("ĐÃ ghi lịch sau khi 'ừ'", len(mine) == 1, mine)
        print(f"        AI: {ans}")

        print("\n[7] Chat — câu ngoài chủ đề")
        ev, ans = await chat(ut, "cháu bán bảo hiểm không")
        check("bị từ chối, không gọi tool", "tool_started" not in ev, ev)
        check("trả lời hướng về đặt lịch", "đặt lịch" in ans.lower(), ans[:80])

        print("\n[8] Lịch sử trò chuyện")
        r = await c.get(f"{API}/conversations/days", headers=hdr(ut))
        days = r.json().get("days", [])
        check("có ngày hôm nay", len(days) >= 1, r.text[:100])
        if days:
            d = (await c.get(f"{API}/conversations/days/{days[0]['day']}", headers=hdr(ut))).json()
            check("ngày hôm nay is_today=true", d.get("is_today") is True, str(d)[:80])
            check("lưu đủ cả hỏi lẫn đáp", len(d.get("messages", [])) >= 6, len(d.get("messages", [])))

        print("\n[9] Dọn dẹp")
        for a in mine:
            await c.delete(f"{API}/appointments/{a['id']}", headers=hdr(ut))
        check("đã dọn lịch test",
              (await c.get(f"{API}/appointments/mine", headers=hdr(ut))).json() == [])

    print(f"\n{'='*46}\n  PASS {ok}   FAIL {fail}\n{'='*46}")
    sys.exit(1 if fail else 0)

asyncio.run(main())
