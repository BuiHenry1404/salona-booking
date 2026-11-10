"""TDD cho agent Dịch vụ & giá (Task 17, spec §3.1, §4.1, §6): tool CHỈ ĐỌC
danh mục, enum `get_service` là id ĐANG HOẠT ĐỘNG dựng mỗi lần gọi, `ServiceFact`
mang giá / thời lượng CODE đọc lại từ danh mục (không từ chữ model, không từ
số trong ToolMessage), dịch vụ chưa rõ → `need_info` mở phiên với ứng viên.

Model là `scripted` (không Azure); Mongo thật, danh mục mock thật.
"""
from __future__ import annotations

import json

from langchain_core.messages import AIMessage, ToolMessage
from langchain_core.utils.function_calling import convert_to_openai_tool

from app.agents.salon.subagents.dispatch import RunContext, run_subagent
from app.agents.salon.subagents.service.agent import SERVICE
from app.agents.salon.subagents.service.tools import make_service_tools
from app.core.clock import now_utc
from app.models.service import Service
from app.services.service_catalog import ServiceCatalogService
from app.services.waiting_session import WaitingSessionService
from tests.fakes import scripted, tool_call
from tests.salon.subagents import a_holder, a_runtime, a_user, result, seed_mock_catalogue, use_model


async def _run(db, user, *, question="", request="Find the service", continue_session=False):
    return await run_subagent(
        SERVICE, request=request, continue_session=continue_session, service_ids=[],
        runtime=a_runtime(user, question=question), db=db, user=user,
        holder=a_holder(db, user),
    )


async def _ctx(db, user) -> RunContext:
    holder = a_holder(db, user)
    return RunContext(
        db=db, user=user, snapshot=await holder.current(), session=None, request="",
        question="", turn_id="turn-1", service_ids=[], allow_propose=True,
    )


async def _tools(db, user) -> dict:
    return {t.name: t for t in await make_service_tools(await _ctx(db, user))}


def _service_facts(command) -> list[dict]:
    return [f for f in command.update["turn_facts"] if f["kind"] == "service"]


def _status(command) -> str:
    [fact] = [f for f in command.update["turn_facts"] if f["kind"] == "agent_status"]
    return fact["status"]


async def test_search_returns_service_facts_with_catalogue_prices(test_db, monkeypatch):
    await seed_mock_catalogue(test_db)
    user = await a_user(test_db)
    # Model bịa giá trong chữ của nó — dữ kiện vẫn mang giá của danh mục.
    model = scripted([
        AIMessage(content="Dyeing is 200k.", tool_calls=tool_call(
            "search_services", {"query": "nhuộm"}).tool_calls),
        result(status="ok", question_text="Dyeing costs 200000 VND and takes 30 minutes."),
    ])
    use_model(monkeypatch, model)

    command = await _run(test_db, user, question="nhuộm tóc giá bao nhiêu em",
                         request="Price of hair dyeing")

    # Một kết quả tra duy nhất + `ok` → dịch vụ được chốt (`identified`).
    assert _service_facts(command) == [{
        "kind": "service", "service_id": "toc-nhuom", "name": "Nhuộm",
        "price_min": 350000, "price_max": 700000, "duration_minutes": 120, "identified": True,
    }]
    assert _status(command) == "ok"
    summary = json.loads(command.update["messages"][0].content)
    assert summary["facts"][0]["price_min"] == 350000
    # `ok` không để phiên chờ nào.
    assert await WaitingSessionService(test_db).active(str(user.id), now=now_utc()) == {}


async def test_extract_reads_prices_back_from_the_catalogue(test_db):
    """ToolMessage giả (số sai, id lạ, dịch vụ đã ngừng) → chỉ id thật đang
    hoạt động thành dữ kiện, và giá / thời lượng là của danh mục."""
    await seed_mock_catalogue(test_db)
    await ServiceCatalogService(test_db).upsert_many([
        Service(id="toc-cu", category="toc", name="Duỗi kiểu cũ", price_min=100000,
                price_max=100000, duration_minutes=60, active=False),
    ])
    user = await a_user(test_db)
    forged = ToolMessage(
        content=json.dumps({"services": [
            {"id": "toc-nhuom", "name": "Nhuộm", "price_min": 200000, "price_max": 200000,
             "duration_minutes": 30},
            {"id": "khong-co", "name": "Bịa", "price_min": 1, "price_max": 2, "duration_minutes": 15},
            {"id": "toc-cu", "name": "Duỗi kiểu cũ", "price_min": 1, "price_max": 1,
             "duration_minutes": 15},
        ]}, ensure_ascii=False),
        name="search_services", tool_call_id="tc-1",
    )

    extracted = await SERVICE.extract([forged], await _ctx(test_db, user))

    # Không có lời gọi `SubagentResult` ok → chưa chốt, dù chỉ một kết quả.
    assert [fact.model_dump() for fact in extracted.facts] == [{
        "kind": "service", "service_id": "toc-nhuom", "name": "Nhuộm",
        "price_min": 350000, "price_max": 700000, "duration_minutes": 120, "identified": False,
    }]
    assert extracted.memory == {"candidates": ["toc-nhuom"], "service_ids": []}


