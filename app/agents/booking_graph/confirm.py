"""Node `confirm`: chờ khách trả lời đề xuất bằng `interrupt()`, rồi CODE quyết.

Thay node regex cũ (`is_affirmative` + cờ `pending_confirmation` trong
`conversations`). Luồng:

1. Agent booking gọi `propose_appointment` / `cancel_appointment` → đề xuất nằm
   trong DB (`proposals`), state chỉ mang `proposal_id` (ConfirmMiddleware).
2. Câu hỏi xác nhận của agent đi qua guard (middleware) → record như mọi câu khác (stream
   token `respond` bình thường), rồi tới đây: `interrupt(...)`. Lượt chat kết
   thúc với `complete` như A.
3. Lượt sau `run_turn` resume bằng câu khách. Node đọc lại đề xuất từ DB (theo
   chủ), rồi `decide`: lưới code phủ quyết trước, go-ahead, rồi nhãn của bộ
   phân tích đầu lượt (`analyzer.analyze_turn`, Task 12b). Nút bấm (dict)
   không bao giờ gọi bộ phân tích. CHỈ approve
   mới gọi `ProposalService.approve` — cổng ghi duy nhất, kiểm lại mọi thứ.

Không có tool ghi. Code sau `interrupt()` chạy đúng một lần mỗi lần resume; nếu
lượt resume bị huỷ giữa chừng (lượt mới thay), lần resume sau thấy đề xuất đã
không còn pending và coi câu khách là tin mới — `approve` idempotent nên không
bao giờ ghi hai lần.
"""
import asyncio
from datetime import datetime
from typing import Awaitable, Callable, Optional

from langchain_core.messages import AIMessage
from langgraph.types import interrupt
from motor.motor_asyncio import AsyncIOMotorDatabase

from app.agents.booking_graph import analyzer, reply
from app.agents.booking_graph.context import (RECENT_VISITS_HEADER, address_phrase,
                                              declined_line, format_vi_datetime, live_queue,
                                              queue_offer, with_line)
from app.agents.booking_graph.state import GraphState
from app.core.clock import now_utc
from app.core.config import settings
from app.core.logging import get_logger
from app.models.user import User
from app.services.proposal import (PROPOSAL_MAX_AGE_MINUTES,  # noqa: F401 (re-export)
                                   ApprovalResult, ProposalService)

logger = get_logger(__name__)

# Lượt resume trước bị huỷ giữa lúc duyệt: việc duyệt (shield) còn đang chạy.
# Chờ nó xong một chút rồi đọc lại, thay vì coi câu khách là tin mới.
APPROVING_WAIT_SECONDS = 3.0
APPROVING_POLL_SECONDS = 0.1

# Resume dạng nút bấm (UI, Task 5/6): CODE quyết thẳng, không qua `reply.decide`.
# "anh chị" trong hai câu này được thay bằng `address` thật khi dùng (_button_text).
BUTTON_NO_REPLY = "Dạ, anh chị muốn đổi giờ khác hay đổi dịch vụ ạ?"
BUTTON_STALE_REPLY = "Đề xuất lúc nãy hết hạn rồi, anh chị nhắn lại giờ giúp em nhé."


def _sentence_start(phrase: str) -> str:
    """Viết hoa chữ đầu mà KHÔNG hạ chữ tên: str.capitalize() biến "anh Hùng"
    thành "Anh hùng"."""
    return phrase[:1].upper() + phrase[1:]


def _done(address, kind, when=None, note=None, error=None, fallback="", context="first_time"):
    """`confirm_fact` (số liệu, cho phrase viết câu chốt) + `fallback` (câu cứng,
    guard dùng khi LLM viết sai số liệu). `context` (Task 15): mạch câu chốt
    ("first_time"/"returning"/"changed_time"/"after_decline") — `phrase` đọc
    để chọn mẫu/văn phong, thiếu thì coi là lần đầu."""
    return {"confirm_fact": {"kind": kind, "when": when, "note": note,
                             "address": address, "error": error, "context": context},
            "fallback": fallback}


