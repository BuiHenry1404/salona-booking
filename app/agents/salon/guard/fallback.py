"""Câu mẫu code (spec §7-8, spec plan-guided reply §1): câu của từng hành động
trong kế hoạch câu trả lời (`guard/plan.py::build_plan` dựng câu bằng các mẫu
ở đây; câu của kế hoạch cũng là câu dự phòng khi guard từ chối / lượt chạm
trần / lỗi — `fallback_reply`), và câu báo của lượt nút bấm (`button_reply` —
nút bấm là API bằng code, không gọi model, docs/architech-agent.md luật 3).

Đây là câu tiếng Việt CỐ ĐỊNH trong code, không phải prompt (CONTEXT.md bẫy
17-19 nói về prompt). `{at}` là nhãn giờ ngắn `short_label(moment, today)`
("3 giờ chiều mai"; ngày khác hôm nay / mai giữ `Moment.spoken`). `{what}` là
"{dịch vụ} lúc {at}" khi đề xuất / quyết định có tên dịch vụ, không thì `{at}`
(dời lịch có dịch vụ dùng `DONE_MOVE_SERVICES_REPLY`).
Số điện thoại tiệm chỉ có trong nhánh ghi thất bại; `.env` chưa có số thì
không bịa số.

Không import `guard/facts.py` hay `guard/plan.py` lúc chạy (hai module đó dựng
câu bằng các mẫu ở đây — import ngược lại sẽ thành vòng).
"""
from __future__ import annotations

from datetime import date
from typing import TYPE_CHECKING, Optional, Sequence

from app.agents.salon.guard.catalog import format_price, short_label
from app.agents.salon.shared.schemas import PendingOutcome
from app.agents.salon.shared.text_scan import digits_in, split_sentences

if TYPE_CHECKING:
    from app.agents.salon.guard.facts import ReplyFacts

GENERIC_FALLBACK = "Dạ {address} nói lại giúp em được không ạ?"
BUTTON_STALE_REPLY = "Đề xuất lúc nãy hết hạn rồi, {address} nhắn lại giờ giúp em nhé."

FAILED_REPLY = "{address} ơi, {at} em chưa ghi được, {address} gọi giúp em số {shop_phone} nhé ạ."
# `.env` chưa có số điện thoại tiệm (bắt buộc trước khi chạy thật) — không bịa số.
FAILED_NO_PHONE_REPLY = "Dạ {at} em chưa ghi được, {address} nhắn lại giờ khác giúp em nhé."
FAILED_NEAREST_LEAD = "Dạ {at} vừa có người đặt mất rồi ạ."
# Phần sau của `FAILED_NEAREST_REPLY` — cũng là câu "mời giờ trống" của kế hoạch
# khi lượt gõ chữ duyệt thất bại có `nearest`.
NEAREST_OFFER = "Còn trống {nearest}, {address} nhắn em giờ muốn đặt nhé."
FAILED_NEAREST_REPLY = f"{FAILED_NEAREST_LEAD} {NEAREST_OFFER}"
# R0 thất bại khi lượt này CŨNG thất bại: câu trước đã có số điện thoại.
ALSO_FAILED_REPLY = "Dạ lịch {at} em cũng chưa ghi được ạ."
REJECTED_REPLY = "Dạ vâng, em bỏ lịch {at} rồi ạ. {address} muốn giờ khác thì nhắn em nhé."
# Từ chối một đề xuất HUỶ = lịch vẫn còn nguyên — "em bỏ lịch …" sẽ làm khách
# tưởng lịch đã mất.
REJECTED_CANCEL_REPLY = "Dạ vâng, em giữ nguyên lịch {at} cho {address} ạ."
DONE_REPLY = {
    "book": "Dạ em đã đặt xong {what} cho {address} ạ.",
    "move": "Dạ em đã dời lịch sang {what} cho {address} ạ.",
    "cancel": "Dạ em đã huỷ lịch {what} cho {address} ạ.",
}
# Dời lịch có tên dịch vụ: "dời lịch {dịch vụ} sang {giờ}" — `{what}` ("… lúc
# {giờ}") sau "sang" sẽ thành "dời lịch sang cắt tóc nữ lúc …" (sai nghĩa).
DONE_MOVE_SERVICES_REPLY = "Dạ em đã dời lịch {services} sang {at} cho {address} ạ."
PROPOSAL_REPLY = "Dạ em giữ chỗ {what} cho {address}, {address} đồng ý không ạ?"
# Giờ khách hỏi bị kín trong lượt (mục F3): câu mẫu báo trước rồi mới hỏi.
TAKEN_REPLY = "Dạ {at} đã có khách rồi ạ."
# Phiên hỏi giờ vừa mở có giờ trống (review cuối 11/11): nêu luôn các giờ đó.
TIME_OPTIONS_QUESTION = "Dạ còn trống {options}, {address} chọn giờ nào ạ?"
# Đề xuất huỷ: không có "chỗ" nào để giữ — hỏi khách có đồng ý huỷ không.
CANCEL_PROPOSAL_REPLY = "Dạ em huỷ lịch {at} cho {address} nhé, {address} đồng ý không ạ?"
# Báo giá một dịch vụ khách chọn / nêu; `{price}` = `format_price(low, high)`.
PRICE_REPLY = "Dạ {service} giá {price} ạ."
# Kể các lịch hẹn agent Đặt lịch trả trong lượt; `{times}` = nhãn ngắn nối "và".
APPOINTMENTS_REPLY = "Dạ {address} có lịch hẹn {times} ạ."
# Giờ mở / đóng cửa, đọc bằng `format_vi_hhmm` ("8 giờ sáng").
HOURS_REPLY = "Dạ tiệm mở cửa từ {open} đến {close} ạ."
BUSY_REPLY = "Dạ tiệm đang bận tới {at} ạ."

