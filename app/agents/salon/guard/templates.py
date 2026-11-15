"""Câu mẫu tiếng Việt (spec guard rebuild, mục "Câu mẫu (templates.py)"): các
câu CỐ ĐỊNH trong code, không phải prompt. `compose(plan, address)` là câu trả
lời khi model thất bại hai lần (`taken_sentence` + `body`, hoặc
`GENERIC_FALLBACK` khi kế hoạch rỗng); `taken_sentence` là câu báo giờ kín
code tự biết (model không nhắc); `button_reply` là câu của lượt bấm nút
(không gọi model).

Giữ nguyên giọng các câu mẫu cũ (`guard/fallback.py`, đã xoá ở Task 8). Mọi
câu `taken_sentence` / `body` / `compose` / `button_reply` trả đều viết hoa
chữ đầu mỗi câu (`{address}` có thể mở câu: "Chị Lan ơi, …").

`{at}` / các nhãn giờ trong `body` là nhãn nói đã có sẵn trong trường của
`PlanAct` (`short_label`, hoặc `Moment.spoken` với ngày khác hôm nay / mai) —
module này không tự tính nhãn giờ cho `body`, chỉ cho `button_reply` (đọc
thẳng `PendingOutcome`, không qua kế hoạch).

Chỉ import `results.py` và `planner.py` trong `guard/`.
"""
from __future__ import annotations

import unicodedata
from datetime import date
from typing import Optional, Sequence

from app.agents.salon.guard.planner import PlanAct, PlanAsk, ReplyPlan
from app.agents.salon.guard.results import short_label
from app.agents.salon.shared.schemas import PendingOutcome
from app.agents.salon.shared.text_scan import digits_in, split_sentences

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
# Báo giá một dịch vụ khách chọn / nêu; `{price}` đã là chuỗi nói (`spoken_price`).
PRICE_REPLY = "Dạ {service} giá {price} ạ."
# Kể các lịch hẹn agent Đặt lịch trả trong lượt; `{times}` = nhãn ngắn nối "và".
APPOINTMENTS_REPLY = "Dạ {address} có lịch hẹn {times} ạ."
# Giờ mở / đóng cửa, đọc bằng `format_vi_hhmm` ("8 giờ sáng").
HOURS_REPLY = "Dạ tiệm mở cửa từ {open} đến {close} ạ."
BUSY_REPLY = "Dạ tiệm đang bận tới {at} ạ."

# Câu hỏi của phiên chờ vừa mở, theo từng `QuestionField` riêng lẻ. Field lạ ->
# `GENERIC_FALLBACK`.
FIELD_QUESTION = {
    "day": "Dạ {address} muốn ngày nào ạ?",
    "time": "Dạ {address} muốn mấy giờ ạ?",
    "part_of_day": "Dạ {address} muốn buổi sáng, buổi chiều hay buổi tối ạ?",
    "service": "Dạ {address} muốn làm dịch vụ gì ạ?",
    "which_appointment": "Dạ {address} nói giúp em là lịch hẹn nào ạ?",
    "week": "Dạ {address} muốn tuần này hay tuần sau ạ?",
    "confirm": "Dạ {address} xác nhận giúp em được không ạ?",
}

# Vế giữa câu khi hỏi gộp nhiều trường ("muốn {vế1}, {vế2} ạ?") — chỉ các
# trường của agent Đặt lịch gộp được với nhau (spec "Hỏi gộp"); `service`,
# `which_appointment`, `confirm` luôn đứng riêng (dùng `FIELD_QUESTION`).
_FIELD_CORE = {
    "day": "ngày nào",
    "time": "mấy giờ",
    "part_of_day": "buổi sáng, buổi chiều hay buổi tối",
    "week": "tuần này hay tuần sau",
}


def _capitalized(text: str) -> str:
    """Viết hoa chữ đầu mỗi câu ("{address} ơi" -> "Chị Lan ơi")."""
    return " ".join(sentence[:1].upper() + sentence[1:] for sentence in split_sentences(text))


