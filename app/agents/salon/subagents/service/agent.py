"""Agent Dịch vụ & giá (spec §3.1, §4.1, §6): `SERVICE` cho
`dispatch.run_subagent`, và `extract_service` — CODE trích dữ kiện của lượt
và bộ nhớ phiên từ ToolMessage, không bao giờ từ chữ model.

ToolMessage chỉ cho biết NHỮNG id nào tool đã trả; giá, tên, thời lượng của
`ServiceFact` đọc LẠI từ danh mục (`ServiceCatalogService.get`) — số trong
ToolMessage hay trong chữ model không bao giờ thành dữ kiện. Id lạ hay dịch vụ
đã ngừng thì bỏ.

Bộ nhớ phiên: `candidates` = mọi dịch vụ tool đã trả (để hỏi khách chọn một),
`service_ids` = dịch vụ đã chốt — chọn qua `get_service`, hoặc kết quả duy
nhất khi tra ra đúng một dịch vụ.
"""
from __future__ import annotations

import json
from typing import Any, Optional

from langchain_core.messages import BaseMessage, ToolMessage

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


async def extract_service(messages: list[BaseMessage], ctx: RunContext) -> Extracted:
    catalog = ServiceCatalogService(ctx.db)
    prior = ctx.session.memory if ctx.session is not None else {}

    returned: list[str] = []    # mọi id tool đã trả, theo thứ tự, không trùng
    picked: list[str] = []      # id lấy qua get_service
    for message in messages:
        if not isinstance(message, ToolMessage) or message.name not in (
                "search_services", "get_service", "list_services"):
            continue
        data = _load(message.content)
        if data is None:
            continue
        for service_id in _ids_in(data):
            if service_id not in returned:
                returned.append(service_id)
            if message.name == "get_service" and service_id not in picked:
                picked.append(service_id)

    facts: list[ServiceFact] = []
    for service_id in returned:
        service = await catalog.get(service_id)
        if service is None or not service.active:
            continue
        facts.append(ServiceFact(service_id=service.id, name=service.name,
                                 price_min=service.price_min, price_max=service.price_max,
                                 duration_minutes=service.duration_minutes))

    real = [fact.service_id for fact in facts]
    chosen = [sid for sid in picked if sid in real] or (real if len(real) == 1 else [])
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
