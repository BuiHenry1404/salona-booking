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
# Đang chốt HỦY thì "hủy" là đồng ý, không phải phủ định: "ừ hủy đi em".
_NO_WHEN_CANCELLING = re.compile(
    r"\b(không|thôi|khỏi|đổi|khác|chưa)\b",
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
_NO_BARE_WHEN_CANCELLING = re.compile(
    r"\b(khong|ko|thoi|khoi|doi|khac|chua)\b",
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


def _sentence_start(phrase: str) -> str:
    """Viết hoa chữ đầu mà KHÔNG hạ chữ tên: str.capitalize() biến "anh Hùng"
    thành "Anh hùng"."""
    return phrase[:1].upper() + phrase[1:]


def is_affirmative(text: str, cancelling: bool = False) -> bool:
    """Phủ định thắng khẳng định: "dạ không" và "đúng rồi nhưng đổi giờ" đều phải
    ra False. Sai hướng này chỉ mất một câu hỏi lại; sai hướng kia là đặt nhầm lịch.

    `cancelling`: câu hỏi đang chờ là "hủy lịch này đúng không?" — khi đó "hủy"
    trong "ừ hủy đi" là đồng ý, không phải từ chối. Một bộ từ cho cả hai ngữ
    cảnh là "ừ hủy đi em" bị đẩy về booking mãi và khách không hủy được.
    """
    cleaned = (text or "").strip()
    bare = strip_diacritics(cleaned)
    if bare == cleaned:
        # Khách gõ không dấu — nới luật ra.
        target, yes = bare, _YES_BARE
        no = _NO_BARE_WHEN_CANCELLING if cancelling else _NO_BARE
    else:
        # Khách có bỏ dấu — tin đúng cái họ gõ, không suy diễn thêm.
        target, yes = cleaned, _YES
        no = _NO_WHEN_CANCELLING if cancelling else _NO
    if no.search(target):
        return False
    if cancelling and re.search(r"\b(hủy|huy)\b", target, re.IGNORECASE):
        return True
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

        cancelling = pending.get("cancel_appointment_id")
        if not is_affirmative(last_message, cancelling=bool(cancelling)):
            # Khách chưa chốt thì VẪN đang đặt lịch — "khoan để chị xem lại",
            # "thôi 10 giờ đi em", "đổi sang thứ Năm" đều là chuyện của
            # booking. Node này không có LLM nên trả lời cứng chỗ nào cũng
            # trật; đẩy sang agent có lịch sử và đủ tool.
            return {"route": "booking"}

        if cancelling:
            # Hủy cũng đi qua đây, không hủy ngay trong tool: ràng buộc "hủy
            # phải qua một bước xác nhận" áp cho cả chat, không riêng giao diện.
            try:
                await service.cancel(user, cancelling)
            except AppError as exc:
                return {"answer": f"Dạ {exc.message} ạ."}
            try:
                when = format_vi_datetime(datetime.fromisoformat(pending["start_at"]))
            except (KeyError, ValueError):
                return {"answer": f"Em hủy lịch xong rồi ạ, {address} cần gì cứ nhắn em nhé."}
            return {"answer": f"Em hủy lịch {when} cho {address} xong rồi ạ. "
                              "Cần đặt lại thì cứ nhắn em nhé."}

        # Có `replaces_appointment_id` là khách đang DỜI lịch: đi qua
        # `reschedule` để lịch cũ được hủy trong cùng một bước. Đi qua `create`
        # ở đây là ra hai lịch (BUG-1).
        replaces = pending.get("replaces_appointment_id")
        try:
            start = datetime.fromisoformat(pending["start_at"])
            if replaces:
                appointment = await service.reschedule(user, replaces, start, pending.get("note"))
            else:
                appointment = await service.create(user, start, pending.get("note"))
        except AppError as exc:
            return {"answer": f"Dạ {exc.message} ạ. {_sentence_start(address)} chọn giờ khác giúp em nhé."}
        except (KeyError, ValueError):
            logger.warning("bad_pending_payload", extra={"payload": str(pending)[:120]})
            return {"answer": f"Dạ em nhầm mất rồi, {address} nhắc lại ngày giờ giúp em ạ."}

        when = format_vi_datetime(appointment.start_at)
        if replaces:
            return {"answer": f"Em dời lịch xong rồi ạ. Hẹn gặp {address} {when} nhé."}
        return {"answer": f"Xong rồi ạ. Hẹn gặp {address} {when} nhé."}

    return node


def route_after_confirm(state: GraphState) -> str:
    """Sau confirm: đã ghi lịch (có `answer`) thì xong; chưa chốt thì sang booking."""
    return "booking" if state.get("route") == "booking" else "end"
