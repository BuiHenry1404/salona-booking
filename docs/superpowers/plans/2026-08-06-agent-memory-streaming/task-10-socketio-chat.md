# Task 10 · Socket.IO chat handler

> Thuộc plan [Agent, memory và streaming](README.md). **Đọc [Ràng buộc toàn cục](README.md#ràng-buộc-toàn-cục) trước khi bắt đầu** — chúng áp cho mọi task, kể cả khi không nhắc lại ở đây.

**Files:**
- Rewrite: `app/services/socketio_service.py`
- Delete: `app/services/chat.py`, `app/api/v1/routers/chat.py`
- Create: `tests/test_socketio_chat.py`
- Modify: `static/socketio_test.html`

**Interfaces:**
- Consumes: `run_turn`, `AgentEvent` (task 9), `AuthService` (Plan 1)
- Produces:
  - `SocketIOService(db).sio` — server Socket.IO
  - Sự kiện client gửi lên: `send_message` với `{"message": str}`
  - Sự kiện server gửi xuống: `turn_started`, `tool_started`, `tool_finished`, `token`, `complete`, `error`, `shop_status_changed`, `appointment_created`, `appointment_cancelled`
  - `SocketIOService.broadcast(event: str, data: dict) -> None`
  - `SocketIOService.emit_to_user(user_id: str, event: str, data: dict) -> None`
  - `SocketIOService.emit_to_admins(event: str, data: dict) -> None`

> Ba sự kiện cuối (`shop_status_changed`, `appointment_created`, `appointment_cancelled`) chỉ được **định nghĩa kênh** ở đây. Chỗ thực sự phát chúng là `SocketNotifier` ở [Plan 3 task 5](../2026-08-06-telegram-bot/task-05-wire-services.md). Task này xong mà chưa có Plan 3 thì kênh vẫn im lặng — đúng như thiết kế, không phải lỗi.

- [ ] **Step 1: Xóa phần chat cũ của template**

```bash
git rm app/services/chat.py app/api/v1/routers/chat.py
```

Trong `app/api/v1/routers/__init__.py`, xóa dòng nhắc `chat`.

- [ ] **Step 2: Viết test (sẽ fail)**

Tạo `tests/test_socketio_chat.py`:

```python
import pytest

from app.agents.booking_graph.events import AgentEvent
from app.services.auth import AuthService
from app.services.socketio_service import SocketIOService

pytestmark = pytest.mark.asyncio


class Recorder:
    """Ghi lại mọi emit thay vì gửi qua mạng."""

    def __init__(self):
        self.sent = []

    async def emit(self, event, data=None, room=None, **kwargs):
        self.sent.append((event, data, room))

    def events(self):
        return [e for e, _, _ in self.sent]


@pytest.fixture
def service(test_db):
    svc = SocketIOService(test_db)
    svc.sio = Recorder()
    return svc


async def test_events_are_forwarded_in_order(service, test_db, monkeypatch):
    user = await AuthService(test_db).create_user("0912345678", "matkhau123", "Cô Lan")

    async def fake_turn(db, u, question):
        yield AgentEvent("turn_started")
        yield AgentEvent("tool_started", {"name": "find_free_slots"})
        yield AgentEvent("tool_finished", {"name": "find_free_slots", "ok": True})
        yield AgentEvent("token", {"text": "Dạ "})
        yield AgentEvent("token", {"text": "được ạ"})
        yield AgentEvent("complete", {"answer": "Dạ được ạ"})

    monkeypatch.setattr("app.services.socketio_service.run_turn", fake_turn)

    await service.handle_message(sid="s1", user=user, message="mai 3h được không")

    assert service.sio.events() == [
        "turn_started", "tool_started", "tool_finished", "token", "token", "complete",
    ]


async def test_every_event_goes_only_to_that_users_socket(service, test_db, monkeypatch):
    user = await AuthService(test_db).create_user("0912345678", "matkhau123", "Cô Lan")

    async def fake_turn(db, u, question):
        yield AgentEvent("token", {"text": "riêng tư"})

    monkeypatch.setattr("app.services.socketio_service.run_turn", fake_turn)
    await service.handle_message(sid="s1", user=user, message="hỏi gì đó")

    assert {room for _, _, room in service.sio.sent} == {"s1"}


async def test_agent_failure_emits_error_not_a_crash(service, test_db, monkeypatch):
    user = await AuthService(test_db).create_user("0912345678", "matkhau123", "Cô Lan")

    async def broken(db, u, question):
        raise RuntimeError("hỏng")
        yield  # pragma: no cover

    monkeypatch.setattr("app.services.socketio_service.run_turn", broken)
    await service.handle_message(sid="s1", user=user, message="gì đó")

    assert "error" in service.sio.events()


async def test_empty_message_is_ignored(service, test_db):
    user = await AuthService(test_db).create_user("0912345678", "matkhau123", "Cô Lan")
    await service.handle_message(sid="s1", user=user, message="   ")
    assert service.sio.events() == []
```

- [ ] **Step 3: Chạy test để xác nhận fail**

Run: `pytest tests/test_socketio_chat.py -v`
Expected: FAIL — `SocketIOService` chưa có `handle_message`

- [ ] **Step 4: Viết lại `app/services/socketio_service.py`**

```python
from typing import Dict, Optional

import socketio
from motor.motor_asyncio import AsyncIOMotorDatabase

from app.agents.booking_graph import run_turn
from app.core.config import settings
from app.core.logging import get_logger
from app.core.security import verify_token
from app.models.user import User
from app.services.auth import AuthService

logger = get_logger(__name__)


class SocketIOService:
    """Kênh realtime duy nhất: chat của khách và thông báo cho chủ tiệm."""

    def __init__(self, db: AsyncIOMotorDatabase):
        self.db = db
        self.auth_service = AuthService(db)
        self.sio = socketio.AsyncServer(
            async_mode="asgi",
            cors_allowed_origins=settings.allowed_origins
            if isinstance(settings.allowed_origins, list)
            else [settings.allowed_origins],
            logger=False,
            engineio_logger=False,
        )
        self.sid_to_user: Dict[str, str] = {}
        self._register_handlers()

    def _register_handlers(self) -> None:
        @self.sio.event
        async def connect(sid, environ, auth):
            user = await self._authenticate(auth)
            if user is None:
                return False
            self.sid_to_user[sid] = str(user.id)
            await self.sio.enter_room(sid, self._room(str(user.id)))
            if user.role == "admin":
                await self.sio.enter_room(sid, "admins")
            await self.sio.emit("connected", {"full_name": user.full_name}, room=sid)
            return True

        @self.sio.event
        async def disconnect(sid):
            self.sid_to_user.pop(sid, None)

        @self.sio.event
        async def send_message(sid, data):
            user_id = self.sid_to_user.get(sid)
            if not user_id:
                return
            user = await self.auth_service.get_user_by_id(user_id)
            if user:
                await self.handle_message(sid, user, (data or {}).get("message", ""))

    async def _authenticate(self, auth) -> Optional[User]:
        if not auth or "token" not in auth:
            return None
        try:
            user_id = verify_token(auth["token"]).get("sub")
        except Exception:
            return None
        if not user_id:
            return None
        user = await self.auth_service.get_user_by_id(user_id)
        return user if user and user.is_active else None

    @staticmethod
    def _room(user_id: str) -> str:
        return f"user_{user_id}"

    async def handle_message(self, sid: str, user: User, message: str) -> None:
        """Chạy một lượt chat, đẩy từng sự kiện về đúng socket của khách."""
        question = (message or "").strip()
        if not question:
            return

        answer = ""
        try:
            async for event in run_turn(self.db, user, question):
                if event.type == "complete":
                    answer = event.data.get("answer", "")
                await self.sio.emit(event.type, event.data, room=sid)
        except Exception as exc:
            logger.error("chat_turn_failed", extra={"error": str(exc)})
            await self.sio.emit(
                "error",
                {"message": "Máy đang bận chút xíu, cô chú nhắn lại giúp con nhé."},
                room=sid,
            )
            return

    async def emit_to_user(self, user_id: str, event: str, data: dict) -> None:
        await self.sio.emit(event, data, room=self._room(user_id))

    async def broadcast(self, event: str, data: dict) -> None:
        """Dùng cho shop_status_changed: mọi khách đang mở app phải thấy thẻ
        trạng thái đổi mà không cần tải lại."""
        await self.sio.emit(event, data)

    async def emit_to_admins(self, event: str, data: dict) -> None:
        await self.sio.emit(event, data, room="admins")

    def get_asgi_app(self):
        """App ASGI để main.py mount vào /socket.io."""
        return socketio.ASGIApp(self.sio)
```

`main.py` dựng service bằng `SocketIOService(db, llm_manager)` — bỏ tham số thứ
hai đi, agent tự lấy model qua `build_chat_model`, không còn dùng `LLMManager`
của template nữa.

- [ ] **Step 5: Cập nhật trang test tay**

Thay phần script trong `static/socketio_test.html` để lắng nghe đúng sáu sự kiện mới:

```html
<script src="https://cdn.socket.io/4.7.5/socket.io.min.js"></script>
<script>
  const token = prompt("Dán JWT vào đây:");
  const socket = io({ auth: { token } });
  const log = (line) => {
    const el = document.createElement("div");
    el.textContent = line;
    document.body.appendChild(el);
  };

  socket.on("connected", (d) => log(`● đã kết nối: ${d.full_name}`));
  socket.on("turn_started", () => log("… con đang đọc"));
  socket.on("tool_started", (d) => log(`… đang chạy ${d.name}`));
  socket.on("tool_finished", (d) => log(`✓ xong ${d.name} (ok=${d.ok})`));
  socket.on("token", (d) => log(`token: ${JSON.stringify(d.text)}`));
  socket.on("complete", (d) => log(`■ ${d.answer}`));
  socket.on("error", (d) => log(`✗ ${d.message}`));
  socket.on("shop_status_changed", (d) => log(`⟳ trạng thái tiệm: ${JSON.stringify(d)}`));

  window.say = (text) => socket.emit("send_message", { message: text });
  log('Gõ say("mai 3h chiều làm tóc được không con") trong console');
</script>
```

- [ ] **Step 6: Chạy test để xác nhận pass**

Run: `pytest tests/test_socketio_chat.py -v`
Expected: PASS (5 passed)

- [ ] **Step 7: Chạy toàn bộ test**

Run: `pytest -v`
Expected: PASS toàn bộ

- [ ] **Step 8: Kiểm tay đầu-cuối**

```bash
docker compose up -d mongo
uvicorn main:app --reload
```

Lấy JWT bằng `POST /api/v1/auth/login`, mở `http://localhost:8000/static/socketio_test.html`, dán token, rồi trong console gõ:

```js
say("mai 3h chiều làm tóc được không con")
```

Phải thấy lần lượt `turn_started` → `tool_started` → `tool_finished` → nhiều `token` → `complete`. Sau đó gõ `say("cháu bán bảo hiểm không")` — phải bị từ chối lịch sự và **không** có `tool_started` nào.

- [ ] **Step 9: Commit**

```bash
git add -A
git commit -m "feat: stream agent turns and tool progress over Socket.IO"
```