def _phone(shop_phone: Optional[str]) -> Optional[str]:
    return shop_phone if shop_phone and digits_in(shop_phone) else None


def _lower_first(name: str) -> str:
    """"Cắt tóc nữ" -> "cắt tóc nữ": tên dịch vụ đứng giữa câu."""
    return name[:1].lower() + name[1:]


def _services(names: Sequence[str]) -> str:
    return " và ".join(_lower_first(name) for name in names)


def _what(at: str, service_names: Sequence[str]) -> str:
    """"{dịch vụ} lúc {giờ}" khi đề xuất / quyết định có tên dịch vụ, không thì giờ."""
    return f"{_services(service_names)} lúc {at}" if service_names else at


def _spoken_list(labels: Sequence[str], conjunction: str = "hoặc") -> str:
    """"a, b hoặc c" — "hoặc" cho các giờ để khách chọn, "và" cho danh sách
    (các lịch hẹn đang có, các giờ kín code báo)."""
    labels = list(labels)
    if len(labels) == 1:
        return labels[0]
    return f"{', '.join(labels[:-1])} {conjunction} {labels[-1]}"


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


def _failed(at: str, *, address: str, shop_phone: Optional[str]) -> str:
    phone = _phone(shop_phone)
    if phone is None:
        return FAILED_NO_PHONE_REPLY.format(at=at, address=address)
    return FAILED_REPLY.format(at=at, address=address, shop_phone=phone)


# ---------------------------------------------------------------------------
# taken_sentence / body / compose
# ---------------------------------------------------------------------------


def taken_sentence(plan: ReplyPlan, address: str) -> str:
    """Câu báo kín CODE tự biết ("3 giờ chiều mai đã có khách rồi ạ.") — mấy
    giờ kín gộp một câu bằng "và". `""` khi kế hoạch không báo giờ kín nào."""
    if not plan.taken:
        return ""
    return _capitalized(TAKEN_REPLY.format(at=_spoken_list(plan.taken, "và")))


# Tiếng mở đầu câu báo kín ("Dạ") — chữ model mở bằng đúng tiếng này thì bỏ
# đi khi ghép sau câu báo kín (review F9), lấy từ chính câu mẫu, không lặp chữ.
_TAKEN_LEAD = TAKEN_REPLY.split()[0]


def _without_lead(text: str) -> str:
    """Bỏ tiếng `_TAKEN_LEAD` mở đầu `text` (không phân biệt hoa thường, đứng
    riêng một từ) cùng dấu phẩy / khoảng trắng theo sau, viết hoa chữ kế tiếp.
    Không mở bằng tiếng đó, hay bỏ xong không còn chữ nào: giữ nguyên."""
    stripped = unicodedata.normalize("NFC", text.lstrip())
    head = stripped[:len(_TAKEN_LEAD)]
    rest = stripped[len(_TAKEN_LEAD):]
    if head.lower() != _TAKEN_LEAD.lower() or (rest and rest[0].isalnum()):
        return text
    rest = rest.lstrip(" ,")
    if not rest:
        return text
    return rest[:1].upper() + rest[1:]


def with_taken(plan: ReplyPlan, address: str, text: str) -> str:
    """Câu gửi khách khi chữ model đạt: câu báo kín của code + chữ model. Có câu
    báo kín thì tiếng "Dạ" mở đầu chữ model bị bỏ, để không thành "Dạ … ạ.
    Dạ …" (review F9)."""
    taken = taken_sentence(plan, address)
    if not taken:
        return text
    return f"{taken} {_without_lead(text)}".strip()


