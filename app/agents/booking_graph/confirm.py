import re
import unicodedata
from datetime import datetime
from typing import Awaitable, Callable

from motor.motor_asyncio import AsyncIOMotorDatabase

from app.agents.booking_graph.context import address_phrase, format_vi_datetime
from app.agents.booking_graph.state import GraphState
from app.core.errors import AppError
from app.core.logging import get_logger
from app.models.user import User
from app.services.appointment import AppointmentService
from app.services.conversation import ConversationService

logger = get_logger(__name__)

# Hai bộ luật, chọn theo cách khách gõ.
#
# Có dấu thì so khớp CHÍNH XÁC: "đúng" là đồng ý, "dùng" thì không — bỏ dấu cả
# hai đều thành "dung" nên "cho cô dùng thử" sẽ bị hiểu nhầm là chốt lịch.
_YES = re.compile(
    r"\b(ừ|ừa|ờ|uh|uhm|um|ok|okay|okie|vâng|dạ|đúng|phải|được|chuẩn"
    r"|nhất trí|đồng ý|chính xác)\b",
    re.IGNORECASE,
)
_NO = re.compile(
    r"\b(không|thôi|khỏi|đổi|khác|chưa|hủy)\b",
    re.IGNORECASE,
)

# Không dấu thì nới ra. Khách lớn tuổi gõ điện thoại phần lớn không bỏ dấu;
# bắt họ gõ lại thì lần sau vẫn không dấu.
_YES_BARE = re.compile(
    r"\b(u|ua|o|oa|uh|uhm|um|ok|okay|okie|vang|da|dung|phai|duoc|dc|chuan"
    r"|nhat tri|dong y|chinh xac)\b",
    re.IGNORECASE,
)
_NO_BARE = re.compile(
    r"\b(khong|ko|thoi|khoi|doi|khac|chua|huy)\b",
    re.IGNORECASE,
)


def strip_diacritics(text: str) -> str:
    """Bỏ dấu tiếng Việt. 'đúng rồi' và 'dung roi' phải cùng ra một chuỗi.

    NFD tách dấu thành ký tự riêng để lọc, nhưng đ/Đ không phải d kèm dấu — nó
    là một ký tự độc lập nên phải thay tay.
    """
    text = (text or "").replace("đ", "d").replace("Đ", "D")
    return "".join(
        c for c in unicodedata.normalize("NFD", text)
        if unicodedata.category(c) != "Mn"
    )


def is_affirmative(text: str) -> bool:
    """Phủ định thắng khẳng định: "dạ không" và "đúng rồi nhưng đổi giờ" đều phải
    ra False. Sai hướng này chỉ mất một câu hỏi lại; sai hướng kia là đặt nhầm lịch.
    """
    cleaned = (text or "").strip()
    bare = strip_diacritics(cleaned)
    if bare == cleaned:
        # Khách gõ không dấu — nới luật ra.
        target, yes, no = bare, _YES_BARE, _NO_BARE
    else:
        # Khách có bỏ dấu — tin đúng cái họ gõ, không suy diễn thêm.
        target, yes, no = cleaned, _YES, _NO
    if no.search(target):
        return False
    return bool(yes.search(target))


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

        # Suy từ full_name bằng code, không nhận từ model nữa: model quên
        # truyền là câu chốt mất lời gọi, mà nó quên thật — đó là lý do
        # hard rule 6 từng tồn tại.
        address = address_phrase(user.full_name)

        if not is_affirmative(last_message):
            # Khách chưa chốt thì VẪN đang đặt lịch — "khoan để chị xem lại",
            # "thôi 10 giờ đi em", "đổi sang thứ Năm" đều là chuyện của
            # booking. Node này không có LLM nên trả lời cứng chỗ nào cũng
            # trật; đẩy sang agent có lịch sử và đủ tool.
            return {"route": "booking"}

        try:
            appointment = await service.create(
                user, datetime.fromisoformat(pending["start_at"]), pending.get("note")
            )
        except AppError as exc:
            return {"answer": f"Dạ {exc.message} ạ. {address.capitalize()} chọn giờ khác giúp em nhé."}
        except (KeyError, ValueError):
            logger.warning("bad_pending_payload", extra={"payload": str(pending)[:120]})
            return {"answer": f"Dạ em nhầm mất rồi, {address} nhắc lại ngày giờ giúp em ạ."}

        return {"answer": f"Xong rồi ạ. Hẹn gặp {address} "
                          f"{format_vi_datetime(appointment.start_at)} nhé."}

    return node


def route_after_confirm(state: GraphState) -> str:
    """Sau confirm: đã ghi lịch (có `answer`) thì xong; chưa chốt thì sang booking."""
    return "booking" if state.get("route") == "booking" else "end"
