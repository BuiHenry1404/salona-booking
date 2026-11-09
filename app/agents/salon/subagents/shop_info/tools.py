"""Tool của agent Thông tin tiệm (spec §3.1, §6.2). Ba tool CHỈ ĐỌC — không
tool nào ghi gì (`tests/test_write_fence.py` không cần biết tới gói này).

`get_shop_hours` / `get_shop_status` đọc `ShopService`, luôn trả MỐC giờ đầy
đủ (`iso` + `spoken`) chứ không chỉ `HH:MM`, để `extract` trích được
`TimeFact` mà không phải suy ngày từ chữ model. `is_open_at` CHỈ nhận một
chuỗi ISO đầy đủ (của `parse_time`, hoặc mốc từ hai tool trên) — một
`HH:MM` hay chuỗi không đọc được đều bị từ chối, model không được tự suy giờ
cho ngày khác.
"""
from __future__ import annotations

import json
from datetime import datetime, time

from langchain_core.tools import BaseTool, StructuredTool
from pydantic import BaseModel, Field, create_model

from app.agents.salon.shared.context import closed_day_names
from app.agents.salon.subagents.dispatch import RunContext
from app.agents.salon.subagents.time_tool import parse_time
from app.core.clock import TZ, now_utc, to_local
from app.core.vi_format import format_vi_datetime
from app.services.shop import ShopService, is_within


def _dumps(payload: dict) -> str:
    return json.dumps(payload, ensure_ascii=False)


def _moment(dt: datetime) -> dict:
    local = to_local(dt)
    return {"iso": local.isoformat(), "spoken": format_vi_datetime(local)}


def _hhmm_today(hhmm: str) -> datetime:
    """`'08:00'` của giờ tiệm hôm nay -> mốc giờ VN đầy đủ (ngày + giờ)."""
    hour, minute = (int(x) for x in hhmm.split(":"))
    today = to_local(now_utc()).date()
    return datetime.combine(today, time(hour, minute), tzinfo=TZ)


def _full_iso(value: str) -> datetime | None:
    """Chuỗi ISO đầy đủ (ngày + giờ, có múi giờ) hay không — không regex: chỉ
    cần có đúng một `T` tách ngày/giờ (một `HH:MM` hay một `YYYY-MM-DD` trần
    đều không có), và `fromisoformat` đọc được."""
    parts = value.strip().split("T")
    if len(parts) != 2:
        return None
    try:
        parsed = datetime.fromisoformat(value.strip())
    except ValueError:
        return None
    return parsed.replace(tzinfo=TZ) if parsed.tzinfo is None else parsed


def make_shop_info_tools(ctx: RunContext) -> list[BaseTool]:
    shop = ShopService(ctx.db)

    async def get_shop_hours() -> str:
        hours = await shop.get_hours()
        return _dumps({
            "open": _moment(_hhmm_today(hours.open_time)),
            "close": _moment(_hhmm_today(hours.close_time)),
            # Tên thứ tiếng Việt, không phải số thô — model không được tự dịch
            # quy ước `ShopHours.closed_days` (Task 7 review; round 1 ở đây).
            "closed_days": closed_day_names(hours.closed_days),
        })

    async def get_shop_status() -> str:
        status = await shop.get_status()
        payload: dict = {"is_busy": status.is_busy}
        if status.is_busy and status.busy_until is not None:
            payload["busy_until"] = _moment(status.busy_until)
            payload["minutes_left"] = status.minutes_left
        return _dumps(payload)

    async def is_open_at(iso: str) -> str:
        at = _full_iso(iso)
        if at is None:
            return _dumps({"error": "need_iso_from_parse_time"})
        hours = await shop.get_hours()
        return _dumps({"at": _moment(at), "open": is_within(hours, at)})

    return [
        parse_time,
        StructuredTool.from_function(
            coroutine=get_shop_hours, name="get_shop_hours",
            description=("Today's opening and closing time, as JSON `open`/`close` moments, "
                        "plus `closed_days` (the Vietnamese names of the days the shop is "
                        "shut). The shop keeps the same hours every open day."),
            args_schema=create_model("GetShopHoursArgs", __base__=BaseModel),
        ),
        StructuredTool.from_function(
            coroutine=get_shop_status, name="get_shop_status",
            description=("Whether the shop is busy right now. `is_busy` false means free. "
                        "When busy, `busy_until` is the moment it frees up and `minutes_left` "
                        "counts down from now."),
            args_schema=create_model("GetShopStatusArgs", __base__=BaseModel),
        ),
        StructuredTool.from_function(
            coroutine=is_open_at, name="is_open_at",
            description=("Whether the shop is open at an exact moment. `iso` MUST be an `iso` "
                        "value copied unchanged from parse_time (or from get_shop_hours / "
                        "get_shop_status) — never a bare HH:MM or a guess. Anything else is "
                        "refused with need_iso_from_parse_time."),
            args_schema=create_model(
                "IsOpenAtArgs", __base__=BaseModel,
                iso=(str, Field(description="A full ISO datetime copied unchanged from "
                                            "parse_time's `iso`.")),
            ),
        ),
    ]
