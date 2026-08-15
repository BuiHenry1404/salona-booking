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

        try:
            async for event in run_turn(self.db, user, question):
                await self.sio.emit(event.type, event.data, room=sid)
        except Exception as exc:
            logger.error("chat_turn_failed", extra={"error": str(exc)})
            await self.sio.emit(
                "error",
                {"message": "Máy đang bận chút xíu, cô chú nhắn lại giúp con nhé."},
                room=sid,
            )

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
