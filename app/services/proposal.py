"""Vòng đời đề xuất: tạo (tool) → khách trả lời ở lượt sau → duyệt/từ chối (code).

Đây là CỔNG GHI DUY NHẤT của chat. Agent không có tool ghi; `propose_appointment`
và `cancel_appointment` chỉ tạo đề xuất, node `confirm` gọi `approve` khi lưới
code + bộ phân loại cùng nói "đồng ý". `approve` không tin gì từ graph ngoài
`proposal_id`: chủ sở hữu, hạn 10 phút, lượt sau, một lần duy nhất, lịch đích
chưa đổi — đều kiểm ở đây, trên DB.

Không transaction (mongo:7 standalone trong compose): giao thức claim.
1. `find_one_and_update` pending → approving (lọc chủ, hạn, lượt) — chỉ một lần thắng.
2. Ghi lịch qua `AppointmentService` — index unique partial `slot_keys` là bước
   kiểm lại lúc ghi (giờ vừa bị người khác lấy → `SlotTakenError`).
3. approved + `result_appointment_id`; lỗi nghiệp vụ → failed + `error`.
Duyệt lại một đề xuất đã approved trả đúng kết quả cũ (idempotent). `approving`
treo quá 60 giây (tiến trình chết giữa chừng) đọc ra là `failed`.
"""
import asyncio
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Literal, Optional

from motor.motor_asyncio import AsyncIOMotorDatabase

from app.core.clock import now_utc
from app.core.config import settings
from app.core.errors import AppError, NotFoundError
from app.core.logging import get_logger
from app.models.appointment import Appointment
from app.models.proposal import Proposal, ProposalKind
from app.models.user import User
from app.repositories.proposal import ProposalRepository
from app.services.appointment import AppointmentService

logger = get_logger(__name__)

# Như cờ pending của A: quá 10 phút thì "ừ" nhiều khả năng là chuyện khác.
PROPOSAL_MAX_AGE_MINUTES = 10
APPROVING_STALE_SECONDS = 60
# Lần thứ hai liên tiếp không rõ ý → bỏ đề xuất, không hỏi lại mãi.
UNCLEAR_LIMIT = 2

TARGET_CHANGED = "Lịch này vừa thay đổi"

# Lần duyệt đang chạy nền sau khi lượt gọi nó bị huỷ (shield). Giữ tham chiếu
# mạnh: event loop chỉ giữ weak-ref, task không ai giữ có thể bị GC giữa chừng.
_INFLIGHT_APPROVALS: set = set()


@dataclass
class ApprovalResult:
    """`approved`: đã ghi (hoặc đã ghi từ lần trước). `failed`: ghi không được
    (`error` là câu tiếng Việt của AppError). `not_approvable`: không đụng gì —
    `reason` là trạng thái chặn (expired/rejected/superseded/failed/approving/
    same_turn/not_found)."""

    outcome: Literal["approved", "failed", "not_approvable"]
    kind: Optional[str] = None
    proposal: Optional[Proposal] = None
    appointment: Optional[Appointment] = None
    error: Optional[str] = None
    reason: Optional[str] = None


