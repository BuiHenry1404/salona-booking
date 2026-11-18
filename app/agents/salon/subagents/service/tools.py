"""Tool của agent Dịch vụ & giá (spec §3.1, §6.1). Ba tool CHỈ ĐỌC danh mục
qua `ServiceCatalogService` — không tool nào ghi gì.

Dựng lại MỖI LẦN GỌI agent con (`make_service_tools(ctx)`, async vì enum phải
đọc DB): enum `service_id` của `get_service` là các id ĐANG HOẠT ĐỘNG lúc này
— model không gõ được id bịa hay id dịch vụ đã ngừng. Danh mục rỗng thì không
dựng `get_service` (Literal rỗng không hợp lệ), như `propose_cancel` khi khách
không có lịch.

Giá / thời lượng trong JSON trả model chỉ để model đọc; dữ kiện của lượt
(`ServiceFact`) do `agent.py::extract_service` đọc LẠI từ danh mục.
"""
from __future__ import annotations

import json
from typing import Literal, Optional

from langchain_core.tools import BaseTool, StructuredTool
from pydantic import BaseModel, Field, create_model

from app.agents.salon.subagents.dispatch import RunContext
from app.models.service import Service, ServiceCategory
from app.services.service_catalog import ServiceCatalogService


def _dumps(payload: dict) -> str:
    return json.dumps(payload, ensure_ascii=False)


def _view(service: Service) -> dict:
    """Một dịch vụ cho model: giá là KHOẢNG (VND), thời lượng tính bằng phút."""
    return {
        "id": service.id,
        "name": service.name,
        "category": service.category,
        "price_min": service.price_min,
        "price_max": service.price_max,
        "duration_minutes": service.duration_minutes,
    }


async def make_service_tools(ctx: RunContext) -> list[BaseTool]:
    catalog = ServiceCatalogService(ctx.db)
    active_ids = [service.id for service in await catalog.list()]

    async def search_services(query: str) -> str:
        found = await catalog.search(query)
        return _dumps({"query": query, "services": [_view(service) for service in found]})

    async def get_service(service_id: str) -> str:
        service = await catalog.get(service_id)
        # Ngừng giữa lúc dựng enum và lúc gọi — coi như không có.
        if service is None or not service.active:
            return _dumps({"error": "service_not_found"})
        return _dumps({"service": _view(service)})

    async def list_services(category: Optional[ServiceCategory] = None) -> str:
        services = await catalog.list(category)
        return _dumps({"category": category, "services": [_view(s) for s in services]})

    tools: list[BaseTool] = [
        StructuredTool.from_function(
            coroutine=search_services, name="search_services",
            description=(
                "Find active services whose name matches `query`, as JSON `services` (each with "
                "id, name, category, price_min, price_max in VND, duration_minutes). Use the "
                "customer's own words for the service. An empty list means no match."),
            args_schema=create_model(
                "SearchServicesArgs", __base__=BaseModel,
                query=(str, Field(description="The service as the customer named it.")),
            ),
        ),
        StructuredTool.from_function(
            coroutine=list_services, name="list_services",
            description=(
                "Every active service, or only one category (nail or toc = hair), as JSON "
                "`services` with prices and durations. Use it when the customer asks for the "
                "price list, or when a search finds nothing."),
            args_schema=create_model(
                "ListServicesArgs", __base__=BaseModel,
                category=(Optional[ServiceCategory], Field(
                    default=None, description="nail, toc (hair), or null for all.")),
            ),
        ),
    ]
    if active_ids:
        tools.insert(1, StructuredTool.from_function(
            coroutine=get_service, name="get_service",
            description=("One service by its id (from a search or list result, or the session "
                         "memory), as JSON `service` with price range and duration."),
            args_schema=create_model(
                "GetServiceArgs", __base__=BaseModel,
                service_id=(Literal[tuple(active_ids)], Field(
                    description="The service id, copied from a tool result.")),
            ),
        ))
    return tools
