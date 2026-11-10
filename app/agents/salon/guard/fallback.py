"""Câu mẫu code (spec §7-8): câu gửi khách khi guard từ chối hai lần, khi lượt
chạm trần / lỗi sau khi đã có việc chạy được (`fallback_reply`), và câu báo
của lượt nút bấm (`button_reply` — nút bấm là API bằng code, không gọi model,
docs/architech-agent.md luật 3).

Đây là câu tiếng Việt CỐ ĐỊNH trong code, không phải prompt (CONTEXT.md bẫy
17-19 nói về prompt). `{at}` luôn là `Moment.spoken` nguyên văn. Số điện thoại
tiệm chỉ có trong nhánh ghi thất bại; `.env` chưa có số thì không bịa số.

Không import `guard/facts.py` lúc chạy (facts dựng câu mẫu bằng
`compose_fallback_asking` ở đây — import ngược lại sẽ thành vòng).
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Optional

from app.agents.salon.shared.schemas import Moment, PendingOutcome, UnannouncedDecision
from app.agents.salon.shared.text_scan import digits_in, split_sentences

if TYPE_CHECKING:
    from app.agents.salon.guard.facts import ReplyFacts

GENERIC_FALLBACK = "Dạ {address} nói lại giúp em được không ạ?"
BUTTON_STALE_REPLY = "Đề xuất lúc nãy hết hạn rồi, {address} nhắn lại giờ giúp em nhé."

FAILED_REPLY = "{address} ơi, {at} em chưa ghi được, {address} gọi giúp em số {shop_phone} nhé ạ."
# `.env` chưa có số điện thoại tiệm (bắt buộc trước khi chạy thật) — không bịa số.
FAILED_NO_PHONE_REPLY = "Dạ {at} em chưa ghi được, {address} nhắn lại giờ khác giúp em nhé."
FAILED_NEAREST_REPLY = (
    "Dạ {at} vừa có người đặt mất rồi ạ. Còn trống {nearest}, {address} nhắn em giờ muốn đặt nhé."
)
# R0 thất bại khi lượt này CŨNG thất bại: câu trước đã có số điện thoại.
ALSO_FAILED_REPLY = "Dạ lịch {at} em cũng chưa ghi được ạ."
REJECTED_REPLY = "Dạ vâng, em bỏ lịch {at} rồi ạ. {address} muốn giờ khác thì nhắn em nhé."
# Từ chối một đề xuất HUỶ = lịch vẫn còn nguyên — "em bỏ lịch …" sẽ làm khách
# tưởng lịch đã mất.
REJECTED_CANCEL_REPLY = "Dạ vâng, em giữ nguyên lịch {at} cho {address} ạ."
DONE_REPLY = {
    "book": "Dạ em đã đặt xong {at} cho {address} ạ.",
    "move": "Dạ em đã dời lịch sang {at} cho {address} ạ.",
    "cancel": "Dạ em đã huỷ lịch {at} cho {address} ạ.",
}
PROPOSAL_REPLY = "Dạ em giữ chỗ {at} cho {address}, {address} đồng ý không ạ?"
# Giờ khách hỏi bị kín trong lượt (mục F3): câu mẫu báo trước rồi mới hỏi.
TAKEN_REPLY = "Dạ {at} đã có khách rồi ạ."
# Đề xuất huỷ: không có "chỗ" nào để giữ — hỏi khách có đồng ý huỷ không.
CANCEL_PROPOSAL_REPLY = "Dạ em huỷ lịch {at} cho {address} nhé, {address} đồng ý không ạ?"

# Câu hỏi của phiên chờ vừa mở, theo `SessionQuestion.field` (7 field của
# `shared/schemas.py::QuestionField`). Field lạ -> `GENERIC_FALLBACK`.
FIELD_QUESTION = {
    "day": "Dạ {address} muốn ngày nào ạ?",
    "time": "Dạ {address} muốn mấy giờ ạ?",
    "part_of_day": "Dạ {address} muốn buổi sáng, buổi chiều hay buổi tối ạ?",
    "service": "Dạ {address} muốn làm dịch vụ gì ạ?",
    "which_appointment": "Dạ {address} nói giúp em là lịch hẹn nào ạ?",
    "week": "Dạ {address} muốn tuần này hay tuần sau ạ?",
    "confirm": "Dạ {address} xác nhận giúp em được không ạ?",
}


def _capitalized(text: str) -> str:
    """Viết hoa chữ đầu mỗi câu ("{address} ơi" -> "Chị Lan ơi")."""
    return " ".join(sentence[:1].upper() + sentence[1:] for sentence in split_sentences(text))


def _phone(shop_phone: Optional[str]) -> Optional[str]:
    return shop_phone if shop_phone and digits_in(shop_phone) else None


def _failed(at: Moment, *, address: str, shop_phone: Optional[str]) -> str:
    phone = _phone(shop_phone)
    if phone is None:
        return FAILED_NO_PHONE_REPLY.format(at=at.spoken, address=address)
    return FAILED_REPLY.format(at=at.spoken, address=address, shop_phone=phone)


def _done(kind: str, at: Moment, *, address: str) -> str:
    return DONE_REPLY.get(kind, DONE_REPLY["book"]).format(at=at.spoken, address=address)


def _proposal(kind: str, at: Moment, *, address: str) -> str:
    template = CANCEL_PROPOSAL_REPLY if kind == "cancel" else PROPOSAL_REPLY
    return template.format(at=at.spoken, address=address)


def _spoken_list(moments: list[Moment]) -> str:
    spoken = [moment.spoken for moment in moments]
    if len(spoken) == 1:
        return spoken[0]
    return f"{', '.join(spoken[:-1])} hoặc {spoken[-1]}"


def compose_fallback_asking(
    *, address: str, shop_phone: Optional[str], failed_at: Optional[Moment],
    approved: Optional[tuple[str, Moment]], proposal: Optional[tuple[str, Moment]],
    announce: Optional[UnannouncedDecision], opened_field: Optional[str],
    taken_at: Optional[Moment] = None,
) -> tuple[str, bool]:
    """(câu mẫu, câu đó có hỏi câu hỏi của phiên vừa mở không). Phiên được hỏi
    thì caller giữ nó mở (Ruling R12) — khách đã nghe câu hỏi của nó.

    Câu mẫu của lượt, theo thứ tự: ghi thất bại (của lượt, hoặc R0 thất
    bại — cả hai kèm số điện thoại tiệm) -> duyệt xong -> đề xuất đang giữ ->
    R0 đã duyệt -> phiên vừa mở -> câu chung. Lượt có giờ bị kín (`taken_at`):
    bốn nhánh sau cùng báo giờ kín trước (mục F3); nhánh thất bại / duyệt xong
    giữ nguyên.

    Ghi thất bại / duyệt xong mà lượt còn có đề xuất mới: nối thêm câu hỏi
    đề xuất — nút Đồng ý/Không hiện ra thì câu phải có câu hỏi đi kèm.
    `failed_at` / `approved` là của lượt GÕ CHỮ (lượt nút bấm đã có câu báo
    của `button_reply`, caller truyền `None`)."""
    r0_failed = announce is not None and announce.outcome == "failed"
    parts: list[str] = []
    if failed_at is not None:
        parts.append(_failed(failed_at, address=address, shop_phone=shop_phone))
        if r0_failed:
            parts.append(ALSO_FAILED_REPLY.format(at=announce.at.spoken))
    elif r0_failed:
        parts.append(_failed(announce.at, address=address, shop_phone=shop_phone))
    if approved is not None:
        parts.append(_done(approved[0], approved[1], address=address))
    if parts:
        if proposal is not None:
            parts.append(_proposal(proposal[0], proposal[1], address=address))
        return _capitalized(" ".join(parts)), False
    lead = [TAKEN_REPLY.format(at=taken_at.spoken)] if taken_at is not None else []

    def _led(text: str) -> str:
        return _capitalized(" ".join([*lead, text]))

    if proposal is not None:
        return _led(_proposal(proposal[0], proposal[1], address=address)), False
    if announce is not None:
        return _led(_done(announce.proposal_kind, announce.at, address=address)), False
    if opened_field in FIELD_QUESTION:
        return _led(FIELD_QUESTION[opened_field].format(address=address)), True
    return _led(GENERIC_FALLBACK.format(address=address)), False


def fallback_reply(facts: "ReplyFacts") -> str:
    """Câu mẫu code của lượt — dựng sẵn trong `build_reply_facts`."""
    return facts.fallback


def button_reply(outcome: PendingOutcome, *, address: str, shop_phone: Optional[str]) -> str:
    """Câu báo của lượt nút bấm Đồng ý / Không — code viết, không gọi model."""
    at = outcome.at
    if outcome.outcome == "approved":
        return _capitalized(_done(outcome.kind, at, address=address))
    if outcome.outcome == "rejected":
        template = REJECTED_CANCEL_REPLY if outcome.kind == "cancel" else REJECTED_REPLY
        return _capitalized(template.format(at=at.spoken, address=address))
    if outcome.outcome == "failed" and outcome.nearest and outcome.kind != "cancel":
        return _capitalized(FAILED_NEAREST_REPLY.format(
            at=at.spoken, nearest=_spoken_list(outcome.nearest), address=address))
    if outcome.outcome in ("failed", "not_approvable"):
        return _capitalized(_failed(at, address=address, shop_phone=shop_phone))
    return _capitalized(GENERIC_FALLBACK.format(address=address))