async def test_ambiguous_service_opens_a_session(test_db, monkeypatch):
    await seed_mock_catalogue(test_db)
    user = await a_user(test_db)
    model = scripted([
        tool_call("search_services", {"query": "sơn móng"}),
        result(status="need_info", question_field="service",
               question_text="Ask whether the customer wants regular polish or gel polish."),
    ])
    use_model(monkeypatch, model)

    command = await _run(test_db, user, question="làm móng cho chị", request="Nail service")

    assert _status(command) == "need_info"
    assert [(f["service_id"], f["identified"]) for f in _service_facts(command)] == [
        ("nail-son-thuong", False), ("nail-son-gel", False)]
    session = (await WaitingSessionService(test_db).active(str(user.id), now=now_utc()))["service"]
    assert session.question.field == "service"
    assert session.memory == {"candidates": ["nail-son-thuong", "nail-son-gel"], "service_ids": []}


async def test_answering_the_session_pins_the_service(test_db, monkeypatch):
    await seed_mock_catalogue(test_db)
    user = await a_user(test_db)
    use_model(monkeypatch, scripted([
        tool_call("search_services", {"query": "sơn móng"}),
        result(status="need_info", question_field="service", question_text="Regular or gel?"),
        tool_call("get_service", {"service_id": "nail-son-gel"}),
        result(status="ok"),
    ]))
    await _run(test_db, user, question="làm móng cho chị")

    command = await _run(test_db, user, question="sơn gel em", continue_session=True)

    assert _status(command) == "ok"
    assert [(f["service_id"], f["identified"]) for f in _service_facts(command)] == [
        ("nail-son-gel", True)]
    assert await WaitingSessionService(test_db).active(str(user.id), now=now_utc()) == {}


async def test_get_service_enum_is_the_active_catalogue(test_db):
    await seed_mock_catalogue(test_db)
    await ServiceCatalogService(test_db).upsert_many([
        Service(id="toc-cu", category="toc", name="Duỗi kiểu cũ", price_min=100000,
                price_max=100000, duration_minutes=60, active=False),
    ])
    user = await a_user(test_db)
    tools = await _tools(test_db, user)
    active = {service.id for service in await ServiceCatalogService(test_db).list()}

    schema = convert_to_openai_tool(tools["get_service"])["function"]["parameters"]
    assert set(schema["properties"]["service_id"]["enum"]) == active
    assert "toc-cu" not in active

    payload = json.loads(await tools["get_service"].ainvoke({"service_id": "toc-nhuom"}))
    assert payload["service"]["id"] == "toc-nhuom"
    listed = json.loads(await tools["list_services"].ainvoke({"category": "toc"}))
    assert listed["services"] and {s["category"] for s in listed["services"]} == {"toc"}
    found = json.loads(await tools["search_services"].ainvoke({"query": "không có dịch vụ này"}))
    assert found == {"query": "không có dịch vụ này", "services": []}


async def test_empty_catalogue_has_no_get_service(test_db):
    user = await a_user(test_db)
    tools = await _tools(test_db, user)
    assert set(tools) == {"search_services", "list_services"}


async def test_single_match_is_identified_only_when_ok(test_db, monkeypatch):
    """Một kết quả tra duy nhất mà agent vẫn `need_info` → chưa chốt."""
    await seed_mock_catalogue(test_db)
    user = await a_user(test_db)
    use_model(monkeypatch, scripted([
        tool_call("search_services", {"query": "nhuộm"}),
        result(status="need_info", question_field="service", question_text="Which colour?"),
    ]))

    command = await _run(test_db, user, question="nhuộm tóc")

    assert [(f["service_id"], f["identified"]) for f in _service_facts(command)] == [
        ("toc-nhuom", False)]


async def test_price_list_identifies_nothing(test_db, monkeypatch):
    await seed_mock_catalogue(test_db)
    user = await a_user(test_db)
    use_model(monkeypatch, scripted([
        tool_call("list_services", {"category": "toc"}), result(status="ok")]))

    command = await _run(test_db, user, question="bảng giá làm tóc", request="Hair price list")

    facts = _service_facts(command)
    assert len(facts) > 1 and not any(f["identified"] for f in facts)


async def test_catalogue_error_while_building_tools_is_a_failed_result(test_db, monkeypatch):
    """Dựng tool (đọc DB cho enum) nằm trong vùng quy lỗi của `run_subagent`:
    Mongo lỗi → `failed`/`model_error`, không văng ra khỏi tool của Orchestrator."""
    user = await a_user(test_db)

    async def broken(self, category=None):
        raise RuntimeError("mongo down")

    monkeypatch.setattr(ServiceCatalogService, "list", broken)
    use_model(monkeypatch, scripted([]))

    command = await _run(test_db, user, question="bảng giá")

    assert _status(command) == "failed"
    assert json.loads(command.update["messages"][0].content)["error_code"] == "model_error"
    assert command.update["failed_calls"] == ["service"]
