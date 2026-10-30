"""Task 1.1: hợp đồng vào/ra cho `app/agents/booking` (M2–M6).

Kiểm hai bất biến: (1) model tool-facing strict-safe (không default/min/max,
mọi field bắt buộc); (2) `TaskData` round-trip giữ đúng lớp cụ thể qua
`model_dump_json` → `model_validate_json` (Pydantic v2 smart union có thể
chọn sai lớp khi các shape chồng nhau).
"""
from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any

import pytest
from pydantic import ValidationError

from app.agents.booking.schemas import (
    AppointmentArgs,
    AppointmentList,
    AppointmentRefOut,
    AskCustomerArgs,
    DecideArgs,
    ExtraRequest,
    FinishArgs,
    FreeSlots,
    Moment,
    ProposalCreated,
    ResolvedTime,
    ShopArgs,
    ShopHoursOut,
    ShopOpenOut,
    ShopStatusOut,
    TaskResult,
    TimeHint,
    appointment_args_model,
)

TOOL_FACING = [TimeHint, ExtraRequest, AppointmentArgs, ShopArgs, DecideArgs, AskCustomerArgs, FinishArgs]


def _walk(node: Any, keys: set[str]) -> set[str]:
    """Thu mọi khoá cấm (`keys`) tìm thấy ở bất kỳ độ sâu nào, kể cả trong `$defs`."""
    found: set[str] = set()
    if isinstance(node, dict):
        for k, v in node.items():
            if k in keys:
                found.add(k)
            found |= _walk(v, keys)
    elif isinstance(node, list):
        for item in node:
            found |= _walk(item, keys)
    return found


@pytest.mark.parametrize("model", TOOL_FACING, ids=lambda m: m.__name__)
def test_tool_facing_schemas_are_strict_safe(model):
    """Schema JSON của model tool-facing không chứa default/minimum/maximum, ở mọi độ sâu."""
    schema = model.model_json_schema()
    forbidden = _walk(schema, {"default", "minimum", "maximum"})
    assert forbidden == set(), f"{model.__name__}: {forbidden}"


@pytest.mark.parametrize("model", TOOL_FACING, ids=lambda m: m.__name__)
def test_tool_facing_models_have_no_defaults_so_every_field_is_required(model):
    """Mọi field của model tool-facing là bắt buộc (không default) — luật strict §12.17."""
    assert all(f.is_required() for f in model.model_fields.values()), model.__name__


def _min_time_hint() -> dict:
    return {
        "day": None,
        "weekday": None,
        "day_offset": None,
        "hour": None,
        "minute": None,
        "part_of_day": None,
        "ambiguous_week": None,
        "is_now": None,
        "anchor": None,
        "queue_id": None,
    }


def _min_appointment_args(appointment_id: str | None) -> dict:
    return {
        "time": None,
        "service": None,
        "appointment_id": appointment_id,
        "for_whom": None,
        "also": None,
    }


def test_dynamic_appointment_args_restrict_ids():
    """appointment_args_model với ids không rỗng: chỉ chấp nhận id trong danh sách."""
    M = appointment_args_model(["65a", "65b"], [])
    assert M.model_validate(_min_appointment_args("65a"))
    with pytest.raises(ValidationError):
        M.model_validate(_min_appointment_args("zzz"))


def test_dynamic_appointment_args_with_no_ids_accept_only_none():
    """appointment_args_model với ids rỗng: appointment_id chỉ nhận None (vẫn bắt buộc có mặt)."""
    M = appointment_args_model([], [])
    assert M.model_validate(_min_appointment_args(None))
    with pytest.raises(ValidationError):
        M.model_validate(_min_appointment_args("65a"))


@pytest.mark.parametrize("appointment_ids,queue_ids", [(["65a", "65b"], []), ([], [])])
def test_dynamic_appointment_args_stay_strict_safe(appointment_ids, queue_ids):
    """Model động (dù có hay không id) vẫn thoả luật strict-safe."""
    M = appointment_args_model(appointment_ids, queue_ids)
    schema = M.model_json_schema()
    forbidden = _walk(schema, {"default", "minimum", "maximum"})
    assert forbidden == set(), f"{appointment_ids}: {forbidden}"
    assert all(f.is_required() for f in M.model_fields.values())


def test_dynamic_time_hint_restricts_queue_id():
    """TimeHint động (qua AppointmentArgs.time) giới hạn queue_id theo danh sách queue."""
    M = appointment_args_model([], ["q1", "q2"])
    time_field = M.model_fields["time"].annotation
    # time: DynamicTimeHint | None — lấy lớp TimeHint động để kiểm trực tiếp
    args = {
        **_min_appointment_args(None),
        "time": {**_min_time_hint(), "queue_id": "q1"},
    }
    assert M.model_validate(args)
    bad = {**_min_appointment_args(None), "time": {**_min_time_hint(), "queue_id": "nope"}}
    with pytest.raises(ValidationError):
        M.model_validate(bad)


def _moment() -> Moment:
    return Moment(iso="2026-10-30T10:00:00+07:00", spoken="10 giờ sáng thứ Sáu")


def _task_data_variants() -> list[Any]:
    return [
        ResolvedTime(start_at=_moment(), partial_date=None, missing=[], day_from=None),
        FreeSlots(day=date(2026, 10, 30), slots=[_moment()], more=False, fetched_at=datetime.now(timezone.utc)),
        ProposalCreated(
            proposal_id="p1", kind="book", start_at=_moment(), note=None, replaces=None, queued=[]
        ),
        AppointmentList(appointments=[AppointmentRefOut(id="a1", at=_moment(), note=None)]),
        ShopHoursOut(open=_moment(), close=_moment(), closed_days=[]),
        ShopStatusOut(busy=False, until=None),
        ShopOpenOut(at=_moment(), open=True, reason="open", next_change=None),
    ]


@pytest.mark.parametrize("data", _task_data_variants(), ids=lambda d: type(d).__name__)
def test_task_result_round_trips_every_data_variant(data):
    """TaskResult.data giữ đúng lớp cụ thể sau model_dump_json → model_validate_json."""
    result = TaskResult(task_id="t1.1", agent="appointment", action="resolve_time", status="ok", data=data)
    payload = result.model_dump_json()
    restored = TaskResult.model_validate_json(payload)
    assert type(restored.data) is type(data)
    assert restored.data == data
