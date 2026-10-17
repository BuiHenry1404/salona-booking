from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel

from app.models.base import BaseDocument

ProposalKind = Literal["book", "move", "cancel"]
ProposalStatus = Literal[
    "pending", "approving", "approved", "rejected", "expired", "superseded", "failed",
]


class TargetSnapshot(BaseModel):
    """Lịch đích (dời/hủy) LÚC ĐỀ XUẤT. Lúc duyệt mà lịch đã khác — bị hủy, bị
    dời qua REST hay Telegram — thì không duyệt: khách đồng ý với lịch họ đã
    thấy, không phải với lịch hiện tại."""

    status: str
    start_at: datetime


class Proposal(BaseDocument):
    """Một đề xuất đặt/dời/hủy đang chờ khách đồng ý.

    Nằm trong DB nghiệp vụ (collection `proposals`), KHÔNG trong checkpoint:
    checkpoint có TTL và chỉ là chỗ cất lịch sử model, không phải nguồn sự thật
    cho việc ghi lịch. Graph chỉ mang `proposal_id`.
    """

    user_id: str
    kind: ProposalKind
    start_at: datetime
    note: Optional[str] = None
    duration_minutes: int
    appointment_id: Optional[str] = None          # lịch đích của move/cancel
    target_snapshot: Optional[TargetSnapshot] = None
    status: ProposalStatus = "pending"
    unclear_count: int = 0
    expires_at: datetime
    approving_at: Optional[datetime] = None
    approved_at: Optional[datetime] = None
    result_appointment_id: Optional[str] = None
    error: Optional[str] = None