def confirm_fact_for(result: ApprovalResult, address: str, *, context: str = "first_time") -> Optional[dict]:
    """Kết quả `approve` → câu chốt, ĐÚNG các nhánh và chuỗi của node cũ.
    `not_approvable` → None: không có gì để chốt, câu khách là tin mới.

    `context` (Task 15) do chỗ gọi tính sẵn (`_resolve_context`) — hàm này chỉ
    mang nó vào `confirm_fact`, không tự quyết.
    """
    if result.outcome == "not_approvable":
        return None
    kind = result.kind
    if result.outcome == "failed":
        message = result.error or "Có lỗi xảy ra"
        if kind == "cancel":
            return _done(address, "failed", error=message, fallback=f"Dạ {message} ạ.", context=context)
        return _done(address, "failed", error=message, context=context,
                     fallback=f"Dạ {message} ạ. {_sentence_start(address)} chọn giờ khác giúp em nhé.")

    if kind == "cancel":
        when = format_vi_datetime(result.proposal.start_at)
        return _done(address, "cancelled", when=when, context=context,
                     fallback=f"Em hủy lịch {when} cho {address} xong rồi ạ. Cần đặt lại thì cứ nhắn em nhé.")

    appointment = result.appointment
    if appointment is None:
        # approved mà không đọc lại được lịch — không bao giờ nên xảy ra; nói
        # thật là nhầm thay vì báo một giờ không kiểm được.
        logger.warning("bad_proposal_result", extra={
            "proposal_id": str(result.proposal.id) if result.proposal else None})
        return _done(address, "failed", error="em nhầm mất rồi", context=context,
                     fallback=f"Dạ em nhầm mất rồi, {address} nhắc lại ngày giờ giúp em ạ.")
    when = format_vi_datetime(appointment.start_at)
    if kind == "move":
        return _done(address, "moved", when=when, note=appointment.note, context=context,
                     fallback=f"Em dời lịch xong rồi ạ. Hẹn gặp {address} {when} nhé.")
    return _done(address, "booked", when=when, note=appointment.note, context=context,
                 fallback=f"Xong rồi ạ. Hẹn gặp {address} {when} nhé.")


async def _resolve_context(db: AsyncIOMotorDatabase, user: User, proposal, context_block: str) -> str:
    """Task 15 review: mạch câu chốt — ưu tiên `changed_time` > `after_decline`
    > `returning` > `first_time` (controller ruling).

    `changed_time`/`after_decline` suy từ CÁC ĐỀ XUẤT đã đóng trong ngày
    (`ProposalService.closed_earlier_today`), KHÔNG từ chữ trong khối bối
    cảnh: lúc duyệt, đề xuất MỚI NHẤT của khách chính là đề xuất đang duyệt,
    nên dòng "[declined" (review v1 từng dựa vào) không bao giờ có mặt ở đây.
    Có đề xuất cũ đóng CÙNG NGÀY mà GIỜ KHÁC đề xuất vừa duyệt → khách đã đổi
    giờ (`changed_time`); có đề xuất cũ đóng nhưng CÙNG GIỜ → khách từng nói
    không/bỏ dở rồi quay lại chốt y nguyên giờ đó (`after_decline`).

    `returning` vẫn đọc dòng "Khách đã đến:" (hằng số dùng chung
    `RECENT_VISITS_HEADER`, Task 14) đã có sẵn trong khối bối cảnh — không hỏi
    lại DB.

    Một lượt ĐÃ GHI LỊCH THÀNH CÔNG không được hỏng vì lỗi ở ĐÂY (Mongo chập
    chờn...): bắt mọi exception, về "first_time" + log warning thay vì để lỗi
    văng lên và biến một lượt đặt lịch thành công thành lượt lỗi.
    """
    try:
        if proposal is not None:
            earlier = await ProposalService(db).closed_earlier_today(user, proposal.created_at)
            if earlier:
                if any(p.start_at != proposal.start_at for p in earlier):
                    return "changed_time"
                return "after_decline"
        if RECENT_VISITS_HEADER in (context_block or ""):
            return "returning"
    except Exception as exc:
        logger.warning("confirm_context_lookup_failed", extra={"error": str(exc)[:200]})
    return "first_time"


