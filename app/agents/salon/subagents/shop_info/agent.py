"""Agent Thông tin tiệm (spec §3.1, §4.2): `SHOP_INFO` cho
`dispatch.run_subagent`, và `extract_shop_info` — CODE trích dữ kiện của lượt
từ ToolMessage, không bao giờ từ chữ model.

Chỉ đọc (`get_shop_hours`, `get_shop_status`, `is_open_at`) — không tool nào
ghi gì, không cần `ProposalService`/`AppError` như agent Đặt lịch.
"""
from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Optional

from langchain_core.messages import BaseMessage, ToolMessage

from app.agents.salon.shared.schemas import Moment, TimeFact, TimeRole, TurnFact
from app.agents.salon.subagents.dispatch import Extracted, RunContext, SubagentSpec
from app.agents.salon.subagents.shop_info.prompt import SHOP_INFO_PROMPT
from app.agents.salon.subagents.shop_info.tools import make_shop_info_tools


def _load(content: Any) -> Optional[dict]:
    if not isinstance(content, str):
        return None
    try:
        data = json.loads(content)
    except ValueError:
        return None
    return data if isinstance(data, dict) else None


def _moment_of(value: Any) -> Optional[Moment]:
    """Một `Moment` hợp lệ (iso đọc được, có múi giờ) hoặc None."""
    if not isinstance(value, dict):
        return None
    iso, spoken = value.get("iso"), value.get("spoken")
    if not isinstance(iso, str) or not isinstance(spoken, str):
        return None
    try:
        if datetime.fromisoformat(iso).tzinfo is None:
            return None
    except ValueError:
        return None
    return Moment(iso=iso, spoken=spoken)


class _Collector:
    """Gom dữ kiện theo thứ tự xuất hiện, bỏ trùng (cùng mẫu với agent Đặt lịch)."""

    def __init__(self) -> None:
        self.facts: list[TurnFact] = []
        self._keys: set[str] = set()

    def time(self, role: TimeRole, moment: Optional[Moment]) -> None:
        if moment is None:
            return
        fact = TimeFact(role=role, moment=moment)
        key = fact.model_dump_json()
        if key not in self._keys:
            self._keys.add(key)
            self.facts.append(fact)


async def extract_shop_info(messages: list[BaseMessage], ctx: RunContext) -> Extracted:
    collector = _Collector()

    for message in messages:
        if not isinstance(message, ToolMessage):
            continue
        data = _load(message.content)
        if data is None:
            continue

        if message.name == "get_shop_hours":
            collector.time("shop_open", _moment_of(data.get("open")))
            collector.time("shop_close", _moment_of(data.get("close")))
        elif message.name == "get_shop_status" and data.get("is_busy"):
            collector.time("busy_until", _moment_of(data.get("busy_until")))
        elif message.name == "is_open_at" and "error" not in data:
            collector.time("at", _moment_of(data.get("at")))

    return Extracted(facts=collector.facts, memory={})


SHOP_INFO = SubagentSpec(
    name="shop_info",
    prompt=SHOP_INFO_PROMPT,
    build_tools=make_shop_info_tools,
    extract=extract_shop_info,
)
