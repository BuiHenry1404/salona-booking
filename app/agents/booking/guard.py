"""`guard.py` — tầng gác SỰ THẬT trên câu trả lời cuối cùng (Task 4.3, §9.8 của
`docs/superpowers/specs/2026-10-29-llm-orchestrator-design.md`).

M4 thu hẹp guard: không còn kiểm giọng (`repeat`/`pronoun`/`clock` cũ gộp ba
chuyện khác nhau) — gói mới chỉ còn kiểm SỰ THẬT (`claim`, `missing_fact`) và
`repeat` (lặp câu, vẫn là một phép kiểm tất định, không phải giọng). Ba hàm
tất định (`strip_tool_names`, `unclaim`, `has_false_claim`, `repeats`,
`clock_keys`) tái dùng NGUYÊN từ gói cũ `booking_graph.guard` — M6 mới dời
hẳn vào đây, lúc đó `booking_graph` bị gỡ.

`facts_from_brief` là hàm MỚI: nó đọc `ReplyBrief.facts`/`must_not_claim` (đã
được `build_reply_brief`, Task 4.1, dựng từ kết quả tool thật) thay vì đọc lại
toàn bộ `state` của graph cũ."""
from __future__ import annotations

from datetime import datetime
from typing import Literal, Sequence

from pydantic import BaseModel

from app.agents.booking.schemas import ReplyBrief
from app.agents.booking_graph import guard as old_guard
from app.agents.booking_graph import rewrite
from app.core.clock import to_local
from app.core.logging import get_logger
from app.core.vi_format import _clock_phrase

logger = get_logger(__name__)

_ALL_CLAIMS = frozenset({"booked", "cancelled", "moved"})

# Khoá `Fact` mang mốc giờ ĐANG/ĐÃ giữ thật (không phải mọi fact có `iso` —
# ví dụ `slot`/`queued_at` chỉ là giờ trống, không phải giờ đã cam kết).
_HELD_FACT_KEYS = frozenset({"held_at", "booked_at", "moved_to", "cancelled_at", "will_cancel"})

# Hai nhóm vi phạm có thể SỬA được bằng câu chốt CODE (`brief.fallback`) khi
# rewrite không gỡ được — chỉ áp dụng cho lượt có câu chốt riêng (§5.4).
_FIXABLE_BY_FALLBACK = frozenset({"missing_fact", "claim"})
_FALLBACK_KINDS = frozenset({"confirmed", "failed", "proposal_made"})


class GuardResult(BaseModel):
    answer: str
    source: Literal["llm", "code"]
    violations: list[str]


def facts_from_brief(brief: ReplyBrief) -> dict:
    """Sự thật CODE biết của lượt này, dựng từ `ReplyBrief` — guard dùng để
    kiểm câu trả lời không bịa, không đọc lại `state`/DB.

    - `clocks`: mọi mốc giờ `(giờ đọc, phút, buổi|None)` xuất hiện trong
      `brief.facts`, đọc từ `iso` (máy, không bịa được) chứ không từ `spoken`
      (model có thể chép sai `spoken`, `has_false_claim` so với giờ THẬT).
    - `held_iso`: mốc đang/đã giữ thật, nếu có (phân biệt "có giờ thật nhưng
      chưa phải đã ĐẶT" — `proposal_made` — khỏi "đã đặt xong" — `confirmed`).
    - `allowed_claims`: động từ cam kết (đặt/huỷ/dời) được phép nói ở lượt
      này — mọi claim KHÔNG nằm trong `must_not_claim`.
    """
    clocks: set[tuple[int, int, str | None]] = set()
    held_iso: str | None = None
    for fact in brief.facts:
        if fact.iso:
            local = to_local(datetime.fromisoformat(fact.iso))
            keys = old_guard.clock_keys(_clock_phrase(local.hour, local.minute))
            if keys:
                clocks.add(keys[0])
            if fact.key in _HELD_FACT_KEYS:
                held_iso = fact.iso
    allowed_claims = _ALL_CLAIMS - set(brief.must_not_claim)
    return {"clocks": clocks, "held_iso": held_iso, "allowed_claims": allowed_claims}


def normalize(text: str) -> str:
    """casefold + gộp khoảng trắng — không regex, chỉ `split`/`join`."""
    return " ".join((text or "").casefold().split())


