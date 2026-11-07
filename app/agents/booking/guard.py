"""`guard.py` — tầng gác SỰ THẬT trên câu trả lời cuối cùng (Task 4.3, §9.8 của
`docs/superpowers/specs/2026-10-29-llm-orchestrator-design.md`).

M4 thu hẹp guard: không còn kiểm giọng (`repeat`/`pronoun`/`clock` cũ gộp ba
chuyện khác nhau) — gói mới chỉ còn kiểm SỰ THẬT (`claim`, `missing_fact`) và
`repeat` (lặp câu, vẫn là một phép kiểm tất định, không phải giọng). Các hàm
tất định (`strip_tool_names`, `unclaim`, `has_false_claim`, `repeats`,
`clock_keys`) tái dùng NGUYÊN từ `draft_checks.py` (dời từ guard của gói cũ ở
Task 6.1; vì sao tách module riêng — xem docstring ở đó).

`facts_from_brief` là hàm MỚI: nó đọc `ReplyBrief.facts`/`must_not_claim` (đã
được `build_reply_brief`, Task 4.1, dựng từ kết quả tool thật) thay vì đọc lại
toàn bộ `state` của graph cũ."""
from __future__ import annotations

import unicodedata
from datetime import datetime
from typing import Literal, Sequence

from pydantic import BaseModel

from app.agents.booking.reply_brief import HELD_FACT_KEYS
from app.agents.booking.schemas import ReplyBrief
from app.agents.booking import draft_checks, rewrite, tokens
from app.core.clock import to_local
from app.core.logging import get_logger
from app.core.vi_format import _clock_phrase

logger = get_logger(__name__)

_ALL_CLAIMS = frozenset({"booked", "cancelled", "moved"})

# Động từ cam kết (`draft_checks._CLAIM_VERBS`) ánh xạ sang claim nó nói lên.
# `has_false_claim`/`_is_claimed_clause` cũ chỉ có từ vựng cho "đặt" (giữ/đặt/
# ghi/chốt) — "huỷ"/"dời"/"đổi" không nằm trong `_CLAIM_PHRASES` của nó, nên
# nó không bao giờ bắt được câu "đã huỷ ... rồi"/"đã dời ... rồi". Phải kiểm
# riêng cho hai claim còn lại (Fix round 1, concern đã xác nhận Important).
_CLAIM_FOR_VERB = {
    "đặt": "booked", "chốt": "booked", "ghi": "booked",
    "dời": "moved", "đổi": "moved",
    "hủy": "cancelled", "huỷ": "cancelled",
}

# Fix round 2: "đổi" cũng là "đổi ý"/"đổi số điện thoại" — không phải lúc nào
# cũng nói chuyện lịch hẹn. Đối tượng cam kết (`draft_checks._CLAIM_OBJS`, bỏ
# khoảng trắng đệm) để đòi thêm điều kiện "câu có nhắc tới lịch hẹn".
_CLAIM_OBJ_WORDS = frozenset(o.strip() for o in draft_checks._CLAIM_OBJS)

# Hai nhóm vi phạm có thể SỬA được bằng câu chốt CODE (`brief.fallback`) khi
# rewrite không gỡ được — chỉ áp dụng cho lượt có câu chốt riêng (§5.4).
_FIXABLE_BY_FALLBACK = frozenset({"missing_fact", "claim"})
# `limit_reached` (Task 5.3 fix round 3): câu chốt là lời mời gọi tiệm (§7.2)
# — thiếu nó sau rewrite thì dùng thẳng câu đó.
_FALLBACK_KINDS = frozenset({"confirmed", "failed", "proposal_made", "limit_reached"})


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
            keys = draft_checks.clock_keys(_clock_phrase(local.hour, local.minute))
            if keys:
                clocks.add(keys[0])
            if fact.key in HELD_FACT_KEYS:
                held_iso = fact.iso
    allowed_claims = _ALL_CLAIMS - set(brief.must_not_claim)
    return {"clocks": clocks, "held_iso": held_iso, "allowed_claims": allowed_claims}


def normalize(text: str) -> str:
    """NFC + casefold + gộp khoảng trắng — không regex, chỉ `split`/`join`.

    Fix round 1 (item 3): chữ Việt gõ dấu tổ hợp (NFD, vài bộ gõ/IME phát ra)
    và dấu dựng sẵn (NFC, phổ biến hơn) KHÁC NHAU về byte dù hiển thị giống
    hệt — so sánh thẳng sẽ trật dù mắt người không thấy khác biệt gì."""
    return " ".join(unicodedata.normalize("NFC", text or "").casefold().split())


