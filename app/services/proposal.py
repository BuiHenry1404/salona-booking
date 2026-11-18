"""Vòng đời đề xuất: tạo (tool) → khách trả lời ở lượt sau → duyệt/từ chối (code).

Đây là CỔNG GHI DUY NHẤT của chat. Agent không có tool ghi; `propose_appointment`
và `cancel_appointment` chỉ tạo đề xuất, node `confirm` gọi `approve` khi lưới
code + bộ phân loại cùng nói "đồng ý". `approve` không tin gì từ graph ngoài
`proposal_id`: chủ sở hữu, hạn 10 phút, lượt sau, một lần duy nhất, lịch đích
chưa đổi — đều kiểm ở đây, trên DB.

Không `session` (REST, Telegram, nút bấm cũ): giao thức claim, không transaction.
1. `find_one_and_update` pending → approving (lọc chủ, hạn, lượt) — chỉ một lần thắng.
2. Ghi lịch qua `AppointmentService` — index unique partial `slot_keys` là bước
   kiểm lại lúc ghi (giờ vừa bị người khác lấy → `SlotTakenError`).
3. approved + `result_appointment_id`; lỗi nghiệp vụ → failed + `error`.
Duyệt lại một đề xuất đã approved trả đúng kết quả cũ (idempotent). `approving`
treo quá 60 giây (tiến trình chết giữa chừng) đọc ra là `failed`.

Task 3.6 (quyết định chủ dự án 05/11): Mongo chạy replica set một node, và
`approve(..., session=...)` chạy cả giao thức trên TRONG transaction của caller
(`decide.apply_decision` ghi luôn `turn_ledger` cùng transaction). Không có
`session` (REST, Telegram, nút bấm cũ) thì hành vi giữ nguyên như trên.
"""
import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import List, Literal, Optional, Tuple

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
    # Chỉ có khi duyệt trong transaction: báo tin chủ tiệm đã HOÃN, caller gửi
    # bằng `app.services.appointment.send_notices` sau khi commit.
    notices: List[Tuple[str, Appointment]] = field(default_factory=list)


class ApprovalFailed(Exception):
    """Chỉ dùng khi `approve` chạy trong transaction (`session`): lỗi nghiệp
    vụ lúc ghi (giờ vừa bị lấy, lịch đích đổi, quá hạn mức...).

    Không thể ghi `failed` ngay trong transaction đó: DuplicateKeyError làm
    Mongo abort transaction phía server, mọi lệnh sau lỗi NoSuchTransaction
    (nhãn TransientTransactionError — `with_transaction` sẽ chạy lại mãi).
    Nên `approve` NÉM lỗi này ra để transaction abort sạch (claim cũng mất, đề
    xuất về `pending`); caller ghi `mark_failed` + outcome trong một
    transaction RIÊNG. `result` là ApprovalResult("failed") để báo khách."""

    def __init__(self, result: ApprovalResult) -> None:
        super().__init__(result.error)
        self.result = result