async def execute_proposal(db: AsyncIOMotorDatabase, user: User, proposal_id: str, *,
                           turn_at, context_block: str = "") -> Optional[dict]:
    """Duyệt đề xuất qua cổng ghi của service rồi dựng câu chốt.

    Xưng hô suy từ `full_name` bằng code, không nhận từ model: model quên
    truyền là câu chốt mất lời gọi, mà nó quên thật. `context_block` (Task 15)
    dùng để suy `confirm_fact["context"]`.
    """
    result = await ProposalService(db).approve(user, proposal_id, turn_at=turn_at)
    if result.outcome == "not_approvable":
        return None
    context = await _resolve_context(db, user, result.proposal, context_block)
    return confirm_fact_for(result, address_phrase(user), context=context)


def with_queue_offer(out: dict, queue: list, address: str) -> dict:
    """Chốt xong một lịch (booked/moved/cancelled) mà còn hàng chờ: CODE mời
    lịch kế (guard nối câu này sau câu chốt). Chưa giữ chỗ gì — lượt sau agent
    mới đề xuất. Dùng chung cho cả đường gõ chữ và đường nút bấm — khách bấm
    "có" cũng phải được mời lịch kế như gõ "ừ" (review controller M3-task2)."""
    fact = out["confirm_fact"]
    if not queue or fact.get("kind") not in ("booked", "moved", "cancelled"):
        return out
    return {**out, "confirm_fact": {
        **fact, "then": queue_offer(queue, address),
        # Phrase phải biết các giờ này CHƯA giữ (lỗi live B3 #1).
        "queued_when": [format_vi_datetime(datetime.fromisoformat(q["start_at"]))
                        for q in queue]}}


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


def _button_text(template: str, address: str) -> str:
    """"anh chị" cứng trong hằng số → xưng hô thật của khách."""
    return template.replace("anh chị", address)


def _stale_button_reply(address: str) -> dict:
    """`proposal_id` không khớp, hoặc `approve` không còn duyệt được (hết hạn,
    đã bị đổi ý, ai đó đã bấm trước...): không ghi gì, nói thẳng là đã hết hạn."""
    text = _button_text(BUTTON_STALE_REPLY, address)
    return {"draft": text, "fallback": text, "answer_source": "code",
            "gate_next": "phrase", "proposal_id": None,
            "confirm_fact": {"kind": "clarify", "when": None, "note": None,
                             "address": address, "error": None}}