def _claim_violation(draft: str, facts: dict) -> bool:
    """`has_false_claim(draft, facts)` so giờ model nhận với giờ THẬT; khi
    giờ đúng nhưng động từ cam kết (vd "đã đặt") vẫn bị cấm — mới GIỮ chỗ,
    chưa đặt xong — kiểm lại coi như không có giờ nào để buộc MỌI câu nhận
    "đã đặt/giữ/ghi/chốt rồi" bị bắt (`draft_checks._CLAIM_VERBS`)."""
    time_facts = {"clocks": facts["clocks"], "any": bool(facts["clocks"]), "unknown": False}
    if draft_checks.has_false_claim(draft, time_facts):
        return True
    if "booked" in facts["allowed_claims"]:
        return False
    no_held_facts = {"clocks": set(), "any": False, "unknown": False}
    return draft_checks.has_false_claim(draft, no_held_facts)


def _mentions_appointment(clause: str, words: list[str]) -> bool:
    """Câu có đang nói về MỘT lịch hẹn không — "đổi" cũng dùng cho "đổi ý",
    "đổi số điện thoại"; chỉ tính là claim lịch khi mệnh đề có đối tượng cam
    kết (`_CLAIM_OBJ_WORDS`: lịch/chỗ/hẹn) hoặc một mốc giờ (`tokens.scan_times`)."""
    return any(w in _CLAIM_OBJ_WORDS for w in words) or bool(tokens.scan_times(clause))


def _clause_claims(clause: str) -> set[str]:
    """Claim ('booked'/'moved'/'cancelled') mà MỆNH ĐỀ này nhận đã xong —
    một động từ cam kết (`_CLAIM_FOR_VERB`) đi kèm dấu hiệu đã hoàn tất GẦN nó
    ("đã" trong 2 từ trước, hoặc "rồi" trong 4 từ sau — cùng cách nhận biết
    của `_is_claimed_clause` cũ, chỉ khác từ vựng), VÀ mệnh đề thật sự đang
    nói về một lịch hẹn (`_mentions_appointment`) — không thì "đã đổi ý rồi"/
    "đã đổi số điện thoại rồi" cũng bị bắt oan (Fix round 2).

    Fix round 3: một động từ có "chưa"/"không" trong 2 từ TRƯỚC nó (phủ định)
    không phải lời nhận — "em chưa huỷ được lịch" không phải "em đã huỷ"."""
    words = tokens.words(clause)
    claims: set[str] = set()
    for i, word in enumerate(words):
        claim = _CLAIM_FOR_VERB.get(word)
        if claim is None:
            continue
        before = words[max(0, i - 2):i]
        if "chưa" in before or "không" in before:
            continue
        near_da = "đã" in before
        near_roi = "rồi" in words[i + 1:i + 5]
        if (near_da or near_roi) and _mentions_appointment(clause, words):
            claims.add(claim)
    return claims


def _is_question(sentence: str) -> bool:
    """Câu hỏi KHÔNG BAO GIỜ là một lời nhận — kết thúc bằng "?" (sau khi bỏ
    khoảng trắng cuối), hoặc mang cụm hỏi "không ạ"/"chưa ạ" (Fix round 3)."""
    if sentence.rstrip().endswith("?"):
        return True
    return tokens.has_phrase(tokens.padded(sentence), ("không ạ", "chưa ạ"))


def _forbidden_verb_claim(draft: str, must_not_claim: list[str]) -> bool:
    """`has_false_claim` không có từ vựng cho "huỷ"/"dời" (xem comment
    `_CLAIM_FOR_VERB`) — kiểm riêng câu "đã huỷ/dời ... rồi" khi claim đó nằm
    trong `must_not_claim`. Giữ nguyên đường "booked" đã có (`_claim_violation`
    qua `has_false_claim`), chỉ bù thêm hai claim còn lại. Câu hỏi
    (`_is_question`) bị bỏ qua hoàn toàn — cả câu, không chỉ mệnh đề có dấu
    hỏi (Fix round 3)."""
    forbidden = set(must_not_claim) & {"cancelled", "moved"}
    if not forbidden:
        return False
    for sentence in tokens.split_sentences(draft or ""):
        if _is_question(sentence):
            continue
        for clause in tokens.split_clauses(sentence, break_words=("còn",), preserve_date_comma=True):
            if _clause_claims(clause) & forbidden:
                return True
    return False


def _announced_clock(brief: ReplyBrief) -> tuple | None:
    """Mốc giờ của dòng R0, đọc từ `iso` của Fact `prior_outcome` (máy viết)."""
    for fact in brief.facts:
        if fact.key == "prior_outcome" and fact.iso:
            local = to_local(datetime.fromisoformat(fact.iso))
            keys = draft_checks.clock_keys(_clock_phrase(local.hour, local.minute))
            return keys[0] if keys else None
    return None


