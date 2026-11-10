"""Agent Dịch vụ & giá (spec §3.1, §4.1, §6): `SERVICE` cho
`dispatch.run_subagent`, và `extract_service` — CODE trích dữ kiện của lượt
và bộ nhớ phiên từ ToolMessage, không bao giờ từ chữ model.

ToolMessage chỉ cho biết NHỮNG id nào tool đã trả; giá, tên, thời lượng của
`ServiceFact` đọc LẠI từ danh mục (`ServiceCatalogService.get`) — số trong
ToolMessage hay trong chữ model không bao giờ thành dữ kiện. Id lạ hay dịch vụ
đã ngừng thì bỏ.

Dịch vụ ĐÃ CHỐT (`ServiceFact.identified`, Ruling R16): chọn qua
`get_service`, hoặc một lần `search_services` ra đúng một dịch vụ VÀ agent kết
thúc `ok`. Dịch vụ chỉ được tra ra cùng nhiều dịch vụ khác hay liệt kê trong
bảng giá vẫn thành dữ kiện (guard cho báo giá của nó) nhưng không được chốt —
nên không thành "dịch vụ đã biết" của cuộc trò chuyện.

Bộ nhớ phiên: `candidates` = mọi dịch vụ tool đã trả (để hỏi khách chọn một),
`service_ids` = dịch vụ đã chốt.
"""
from __future__ import annotations

import json
from typing import Any, Optional

from langchain_core.messages import AIMessage, BaseMessage, ToolMessage

from app.agents.salon.shared.schemas import ServiceFact
from app.agents.salon.subagents.dispatch import Extracted, RunContext, SubagentSpec
from app.agents.salon.subagents.service.prompt import SERVICE_PROMPT
from app.agents.salon.subagents.service.tools import make_service_tools
from app.services.service_catalog import ServiceCatalogService


def _load(content: Any) -> Optional[dict]:
    if not isinstance(content, str):
        return None
    try:
        data = json.loads(content)
    except ValueError:
        return None
    return data if isinstance(data, dict) else None


def _ids_in(data: dict) -> list[str]:
    """Id dịch vụ trong một kết quả tool (`services` của search/list, `service` của get)."""
    items = data.get("services") if isinstance(data.get("services"), list) else []
    if isinstance(data.get("service"), dict):
        items = [data["service"]]
    return [item["id"] for item in items if isinstance(item, dict) and isinstance(item.get("id"), str)]


def _strings(value: Any) -> list[str]:
    return [item for item in value if isinstance(item, str)] if isinstance(value, list) else []


def _finished_ok(messages: list[BaseMessage]) -> bool:
    """Lời gọi `SubagentResult` (ToolStrategy) cuối cùng có `status` ok không —
    chỉ đọc trường có cấu trúc, không đọc chữ."""
    for message in reversed(messages):
        if not isinstance(message, AIMessage):
            continue
        for call in reversed(message.tool_calls):
            if call["name"] == "SubagentResult":
                return call["args"].get("status") == "ok"
    return False


async def extract_service(messages: list[BaseMessage], ctx: RunContext) -> Extracted:
    catalog = ServiceCatalogService(ctx.db)
    prior = ctx.session.memory if ctx.session is not None else {}

    returned: list[str] = []    # mọi id tool đã trả, theo thứ tự, không trùng
    picked: list[str] = []      # id lấy qua get_service
    single: list[str] = []      # kết quả của các lần search ra đúng một dịch vụ
    for message in messages:
        if not isinstance(message, ToolMessage) or message.name not in (
                "search_services", "get_service", "list_services"):
            continue
        data = _load(message.content)
        if data is None:
            continue
        ids = _ids_in(data)
        for service_id in ids:
            if service_id not in returned:
                returned.append(service_id)
            if message.name == "get_service" and service_id not in picked:
                picked.append(service_id)
        if message.name == "search_services" and len(ids) == 1 and ids[0] not in single:
            single.append(ids[0])

    chosen_ids = set(picked) | (set(single) if _finished_ok(messages) else set())
    facts: list[ServiceFact] = []
    for service_id in returned:
        service = await catalog.get(service_id)
        if service is None or not service.active:
            continue
        facts.append(ServiceFact(service_id=service.id, name=service.name,
                                 price_min=service.price_min, price_max=service.price_max,
                                 duration_minutes=service.duration_minutes,
                                 identified=service.id in chosen_ids))

    real = [fact.service_id for fact in facts]
    chosen = [fact.service_id for fact in facts if fact.identified]
    memory = {
        "candidates": real or _strings(prior.get("candidates")),
        "service_ids": chosen or _strings(prior.get("service_ids")),
    }
    return Extracted(facts=facts, memory=memory)


SERVICE = SubagentSpec(
    name="service",
    prompt=SERVICE_PROMPT,
    build_tools=make_service_tools,
    extract=extract_service,
)
