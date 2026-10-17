"""Chấm điểm một lần chạy kịch bản: đúng/sai, ghi sai, vi phạm luật văn bản,
hợp lệ (tiền điều kiện), pass^k, và so khớp hành vi giữa hai ref.

Hộp đen: module này KHÔNG import `app`. Chỉ dùng dữ liệu đã ghi lại
(`TurnRecord`), hai lần chụp Mongo (`ApptRow`), và kịch bản đã nạp
(`Scenario`/`Expect`) — không tự đoán lại điều gì "đúng" ngoài những gì
`Expect` nói trước (xem `scenario.py`: kỳ vọng viết trước, không sửa theo
output thật).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Callable

from bench.dbread import ApptRow, Changes, diff
from bench.scenario import Expect, ExpectAppt, Scenario, Turn, resolve_at
from bench.adapters.base import TurnRecord
from bench.vi_time import mentions

# Câu nhận "đã xong" — nếu DB không đổi ở lượt đó, đó là một lời nói dối.
# IGNORECASE: bot thật có thể viết hoa đầu câu ("Xong rồi ạ").
_CLAIM_RE = re.compile(
    r"xong rồi ạ|em (đã )?(đặt|hủy|dời) (xong|rồi)", re.IGNORECASE
)


def check_precondition(turn: Turn, prev: TurnRecord | None, now_vn: datetime) -> bool:
    """`turn.requires_asked` khác None thì đòi `prev` tồn tại, không lỗi, và
    câu trả lời của `prev` có nhắc đúng ngày + giờ của mốc đó (`vi_time.mentions`).

    `turn.requires_asked` là None thì luôn hợp lệ (không có tiền điều kiện).
    """
    if turn.requires_asked is None:
        return True
    if prev is None:
        return False
    if prev.error is not None:
        return False
    return mentions(prev.answer, resolve_at(turn.requires_asked, now_vn))


# ---------------------------------------------------------------------------
# TEXT_RULES — R2 (+ I8 tool_name_leak); claim_without_write được đánh giá
# riêng trong score_run (cần so sánh DB giữa hai lượt, không chỉ xét chữ).
# ---------------------------------------------------------------------------

_COMMON_EN_WORDS = {
    "the", "a", "an", "is", "are", "was", "were", "be", "been", "to", "of",
    "in", "on", "for", "with", "at", "as", "it", "this", "that", "and", "or",
    "not", "please", "try", "again", "error", "errors", "failed", "failure",
    "invalid", "request", "rejected", "service", "unavailable", "internal",
    "server", "model", "call", "calls", "limit", "limits", "exceeded",
    "timeout", "connection", "network", "down", "unknown", "occurred",
    "system", "currently", "sorry", "later", "temporarily", "busy",
}


def _english_violation(text: str) -> bool:
    lowered = text.lower()
    if "model call" in lowered or "limit" in lowered or "rejected" in lowered:
        return True
    run = 0
    for raw in text.split():
        word = raw.strip(".,;:!?()[]\"'")
        if word.isascii() and word.isalpha() and word.lower() in _COMMON_EN_WORDS:
            run += 1
            if run >= 4:
                return True
        else:
            run = 0
    return False


# Loại trừ các cụm vô hại dùng "cô/chú/bác" không phải để xưng hô với khách:
# "chú ý", "bác sĩ", "cô ấy".
_REGISTER_WORD_RE = re.compile(
    r"\b(cô|chú|bác)\b(?!\s+(ý|sĩ|ấy)\b)", re.IGNORECASE
)
_REGISTER_CON_RE = re.compile(r"\bcon (xem|đặt|giữ|hỏi)\b", re.IGNORECASE)


def _register_violation(text: str) -> bool:
    return bool(_REGISTER_WORD_RE.search(text)) or bool(_REGISTER_CON_RE.search(text))


_CLOCK_COLON_RE = re.compile(r"\b\d{1,2}:\d{2}\b")


def _clock_colon_violation(text: str) -> bool:
    return bool(_CLOCK_COLON_RE.search(text))


# I8: tên 7 tool của agent (app/agents/booking_graph/tools.py) lọt vào câu trả
# lời, hoặc một danh sách định danh snake_case trong ngoặc "(a_b, c_d)".
AGENT_TOOL_NAMES = (
    "get_shop_status",
    "get_shop_hours",
    "parse_time",
    "find_free_slots",
    "propose_appointment",
    "list_my_appointments",
    "cancel_appointment",
)
_TOOL_NAME_RE = re.compile(r"\b(" + "|".join(AGENT_TOOL_NAMES) + r")\b", re.IGNORECASE)
_TOOL_LIST_RE = re.compile(r"\(\s*[a-z]+_[a-z_]+(\s*,\s*[a-z]+_[a-z_]+)*\s*\)", re.IGNORECASE)


def _tool_name_leak_violation(text: str) -> bool:
    return bool(_TOOL_NAME_RE.search(text)) or bool(_TOOL_LIST_RE.search(text))


TEXT_RULES: dict[str, Callable[[str], bool]] = {
    "english": _english_violation,
    "register": _register_violation,
    "clock_colon": _clock_colon_violation,
    "tool_name_leak": _tool_name_leak_violation,
}


@dataclass(frozen=True)
class RunScore:
    passed: bool
    valid: bool
    invalid_reason: str | None
    wrong_writes: list[str]
    violations: list[str]
    turns_used: int
    # Không đạt kỳ vọng nhưng KHÔNG phải ghi sai (C2): "missing:<t>" (thiếu lịch
    # kỳ vọng), "note_mismatch:<t>" (đúng phút, sai ghi chú), "unchanged:<t>"
    # (lịch có sẵn không ai đụng tới nhưng lẽ ra phải dời/hủy).
    mismatches: list[str] = field(default_factory=list)


def _match_booked(
    expect_booked: tuple[ExpectAppt, ...],
    actual_rows: list[ApptRow],
    now_vn: datetime,
) -> tuple[list[ApptRow], list[ApptRow], list[datetime]]:
    """So tập lịch thật (`actual_rows`, đã lọc status="booked" của khách) với
    tập kỳ vọng, khớp theo PHÚT. Trả về (`extras`: lịch không khớp phút kỳ
    vọng nào; `note_mismatch`: lịch khớp phút nhưng sai `note_contains`;
    `missing`: các mốc kỳ vọng không có lịch nào). Ưu tiên ghép lịch có ghi
    chú khớp trước, để một lịch đúng không bị "giành" bởi lịch sai ghi chú."""
    expected = [
        (resolve_at(e, now_vn).replace(second=0, microsecond=0), e.note_contains)
        for e in expect_booked
    ]
    used = [False] * len(expected)
    pending = list(actual_rows)
    note_mismatch: list[ApptRow] = []

    def _note_ok(row: ApptRow, needle: str | None) -> bool:
        return needle is None or needle.lower() in (row.note or "").lower()

    for require_note in (True, False):
        still: list[ApptRow] = []
        for row in pending:
            row_minute = row.start_at.replace(second=0, microsecond=0)
            matched = False
            for idx, (exp_dt, needle) in enumerate(expected):
                if used[idx] or row_minute != exp_dt:
                    continue
                if require_note and not _note_ok(row, needle):
                    continue
                used[idx] = True
                matched = True
                if not require_note and not _note_ok(row, needle):
                    note_mismatch.append(row)
                break
            if not matched:
                still.append(row)
        pending = still

    missing = [expected[i][0] for i, flag in enumerate(used) if not flag]
    return pending, note_mismatch, missing


def _label_time(dt: datetime) -> str:
    return f"{dt.strftime('%H:%M')} {dt.day}/{dt.month}"


def score_run(
    scenario: Scenario,
    turns: list[TurnRecord],
    before: list[ApptRow],
    after: list[ApptRow],
    customer_id: str,
    now_vn: datetime,
) -> RunScore:
    """Chấm một lần chạy kịch bản dựa trên các lượt đã ghi lại và hai lần
    chụp DB (trước/sau toàn bộ kịch bản). Xem docstring module cho ngữ cảnh.
    """
    expect: Expect = scenario.expect
    wrong_writes: list[str] = []
    violations: list[str] = []

    # --- Hợp lệ: tiền điều kiện của mỗi lượt đã thực sự xảy ra ---
    valid = True
    invalid_reason: str | None = None
    for i, turn in enumerate(scenario.turns):
        if i >= len(turns):
            break
        if turn.requires_asked is None:
            continue
        prev = turns[i - 1] if i > 0 else None
        if not check_precondition(turn, prev, now_vn):
            valid = False
            invalid_reason = f"precondition@{i}"
            break

    # --- Lịch của khách khác không được đổi ---
    full_changes: Changes = diff(before, after)
    if expect.others_unchanged:
        other_ids: list[str] = []
        for row in full_changes.created:
            if row.user_id != customer_id:
                other_ids.append(row.id)
        for row in full_changes.cancelled:
            if row.user_id != customer_id:
                other_ids.append(row.id)
        for _, after_row in full_changes.other:
            if after_row.user_id != customer_id:
                other_ids.append(after_row.id)
        for rid in other_ids:
            wrong_writes.append(f"other_customer:{rid}")

    # --- Tập lịch "booked" của khách sau cùng phải khớp đúng kỳ vọng ---
    # C2: chỉ lịch THAY ĐỔI trong lần chạy (mới tạo, hoặc đổi giờ/trạng thái
    # so với `before`) mới có thể là ghi sai. Lịch có sẵn còn nguyên mà lẽ ra
    # phải dời/hủy là KHÔNG ĐẠT (`unchanged:`), không phải ghi sai; đúng phút
    # mà sai ghi chú là `note_mismatch:`; thiếu lịch kỳ vọng là `missing:`.
    mismatches: list[str] = []
    expect_satisfied = True
    if expect.booked is not None:
        actual_booked = [
            row for row in after if row.user_id == customer_id and row.status == "booked"
        ]
        extras, note_bad, missing = _match_booked(expect.booked, actual_booked, now_vn)
        before_by_id = {row.id: row for row in before}
        for row in extras:
            prior = before_by_id.get(row.id)
            if prior is not None and prior == row:
                mismatches.append(f"unchanged:{_label_time(row.start_at)}")
                expect_satisfied = False
            else:
                wrong_writes.append(f"extra:{_label_time(row.start_at)}")
        for row in note_bad:
            mismatches.append(f"note_mismatch:{_label_time(row.start_at)}")
            expect_satisfied = False
        for dt in missing:
            mismatches.append(f"missing:{_label_time(dt)}")
            expect_satisfied = False

        expected_minutes = {
            resolve_at(e, now_vn).replace(second=0, microsecond=0) for e in expect.booked
        }
        booked_minutes = {
            row.start_at.replace(second=0, microsecond=0) for row in actual_booked
        }
        for cancelled_row in full_changes.cancelled:
            if cancelled_row.user_id != customer_id:
                continue
            minute = cancelled_row.start_at.replace(second=0, microsecond=0)
            # T6: hủy rồi đặt lại đúng phút đó (vẫn còn lịch booked) không phải ghi sai.
            if minute in expected_minutes and minute not in booked_minutes:
                wrong_writes.append(f"cancelled:{_label_time(cancelled_row.start_at)}")

    # --- Câu trả lời cuối ---
    final_answer = turns[-1].answer if turns else ""
    for needle in expect.answer_must_include:
        if needle not in final_answer:
            expect_satisfied = False
    for needle in expect.answer_must_not_include:
        if needle in final_answer:
            expect_satisfied = False

    turns_used = len(turns)
    if expect.max_turns is not None and turns_used > expect.max_turns:
        expect_satisfied = False

    # R11: lỗi ở lượt có khai báo `fault` trong kịch bản KHÔNG làm trượt (kịch
    # bản fault chủ ý mong lỗi đó xảy ra); lỗi ở lượt không có fault vẫn trượt.
    any_turn_error = False
    for i, record in enumerate(turns):
        if record.error is None:
            continue
        if i < len(scenario.turns) and scenario.turns[i].fault is not None:
            continue
        any_turn_error = True
        break

    # --- claim_without_write: nhận "đã xong" nhưng DB không đổi ở lượt đó ---
    for i, record in enumerate(turns):
        if record.db_after is None:
            continue
        prev_db = before if i == 0 else turns[i - 1].db_after
        if prev_db is None:
            continue
        step_changes = diff(prev_db, record.db_after)
        no_change = (
            not step_changes.created
            and not step_changes.cancelled
            and not step_changes.other
        )
        if no_change and _CLAIM_RE.search(record.answer):
            violations.append(f"claim_without_write@{i}")

    # --- TEXT_RULES trên mọi câu trả lời ---
    for i, record in enumerate(turns):
        for rule_name, rule_fn in TEXT_RULES.items():
            if rule_fn(record.answer):
                violations.append(f"{rule_name}@{i}")

    passed = valid and not wrong_writes and expect_satisfied and not any_turn_error

    return RunScore(
        passed=passed,
        valid=valid,
        invalid_reason=invalid_reason,
        wrong_writes=wrong_writes,
        violations=violations,
        turns_used=turns_used,
        mismatches=mismatches,
    )


def pass_hat_k(results: list[bool], k: int) -> bool:
    """pass^k: đủ ít nhất `k` lần chạy VÀ tất cả đều đạt (`passed`)."""
    return len(results) >= k and all(results)


def aggregate(scores: list["RunScore"], k: int) -> dict:
    """Tổng hợp điểm cho MỘT scenario x ref (các lần chạy lặp lại = k lần thử
    của pass^k). `scores` phải là danh sách `RunScore` của ĐÚNG một kịch bản
    chạy trên ĐÚNG một ref — hàm này không tự nhóm theo kịch bản (`RunScore`
    không giữ id kịch bản). Bộ gọi (runner/report, Task 9/10) nhóm trước theo
    scenario x ref rồi gọi hàm này cho từng nhóm, sau đó tự lấy trung bình
    `pass_hat_k_rate` qua nhiều kịch bản.

    Lần chạy không hợp lệ (`valid=False`) bị loại khỏi mẫu số của `pass_rate`
    và `pass_hat_k`/`pass_hat_k_rate`, nhưng vẫn được đếm trong `runs` và
    trong `wrong_writes_total`/`violations_by_rule`. Không bao giờ coi lần
    chạy không hợp lệ là trượt: khi không đủ dữ liệu để kết luận (không có
    lần chạy hợp lệ nào, hoặc chưa đủ `k` lần hợp lệ), giá trị trả về là
    `None` thay vì `False`/`0` (R10).
    """
    runs = len(scores)
    valid_scores = [s for s in scores if s.valid]
    valid_runs = len(valid_scores)
    passed_count = sum(1 for s in valid_scores if s.passed)
    pass_rate = (passed_count / valid_runs) if valid_runs else None

    hat_k: bool | None
    if valid_runs < k:
        hat_k = None
    else:
        hat_k = pass_hat_k([s.passed for s in valid_scores], k)

    wrong_writes_total = sum(len(s.wrong_writes) for s in scores)
    violations_by_rule: dict[str, int] = {}
    for s in scores:
        for v in s.violations:
            rule = v.split("@", 1)[0]
            violations_by_rule[rule] = violations_by_rule.get(rule, 0) + 1

    return {
        "runs": runs,
        "valid_runs": valid_runs,
        "pass_rate": pass_rate,
        "pass_hat_k": hat_k,
        "pass_hat_k_rate": (None if hat_k is None else float(hat_k)),
        "wrong_writes_total": wrong_writes_total,
        "violations_by_rule": violations_by_rule,
    }


def _turn_diff_shape(
    prev_db: list[ApptRow] | None, record: TurnRecord
) -> tuple[tuple, tuple, tuple] | None:
    """Hình dạng thay đổi DB của một lượt, không phụ thuộc id/user_id tuyệt
    đối — hai lần chạy khác nhau (hai ref, hai khách khác nhau được seed
    riêng) luôn sinh id khác nhau dù hành vi hệt nhau. None nếu thiếu
    `db_after`. Trả về:
    - `created`/`cancelled`: multiset (đã sắp xếp) các `(HH:MM giờ VN, status, note)`.
    - `other` (vd: dời giờ): multiset (đã sắp xếp) các
      `(HH:MM trước, HH:MM sau, status sau)`.
    """
    if record.db_after is None:
        return None
    changes = diff(prev_db or [], record.db_after)
    created = tuple(
        sorted((row.start_at.strftime("%H:%M"), row.status, row.note) for row in changes.created)
    )
    cancelled = tuple(
        sorted(
            (row.start_at.strftime("%H:%M"), row.status, row.note) for row in changes.cancelled
        )
    )
    other = tuple(
        sorted(
            (before_row.start_at.strftime("%H:%M"), after_row.start_at.strftime("%H:%M"), after_row.status)
            for before_row, after_row in changes.other
        )
    )
    return (created, cancelled, other)


def behavior_match(
    base: list[TurnRecord],
    cand: list[TurnRecord],
    before: list[ApptRow] | None = None,
    *,
    base_before: list[ApptRow] | None = None,
    cand_before: list[ApptRow] | None = None,
) -> list[bool]:
    """So khớp hành vi theo từng lượt giữa hai lần chạy (vd: ref A so với ref
    B trên cùng kịch bản): cùng danh sách `tools` được gọi VÀ cùng hình dạng
    thay đổi DB (`_turn_diff_shape`: giờ/trạng thái/ghi chú, không phải chỉ số
    lượng) ở lượt đó.

    Nền của lượt 0: `base_before`/`cand_before` là lần chụp DB trước lượt 0
    của TỪNG lần chạy (I3 — id lịch seed khác nhau giữa hai ref, nên dùng
    `before` của ref này cho ref kia sẽ thấy lịch seed như "mới tạo"). `before`
    (cũ) là nền chung khi không truyền riêng; mặc định `[]`. Trả về một `bool`
    cho mỗi lượt chung của hai danh sách (độ dài = min(len(base), len(cand)))."""
    shared = before if before is not None else []
    base_root = base_before if base_before is not None else shared
    cand_root = cand_before if cand_before is not None else shared
    n = min(len(base), len(cand))
    result: list[bool] = []
    for i in range(n):
        base_record, cand_record = base[i], cand[i]
        tools_match = base_record.tools == cand_record.tools
        base_shape = _turn_diff_shape(
            base[i - 1].db_after if i > 0 else base_root, base_record
        )
        cand_shape = _turn_diff_shape(
            cand[i - 1].db_after if i > 0 else cand_root, cand_record
        )
        result.append(tools_match and base_shape == cand_shape)
    return result
