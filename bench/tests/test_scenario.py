"""Test mô hình kịch bản YAML + phân giải ngày/giờ tương đối."""

from __future__ import annotations

import re
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest

from bench.scenario import (
    ApptSpec,
    Expect,
    ExpectAppt,
    Scenario,
    Setup,
    SimGoal,
    Turn,
    load_scenarios,
    resolve_at,
    resolve_day,
)

VN_TZ = timezone(timedelta(hours=7))

SCENARIO_YAML = """
id: s01_dat_lich_co_ban
title: Đặt lịch cơ bản, khách đồng ý giờ đề xuất
kind: scripted
tags: [edge, smoke]
setup:
  customer_name: "Nguyễn Thị Lan"
  own:
    - day: today
      time: "08:00"
      note: cắt tóc cũ
  others:
    - day: tomorrow
      time: "09:00"
  owner_busy_minutes: 30
turns:
  - say: "Tôi muốn đặt lịch làm nail ngày mai lúc 9h"
  - intent: agree
    requires_asked:
      day: tomorrow
      time: "09:30"
sim: null
expect:
  booked:
    - day: tomorrow
      time: "09:30"
      note_contains: nail
  others_unchanged: true
  answer_must_include: ["9 giờ rưỡi"]
  answer_must_not_include: ["lỗi"]
  max_turns: 4
"""

BOTH_SAY_AND_INTENT_YAML = """
id: s02_loi
title: Lượt vừa có say vừa có intent
kind: scripted
tags: []
setup: {}
turns:
  - say: "xin chào"
    intent: agree
sim: null
expect: {}
"""


def _write(tmp_path: Path, name: str, content: str) -> Path:
    path = tmp_path / name
    path.write_text(content, encoding="utf-8")
    return path


def test_load_yaml_roundtrip(tmp_path: Path):
    path = _write(tmp_path, "s01.yaml", SCENARIO_YAML)

    [scenario] = load_scenarios([path])

    assert scenario == Scenario(
        id="s01_dat_lich_co_ban",
        title="Đặt lịch cơ bản, khách đồng ý giờ đề xuất",
        kind="scripted",
        tags=("edge", "smoke"),
        setup=Setup(
            customer_name="Nguyễn Thị Lan",
            own=(ApptSpec(day="today", time="08:00", note="cắt tóc cũ"),),
            others=(ApptSpec(day="tomorrow", time="09:00"),),
            owner_busy_minutes=30,
        ),
        turns=(
            Turn(say="Tôi muốn đặt lịch làm nail ngày mai lúc 9h"),
            Turn(
                intent="agree",
                requires_asked=ApptSpec(day="tomorrow", time="09:30"),
            ),
        ),
        sim=None,
        expect=Expect(
            booked=(ExpectAppt(day="tomorrow", time="09:30", note_contains="nail"),),
            others_unchanged=True,
            answer_must_include=("9 giờ rưỡi",),
            answer_must_not_include=("lỗi",),
            max_turns=4,
        ),
    )


def test_turn_rejects_both_say_and_intent(tmp_path: Path):
    path = _write(tmp_path, "s02.yaml", BOTH_SAY_AND_INTENT_YAML)

    with pytest.raises(ValueError, match=re.escape(str(path))):
        load_scenarios([path])


def test_tag_filter(tmp_path: Path):
    tagged = _write(tmp_path, "s01.yaml", SCENARIO_YAML)
    untagged = _write(
        tmp_path,
        "s03.yaml",
        SCENARIO_YAML.replace(
            "id: s01_dat_lich_co_ban", "id: s03_khong_tag"
        ).replace("tags: [edge, smoke]", "tags: [other]"),
    )

    result = load_scenarios([tagged, untagged], tags={"edge"})

    assert [s.id for s in result] == ["s01_dat_lich_co_ban"]


def test_turn_requires_exactly_one_of_say_or_intent():
    with pytest.raises(ValueError):
        Turn()
    with pytest.raises(ValueError):
        Turn(say="hi", intent="agree")


def test_sim_goal_and_scenario_kind_validation():
    sim = SimGoal(goal="đặt lịch", persona="khách lớn tuổi, nói chậm")
    assert sim.max_turns == 8

    with pytest.raises(ValueError):
        Scenario(
            id="x",
            title="x",
            kind="not_a_kind",
            tags=(),
            setup=Setup(),
            turns=(Turn(say="hi"),),
            sim=None,
            expect=Expect(),
        )


def test_resolve_day():
    now_vn = datetime(2026, 10, 17, 23, 30, tzinfo=VN_TZ)

    assert resolve_day("today", now_vn) == date(2026, 10, 17)
    assert resolve_day("tomorrow", now_vn) == date(2026, 10, 18)
    assert resolve_day("+2", now_vn) == date(2026, 10, 19)

    with pytest.raises(ValueError):
        resolve_day("yesterday", now_vn)


def test_resolve_at():
    now_vn = datetime(2026, 10, 17, 23, 30, tzinfo=VN_TZ)
    spec = ApptSpec(day="tomorrow", time="09:30")

    resolved = resolve_at(spec, now_vn)

    assert resolved == datetime(2026, 10, 18, 9, 30, tzinfo=VN_TZ)


def test_scenario_fingerprint_ignores_expect_but_not_world():
    import dataclasses

    from bench.scenario import Expect, ExpectAppt, Scenario, Setup, SimGoal, scenario_fingerprint

    base = Scenario(
        id="x", title="t", kind="simulated", tags=("llm",), setup=Setup(), turns=(),
        sim=SimGoal(goal="g", persona="p"), expect=Expect(),
    )
    tighter = dataclasses.replace(base, expect=Expect(booked=(ExpectAppt("tomorrow", "11:00"),)))
    assert scenario_fingerprint(base) == scenario_fingerprint(tighter)
    new_persona = dataclasses.replace(base, sim=SimGoal(goal="g", persona="p2"))
    assert scenario_fingerprint(base) != scenario_fingerprint(new_persona)
