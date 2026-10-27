import asyncio
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
from app.infrastructure.redis import get_redis
from app.models.user import User
from app.services.auth import AuthService
from app.services.dedupe import MessageDedupe
from app.services.rate_limit import RateLimitService

logger = get_logger(__name__)


class SocketIOService:
    """Kênh realtime duy nhất: chat của khách và thông báo cho chủ tiệm."""

    def __init__(self, db: AsyncIOMotorDatabase):
        self.db = db
        self.auth_service = AuthService(db)
        self.rate_limit = RateLimitService(db)
        # Redis tuỳ chọn (get_redis() trả None nếu REDIS_URL không cấu hình
        # hoặc ping lúc lifespan thất bại) — MessageDedupe tự rơi xuống Mongo.
        self.dedupe = MessageDedupe(db, get_redis())
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
                payload = data or {}
                await self.handle_message(
                    sid, user, payload.get("message", ""), payload.get("message_id")
                )

        @self.sio.event
        async def confirm_proposal(sid, data):
            user_id = self.sid_to_user.get(sid)
            if not user_id:
                return
            user = await self.auth_service.get_user_by_id(user_id)
            if user:
                payload = data if isinstance(data, dict) else {}
                await self.handle_button(sid, user, data or {}, payload.get("message_id"))

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

    async def handle_message(
        self, sid: str, user: User, message: str, message_id: Optional[str] = None
    ) -> None:
        """Chạy một lượt chat, đẩy từng sự kiện về đúng socket của khách.

        `message_id` (uuid của client, Task 6) chặn xử lý trùng khi Socket.IO
        gửi lại cùng một tin (client retry/reconnect) — xem `app.services.dedupe`.
        """
        question = (message or "").strip()
        if not question:
            return

        # Dedupe TRƯỚC trần chi phí: một tin gửi lại không được tiêu thêm một
        # dấu hạn mức, nếu không khách bấm gửi lại khi mạng chập chờn sẽ tự
        # khoá hạn mức của chính mình.
        user_id = str(user.id)
        state = await self.dedupe.begin(user_id, message_id)
        if state == "running":
            logger.info("duplicate_in_flight", extra={
                "user_id": user_id, "message_id": message_id,
            })
            return
        if isinstance(state, dict):
            await self.sio.emit("complete", state, room=sid)
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
            # Lượt không chạy — giải phóng khoá "đang chạy" để lần gửi lại
            # SAU (cùng message_id, hết bị chặn hạn mức) không kẹt mãi.
            await self.dedupe.abandon(user_id, message_id)
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
            complete_payload = None
            async for event in run_turn(self.db, user, question):
                if event.type == "complete":
                    complete_payload = event.data
                await self.sio.emit(event.type, event.data, room=sid)
            if complete_payload is not None:
                await self.dedupe.finish(user_id, message_id, complete_payload)
            else:
                await self.dedupe.abandon(user_id, message_id)
        except asyncio.CancelledError:
            # Lượt mới của CÙNG khách đã thay thế lượt này (`_supersede`,
            # khác `message_id`) — giải phóng khoá "đang chạy" của message_id
            # NÀY rồi để CancelledError tiếp tục lan, không nuốt mất nó.
            await self.dedupe.abandon(user_id, message_id)
            raise
        except Exception as exc:
            logger.error("chat_turn_failed", extra={"error": str(exc)})
            await self.dedupe.abandon(user_id, message_id)
            await self.sio.emit(
                "error",
                {"message": "Máy đang bận chút xíu, anh chị nhắn lại giúp em nhé."},
                room=sid,
            )

    async def handle_button(
        self, sid: str, user: User, data: dict, message_id: Optional[str] = None
    ) -> None:
        """Nút Đồng ý/Không trên đề xuất — đường riêng, KHÔNG qua trần chat
        (`check_and_hit`): chi phí LLM của bước này đã tính vào lượt đã sinh ra
        đề xuất, bấm nút không gọi thêm model nào mà bị trừ hạn mức oan.

        `message_id` dedupe như `handle_message` — client có thể gửi lại cùng
        `confirm_proposal` (vd. socket rớt ngay sau khi bấm).
        """
        # `data` có thể không phải dict (list/chuỗi/số) — fallback {} để rơi
        # đúng vào nhánh `error` bên dưới thay vì nổ ở `.get()` (payload sai
        # kiểu cũng là "sai kiểu", không riêng gì `decision` sai).
        payload = data if isinstance(data, dict) else {}
        proposal_id = payload.get("proposal_id")
        decision = payload.get("decision")
        # `decision` sai kiểu (list/dict) không băm được — tra bằng `isinstance`
        # trước để không nổ ở `.get()` thay vì trả `error` gọn gàng.
        label = BUTTON_LABELS.get(decision) if isinstance(decision, str) else None
        if not isinstance(proposal_id, str) or not proposal_id or label is None:
            await self.sio.emit("error", {"message": TURN_ERROR_MESSAGE}, room=sid)
            return

        user_id = str(user.id)
        state = await self.dedupe.begin(user_id, message_id)
        if state == "running":
            logger.info("duplicate_in_flight", extra={
                "user_id": user_id, "message_id": message_id,
            })
            return
        if isinstance(state, dict):
            await self.sio.emit("complete", state, room=sid)
            return

        reply = ButtonReply(decision=decision, proposal_id=proposal_id, label=label)
        try:
            complete_payload = None
            async for event in run_turn(self.db, user, reply):
                if event.type == "complete":
                    complete_payload = event.data
                await self.sio.emit(event.type, event.data, room=sid)
            if complete_payload is not None:
                await self.dedupe.finish(user_id, message_id, complete_payload)
            else:
                await self.dedupe.abandon(user_id, message_id)
        except asyncio.CancelledError:
            await self.dedupe.abandon(user_id, message_id)
            raise
        except Exception as exc:
            logger.error("chat_turn_failed", extra={"error": str(exc)})
            await self.dedupe.abandon(user_id, message_id)
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
