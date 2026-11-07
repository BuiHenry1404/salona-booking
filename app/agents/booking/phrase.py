"""Câu chốt lịch do CODE viết (đường nút Đồng ý) — mẫu `_TEMPLATES` +
`code_phrase`, không gọi LLM. Dời nguyên văn từ `phrase.py` của gói cũ ở
Task 6.1 (`write_phrase` — bản LLM — ở lại gói cũ).
"""
from typing import Any, Dict, Sequence

from app.agents.booking.draft_checks import repeats

# Câu chốt đường NÚT ("Đồng ý") — CODE viết, không gọi LLM (Task 3: lượt nút
# phải xong trong ≤1.5s). Mỗi (kind, context) có ≥3 mẫu để tránh lặp
# (`guard.repeats` so với `previous_replies`); "context" do Task 15 điền vào
# `confirm_fact`, thiếu thì coi là "first_time". `{address}`/`{when}` lấy
# nguyên văn từ fact — không đoán giới tính, không tự viết lại giờ.
_TEMPLATES: Dict[str, Dict[str, list]] = {
    "booked": {
        "first_time": [
            "Dạ, em đặt lịch cho {address} vào {when} rồi ạ.",
            "Dạ {address} ơi, lịch của {address} đã được giữ vào {when} ạ.",
            "Em xác nhận đặt xong cho {address} lúc {when} rồi nhé ạ.",
        ],
        "returning": [
            "Dạ, như mọi lần, em đặt lịch cho {address} vào {when} rồi ạ.",
            "Em giữ chỗ quen thuộc cho {address} vào {when} xong rồi ạ.",
            "Dạ {address}, lịch cũ của mình em đặt tiếp vào {when} nhé ạ.",
        ],
        "changed_time": [
            "Dạ, em đã đặt lại giờ mới cho {address} là {when} ạ.",
            "Em cập nhật xong, lịch của {address} giờ là {when} ạ.",
            "Dạ {address} ơi, giờ đặt đã đổi thành {when}, em ghi xong rồi ạ.",
        ],
        "after_decline": [
            "Dạ, em đặt lịch mới cho {address} vào {when} rồi ạ.",
            "Em ghi nhận và đặt xong cho {address} vào {when} nhé ạ.",
            "Dạ {address}, lần này em đặt vào {when} xong rồi ạ.",
        ],
    },
    "moved": {
        "first_time": [
            "Dạ, em dời lịch của {address} sang {when} rồi ạ.",
            "Em chuyển lịch hẹn của {address} qua {when} xong rồi ạ.",
            "Dạ {address} ơi, lịch cũ đã đổi sang {when} rồi ạ.",
        ],
        "returning": [
            "Dạ, em dời tiếp lịch quen của {address} sang {when} rồi ạ.",
            "Em cập nhật lịch của {address} dời sang {when} xong rồi ạ.",
            "Dạ {address}, lịch mới của mình là {when}, em dời xong rồi ạ.",
        ],
        "changed_time": [
            "Dạ, em đổi giờ hẹn của {address} sang {when} rồi ạ.",
            "Em đã dời lịch, giờ mới của {address} là {when} ạ.",
            "Dạ {address} ơi, lịch được dời thành {when}, em ghi xong rồi ạ.",
        ],
        "after_decline": [
            "Dạ, em dời lịch của {address} sang {when} rồi ạ.",
            "Em đã đổi giờ hẹn của {address} qua {when} xong ạ.",
            "Dạ {address}, lịch mới là {when}, em dời xong giúp {address} rồi ạ.",
        ],
    },
    "cancelled": {
        "first_time": [
            "Dạ, em huỷ lịch {when} của {address} rồi ạ.",
            "Em đã huỷ hẹn vào {when} cho {address} xong ạ.",
            "Dạ {address} ơi, lịch {when} đã được huỷ rồi ạ.",
        ],
        "returning": [
            "Dạ, lịch quen vào {when} của {address} em huỷ xong rồi ạ.",
            "Em huỷ giúp {address} lịch {when} như mọi lần nhé ạ.",
            "Dạ {address}, hẹn {when} em đã huỷ xong ạ.",
        ],
        "changed_time": [
            "Dạ, em huỷ lịch cũ {when} của {address} rồi ạ.",
            "Em đã huỷ hẹn {when}, {address} yên tâm nhé ạ.",
            "Dạ {address} ơi, lịch {when} đã huỷ xong rồi ạ.",
        ],
        "after_decline": [
            "Dạ, em huỷ lịch {when} của {address} theo yêu cầu rồi ạ.",
            "Em đã huỷ hẹn {when} cho {address} xong ạ.",
            "Dạ {address}, lịch {when} em huỷ xong giúp {address} rồi ạ.",
        ],
    },
}


def code_phrase(fact: Dict[str, Any], previous_replies: Sequence[str]) -> str:
    """Câu chốt lịch đường NÚT — CODE viết, không gọi LLM.

    `kind == "failed"` dùng nguyên `fact["fallback"]` (lỗi đặt lịch, câu báo
    lỗi đã cố định sẵn, không có mẫu riêng). Các kind còn lại chọn mẫu đầu
    tiên không bị coi là lặp (`guard.repeats`) so với `previous_replies`; hết
    mẫu (mọi mẫu đều "lặp") thì dùng mẫu đầu tiên.
    """
    kind = fact.get("kind")
    if kind == "failed":
        return fact.get("fallback") or ""
    context = fact.get("context") or "first_time"
    by_context = _TEMPLATES.get(kind) or {}
    templates = by_context.get(context) or by_context.get("first_time") or []
    address = fact.get("address") or "anh chị"
    when = fact.get("when") or ""
    candidates = [t.format(address=address, when=when) for t in templates]
    for candidate in candidates:
        if not repeats(candidate, previous_replies):
            return candidate
    return candidates[0] if candidates else ""