def _claim_violation(draft: str, facts: dict) -> bool:
    """`has_false_claim(draft, facts)` so giờ model nhận với giờ THẬT; khi
    giờ đúng nhưng động từ cam kết (vd "đã đặt") vẫn bị cấm — mới GIỮ chỗ,
    chưa đặt xong — kiểm lại coi như không có giờ nào để buộc MỌI câu nhận
    "đã đặt/giữ/ghi/chốt rồi" bị bắt (`_CLAIM_VERBS` của gói cũ)."""
    time_facts = {"clocks": facts["clocks"], "any": bool(facts["clocks"]), "unknown": False}
    if old_guard.has_false_claim(draft, time_facts):
        return True
    if "booked" in facts["allowed_claims"]:
        return False
    no_held_facts = {"clocks": set(), "any": False, "unknown": False}
    return old_guard.has_false_claim(draft, no_held_facts)


def check_reply(draft: str, brief: ReplyBrief, previous_replies: Sequence[str]) -> list[str]:
    """Ba phép kiểm SỰ THẬT (§9.8): `claim` (nhận vơ), `missing_fact` (thiếu
    câu bắt buộc), `repeat` (lặp câu đáp gần đây — vẫn tất định, không phải
    luật giọng)."""
    violations: list[str] = []
    facts = facts_from_brief(brief)
    if _claim_violation(draft, facts):
        violations.append("claim")
    normalized_draft = normalize(draft)
    if any(normalize(s) not in normalized_draft for s in brief.must_say):
        violations.append("missing_fact")
    if old_guard.repeats(draft, previous_replies):
        violations.append("repeat")
    return violations


async def apply_guard(
    draft: str, brief: ReplyBrief, previous_replies: Sequence[str], *, source: str
) -> GuardResult:
    """Nơi DUY NHẤT quyết câu trả lời cuối cùng — `source="code"` (đã qua
    `brief.fallback`, câu chốt CODE viết) trả nguyên, không kiểm lại.

    Câu từ Conversation Agent (`source="llm"`): xoá tên tool (tất định);
    "claim" ngay từ lần kiểm đầu thì thử `unclaim` (tất định) trước khi tính
    tới rewrite. Không vi phạm → xong. Có vi phạm → `rewrite_once` (LLM, một
    tag `rewrite`) ĐÚNG MỘT LẦN rồi kiểm lại:
    - vẫn `missing_fact`/`claim` ở lượt có câu chốt riêng (`confirmed`/
      `failed`/`proposal_made`) → dùng thẳng `brief.fallback` (sự thật thắng
      giọng điệu);
    - chỉ còn `repeat` → giữ bản viết lại (khác chữ với bản gốc, dù câu gốc
      mới là câu lặp thật — trả bản gốc sẽ phát đúng lỗi vừa bắt);
    - còn lại (vi phạm không sửa được, lượt không có câu chốt) → trả bản GỐC
      (trước rewrite), ghi lại vi phạm để quan sát (§11 `guard_violation`).
    """
    if source == "code":
        return GuardResult(answer=draft, source="code", violations=[])

    draft = old_guard.strip_tool_names(draft)
    codes = check_reply(draft, brief, previous_replies)
    if "claim" in codes:
        draft = old_guard.unclaim(draft)
        codes = check_reply(draft, brief, previous_replies)

    if not codes:
        return GuardResult(answer=draft, source="llm", violations=[])

    rewritten = await rewrite.rewrite_once(
        draft, codes, previous_replies, f"Gọi khách là: {brief.address}"
    )
    rewritten = old_guard.strip_tool_names(rewritten) if rewritten else None
    checked_draft = rewritten if rewritten is not None else draft
    recheck = check_reply(checked_draft, brief, previous_replies)

    if not recheck:
        logger.info("guard_violation", extra={"codes": codes, "fixed_by": "rewrite"})
        return GuardResult(answer=checked_draft, source="llm", violations=[])

    if brief.kind in _FALLBACK_KINDS and any(c in _FIXABLE_BY_FALLBACK for c in recheck):
        logger.warning("guard_violation", extra={"codes": recheck, "fixed_by": "fallback"})
        return GuardResult(answer=brief.fallback, source="code", violations=recheck)

    if set(recheck) == {"repeat"}:
        logger.info("guard_violation", extra={"codes": recheck, "fixed_by": "rewrite"})
        return GuardResult(answer=checked_draft, source="llm", violations=recheck)

    logger.warning("guard_violation", extra={"codes": recheck, "fixed_by": "none"})
    return GuardResult(answer=draft, source="llm", violations=recheck)