def _piggybacks_on_announced(sentences: list[str], needle: str, brief: ReplyBrief) -> bool:
    """Câu CHỨA dòng R0 mà phần còn lại nêu thêm một mốc giờ KHÁC giờ R0 —
    "đã đặt xong <giờ R0> và 5 giờ chiều ngày kia rồi" — là nhận vơ theo đúng
    động từ của dòng R0 (dòng R0 `failed` phủ định, không có động từ claim
    nào -> không áp)."""
    if not _clause_claims(needle):
        return False
    r0_clock = _announced_clock(brief)
    for sentence in sentences:
        if needle not in sentence:
            continue
        rest = sentence.replace(needle, " ")
        for said in draft_checks.clock_keys(rest):
            if r0_clock is None or not draft_checks._same_clock(said, r0_clock):
                return True
    return False


def _claim_text(draft: str, brief: ReplyBrief) -> tuple[str, bool]:
    """`(câu đem kiểm claim, có ké giờ vào dòng R0 không)`.

    CHỈ dòng R0 (`brief.announced_line`, code viết từ sự thật lượt trước) được
    gỡ khỏi câu trước hai phép kiểm claim — các câu `must_say` khác (giờ trần
    của `held_at`/`will_cancel`/`slot`...) vẫn nằm nguyên, vì thiếu giờ thì
    `_mentions_appointment` và đường "booked" mù (Task 5.3 fix round 3). Mọi
    phép kiểm claim tự hạ chữ thường nên chạy trên bản `normalize` không đổi
    kết quả với phần còn lại."""
    needle = normalize(brief.announced_line or "")
    if not needle:
        return draft, False
    text = normalize(draft)
    if needle not in text:
        return draft, False
    piggyback = _piggybacks_on_announced(tokens.split_sentences(text), needle, brief)
    return text.replace(needle, " "), piggyback


def check_reply(draft: str, brief: ReplyBrief, previous_replies: Sequence[str]) -> list[str]:
    """Ba phép kiểm SỰ THẬT (§9.8): `claim` (nhận vơ), `missing_fact` (thiếu
    câu bắt buộc), `repeat` (lặp câu đáp gần đây — vẫn tất định, không phải
    luật giọng). `claim` xét câu đã gỡ riêng dòng R0 (`_claim_text`);
    `missing_fact` vẫn xét trên toàn bộ `draft`."""
    violations: list[str] = []
    facts = facts_from_brief(brief)
    claim_text, piggyback = _claim_text(draft, brief)
    if (
        piggyback
        or _claim_violation(claim_text, facts)
        or _forbidden_verb_claim(claim_text, brief.must_not_claim)
    ):
        violations.append("claim")
    normalized_draft = normalize(draft)
    if any(normalize(s) not in normalized_draft for s in brief.must_say):
        violations.append("missing_fact")
    if draft_checks.repeats(draft, previous_replies):
        violations.append("repeat")
    return violations


async def apply_guard(
    draft: str, brief: ReplyBrief, previous_replies: Sequence[str], *, source: str,
    log_extra: dict | None = None,
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

    `log_extra` (graph truyền `user_id`/`turn_id`/`thread_id`, §11) gộp vào
    `extra` của mọi dòng `guard_violation`.
    """
    ids = log_extra or {}
    if source == "code":
        return GuardResult(answer=draft, source="code", violations=[])

    draft = draft_checks.strip_tool_names(draft)
    codes = check_reply(draft, brief, previous_replies)
    if "claim" in codes:
        draft = draft_checks.unclaim(draft)
        codes = check_reply(draft, brief, previous_replies)

    if not codes:
        return GuardResult(answer=draft, source="llm", violations=[])

    rewritten = await rewrite.rewrite_once(
        draft, codes, previous_replies, f"Gọi khách là: {brief.address}"
    )
    rewritten = draft_checks.strip_tool_names(rewritten) if rewritten else None
    checked_draft = rewritten if rewritten is not None else draft
    recheck = check_reply(checked_draft, brief, previous_replies)

    if not recheck:
        logger.info("guard_violation", extra={**ids, "codes": codes, "fixed_by": "rewrite"})
        return GuardResult(answer=checked_draft, source="llm", violations=[])

    if brief.kind in _FALLBACK_KINDS and any(c in _FIXABLE_BY_FALLBACK for c in recheck):
        logger.warning("guard_violation", extra={**ids, "codes": recheck, "fixed_by": "fallback"})
        return GuardResult(answer=brief.fallback, source="code", violations=recheck)

    if set(recheck) == {"repeat"}:
        logger.info("guard_violation", extra={**ids, "codes": recheck, "fixed_by": "rewrite"})
        return GuardResult(answer=checked_draft, source="llm", violations=recheck)

    logger.warning("guard_violation", extra={**ids, "codes": recheck, "fixed_by": "none"})
    return GuardResult(answer=draft, source="llm", violations=recheck)
