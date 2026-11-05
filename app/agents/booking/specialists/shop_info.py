"""Shop Info specialist agent (Task 2.5, §8.2 của thiết kế) - thực thi đúng
MỘT `TaskSpec` mà Orchestrator giao, chỉ đọc (3 tool `get_shop_hours`,
`get_shop_status`, `is_open_at` - không tool nào ghi).

`run_task` không tin chữ model khi nó đáng ra phải đến từ `ShopService`: sau
khi specialist trả `status="ok"`, code đọc lại `get_hours`/`get_status` (và
`is_open_at(at)` khi `data` là `ShopOpenOut`) rồi gọi `check_facts` - lệch
một mốc giờ, một ngày nghỉ, hay một lý do đóng cửa là `failed`,
`error_code="fact_mismatch"`, `retryable=False` (model chép sai thì thử lại
vẫn chép sai, không khác gì)."""
from __future__ import annotations

from datetime import datetime

from motor.motor_asyncio import AsyncIOMotorDatabase

from app.agents.booking.context import TurnContext
from app.agents.booking.prompts.shop_info import SHOP_INFO_PROMPT
from app.agents.booking.schemas import (ShopHoursOut, ShopOpenOut, ShopStatusOut,
                                         TaskData, TaskResult, TaskSpec)
from app.agents.booking.specialists.base import build_specialist, run_specialist
from app.agents.booking.specialists.guard import allowlist
from app.agents.booking_graph.tools import _CLOSED_DAY_NAMES, make_shop_tools
from app.core.clock import ensure_aware
from app.core.config import settings
from app.models.shop import ShopHours, ShopStatusView
from app.models.user import User
from app.services.shop import ShopService

# §8.2 nguyên văn - allowlist tool theo action, áp khi dựng agent cho MỖI task.
ALLOWLIST: dict[str, tuple[str, ...]] = {
    "get_hours": ("get_shop_hours",),
    "get_status": ("get_shop_status",),
    "is_open_at": ("get_shop_hours", "get_shop_status", "is_open_at"),
}


def _hhmm(iso: str) -> str:
    """Cắt phần HH:MM khỏi `iso` của một `Moment` giờ mở/đóng cửa - model
    chép thẳng `hhmm` của tool ("08:00") vào đó, nhưng có thể lỡ chép một
    chuỗi ISO đủ ("...T08:00:00+07:00"); không dùng regex (ngân sách 0 của
    gói này) nên cắt bằng `split`/slice."""
    part = iso.split("T")[-1] if "T" in iso else iso
    return part[:5]


def check_facts(
    data: TaskData, *, hours: ShopHours, status: ShopStatusView, open_at: bool | None,
) -> bool:
    """So dữ kiện model trả với dữ liệu THẬT đọc lại từ `ShopService` ngay
    sau khi agent trả `ok` (§8.2). Trả `False` ở bất kỳ ca lệch nào."""
    if isinstance(data, ShopHoursOut):
        closed_names = {
            _CLOSED_DAY_NAMES[d] for d in hours.closed_days if 0 <= d < len(_CLOSED_DAY_NAMES)
        }
        return (
            _hhmm(data.open.iso) == hours.open_time
            and _hhmm(data.close.iso) == hours.close_time
            and set(data.closed_days) == closed_names
        )
    if isinstance(data, ShopStatusOut):
        return data.busy == status.is_busy and (data.until is None) == (status.busy_until is None)
    if isinstance(data, ShopOpenOut):
        if data.open != open_at:
            return False
        if data.reason == "owner_busy" and not status.is_busy:
            return False
        return True
    return False


def _fact_mismatch(spec: TaskSpec, *, attempts: int) -> TaskResult:
    return TaskResult(
        task_id=spec.task_id, agent=spec.agent, action=spec.action,
        status="failed", error_code="fact_mismatch", attempts=attempts, retryable=False,
    )


async def run_task(
    db: AsyncIOMotorDatabase, user: User, spec: TaskSpec, ctx: TurnContext, question: str,
) -> TaskResult:
    """Chạy đúng một task của Shop Info Agent."""
    # (1) dựng agent MỖI task: chỉ đúng tool theo action - không lưới đề xuất
    # nào cần ở đây, không tool nào ghi.
    tools = allowlist(make_shop_tools(db, user), ALLOWLIST[spec.action])
    agent = build_specialist(prompt=SHOP_INFO_PROMPT, tools=tools, tag="shop_info")
    result, _messages = await run_specialist(
        agent, spec, ctx, question, timeout=settings.orch_task_timeout_seconds,
    )

    if result.status != "ok" or result.data is None:
        return result

    # (2) đối chiếu sau khi agent trả `ok` - đọc lại DB/service, không tin
    # chữ model. `is_open_at` chỉ cần tính khi `data` là `ShopOpenOut`.
    shop = ShopService(db)
    hours = await shop.get_hours()
    status = await shop.get_status()
    open_at: bool | None = None
    if isinstance(result.data, ShopOpenOut):
        at = ensure_aware(datetime.fromisoformat(result.data.at.iso))
        # `ShopService.is_open_at` chỉ xét giờ mở cửa - nó KHÔNG biết chủ
        # tiệm đang bận (fix round 1: mọi câu "trong giờ mở cửa nhưng chủ
        # đang bận" thật đều bị bắt nhầm fact_mismatch). Lặp lại đúng điều
        # kiện `owner_busy` mà `is_open_at` tool đã dùng
        # (`app/agents/booking_graph/tools.py`): bận VÀ có `busy_until` VÀ
        # mốc hỏi còn trước `busy_until` đó - dùng lại `status` đã đọc ở
        # trên, không gọi `get_status` lần hai.
        within_hours = await shop.is_open_at(at)
        owner_busy = bool(
            status.is_busy and status.busy_until and at < ensure_aware(status.busy_until)
        )
        open_at = within_hours and not owner_busy

    if not check_facts(result.data, hours=hours, status=status, open_at=open_at):
        return _fact_mismatch(spec, attempts=result.attempts)

    return result
