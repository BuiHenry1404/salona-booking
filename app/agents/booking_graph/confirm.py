"""Node `confirm`: chờ khách trả lời đề xuất bằng `interrupt()`, rồi CODE quyết.

Thay node regex cũ (`is_affirmative` + cờ `pending_confirmation` trong
`conversations`). Luồng:

1. Agent booking gọi `propose_appointment` / `cancel_appointment` → đề xuất nằm
   trong DB (`proposals`), state chỉ mang `proposal_id` (ConfirmMiddleware).
2. Câu hỏi xác nhận của agent đi qua guard → record như mọi câu khác (stream
   token `respond` bình thường), rồi tới đây: `interrupt(...)`. Lượt chat kết
   thúc với `complete` như A.
3. Lượt sau `run_turn` resume bằng câu khách. Node đọc lại đề xuất từ DB (theo
   chủ), rồi `decide`: lưới code phủ quyết trước, bộ phân loại sau. CHỈ approve
   mới gọi `ProposalService.approve` — cổng ghi duy nhất, kiểm lại mọi thứ.

Không có tool ghi. Code sau `interrupt()` chạy đúng một lần mỗi lần resume; nếu
lượt resume bị huỷ giữa chừng (lượt mới thay), lần resume sau thấy đề xuất đã
không còn pending và coi câu khách là tin mới — `approve` idempotent nên không
bao giờ ghi hai lần.
"""
import asyncio
from typing import Awaitable, Callable, Optional

from langchain_core.messages import AIMessage
from langgraph.types import interrupt
from motor.motor_asyncio import AsyncIOMotorDatabase

from app.agents.booking_graph import reply
from app.agents.booking_graph.context import address_phrase, format_vi_datetime
from app.agents.booking_graph.state import GraphState
from app.core.clock import now_utc
from app.core.logging import get_logger
from app.models.user import User
from app.services.proposal import (PROPOSAL_MAX_AGE_MINUTES,  # noqa: F401 (re-export)
                                   ApprovalResult, ProposalService)

logger = get_logger(__name__)

# Lượt resume trước bị huỷ giữa lúc duyệt: việc duyệt (shield) còn đang chạy.
# Chờ nó xong một chút rồi đọc lại, thay vì coi câu khách là tin mới.
APPROVING_WAIT_SECONDS = 3.0
APPROVING_POLL_SECONDS = 0.1


def _sentence_start(phrase: str) -> str:
    """Viết hoa chữ đầu mà KHÔNG hạ chữ tên: str.capitalize() biến "anh Hùng"
    thành "Anh hùng"."""
    return phrase[:1].upper() + phrase[1:]


def _done(address, kind, when=None, note=None, error=None, fallback=""):
    """`confirm_fact` (số liệu, cho phrase viết câu chốt) + `fallback` (câu cứng,
    guard dùng khi LLM viết sai số liệu)."""
    return {"confirm_fact": {"kind": kind, "when": when, "note": note,
                             "address": address, "error": error},
            "fallback": fallback}


def confirm_fact_for(result: ApprovalResult, address: str) -> Optional[dict]:
    """Kết quả `approve` → câu chốt, ĐÚNG các nhánh và chuỗi của node cũ.
    `not_approvable` → None: không có gì để chốt, câu khách là tin mới."""
    if result.outcome == "not_approvable":
        return None
    kind = result.kind
    if result.outcome == "failed":
        message = result.error or "Có lỗi xảy ra"
        if kind == "cancel":
            return _done(address, "failed", error=message, fallback=f"Dạ {message} ạ.")
        return _done(address, "failed", error=message,
                     fallback=f"Dạ {message} ạ. {_sentence_start(address)} chọn giờ khác giúp em nhé.")

    if kind == "cancel":
        when = format_vi_datetime(result.proposal.start_at)
        return _done(address, "cancelled", when=when,
                     fallback=f"Em hủy lịch {when} cho {address} xong rồi ạ. Cần đặt lại thì cứ nhắn em nhé.")

    appointment = result.appointment
    if appointment is None:
        # approved mà không đọc lại được lịch — không bao giờ nên xảy ra; nói
        # thật là nhầm thay vì báo một giờ không kiểm được.
        logger.warning("bad_proposal_result", extra={
            "proposal_id": str(result.proposal.id) if result.proposal else None})
        return _done(address, "failed", error="em nhầm mất rồi",
                     fallback=f"Dạ em nhầm mất rồi, {address} nhắc lại ngày giờ giúp em ạ.")
    when = format_vi_datetime(appointment.start_at)
    if kind == "move":
        return _done(address, "moved", when=when, note=appointment.note,
                     fallback=f"Em dời lịch xong rồi ạ. Hẹn gặp {address} {when} nhé.")
    return _done(address, "booked", when=when, note=appointment.note,
                 fallback=f"Xong rồi ạ. Hẹn gặp {address} {when} nhé.")


async def execute_proposal(db: AsyncIOMotorDatabase, user: User, proposal_id: str, *,
                           turn_at) -> Optional[dict]:
    """Duyệt đề xuất qua cổng ghi của service rồi dựng câu chốt.

    Xưng hô suy từ `full_name` bằng code, không nhận từ model: model quên
    truyền là câu chốt mất lời gọi, mà nó quên thật.
    """
    result = await ProposalService(db).approve(user, proposal_id, turn_at=turn_at)
    return confirm_fact_for(result, address_phrase(user.full_name))


