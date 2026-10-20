from typing import Dict, Optional

import socketio
from motor.motor_asyncio import AsyncIOMotorDatabase

from app.agents.booking_graph import run_turn
from app.agents.booking_graph.button import BUTTON_LABELS, ButtonReply
from app.agents.booking_graph.events import TURN_ERROR_MESSAGE
from app.core.config import settings
from app.core.logging import get_logger
from app.core.errors import RateLimitedError
from app.core.security import verify_token
from app.models.user import User
from app.services.auth import AuthService
from app.services.rate_limit import RateLimitService

logger = get_logger(__name__)


class SocketIOService:
    """Kênh realtime duy nhất: chat của khách và thông báo cho chủ tiệm."""

    def __init__(self, db: AsyncIOMotorDatabase):
        self.db = db
        self.auth_service = AuthService(db)
        self.rate_limit = RateLimitService(db)
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

        @self.sio.event
        async def confirm_proposal(sid, data):
            user_id = self.sid_to_user.get(sid)
            if not user_id:
                return
            user = await self.auth_service.get_user_by_id(user_id)
            if user:
                await self.handle_button(sid, user, data or {})

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

    @staticmethod
    def _too_fast_message(retry_after_seconds: int) -> str:
        minutes = max(round(retry_after_seconds / 60), 1)
        # "khoảng 60 phút" nghe lủng củng; người ta nói "một tiếng".
        when = "1 tiếng" if minutes >= 60 else f"{minutes} phút"
        return (
            f"Anh chị nhắn hơi nhanh, khoảng {when} nữa nhắn lại giúp em nhé. "
            "Gấp thì anh chị gọi thẳng cho tiệm ạ."
        )

    async def handle_message(self, sid: str, user: User, message: str) -> None:
        """Chạy một lượt chat, đẩy từng sự kiện về đúng socket của khách."""
        question = (message or "").strip()
        if not question:
            return

        # Trần chi phí LLM. Đặt SAU nhánh tin rỗng: gõ hụt rồi bấm Gửi không
        # được tiêu hạn mức. Khoá theo user chứ không theo sid — mở thêm tab là
        # có sid mới, tính theo sid thì nhân đôi hạn mức bằng một cú Ctrl+T.
        key = f"chat:user:{user.id}"
        try:
            await self.rate_limit.check_and_hit(
                key, settings.chat_max_per_hour, 3600
            )
        except RateLimitedError:
            retry_after = await self.rate_limit.retry_after_seconds(
                key, settings.chat_max_per_hour, 3600
            )
            await self.sio.emit(
                "error",
                {
                    "message": self._too_fast_message(retry_after),
                    "retry_after_seconds": retry_after,
                },
                room=sid,
            )
            return

        try:
            async for event in run_turn(self.db, user, question):
                await self.sio.emit(event.type, event.data, room=sid)
        except Exception as exc:
            logger.error("chat_turn_failed", extra={"error": str(exc)})
            await self.sio.emit(
                "error",
                {"message": "Máy đang bận chút xíu, anh chị nhắn lại giúp em nhé."},
                room=sid,
            )

    async def handle_button(self, sid: str, user: User, data: dict) -> None:
        """Nút Đồng ý/Không trên đề xuất — đường riêng, KHÔNG qua trần chat
        (`check_and_hit`): chi phí LLM của bước này đã tính vào lượt đã sinh ra
        đề xuất, bấm nút không gọi thêm model nào mà bị trừ hạn mức oan.
        """
        payload = data or {}
        proposal_id = payload.get("proposal_id")
        decision = payload.get("decision")
        # `decision` sai kiểu (list/dict) không băm được — tra bằng `isinstance`
        # trước để không nổ ở `.get()` thay vì trả `error` gọn gàng.
        label = BUTTON_LABELS.get(decision) if isinstance(decision, str) else None
        if not isinstance(proposal_id, str) or not proposal_id or label is None:
            await self.sio.emit("error", {"message": TURN_ERROR_MESSAGE}, room=sid)
            return

        reply = ButtonReply(decision=decision, proposal_id=proposal_id, label=label)
        try:
            async for event in run_turn(self.db, user, reply):
                await self.sio.emit(event.type, event.data, room=sid)
        except Exception as exc:
            logger.error("chat_turn_failed", extra={"error": str(exc)})
            await self.sio.emit(
                "error",
                {"message": "Máy đang bận chút xíu, anh chị nhắn lại giúp em nhé."},
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
        """App ASGI để main.py mount vào /socket.io.

        `socketio_path=""` là bắt buộc. Mặc định ASGIApp tự phục vụ dưới tiền tố
        "socket.io"; mount nó vào /socket.io nữa thì địa chỉ thật thành
        /socket.io/socket.io/ và mọi client gọi `io()` (đường mặc định
        /socket.io/) đều nhận 404.
        """
        return socketio.ASGIApp(self.sio, socketio_path="")
