"""TDD cho agent Thông tin tiệm (Task 10, spec §3.1 / §4.1): tool CHỈ ĐỌC,
`is_open_at` từ chối mọi thứ không phải ISO đầy đủ của `parse_time`, giờ mở
cửa / bận-rảnh / mốc đã kiểm trở thành `TimeFact`.

Model là `scripted` (không Azure); Mongo thật.
"""
from __future__ import annotations

import json

from app.agents.salon.subagents.dispatch import RunContext, run_subagent
from app.agents.salon.subagents.shop_info.agent import SHOP_INFO
from app.agents.salon.subagents.shop_info.tools import make_shop_info_tools
from app.core.clock import now_utc, to_local
from app.services.shop import ShopService
from tests.fakes import scripted, tool_call
from tests.salon.subagents import a_holder, a_runtime, a_user, default_hours, result, use_model


async def _run(db, user, *, question="", request="Shop hours"):
    return await run_subagent(
        SHOP_INFO, request=request, continue_session=False, service_ids=[],
        runtime=a_runtime(user, question=question), db=db, user=user,
        holder=a_holder(db, user),
    )


async def _ctx(db, user) -> RunContext:
    holder = a_holder(db, user)
    return RunContext(
        db=db, user=user, snapshot=await holder.current(), session=None, request="",
        question="", turn_id="turn-1", service_ids=[], allow_propose=True,
    )


def _time_facts(command, role) -> list[dict]:
    return [f["moment"] for f in command.update["turn_facts"]
            if f["kind"] == "time" and f["role"] == role]


async def test_hours_become_time_facts(test_db, monkeypatch):
    await ShopService(test_db).set_hours("08:00", "19:00", [0])
    user = await a_user(test_db)
    model = scripted([
        tool_call("get_shop_hours", {}),
        result(status="ok"),
    ])
    use_model(monkeypatch, model)

    command = await _run(test_db, user, request="What are the opening hours?")

    today = to_local(now_utc()).date().isoformat()
    [open_moment] = _time_facts(command, "shop_open")
    [close_moment] = _time_facts(command, "shop_close")
    assert open_moment["iso"].startswith(f"{today}T08:00")
    assert close_moment["iso"].startswith(f"{today}T19:00")


async def test_busy_status_becomes_time_fact(test_db, monkeypatch):
    await default_hours(test_db)
    await ShopService(test_db).set_busy(30)
    user = await a_user(test_db)
    model = scripted([
        tool_call("get_shop_status", {}),
        result(status="ok"),
    ])
    use_model(monkeypatch, model)

    command = await _run(test_db, user, request="Is the shop busy right now?")

    [busy_until] = _time_facts(command, "busy_until")
    assert busy_until["iso"]


async def test_is_open_at_rejects_hhmm(test_db):
    await default_hours(test_db)
    user = await a_user(test_db)
    ctx = await _ctx(test_db, user)
    tools = {t.name: t for t in make_shop_info_tools(ctx)}

    for bad in ("15:00", "2026-01-01", "not a time"):
        payload = json.loads(await tools["is_open_at"].ainvoke({"iso": bad}))
        assert payload == {"error": "need_iso_from_parse_time"}


async def test_parse_then_is_open_at_answers_a_relative_question(test_db, monkeypatch):
    await ShopService(test_db).set_hours("08:00", "19:00", [])
    user = await a_user(test_db)
    ctx = await _ctx(test_db, user)
    tools = {t.name: t for t in make_shop_info_tools(ctx)}

    # "tối thứ Năm" (Thursday evening, 8pm) — weekday 3 = Thứ Năm, quy ước
    # text_scan/parse_time 0 = Thứ Hai; "toi" = buổi tối.
    parsed = json.loads(await tools["parse_time"].ainvoke(
        {"weekday": 3, "next_week": False, "hour": 8, "part_of_day": "toi"}))
    checked = json.loads(await tools["is_open_at"].ainvoke({"iso": parsed["iso"]}))
    assert checked["at"]["iso"] == parsed["iso"]
    assert checked["open"] is False  # 20h ngoài giờ đóng cửa 19h

    model = scripted([
        tool_call("parse_time", {"weekday": 3, "next_week": False, "hour": 8, "part_of_day": "toi"}),
        tool_call("is_open_at", {"iso": parsed["iso"]}),
        result(status="ok"),
    ])
    use_model(monkeypatch, model)

    command = await _run(test_db, user, request="Is the shop open Thursday evening?")

    [at_moment] = _time_facts(command, "at")
    assert at_moment["iso"] == parsed["iso"]