_CLARIFY = {
    "book": "Dạ {address} chốt giúp em lịch {when} nha, đúng không ạ?",
    "move": "Dạ {address} dời lịch sang {when} nha, đúng không ạ?",
    "cancel": "Dạ {address} hủy lịch {when} nha, đúng không ạ?",
}


def clarify_question(kind: str, when: str, address: str) -> str:
    """MỘT câu hỏi lại, code viết (không LLM): lần đầu khách trả lời không rõ."""
    return _CLARIFY.get(kind, _CLARIFY["book"]).format(address=address, when=when)


def _last_ai_text(messages) -> str:
    for message in reversed(messages or []):
        if isinstance(message, AIMessage):
            return str(message.content or "")
    return ""


def make_confirm_node(
    db: AsyncIOMotorDatabase, user: User
) -> Callable[[GraphState], Awaitable[dict]]:
    proposals = ProposalService(db)
    address = address_phrase(user.full_name)

    async def node(state: GraphState) -> dict:
        proposal_id = state.get("proposal_id")
        if not proposal_id:
            return {"gate_next": "end"}

        # Mọi thứ TRƯỚC interrupt() chạy lại khi resume — chỉ đọc state, không
        # có tác dụng phụ. Câu hỏi là câu đáp cuối đã qua guard (record vừa ghi).
        question = _last_ai_text(state.get("messages"))
        text = interrupt({"proposal_id": proposal_id, "question": question})

        # --- từ đây: lượt SAU, câu khách vừa tới -----------------------------
        proposal = await proposals.get(user, proposal_id)
        waited = 0.0
        while (proposal is not None and proposal.status == "approving"
               and waited < APPROVING_WAIT_SECONDS):
            await asyncio.sleep(APPROVING_POLL_SECONDS)
            waited += APPROVING_POLL_SECONDS
            proposal = await proposals.get(user, proposal_id)
        if proposal is not None and proposal.status == "approving":
            # Việc duyệt (shield) còn chạy quá thời gian chờ: câu khách thành tin
            # mới; đề xuất sẽ tự thành approved (hoặc failed sau 60 giây).
            logger.warning("approval_still_running", extra={"proposal_id": proposal_id,
                                                            "waited": waited})

        if proposal is not None and proposal.status == "approved":
            # Lượt resume trước đã ghi rồi bị huỷ trước khi kịp nói câu chốt
            # (interrupt vẫn treo). Duyệt lại là idempotent — cùng một lịch —
            # nên đi đường chốt như thường: khách nghe xác nhận cho lịch có thật.
            logger.info("proposal_already_approved", extra={"proposal_id": proposal_id})
            out = await execute_proposal(db, user, proposal_id,
                                         turn_at=state.get("asked_at") or now_utc())
            if out is not None:
                return {**out, "proposal_id": None, "gate_next": "phrase"}

        if proposal is None or proposal.status != "pending":
            # Hết hạn, đã duyệt (lượt resume trước bị huỷ sau khi ghi), bị
            # thay, hay không phải của khách này: không ghi gì, câu khách là
            # tin mới — đi lại từ supervisor như A khi cờ pending đã hết hạn.
            status = proposal.status if proposal else "not_found"
            logger.info("proposal_expired" if status == "expired" else "proposal_not_pending",
                        extra={"proposal_id": proposal_id, "status": status})
            return {"proposal_id": None, "gate_next": "supervisor"}

        facts = {"kind": proposal.kind, "start_at": proposal.start_at}
        decision = await reply.decide(text, facts, bot_question=question, now=now_utc())

        if decision == "approve":
            out = await execute_proposal(db, user, proposal_id,
                                         turn_at=state.get("asked_at") or now_utc())
            if out is None:
                return {"proposal_id": None, "gate_next": "supervisor"}
            return {**out, "proposal_id": None, "gate_next": "phrase"}

        if decision == "unclear":
            marked = await proposals.mark_unclear(user, proposal_id)
            if marked is not None and marked.status == "pending":
                # Lần đầu không rõ: hỏi lại MỘT câu, giữ đề xuất. Câu này đi qua
                # guard như câu chốt (fallback = chính nó) rồi record, và
                # confirm interrupt lại trên cùng đề xuất.
                when = format_vi_datetime(proposal.start_at)
                ask = clarify_question(proposal.kind, when, address)
                logger.info("confirm_unclear", extra={"proposal_id": proposal_id})
                return {"draft": ask, "fallback": ask, "gate_next": "guard",
                        "confirm_fact": {"kind": "clarify", "when": when, "note": None,
                                         "address": address, "error": None}}
        elif decision == "change":
            await proposals.supersede(user, proposal_id)
        else:
            await proposals.reject(user, proposal_id)

        # Chưa chốt thì VẪN đang đặt lịch — "khoan để chị xem lại", "thôi 10
        # giờ đi em" là chuyện của booking (có lịch sử và đủ tool).
        logger.info("confirm_declined", extra={"proposal_id": proposal_id, "decision": decision})
        return {"proposal_id": None, "gate_next": "booking"}

    return node


def route_after_confirm(state: GraphState) -> str:
    return state.get("gate_next") or "end"