class ProposalService:
    def __init__(self, db: AsyncIOMotorDatabase):
        self.repo = ProposalRepository(db)
        self.appointments = AppointmentService(db)

    async def create(self, user: User, kind: ProposalKind, start_at: datetime,
                     note: Optional[str], *, target: Optional[Appointment] = None,
                     service_ids: Optional[List[str]] = None) -> Proposal:
        """Đề xuất mới; đề xuất pending trước đó của khách thành `superseded`.

        Kiểm nghiệp vụ (giờ trống, lịch đích của chính khách) vẫn do tool làm
        TRƯỚC khi gọi đây, đúng như A. `target` là lịch đã qua `own_booked`.

        `service_ids`: dịch vụ khách chọn cho đề xuất này (toàn bộ, không phải
        phần thêm). `move` mà không mang dịch vụ (`None`) thì thời lượng lấy
        từ chính lịch đích (`target.duration_minutes`) — khách không nhắc lại
        dịch vụ thì không đổi thời lượng; `service_ids` trên đề xuất khi đó để
        rỗng, `_execute` đọc rỗng là "không mang dịch vụ mới" và truyền `None`
        cho `reschedule` để nó tự giữ dịch vụ/thời lượng hiện có của lịch. Mọi
        trường hợp khác (book, cancel, hoặc move có mang dịch vụ) tính bằng
        `ServiceCatalogService.duration_for`.
        """
        if kind in ("move", "cancel") and target is None:
            raise ValueError(f"proposal kind {kind!r} needs a target appointment")
        if kind == "book" and target is not None:
            raise ValueError("a book proposal has no target appointment")
        user_id = str(user.id)
        await self.repo.supersede_pending(user_id)
        now = now_utc()
        if kind == "move" and service_ids is None:
            duration = target.duration_minutes
            stored_service_ids: List[str] = []
        else:
            duration = await self.appointments.catalog.duration_for(service_ids or [])
            stored_service_ids = list(service_ids) if service_ids else []
        data = {
            "user_id": user_id,
            "kind": kind,
            "start_at": start_at,
            "note": note,
            "duration_minutes": duration,
            "service_ids": stored_service_ids,
            "appointment_id": str(target.id) if target else None,
            "target_snapshot": ({"status": target.status, "start_at": target.start_at}
                                if target else None),
            "status": "pending",
            "unclear_count": 0,
            "expires_at": now + timedelta(minutes=PROPOSAL_MAX_AGE_MINUTES),
        }
        return await self.repo.insert(data, now=now)

    async def get(self, user: User, proposal_id: str, *, session=None) -> Optional[Proposal]:
        """Đề xuất của CHÍNH khách, trạng thái đã chuẩn hoá lúc đọc: pending quá
        hạn → expired, approving treo quá 60 giây → failed (ghi lại vào DB)."""
        proposal = await self.repo.get_owned(str(user.id), proposal_id, session=session)
        if proposal is None:
            return None
        now = now_utc()
        if proposal.status == "pending" and proposal.expires_at <= now:
            await self.repo.transition(proposal.id, "pending", "expired", session=session)
            return await self.repo.get_owned(str(user.id), proposal_id, session=session)
        if (proposal.status == "approving" and proposal.approving_at is not None
                and now - proposal.approving_at > timedelta(seconds=APPROVING_STALE_SECONDS)):
            await self.repo.transition(proposal.id, "approving", "failed",
                                       {"error": "approving_stale"},
                                       match={"approving_at": proposal.approving_at},
                                       session=session)
            logger.warning("proposal_approving_stale", extra={"proposal_id": proposal_id})
            return await self.repo.get_owned(str(user.id), proposal_id, session=session)
        return proposal

    async def approve(self, user: User, proposal_id: str, *, turn_at: datetime,
                      session=None) -> ApprovalResult:
        """Cổng ghi. `turn_at` = mốc câu khách của lượt đang duyệt (`asked_at`):
        đề xuất phải được tạo TRƯỚC mốc đó — đồng ý chỉ đến từ lượt sau.

        Không `session`: chạy trong `asyncio.shield` — lượt chat bị huỷ (lượt
        mới thay) giữa lúc duyệt thì việc duyệt VẪN chạy xong — claim, ghi lịch,
        rồi approved. Không shield thì `CancelledError` lọt qua `except
        Exception`: lịch đã ghi mà đề xuất kẹt `approving` → 60 giây sau thành
        `failed`, và khách không bao giờ nghe câu chốt cho một lịch có thật
        (review M3).

        Có `session` (đang trong transaction của caller, Task 3.6): KHÔNG shield,
        không task nền — huỷ giữa chừng thì transaction abort, không còn gì nửa
        vời. Mọi đọc/ghi Mongo đi qua `session`; báo tin chủ tiệm hoãn vào
        `ApprovalResult.notices`; lỗi nghiệp vụ lúc ghi -> ném `ApprovalFailed`
        (caller ghi `failed` ở transaction riêng). `with_transaction` có thể
        chạy lại cả callback — mọi thứ ở đây chỉ là lệnh Mongo trong session
        (bị rollback cùng), nên chạy lại không ghi đôi.
        """
        if session is not None:
            return await self._approve(user, proposal_id, turn_at=turn_at, session=session)
        task = asyncio.ensure_future(self._approve(user, proposal_id, turn_at=turn_at))
        _INFLIGHT_APPROVALS.add(task)
        task.add_done_callback(_INFLIGHT_APPROVALS.discard)
        return await asyncio.shield(task)

    async def _approve(self, user: User, proposal_id: str, *, turn_at: datetime,
                       session=None) -> ApprovalResult:
        user_id = str(user.id)
        claimed = await self.repo.claim(user_id, proposal_id, now=now_utc(), turn_at=turn_at,
                                        session=session)
        if claimed is None:
            return await self._not_claimed(user, proposal_id, session=session)

        try:
            appointment = await self._execute(user, claimed, session=session)
        except AppError as exc:
            failed = ApprovalResult("failed", kind=claimed.kind, proposal=claimed, error=exc.message)
            if session is not None:
                raise ApprovalFailed(failed) from exc
            await self.repo.transition(claimed.id, "approving", "failed", {"error": exc.message})
            logger.info("proposal_failed", extra={"proposal_id": proposal_id, "error": exc.message})
            return failed
        except Exception as exc:
            # Lỗi hạ tầng: đóng đề xuất (fail-closed), lượt chat báo lỗi như A.
            # Trong transaction thì không ghi gì thêm: abort đưa đề xuất về
            # `pending`, lỗi nổi lên caller (và lỗi tạm thời được chạy lại).
            if session is None:
                await self.repo.transition(claimed.id, "approving", "failed",
                                           {"error": str(exc)[:200]})
            raise

        flipped = await self.repo.transition(claimed.id, "approving", "approved", {
            "approved_at": now_utc(), "result_appointment_id": str(appointment.id)},
            session=session)
        if not flipped:
            # Ai đó đã đổi trạng thái giữa claim và đây (vd. đọc thấy `approving`
            # quá 60 giây rồi chuyển `failed`). Lịch ĐÃ ghi — báo approved theo
            # sự thật của lịch, và để lại dấu vết.
            logger.warning("proposal_state_race", extra={"proposal_id": proposal_id})
        logger.info("proposal_approved", extra={"proposal_id": proposal_id, "kind": claimed.kind})
        return ApprovalResult("approved", kind=claimed.kind, proposal=claimed,
                              appointment=appointment,
                              notices=list(self.appointments.deferred_notices))

    async def _execute(self, user: User, p: Proposal, *, session=None) -> Appointment:
        if p.kind in ("move", "cancel"):
            current = await self.appointments.repo.get_by_id(p.appointment_id or "",
                                                             session=session)
            snap = p.target_snapshot
            if current is None or snap is None or current.status != snap.status:
                raise NotFoundError("Không tìm thấy lịch này")
            if current.start_at != snap.start_at:
                raise AppError(TARGET_CHANGED)
            if p.kind == "cancel":
                await self.appointments.cancel(user, p.appointment_id, session=session)
                return current
            return await self.appointments.reschedule(user, p.appointment_id, p.start_at, p.note,
                                                      service_ids=(p.service_ids or None),
                                                      session=session)
        return await self.appointments.create(user, p.start_at, p.note,
                                              service_ids=(p.service_ids or None), session=session)

    async def mark_failed(self, user: User, proposal_id: str, error: str, *,
                          session=None) -> bool:
        """Đóng đề xuất `pending` thành `failed` + `error` — nửa sau của
        `ApprovalFailed`: transaction duyệt đã abort (đề xuất về `pending`), caller
        ghi lỗi này cùng outcome ledger trong một transaction riêng."""
        if await self.repo.get_owned(str(user.id), proposal_id, session=session) is None:
            return False
        flipped = await self.repo.transition(proposal_id, "pending", "failed", {"error": error},
                                             session=session)
        logger.info("proposal_failed", extra={"proposal_id": proposal_id, "error": error})
        return flipped

    async def _not_claimed(self, user: User, proposal_id: str, *, session=None) -> ApprovalResult:
        current = await self.get(user, proposal_id, session=session)
        if current is None:
            return ApprovalResult("not_approvable", reason="not_found")
        if current.status == "approved":
            # Idempotent: lượt bị huỷ sau khi đã ghi rồi resume lại → cùng kết quả.
            appointment = await self.appointments.repo.get_by_id(
                current.result_appointment_id or "", session=session)
            return ApprovalResult("approved", kind=current.kind, proposal=current,
                                  appointment=appointment)
        reason = "same_turn" if current.status == "pending" else current.status
        logger.info("proposal_not_approvable", extra={"proposal_id": proposal_id, "reason": reason})
        return ApprovalResult("not_approvable", kind=current.kind, proposal=current, reason=reason)

    async def latest(self, user: User) -> Optional[Proposal]:
        """Đề xuất mới nhất của khách — trạng thái thô trong DB (không chuẩn hoá
        hạn như `get`): khối bối cảnh chỉ cần biết nó đã bị từ chối hay chưa."""
        return await self.repo.latest_for_user(str(user.id))

    async def closed_earlier_today(self, user: User, before: datetime) -> List[Proposal]:
        """Task 15 review: đề xuất của khách, CÙNG NGÀY giờ VN, TẠO TRƯỚC
        `before`, đã đóng mà khách KHÔNG chốt (rejected/superseded/expired) —
        `confirm.py` suy `confirm_fact["context"]` từ danh sách này."""
        return await self.repo.closed_earlier_today(str(user.id), before)

    async def reject(self, user: User, proposal_id: str, *, session=None) -> bool:
        return await self._close(user, proposal_id, "rejected", session=session)

    async def supersede(self, user: User, proposal_id: str, *, session=None) -> bool:
        return await self._close(user, proposal_id, "superseded", session=session)

    async def _close(self, user: User, proposal_id: str, status: str, *, session=None) -> bool:
        if await self.repo.get_owned(str(user.id), proposal_id, session=session) is None:
            return False
        return await self.repo.transition(proposal_id, "pending", status, session=session)

    async def mark_unclear(self, user: User, proposal_id: str, *,
                           session=None) -> Optional[Proposal]:
        """Một lần không rõ ý: vẫn pending (bot hỏi lại một câu). Lần thứ
        `UNCLEAR_LIMIT` liên tiếp: rejected."""
        proposal = await self.repo.increment_unclear(str(user.id), proposal_id, session=session)
        if proposal is None:
            return None
        if proposal.unclear_count >= UNCLEAR_LIMIT:
            await self.repo.transition(proposal.id, "pending", "rejected", session=session)
            return proposal.model_copy(update={"status": "rejected"})
        return proposal