# Câu hỏi của phiên chờ vừa mở, theo `SessionQuestion.fields[0]` (7 field của
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


def _failed(at: str, *, address: str, shop_phone: Optional[str]) -> str:
    phone = _phone(shop_phone)
    if phone is None:
        return FAILED_NO_PHONE_REPLY.format(at=at, address=address)
    return FAILED_REPLY.format(at=at, address=address, shop_phone=phone)


def _lower_first(name: str) -> str:
    """"Cắt tóc nữ" -> "cắt tóc nữ": tên dịch vụ đứng giữa câu."""
    return name[:1].lower() + name[1:]


def _services(names: Sequence[str]) -> str:
    return " và ".join(_lower_first(name) for name in names)


def _what(at: str, service_names: Sequence[str]) -> str:
    """"{dịch vụ} lúc {giờ}" khi đề xuất / quyết định có tên dịch vụ, không thì giờ."""
    return f"{_services(service_names)} lúc {at}" if service_names else at


def _done(kind: str, at: str, *, address: str, service_names: Sequence[str] = ()) -> str:
    if kind == "move" and service_names:
        return DONE_MOVE_SERVICES_REPLY.format(services=_services(service_names), at=at,
                                               address=address)
    template = DONE_REPLY.get(kind, DONE_REPLY["book"])
    return template.format(what=_what(at, service_names), address=address)


def _proposal(kind: str, at: str, *, address: str, service_names: Sequence[str] = ()) -> str:
    if kind == "cancel":
        return CANCEL_PROPOSAL_REPLY.format(at=at, address=address)
    return PROPOSAL_REPLY.format(what=_what(at, service_names), address=address)


def _released(kind: str, at: str, *, address: str) -> str:
    template = REJECTED_CANCEL_REPLY if kind == "cancel" else REJECTED_REPLY
    return template.format(at=at, address=address)


def _price(name: str, low: int, high: int) -> str:
    return PRICE_REPLY.format(service=_lower_first(name), price=format_price(low, high))


def _spoken_list(labels: Sequence[str], conjunction: str = "hoặc") -> str:
    """"a, b hoặc c" — "hoặc" cho các giờ để khách chọn, "và" cho danh sách
    (các lịch hẹn đang có)."""
    labels = list(labels)
    if len(labels) == 1:
        return labels[0]
    return f"{', '.join(labels[:-1])} {conjunction} {labels[-1]}"


def fallback_reply(facts: "ReplyFacts") -> str:
    """Câu mẫu code của lượt — dựng sẵn trong `build_reply_facts`."""
    return facts.fallback


def button_reply(outcome: PendingOutcome, *, address: str, shop_phone: Optional[str],
                 today: date) -> str:
    """Câu báo của lượt nút bấm Đồng ý / Không — code viết, không gọi model."""
    at = short_label(outcome.at, today)
    if outcome.outcome == "approved":
        return _capitalized(_done(outcome.kind, at, address=address))
    if outcome.outcome == "rejected":
        return _capitalized(_released(outcome.kind, at, address=address))
    if outcome.outcome == "failed" and outcome.nearest and outcome.kind != "cancel":
        nearest = _spoken_list([short_label(moment, today) for moment in outcome.nearest])
        return _capitalized(FAILED_NEAREST_REPLY.format(at=at, nearest=nearest, address=address))
    if outcome.outcome in ("failed", "not_approvable"):
        return _capitalized(_failed(at, address=address, shop_phone=shop_phone))
    return _capitalized(GENERIC_FALLBACK.format(address=address))