async def _handle_button(db: AsyncIOMotorDatabase, user: User, state: GraphState,
                         payload: dict, address: str, now: datetime) -> dict:
    """Resume dạng nút bấm: CODE quyết thẳng trên cùng cổng ghi của câu gõ chữ
    (`ProposalService.approve`/`reject`) — không gọi `reply.decide`, không bộ
    phân loại nào chạy. `proposal_id` của nút phải khớp đề xuất đang chờ của
    CHÍNH lượt này; sai thì coi như đề xuất cũ đã hết hạn (bẫy nút bấm từ một
    câu trả lời cũ, hay gửi nhầm đề xuất của người khác).
    """
    proposal_id = state.get("proposal_id")
    if payload.get("proposal_id") != proposal_id:
        return _stale_button_reply(address)

    proposals = ProposalService(db)
    button = payload.get("button")

    if button == "yes":
        # `now`, không `state["asked_at"]`: nút bấm không đi qua `record` (không
        # có lượt mới ghi lại mốc "bây giờ"), nên mốc của CHÍNH cú bấm này mới
        # đúng là lượt sau đề xuất — dùng `asked_at` cũ sẽ luôn là "same_turn".
        out = await execute_proposal(db, user, proposal_id, turn_at=now,
                                     context_block=state.get("context_block", ""))
        if out is None:
            # `approve` không duyệt được (hết hạn/đổi ý/đã bị đóng) — chưa ghi gì.
            return _stale_button_reply(address)
        queue = live_queue(state.get("queued"))
        out = with_queue_offer(out, queue, address)
        return {**out, "proposal_id": None, "gate_next": "phrase", "confirm_source": "button"}

    if button == "no":
        proposal = await proposals.get(user, proposal_id)
        if proposal is None or proposal.status != "pending" or not await proposals.reject(user, proposal_id):
            return _stale_button_reply(address)
        text = _button_text(BUTTON_NO_REPLY, address)
        when = format_vi_datetime(proposal.start_at)
        return {"draft": text, "fallback": text, "answer_source": "code",
                "gate_next": "phrase", "proposal_id": None,
                "confirm_fact": {"kind": "clarify", "when": when, "note": None,
                                 "address": address, "error": None},
                "context_block": with_line(state.get("context_block", ""),
                                           declined_line(proposal))}

    # Giá trị `button` lạ (không phải "yes"/"no"): coi như đề xuất đã hết hạn,
    # không đoán ý — chưa ghi gì.
    return _stale_button_reply(address)


