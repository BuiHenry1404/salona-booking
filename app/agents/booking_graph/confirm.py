import re
from datetime import datetime
from typing import Awaitable, Callable

from motor.motor_asyncio import AsyncIOMotorDatabase

from app.agents.booking_graph.context import format_vi_datetime
from app.agents.booking_graph.state import GraphState
from app.core.errors import AppError
from app.core.logging import get_logger
from app.models.user import User
from app.services.appointment import AppointmentService
from app.services.conversation import ConversationService

logger = get_logger(__name__)

_YES = re.compile(
    r"\b(ừ|ừa|uh|um|ok|okay|okie|vâng|dạ|đúng|phải|được|duoc|dc|chuẩn|nhất trí|đồng ý)\b",
    re.IGNORECASE,
)
_NO = re.compile(
    r"\b(không|khong|ko|thôi|thoi|khỏi|đổi|doi|khác|khac|chưa|chua|hủy|huy)\b",
    re.IGNORECASE,
)


def is_affirmative(text: str) -> bool:
    """Phủ định thắng khẳng định: "dạ không" và "đúng rồi nhưng đổi giờ" đều phải
    ra False. Sai hướng này chỉ mất một câu hỏi lại; sai hướng kia là đặt nhầm lịch.
    """
    cleaned = (text or "").strip()
    if _NO.search(cleaned):
        return False
    return bool(_YES.search(cleaned))


def make_confirm_node(
    db: AsyncIOMotorDatabase, user: User
) -> Callable[[GraphState], Awaitable[dict]]:
    """Nhánh tắt cho câu trả lời xác nhận.

    Khách nói "ừ" sau khi AI đã hỏi "3h chiều Thứ Năm đúng không cô?" thì đi thẳng
    vào thực thi — không quay lại supervisor. Tiết kiệm một lượt LLM và loại bỏ
    hẳn vòng lặp hỏi xác nhận đi xác nhận lại.
    """
    service = AppointmentService(db)
    conversations = ConversationService(db)

    async def node(state: GraphState) -> dict:
        user_id = str(user.id)
        pending = state.get("pending_confirmation") or {}
        last_message = state["messages"][-1].content if state["messages"] else ""

        await conversations.set_pending(user_id, None)

        if not is_affirmative(last_message):
            return {"answer": "Dạ vâng, vậy cô chú muốn đặt ngày giờ nào ạ?"}

        try:
            appointment = await service.create(
                user, datetime.fromisoformat(pending["start_at"]), pending.get("note")
            )
        except AppError as exc:
            return {"answer": f"Dạ {exc.message} ạ. Cô chú chọn giờ khác giúp con nhé."}
        except (KeyError, ValueError):
            logger.warning("bad_pending_payload", extra={"payload": str(pending)[:120]})
            return {"answer": "Dạ con nhầm mất rồi, cô chú nhắc lại ngày giờ giúp con ạ."}

        return {"answer": f"Xong rồi ạ. Hẹn gặp cô chú {format_vi_datetime(appointment.start_at)} nhé."}

    return node