class ProposalService:
    def __init__(self, db: AsyncIOMotorDatabase):
        self.repo = ProposalRepository(db)
        self.appointments = AppointmentService(db)

    async def create(self, user: User, kind: ProposalKind, start_at: datetime,
                     note: Optional[str], *, target: Optional[Appointment] = None) -> Proposal:
        """Đề xuất mới; đề xuất pending trước đó của khách thành `superseded`.

        Kiểm nghiệp vụ (giờ trống, lịch đích của chính khách) vẫn do tool làm
        TRƯỚC khi gọi đây, đúng như A. `target` là lịch đã qua `own_booked`.
        """
        if kind in ("move", "cancel") and target is None:
            raise ValueError(f"proposal kind {kind!r} needs a target appointment")
        if kind == "book" and target is not None:
            raise ValueError("a book proposal has no target appointment")
        user_id = str(user.id)
        await self.repo.supersede_pending(user_id)
        now = now_utc()
        data = {
            "user_id": user_id,
            "kind": kind,
            "start_at": start_at,
            "note": note,
            "duration_minutes": settings.booking_slot_minutes,
            "appointment_id": str(target.id) if target else None,
            "target_snapshot": ({"status": target.status, "start_at": target.start_at}
                                if target else None),
            "status": "pending",
            "unclear_count": 0,
            "expires_at": now + timedelta(minutes=PROPOSAL_MAX_AGE_MINUTES),
        }
        return await self.repo.insert(data, now=now)

    async def get(self, user: User, proposal_id: str) -> Optional[Proposal]:
        """Đề xuất của CHÍNH khách, trạng thái đã chuẩn hoá lúc đọc: pending quá
        hạn → expired, approving treo quá 60 giây → failed (ghi lại vào DB)."""
        proposal = await self.repo.get_owned(str(user.id), proposal_id)
        if proposal is None:
            return None
        now = now_utc()
        if proposal.status == "pending" and proposal.expires_at <= now:
            await self.repo.transition(proposal.id, "pending", "expired")
            return await self.repo.get_owned(str(user.id), proposal_id)
        if (proposal.status == "approving" and proposal.approving_at is not None
                and now - proposal.approving_at > timedelta(seconds=APPROVING_STALE_SECONDS)):
            await self.repo.transition(proposal.id, "approving", "failed",
                                       {"error": "approving_stale"},
                                       match={"approving_at": proposal.approving_at})
            logger.warning("proposal_approving_stale", extra={"proposal_id": proposal_id})
            return await self.repo.get_owned(str(user.id), proposal_id)
        return proposal

    async def approve(self, user: User, proposal_id: str, *, turn_at: datetime) -> ApprovalResult:
        """Cổng ghi. `turn_at` = mốc câu khách của lượt đang duyệt (`asked_at`):
        đề xuất phải được tạo TRƯỚC mốc đó — đồng ý chỉ đến từ lượt sau.

        Chạy trong `asyncio.shield`: lượt chat bị huỷ (lượt mới thay) giữa lúc
        duyệt thì việc duyệt VẪN chạy xong — claim, ghi lịch, rồi approved.
        Không shield thì `CancelledError` lọt qua `except Exception`: lịch đã ghi
        mà đề xuất kẹt `approving` → 60 giây sau thành `failed`, và khách không
        bao giờ nghe câu chốt cho một lịch có thật (review M3).
        """
        task = asyncio.ensure_future(self._approve(user, proposal_id, turn_at=turn_at))
        _INFLIGHT_APPROVALS.add(task)
        task.add_done_callback(_INFLIGHT_APPROVALS.discard)
        return await asyncio.shield(task)

    async def _approve(self, user: User, proposal_id: str, *, turn_at: datetime) -> ApprovalResult:
        user_id = str(user.id)
        claimed = await self.repo.claim(user_id, proposal_id, now=now_utc(), turn_at=turn_at)
        if claimed is None:
            return await self._not_claimed(user, proposal_id)

        try:
            appointment = await self._execute(user, claimed)
        except AppError as exc:
            await self.repo.transition(claimed.id, "approving", "failed", {"error": exc.message})
            logger.info("proposal_failed", extra={"proposal_id": proposal_id, "error": exc.message})
            return ApprovalResult("failed", kind=claimed.kind, proposal=claimed, error=exc.message)
        except Exception as exc:
            # Lỗi hạ tầng: đóng đề xuất (fail-closed), lượt chat báo lỗi như A.
            await self.repo.transition(claimed.id, "approving", "failed", {"error": str(exc)[:200]})
            raise

        flipped = await self.repo.transition(claimed.id, "approving", "approved", {
            "approved_at": now_utc(), "result_appointment_id": str(appointment.id)})
        if not flipped:
            # Ai đó đã đổi trạng thái giữa claim và đây (vd. đọc thấy `approving`
            # quá 60 giây rồi chuyển `failed`). Lịch ĐÃ ghi — báo approved theo
            # sự thật của lịch, và để lại dấu vết.
            logger.warning("proposal_state_race", extra={"proposal_id": proposal_id})
        logger.info("proposal_approved", extra={"proposal_id": proposal_id, "kind": claimed.kind})
        return ApprovalResult("approved", kind=claimed.kind, proposal=claimed,
                              appointment=appointment)

    async def _execute(self, user: User, p: Proposal) -> Appointment:
        if p.kind in ("move", "cancel"):
            current = await self.appointments.repo.get_by_id(p.appointment_id or "")
            snap = p.target_snapshot
            if current is None or snap is None or current.status != snap.status:
                raise NotFoundError("Không tìm thấy lịch này")
            if current.start_at != snap.start_at:
                raise AppError(TARGET_CHANGED)
            if p.kind == "cancel":
                await self.appointments.cancel(user, p.appointment_id)
                return current
            return await self.appointments.reschedule(user, p.appointment_id, p.start_at, p.note)
        return await self.appointments.create(user, p.start_at, p.note)

    async def _not_claimed(self, user: User, proposal_id: str) -> ApprovalResult:
        current = await self.get(user, proposal_id)
        if current is None:
            return ApprovalResult("not_approvable", reason="not_found")
        if current.status == "approved":
            # Idempotent: lượt bị huỷ sau khi đã ghi rồi resume lại → cùng kết quả.
            appointment = await self.appointments.repo.get_by_id(
                current.result_appointment_id or "")
            return ApprovalResult("approved", kind=current.kind, proposal=current,
                                  appointment=appointment)
        reason = "same_turn" if current.status == "pending" else current.status
        logger.info("proposal_not_approvable", extra={"proposal_id": proposal_id, "reason": reason})
        return ApprovalResult("not_approvable", kind=current.kind, proposal=current, reason=reason)

    async def latest(self, user: User) -> Optional[Proposal]:
        """Đề xuất mới nhất của khách — trạng thái thô trong DB (không chuẩn hoá
        hạn như `get`): khối bối cảnh chỉ cần biết nó đã bị từ chối hay chưa."""
        return await self.repo.latest_for_user(str(user.id))

    async def reject(self, user: User, proposal_id: str) -> bool:
        return await self._close(user, proposal_id, "rejected")

    async def supersede(self, user: User, proposal_id: str) -> bool:
        return await self._close(user, proposal_id, "superseded")

    async def _close(self, user: User, proposal_id: str, status: str) -> bool:
        if await self.repo.get_owned(str(user.id), proposal_id) is None:
            return False
        return await self.repo.transition(proposal_id, "pending", status)

    async def mark_unclear(self, user: User, proposal_id: str) -> Optional[Proposal]:
        """Một lần không rõ ý: vẫn pending (bot hỏi lại một câu). Lần thứ
        `UNCLEAR_LIMIT` liên tiếp: rejected."""
        proposal = await self.repo.increment_unclear(str(user.id), proposal_id)
        if proposal is None:
            return None
        if proposal.unclear_count >= UNCLEAR_LIMIT:
            await self.repo.transition(proposal.id, "pending", "rejected")
            return proposal.model_copy(update={"status": "rejected"})
        return proposal