def make_confirm_node(
    db: AsyncIOMotorDatabase, user: User
) -> Callable[[GraphState], Awaitable[dict]]:
    proposals = ProposalService(db)
    address = address_phrase(user)

    async def node(state: GraphState) -> dict:
        proposal_id = state.get("proposal_id")
        if not proposal_id:
            return {"gate_next": "end"}

        # Mọi thứ TRƯỚC interrupt() chạy lại khi resume — chỉ đọc state, không
        # có tác dụng phụ. Câu hỏi là câu đáp cuối đã qua guard (record vừa ghi).
        question = _last_ai_text(state.get("messages"))
        text = interrupt({"proposal_id": proposal_id, "question": question})

        # --- từ đây: lượt SAU, câu khách vừa tới -----------------------------
        if isinstance(text, dict):
            # Resume từ nút bấm (UI, Task 5/6): CODE quyết thẳng, không đụng
            # `reply.decide` — đường chữ bên dưới giữ nguyên như cũ.
            return await _handle_button(db, user, state, text, address, now_utc())

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
                                         turn_at=state.get("asked_at") or now_utc(),
                                         context_block=state.get("context_block", ""))
            if out is not None:
                return {**out, "proposal_id": None, "gate_next": "phrase", "confirm_source": "text"}

        if proposal is None or proposal.status != "pending":
            # Hết hạn, đã duyệt (lượt resume trước bị huỷ sau khi ghi), bị
            # thay, hay không phải của khách này: không ghi gì, câu khách là
            # tin mới — đi lại từ supervisor như A khi cờ pending đã hết hạn.
            status = proposal.status if proposal else "not_found"
            logger.info("proposal_expired" if status == "expired" else "proposal_not_pending",
                        extra={"proposal_id": proposal_id, "status": status})
            out = {"proposal_id": None, "gate_next": "supervisor"}
            if status == "expired":
                # Agent phải biết đề xuất cũ đã hết hạn, chưa ghi gì — không
                # thì "ừ" muộn được hiểu như một lời đặt mới mơ hồ.
                out["context_block"] = with_line(state.get("context_block", ""),
                                                 declined_line(proposal, expired=True))
            return out

        # Lịch đang chờ (đặt lần lượt): lưới và bộ phân loại biết giờ của chúng,
        # để "ừ 9 giờ, còn 2 giờ cho cháu thì sao" không bị chặn chỉ vì giờ chờ.
        queue = live_queue(state.get("queued"))
        facts = {"kind": proposal.kind, "start_at": proposal.start_at,
                 "queued": [q["start_at"] for q in queue]}

        # Nhãn cuối của câu gõ chữ = bộ phân tích đầu lượt (Task 12b), thay
        # `classify_reply`. Chỉ chạy khi lưới phủ quyết VÀ go-ahead đều cho qua
        # (`reply.decide` giữ nguyên thứ tự) — nên tối đa một lời gọi mỗi lượt.
        # Lời gọi đã có đề xuất (`pending=facts`) nên `reply_to_proposal` không
        # bao giờ None sau sanitize; vẫn ép None/lạ → "unclear" ở đây và ở
        # `decide`: không bao giờ ghi khi model không nói rõ approve.
        captured: dict = {}

        async def label_fn(proposal_facts, bot_question, customer_text):
            analysis = await analyzer.analyze_turn(
                [], customer_text, proposal_facts, bot_question=bot_question)
            captured["analysis"] = analysis.model_dump()
            return analysis.reply_to_proposal or "unclear"

        decision = await reply.decide(text, facts, bot_question=question, now=now_utc(),
                                      label_fn=label_fn, veto_mode=settings.confirm_veto_mode)
        # `analysis` chỉ có khi bộ phân tích đã chạy (lưới phủ quyết/go-ahead
        # thì không) — `events` ghi nó vào slots sau `complete`.
        analysed = {"analysis": captured["analysis"]} if captured else {}

        if decision == "approve":
            out = await execute_proposal(db, user, proposal_id,
                                         turn_at=state.get("asked_at") or now_utc(),
                                         context_block=state.get("context_block", ""))
            if out is None:
                # Không duyệt được: về supervisor — nó dùng lại `analysis` này,
                # không gọi bộ phân tích lần hai.
                return {"proposal_id": None, "gate_next": "supervisor", **analysed}
            out = with_queue_offer(out, queue, address)
            return {**out, "proposal_id": None, "gate_next": "phrase", "confirm_source": "text",
                    **analysed}

        if decision == "unclear":
            marked = await proposals.mark_unclear(user, proposal_id)
            if marked is not None and marked.status == "pending":
                # Lần đầu không rõ: hỏi lại MỘT câu, giữ đề xuất. Câu này đi
                # đường câu chốt (agent booking: PhraseMiddleware đặt nguyên
                # văn, GuardMiddleware kiểm, fallback = chính nó) rồi record,
                # và confirm interrupt lại trên cùng đề xuất.
                when = format_vi_datetime(proposal.start_at)
                ask = clarify_question(proposal.kind, when, address)
                logger.info("confirm_unclear", extra={"proposal_id": proposal_id})
                return {"draft": ask, "fallback": ask, "gate_next": "phrase",
                        "confirm_fact": {"kind": "clarify", "when": when, "note": None,
                                         "address": address, "error": None},
                        **analysed}
        elif decision == "change":
            await proposals.supersede(user, proposal_id)
        else:
            await proposals.reject(user, proposal_id)

        # Chưa chốt thì VẪN đang đặt lịch — "khoan để chị xem lại", "thôi 10
        # giờ đi em" là chuyện của booking (có lịch sử và đủ tool).
        #
        # Agent booking KHÔNG thấy tool message của lượt trước (record chỉ lưu
        # câu đáp), nên phải được nói bằng code: đề xuất nào vừa bị từ chối,
        # và chưa ghi gì (lỗi live 3/5/6). Thiếu dòng này nó đề xuất lại ngay,
        # mở lại việc hủy, hoặc mất ngày đang bàn. Lượt sau load_context dựng
        # lại đúng dòng này từ DB.
        logger.info("confirm_declined", extra={"proposal_id": proposal_id, "decision": decision})
        return {"proposal_id": None, "gate_next": "booking",
                "context_block": with_line(state.get("context_block", ""),
                                           declined_line(proposal)),
                **analysed}

    return node


def route_after_confirm(state: GraphState) -> str:
    return state.get("gate_next") or "end"
