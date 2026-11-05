"""`apply_decision` — thân thật của tool `decide_pending` (§5.5).

Task 3.1 chỉ cần CHỮ KÝ ổn định để `tools.py` import được ngay (seam): tool
`decide_pending` gọi thẳng `apply_decision`, không biết/không cần biết cách
nó cài đặt. Task 3.2 lấp nội dung: veto (i) trước khi gọi tới đây, rồi bảng
§5.5 (approve/reject/change/unclear/none) gọi `ProposalService` đúng một chỗ
— đây sẽ là module DUY NHẤT ngoài `button_path` được phép gọi
`ProposalService.approve` (`tests/test_write_fence.py`, cập nhật ở Task 3.2).

Test của Task 3.1 không phụ thuộc hành vi thật của hàm này (ledger/schema
test dùng `run_appointment`/`run_shop` giả, không chạm `decide_pending`).
"""
from __future__ import annotations

from motor.motor_asyncio import AsyncIOMotorDatabase

from app.agents.booking.context import TurnContext
from app.agents.booking.schemas import Aside, DecideLabel, PendingOutcome
from app.models.user import User


async def apply_decision(
    db: AsyncIOMotorDatabase,
    user: User,
    ctx: TurnContext,
    *,
    label: DecideLabel,
    aside: Aside | None,
    reason: str,
) -> PendingOutcome:
    """Chưa cài đặt — Task 3.2 lấp đầy theo §5.5 (veto, bảng nhãn, `ProposalService`)."""
    raise NotImplementedError("Task 3.2 lấp đầy apply_decision (§5.5)")
