"""`turn_ledger` — một doc/lượt, ghi TRONG vòng tool (§5.3, §6).

Không chờ `record`: `decide_pending`/`appointment_task` ghi `pending_outcome`
ngay khi có kết quả, để một lịch đã duyệt không bao giờ mất dù lượt bị huỷ
(`_supersede`/timeout) — `unannounced` ở lượt sau cứu lại cho khách nghe (C1).

TTL 7 ngày (`settings.orch_ledger_ttl_days`) chỉ dọn rác, không phải nguồn
sự thật của hạn đề xuất (đó là `proposals.expires_at`).
"""
from __future__ import annotations

from datetime import date, datetime
from typing import Literal

from motor.motor_asyncio import AsyncIOMotorDatabase

from app.agents.booking.context import PendingProposal, PreviousCall, PreviousTurn, UnannouncedOutcome
from app.agents.booking.schemas import (
    CallRecord,
    EndedBy,
    FreeSlots,
    PendingOutcome,
    ProposalCreated,
    QuestionField,
    ReplyKind,
    ResolvedTime,
    TaskResult,
)
from app.core.clock import to_local
from app.core.config import settings

LEDGER_COLLECTION = "turn_ledger"

# `finish` cần thêm một giá trị ngoài `EndedBy` (schemas.py): lượt ghi
# `pending_outcome` xong nhưng ghi ledger thất bại — không thuộc M2–M6, chỉ
# ledger biết, nên định nghĩa ở đây, không sửa schemas.py.
LedgerEndedBy = EndedBy | Literal["persist_failed"]

# Mỗi tiến trình chỉ tạo index một lần/DB (id(db) đủ phân biệt — test dùng
# một kết nối Motor mới mỗi `test_db`, process thật chỉ có một db).
_indexed_dbs: set[int] = set()


async def _ensure_indexes(db: AsyncIOMotorDatabase) -> None:
    key = id(db)
    if key in _indexed_dbs:
        return
    collection = db[LEDGER_COLLECTION]
    await collection.create_index(
        "created_at", expireAfterSeconds=settings.orch_ledger_ttl_days * 86400
    )
    await collection.create_index([("user_id", 1), ("day", 1), ("asked_at", 1)])
    _indexed_dbs.add(key)


def previous_call_from(call: CallRecord) -> PreviousCall:
    """Rút một `PreviousCall` từ một dòng ledger — thuần, không DB.

    `allowed=False` (guard chặn) -> status "rejected" bất kể `result`.
    `PendingOutcome` (decide_pending/proposal) -> luôn "ok" (lượt sau chỉ cần
    biết đã có hành động, chi tiết đọc ở `unannounced`/`pending`).
    `TaskResult` -> status của nó; `FreeSlots.slots` và `TaskResult.nearest`
    (khi có) gộp vào `offered`; `ResolvedTime.start_at` -> `resolved`;
    `ProposalCreated` -> `proposal`. `ProposalCreated` không mang `expires_at`
    thật (đó là việc của `proposals`) nên điền `expires_at=call.at` (thời
    điểm gọi — giá trị trung tính, không bịa hạn), `unclear_count=0`,
    `target=None` (không có id lịch cũ để dựng `AppointmentRef`).
    """
    action = call.action or call.tool
    result = call.result

    if not call.allowed:
        return PreviousCall(action=action, status="rejected", error_code=call.reject_code)

    if isinstance(result, PendingOutcome):
        return PreviousCall(action=action, status="ok")

    if isinstance(result, TaskResult):
        offered = []
        resolved = None
        proposal = None
        data = result.data
        if isinstance(data, FreeSlots):
            offered = list(data.slots)
        if result.nearest:
            offered = [*offered, *result.nearest]
        if isinstance(data, ResolvedTime) and data.start_at is not None:
            resolved = data.start_at
        if isinstance(data, ProposalCreated):
            proposal = PendingProposal(
                id=data.proposal_id,
                kind=data.kind,
                start_at=datetime.fromisoformat(data.start_at.iso),
                spoken=data.start_at.spoken,
                note=data.note,
                expires_at=call.at,
                unclear_count=0,
                target=None,
            )
        return PreviousCall(
            action=action,
            status=result.status,
            error_code=result.error_code,
            offered=offered,
            resolved=resolved,
            proposal=proposal,
        )

    # dict/None — dạng không mong đợi của `result` (ví dụ lỗi serialize cũ);
    # trung tính: coi như thất bại, không bịa dữ liệu.
    return PreviousCall(action=action, status="failed")


