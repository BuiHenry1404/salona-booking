"""Nạp toàn bộ `bench/scenarios/` và kiểm định dạng — không gọi server/Mongo.

Đây là hàng rào cho Task 11 (`docs/test-scenarios/02-llm-chat-scenarios.md`,
`docs/AGENT_GRAPH_REVIEW.md`): sai sót ở đây là sai một file YAML, không phải
hành vi của app, nên test không cần `live`.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from bench.scenario import load_scenarios

SCENARIOS_DIR = Path(__file__).resolve().parent.parent / "scenarios"

# Mã lỗi hợp lệ cho một kịch bản `edge`: G-xx (review AGENT_GRAPH_REVIEW.md),
# LLM-xx (chuyển thể từ 02-llm-chat-scenarios.md, vd LLM-33 -> pending hết hạn),
# `is_affirmative` (bug cũ ghi trong CONTEXT.md, không có mã G-xx riêng), hoặc
# `BTN` (kịch bản nghiệm thu lượt bấm nút/confirm_proposal, Task 7 — không
# phải một bug cụ thể nên không có số).
_ERROR_CODE_RE = re.compile(r"^(G-\d+|LLM-\d+|is_affirmative|BTN)$")


def _all_paths() -> list[Path]:
    paths = sorted(SCENARIOS_DIR.rglob("*.yaml"))
    assert paths, f"không tìm thấy file .yaml nào dưới {SCENARIOS_DIR}"
    return paths


def test_all_scenarios_load_and_are_well_formed():
    paths = _all_paths()
    scenarios = load_scenarios(paths)
    assert len(scenarios) == len(paths)

    ids = [s.id for s in scenarios]
    dupes = {i for i in ids if ids.count(i) > 1}
    assert not dupes, f"id trùng nhau: {dupes}"

    valid_times = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")
    for scenario in scenarios:
        for turn in scenario.turns:
            if turn.requires_asked is not None:
                assert valid_times.match(turn.requires_asked.time), (
                    f"{scenario.id}: requires_asked.time không hợp lệ: "
                    f"{turn.requires_asked.time!r}"
                )
        if scenario.expect.booked is not None:
            for appt in scenario.expect.booked:
                assert valid_times.match(appt.time), (
                    f"{scenario.id}: expect.booked time không hợp lệ: {appt.time!r}"
                )

        if scenario.kind == "scripted":
            assert len(scenario.turns) >= 1, f"{scenario.id}: scripted phải có >= 1 lượt"
        if scenario.kind == "simulated":
            assert scenario.sim is not None, f"{scenario.id}: simulated phải có `sim`"

        if "edge" in scenario.tags:
            codes = [t for t in scenario.tags if _ERROR_CODE_RE.match(t)]
            assert codes, f"{scenario.id}: kịch bản edge thiếu tag mã lỗi (G-xx/LLM-xx/is_affirmative)"


def test_edge_scenarios_are_scripted_with_edge_tag():
    """Mọi file dưới `edge/` phải là `scripted` + tag `edge`, NGOẠI TRỪ kịch bản
    tag `compaction` (`compaction-long`): đó là kịch bản nghiệm thu riêng của dự
    án (ConversationState compaction), không phải một mã lỗi G-xx, nên nó là
    `simulated` và không mang tag `edge`."""
    paths = sorted((SCENARIOS_DIR / "edge").glob("*.yaml"))
    scenarios = load_scenarios(paths)
    assert len(scenarios) == len(paths)
    for scenario in scenarios:
        if "compaction" in scenario.tags:
            assert scenario.kind == "simulated", scenario.id
            continue
        assert scenario.kind == "scripted", scenario.id
        assert "edge" in scenario.tags, scenario.id


def test_fault_scenarios_declare_a_fault_turn():
    paths = sorted((SCENARIOS_DIR / "fault").glob("*.yaml"))
    scenarios = load_scenarios(paths)
    assert len(scenarios) == len(paths)
    for scenario in scenarios:
        assert scenario.kind == "scripted", scenario.id
        assert "fault" in scenario.tags, scenario.id
        assert any(t.fault for t in scenario.turns), (
            f"{scenario.id}: kịch bản fault phải có ít nhất 1 lượt khai báo `fault`"
        )


def test_llm_scenario_count_is_32():
    paths = sorted((SCENARIOS_DIR / "llm").glob("*.yaml"))
    scenarios = load_scenarios(paths)
    assert len(scenarios) == 32, (
        "LLM-01..LLM-32 của docs/test-scenarios/02-llm-chat-scenarios.md "
        "(LLM-33 chuyển thành edge/pending-het-han, LLM-34 chuyển thành "
        "fault/azure-cham, LLM-35 bỏ)"
    )
    for scenario in scenarios:
        assert scenario.kind == "simulated", scenario.id
        assert "llm" in scenario.tags, scenario.id
        assert scenario.sim is not None, scenario.id

    ids = sorted(s.id for s in scenarios)
    assert ids == [f"LLM-{i:02d}" for i in range(1, 33)]


def _by_id() -> dict:
    return {s.id: s for s in load_scenarios(_all_paths())}


# Persona ghi rõ không có ý định đặt lịch / không tự chốt → kỳ vọng viết sẵn
# là KHÔNG có lịch nào của khách (I7), để ghi sai của chính khách bị bắt.
_NO_BOOKING_INTENT = (
    "LLM-09", "LLM-14", "LLM-15", "LLM-24", "LLM-25", "LLM-26", "LLM-27",
)


@pytest.mark.parametrize("sid", _NO_BOOKING_INTENT)
def test_no_booking_intent_scenarios_expect_no_booking(sid):
    assert _by_id()[sid].expect.booked == ()


def _minutes(time_text: str) -> int:
    h, m = time_text.split(":")
    return int(h) * 60 + int(m)


def test_expected_bookings_do_not_overlap_seeded_others():
    # I4: lịch dài 60 phút trên lưới 15 phút — một lịch kỳ vọng chồng lên
    # lịch của khách khác cùng ngày là bất khả thi (LLM-11 cũ: 10:30 vs 10:00).
    for sc in load_scenarios(_all_paths()):
        for exp in sc.expect.booked or ():
            for other in sc.setup.others:
                if other.day != exp.day:
                    continue
                gap = abs(_minutes(exp.time) - _minutes(other.time))
                assert gap >= 60, f"{sc.id}: kỳ vọng {exp.time} chồng lịch khác {other.time}"


@pytest.mark.parametrize("sid", ("LLM-26", "LLM-27"))
def test_persona_day_matches_seeded_others(sid):
    # T11: lịch của khách khác seed ở ngày mai (+1) → persona phải nói "mai",
    # không nói "hôm nay"/"chiều nay".
    sc = _by_id()[sid]
    assert all(o.day in ("+1", "tomorrow") for o in sc.setup.others)
    text = f"{sc.sim.goal} {sc.sim.persona}".lower()
    assert "hôm nay" not in text and "chiều nay" not in text
    assert "mai" in text
