"""Mô hình dữ liệu kịch bản benchmark (YAML) + phân giải ngày/giờ tương đối.

Hộp đen: module này KHÔNG import `app`. Một kịch bản mô tả: trạng thái lịch
ban đầu (`Setup`), các lượt hội thoại (`Turn`, kịch bản "scripted") hoặc một
mục tiêu cho khách giả lập (`SimGoal`, kịch bản "simulated"), và kỳ vọng
(`Expect`) viết TRƯỚC khi chạy — không sửa theo output thật.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import yaml

# Giờ Việt Nam cố định, không phụ thuộc tzdata của hệ thống.
VN_TZ = timezone(timedelta(hours=7))

_VALID_INTENTS = {"agree", "decline"}
_VALID_KINDS = {"scripted", "simulated"}


@dataclass(frozen=True)
class ApptSpec:
    day: str  # "today" | "tomorrow" | "+N"
    time: str  # "HH:MM"
    note: str | None = None


@dataclass(frozen=True)
class Setup:
    customer_name: str = "Nguyễn Thị Lan"
    own: tuple[ApptSpec, ...] = ()
    others: tuple[ApptSpec, ...] = ()
    owner_busy_minutes: int | None = None


@dataclass(frozen=True)
class Turn:
    say: str | None = None
    intent: str | None = None  # "agree" | "decline"
    requires_asked: ApptSpec | None = None
    wait_s: float = 0
    fault: str | None = None

    def __post_init__(self) -> None:
        if (self.say is None) == (self.intent is None):
            raise ValueError(
                "Turn phải có đúng một trong 'say' hoặc 'intent', không cả hai và "
                "không thiếu cả hai"
            )
        if self.intent is not None and self.intent not in _VALID_INTENTS:
            raise ValueError(f"Turn.intent không hợp lệ: {self.intent!r}")


@dataclass(frozen=True)
class SimGoal:
    goal: str
    persona: str
    max_turns: int = 8


@dataclass(frozen=True)
class ExpectAppt:
    day: str
    time: str
    note_contains: str | None = None


@dataclass(frozen=True)
class Expect:
    # None = không kiểm lịch của khách (vd: kịch bản chỉ kiểm câu trả lời).
    booked: tuple[ExpectAppt, ...] | None = None
    others_unchanged: bool = True
    answer_must_include: tuple[str, ...] = ()
    answer_must_not_include: tuple[str, ...] = ()
    max_turns: int | None = None


@dataclass(frozen=True)
class Scenario:
    id: str
    title: str
    kind: str  # "scripted" | "simulated"
    tags: tuple[str, ...]
    setup: Setup
    turns: tuple[Turn, ...]
    sim: SimGoal | None
    expect: Expect

    def __post_init__(self) -> None:
        if self.kind not in _VALID_KINDS:
            raise ValueError(f"Scenario.kind không hợp lệ: {self.kind!r}")


def _appt_spec(data: dict[str, Any]) -> ApptSpec:
    return ApptSpec(day=data["day"], time=data["time"], note=data.get("note"))


def _setup(data: dict[str, Any] | None) -> Setup:
    data = data or {}
    kwargs: dict[str, Any] = {}
    if "customer_name" in data:
        kwargs["customer_name"] = data["customer_name"]
    kwargs["own"] = tuple(_appt_spec(d) for d in (data.get("own") or []))
    kwargs["others"] = tuple(_appt_spec(d) for d in (data.get("others") or []))
    kwargs["owner_busy_minutes"] = data.get("owner_busy_minutes")
    return Setup(**kwargs)


def _turn(data: dict[str, Any]) -> Turn:
    requires_asked = data.get("requires_asked")
    return Turn(
        say=data.get("say"),
        intent=data.get("intent"),
        requires_asked=_appt_spec(requires_asked) if requires_asked else None,
        wait_s=data.get("wait_s", 0),
        fault=data.get("fault"),
    )


def _sim(data: dict[str, Any] | None) -> SimGoal | None:
    if not data:
        return None
    return SimGoal(
        goal=data["goal"],
        persona=data["persona"],
        max_turns=data.get("max_turns", 8),
    )


def _expect_appt(data: dict[str, Any]) -> ExpectAppt:
    return ExpectAppt(
        day=data["day"], time=data["time"], note_contains=data.get("note_contains")
    )


def _expect(data: dict[str, Any] | None) -> Expect:
    data = data or {}
    booked_raw = data.get("booked")
    booked = (
        tuple(_expect_appt(d) for d in booked_raw) if booked_raw is not None else None
    )
    return Expect(
        booked=booked,
        others_unchanged=data.get("others_unchanged", True),
        answer_must_include=tuple(data.get("answer_must_include") or []),
        answer_must_not_include=tuple(data.get("answer_must_not_include") or []),
        max_turns=data.get("max_turns"),
    )


def _scenario_from_dict(data: dict[str, Any]) -> Scenario:
    return Scenario(
        id=data["id"],
        title=data["title"],
        kind=data["kind"],
        tags=tuple(data.get("tags") or []),
        setup=_setup(data.get("setup")),
        turns=tuple(_turn(t) for t in (data.get("turns") or [])),
        sim=_sim(data.get("sim")),
        expect=_expect(data.get("expect")),
    )


def load_scenarios(paths: list[Path], tags: set[str] | None = None) -> list[Scenario]:
    """Nạp mỗi file YAML trong `paths` thành một `Scenario`.

    Lỗi định dạng (thiếu khoá bắt buộc, giá trị sai) được bọc lại thành
    `ValueError` kèm đường dẫn file để dễ dò khi có nhiều kịch bản.
    Lọc theo `tags`: giữ lại kịch bản có giao khác rỗng với `tags` (bỏ qua
    lọc nếu `tags` là None).
    """
    scenarios: list[Scenario] = []
    for path in paths:
        try:
            raw = yaml.safe_load(path.read_text(encoding="utf-8"))
            if not isinstance(raw, dict):
                raise ValueError("nội dung YAML phải là một mapping")
            scenario = _scenario_from_dict(raw)
        except Exception as exc:
            raise ValueError(f"Lỗi nạp kịch bản '{path}': {exc}") from exc

        if tags is not None and not (set(scenario.tags) & tags):
            continue
        scenarios.append(scenario)

    return scenarios


def resolve_day(day: str, now_vn: datetime) -> date:
    """'today'/'tomorrow'/'+N' -> ngày dương lịch, tính theo `now_vn` (+07:00)."""
    local_now = now_vn.astimezone(VN_TZ) if now_vn.tzinfo is not None else now_vn
    today = local_now.date()

    if day == "today":
        return today
    if day == "tomorrow":
        return today + timedelta(days=1)
    if day.startswith("+"):
        try:
            offset = int(day[1:])
        except ValueError as exc:
            raise ValueError(f"day không hợp lệ: {day!r}") from exc
        return today + timedelta(days=offset)

    raise ValueError(f"day không hợp lệ: {day!r}")


def resolve_at(spec: ApptSpec | ExpectAppt, now_vn: datetime) -> datetime:
    """Gộp `spec.day` + `spec.time` thành một `datetime` có tz +07:00."""
    day = resolve_day(spec.day, now_vn)
    hour, minute = (int(x) for x in spec.time.split(":"))
    return datetime(day.year, day.month, day.day, hour, minute, tzinfo=VN_TZ)


def scenario_fingerprint(scenario: Scenario) -> str:
    """Băm phần "thế giới" của kịch bản — kind, setup, lượt khách, mục tiêu khách
    giả lập — KHÔNG gồm `expect`/title/tags. Hai lần chạy cùng fingerprint thì
    khách nói và DB ban đầu như nhau, nên chấm lại lần chạy cũ theo `expect`
    mới là hợp lệ (`bench rescore`); khác fingerprint thì lần chạy cũ là
    `stale_scenario`."""
    world = {
        "kind": scenario.kind,
        "setup": dataclasses.asdict(scenario.setup),
        "turns": [dataclasses.asdict(t) for t in scenario.turns],
        "sim": dataclasses.asdict(scenario.sim) if scenario.sim is not None else None,
    }
    raw = json.dumps(world, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]