class TurnLedger:
    """Repository `turn_ledger` — mọi ghi chú xảy ra TRONG vòng tool."""

    def __init__(self, db: AsyncIOMotorDatabase):
        self._db = db
        self._collection = db[LEDGER_COLLECTION]

    async def start(
        self, *, turn_id: str, user_id: str, route: str, rule: str, asked_at: datetime
    ) -> None:
        await _ensure_indexes(self._db)
        day = to_local(asked_at).date().isoformat()
        await self._collection.update_one(
            {"turn_id": turn_id},
            {
                "$setOnInsert": {
                    "turn_id": turn_id,
                    "user_id": user_id,
                    "day": day,
                    "route": route,
                    "rule": rule,
                    "asked_at": asked_at,
                    "calls": [],
                    "pending_outcome": None,
                    "ended_by": None,
                    "reply_kind": None,
                    "ask_field": None,
                    "announced": False,
                    "created_at": asked_at,
                }
            },
            upsert=True,
        )

    async def append_call(self, turn_id: str, call: CallRecord) -> None:
        await self._collection.update_one(
            {"turn_id": turn_id}, {"$push": {"calls": call.model_dump(mode="json")}}
        )

    async def set_pending_outcome(self, turn_id: str, outcome: PendingOutcome) -> None:
        await self._collection.update_one(
            {"turn_id": turn_id},
            {"$set": {"pending_outcome": outcome.model_dump(mode="json")}},
        )

    async def set_ask(self, turn_id: str, field: QuestionField) -> None:
        await self._collection.update_one({"turn_id": turn_id}, {"$set": {"ask_field": field}})

    async def finish(
        self, turn_id: str, *, ended_by: LedgerEndedBy, reply_kind: ReplyKind | None
    ) -> None:
        await self._collection.update_one(
            {"turn_id": turn_id},
            {"$set": {"ended_by": ended_by, "reply_kind": reply_kind}},
        )

    async def mark_announced(self, turn_id: str) -> None:
        await self._collection.update_one({"turn_id": turn_id}, {"$set": {"announced": True}})

    async def previous_turns(
        self, user_id: str, *, today: date, limit: int = 2
    ) -> list[PreviousTurn]:
        cursor = (
            self._collection.find({"user_id": user_id, "day": today.isoformat()})
            .sort("asked_at", -1)
            .limit(limit)
        )
        turns: list[PreviousTurn] = []
        async for doc in cursor:
            calls = [previous_call_from(CallRecord.model_validate(c)) for c in doc.get("calls", [])]
            turns.append(
                PreviousTurn(
                    turn_id=doc["turn_id"],
                    route=doc["route"],
                    calls=calls,
                    # Lượt chưa `finish` (ví dụ bị huỷ giữa chừng) không có
                    # reply_kind — trung tính: chuỗi rỗng, không bịa nhãn.
                    reply_kind=doc.get("reply_kind") or "",
                    ask_field=doc.get("ask_field"),
                )
            )
        return turns

    async def unannounced(self, user_id: str, *, today: date) -> UnannouncedOutcome | None:
        doc = await self._collection.find_one(
            {
                "user_id": user_id,
                "day": today.isoformat(),
                "pending_outcome.outcome": {"$in": ["approved", "failed"]},
                "announced": False,
            },
            sort=[("asked_at", -1)],
        )
        if doc is None:
            return None
        outcome = PendingOutcome.model_validate(doc["pending_outcome"])
        return UnannouncedOutcome(
            proposal_id=outcome.proposal_id,
            outcome=outcome.outcome,  # type: ignore[arg-type]  # đã lọc bằng query ở trên
            kind=outcome.kind,
            spoken=outcome.spoken,
            error=outcome.error,
        )

    async def record_of(self, turn_id: str) -> dict | None:
        return await self._collection.find_one({"turn_id": turn_id})
