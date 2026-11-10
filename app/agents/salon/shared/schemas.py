"""Schema dùng chung giữa Orchestrator và agent con — dữ kiện của một lượt
(`TurnFact`), góc nhìn đề xuất và kết quả quyết định. Mọi thứ ở đây là
pydantic thuần, không phụ thuộc graph/state (xem `orchestrator/state.py` cho
cách các model này được dump/đọc lại qua checkpoint).
"""
from datetime import datetime
from typing import Annotated, Literal, Optional

from pydantic import BaseModel, Field

AgentName = Literal["appointment", "shop_info", "service"]

QuestionField = Literal[
    "day", "time", "part_of_day", "service", "which_appointment", "week", "confirm",
]


class Moment(BaseModel):
    iso: str
    spoken: str


TimeRole = Literal[
    "asked", "free", "taken", "held", "appointment",
    "shop_open", "shop_close", "busy_until", "at",
]


class TimeFact(BaseModel):
    kind: Literal["time"] = "time"
    role: TimeRole
    moment: Moment


PendingOutcomeName = Literal[
    "approved", "rejected", "superseded", "clarify", "kept", "failed", "not_approvable",
]


class ProposalFact(BaseModel):
    kind: Literal["proposal"] = "proposal"
    proposal_id: str
    proposal_kind: Literal["book", "move", "cancel"]
    at: Moment
    service_ids: list[str]


class DecisionFact(BaseModel):
    kind: Literal["decision"] = "decision"
    outcome: PendingOutcomeName
    proposal_kind: str
    at: Moment
    error: Optional[str] = None


class ServiceFact(BaseModel):
    kind: Literal["service"] = "service"
    service_id: str
    name: str
    price_min: int
    price_max: int
    duration_minutes: int
    # True: dịch vụ khách CHỌN (chốt qua `get_service`, hoặc tra ra đúng một và
    # agent Dịch vụ `ok`). False: chỉ được tra / liệt kê (ứng viên, bảng giá) —
    # guard vẫn cho báo giá của nó, nhưng nó không thành "dịch vụ đã biết".
    identified: bool = False


class AgentStatusFact(BaseModel):
    kind: Literal["agent_status"] = "agent_status"
    agent: AgentName
    status: Literal["ok", "need_info", "failed"]


TurnFact = Annotated[
    TimeFact | ProposalFact | DecisionFact | ServiceFact | AgentStatusFact,
    Field(discriminator="kind"),
]


class PendingView(BaseModel):
    id: str
    kind: Literal["book", "move", "cancel"]
    at: Moment
    expires_at: datetime
    target_id: Optional[str] = None
    service_ids: list[str]
    unclear_count: int


class PendingOutcome(BaseModel):
    outcome: PendingOutcomeName
    proposal_id: str
    kind: str
    at: Moment
    appointment_id: Optional[str] = None
    error: Optional[str] = None
    reason: Optional[str] = None
    nearest: list[Moment] = []


class UnannouncedDecision(BaseModel):
    """Một quyết định (duyệt/thất bại) chưa được báo cho khách — đọc lại ở
    lượt sau qua `TurnLedger.unannounced` (Task 6; định nghĩa ở đây vì
    `shared/` không được import `turn/`, xem `shared/context.py::LedgerReader`)."""

    turn_id: str
    outcome: Literal["approved", "failed"]
    proposal_kind: str
    at: Moment
    error: Optional[str] = None