def _act_sentence(act: PlanAct, *, address: str, is_also_failed: bool) -> str:
    fields = act.fields
    if act.act == "failed":
        at, nearest, phone = fields["at"], fields["nearest"], fields.get("phone")
        if is_also_failed:
            return ALSO_FAILED_REPLY.format(at=at)
        if nearest:
            return FAILED_NEAREST_REPLY.format(at=at, nearest=_spoken_list(nearest, "hoặc"),
                                               address=address)
        return _failed(at, address=address, shop_phone=phone)
    if act.act == "done":
        return _done(fields["kind"], fields["at"], address=address,
                    service_names=fields.get("services", []))
    if act.act == "released":
        return _released(fields["kind"], fields["at"], address=address)
    if act.act == "proposal":
        return _proposal(fields["kind"], fields["at"], address=address,
                         service_names=fields.get("services", []))
    if act.act == "offer":
        return TIME_OPTIONS_QUESTION.format(options=_spoken_list(fields["times"], "hoặc"),
                                            address=address)
    if act.act == "price":
        return PRICE_REPLY.format(service=_lower_first(fields["service"]), price=fields["price"])
    if act.act == "appointments":
        return APPOINTMENTS_REPLY.format(address=address, times=_spoken_list(fields["times"], "và"))
    if act.act == "hours":
        return HOURS_REPLY.format(open=fields["open"], close=fields["close"])
    if act.act == "busy":
        return BUSY_REPLY.format(at=fields["until"])
    return GENERIC_FALLBACK.format(address=address)


def _ask_sentence(ask: PlanAsk, *, address: str) -> str:
    if len(ask.fields) == 1:
        template = FIELD_QUESTION.get(ask.fields[0])
        if template is not None:
            return template.format(address=address)
        return GENERIC_FALLBACK.format(address=address)
    cores = [_FIELD_CORE[field] for field in ask.fields if field in _FIELD_CORE]
    if not cores:
        template = FIELD_QUESTION.get(ask.fields[0])
        return (template or GENERIC_FALLBACK).format(address=address)
    return f"Dạ {address} muốn {', '.join(cores)} ạ?"


def body(plan: ReplyPlan, address: str) -> str:
    """Câu của từng hành động (theo đúng thứ tự `plan.acts`) + câu hỏi của
    `ask` — không có câu báo kín (đó là `taken_sentence`)."""
    parts: list[str] = []
    failed_seen = 0
    for act in plan.acts:
        parts.append(_act_sentence(act, address=address, is_also_failed=failed_seen > 0))
        if act.act == "failed":
            failed_seen += 1
    if plan.ask is not None:
        parts.append(_ask_sentence(plan.ask, address=address))
    return _capitalized(" ".join(parts))


def compose(plan: ReplyPlan, address: str) -> str:
    """Câu trả lời khi model thất bại hai lần: `taken_sentence` + `body`, hay
    `GENERIC_FALLBACK` khi kế hoạch rỗng (không có gì để nói)."""
    if plan.is_empty():
        return _capitalized(GENERIC_FALLBACK.format(address=address))
    return " ".join(part for part in (taken_sentence(plan, address), body(plan, address)) if part)


# ---------------------------------------------------------------------------
# button_reply (lượt bấm nút — không gọi model)
# ---------------------------------------------------------------------------


def button_reply(outcome: PendingOutcome, *, address: str, shop_phone: Optional[str],
                 today: date) -> str:
    """Câu báo của lượt nút bấm Đồng ý / Không — code viết, không gọi model."""
    at = short_label(outcome.at, today)
    if outcome.outcome == "approved":
        return _capitalized(_done(outcome.kind, at, address=address))
    if outcome.outcome == "rejected":
        return _capitalized(_released(outcome.kind, at, address=address))
    if outcome.outcome == "failed" and outcome.nearest and outcome.kind != "cancel":
        nearest = _spoken_list([short_label(moment, today) for moment in outcome.nearest], "hoặc")
        return _capitalized(FAILED_NEAREST_REPLY.format(at=at, nearest=nearest, address=address))
    if outcome.outcome in ("failed", "not_approvable"):
        return _capitalized(_failed(at, address=address, shop_phone=shop_phone))
    return _capitalized(GENERIC_FALLBACK.format(address=address))
