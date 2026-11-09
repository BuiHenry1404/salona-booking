"""`salon_turns` — một doc/lượt, ledger của bản thiết kế subagents mới.

Collection mới hoàn toàn, không trộn với `turn_ledger` của gói `booking` cũ
(xem `app/agents/booking/ledger.py` chỉ để đối chiếu cách làm, không import).
Mỗi lượt có một bản ghi: `start()` tạo doc; `add_facts`/`set_decision`/
`finish`/`mark_announced` cập nhật nó trong lúc lượt chạy; `record`/
`unannounced`/`service_facts` là các cách đọc lại cho orchestrator và agent
con dùng ở lượt sau.

TTL theo `settings.orch_ledger_ttl_days` chỉ dọn rác định kỳ, không phải
nguồn sự thật của hạn đề xuất (đó là `proposals.expires_at`).
"""
from __future__ import annotations

from datetime import date, datetime
from typing import Literal, Optional, Sequence

from motor.motor_asyncio import AsyncIOMotorDatabase
from pydantic import BaseModel, TypeAdapter

from app.agents.salon.shared.schemas import (
    Moment,
    PendingOutcome,
    ServiceFact,
    TurnFact,
    UnannouncedDecision,
)
from app.core.clock import to_local
from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger(__name__)

LEDGER_COLLECTION = "salon_turns"

EndedBy = Literal["answer", "fallback", "limit", "error", "stale_button", "button"]

_FactAdapter = TypeAdapter(TurnFact)

# Mỗi tiến trình chỉ tạo index một lần/DB (id(db) đủ phân biệt — test dùng
# một kết nối Motor mới mỗi `test_db`, process thật chỉ có một db).
_indexed_dbs: set[int] = set()


class TurnRecord(BaseModel):
    turn_id: str
    user_id: str
    thread_id: str
    day: date
    asked_at: datetime
    kind: Literal["typed", "button"]
    facts: list[TurnFact] = []
    decision: Optional[PendingOutcome] = None
    ended_by: Optional[EndedBy] = None
    answer_source: Optional[Literal["llm", "code"]] = None
    announced: bool = False


async def _ensure_indexes(db: AsyncIOMotorDatabase) -> None:
    key = id(db)
    if key in _indexed_dbs:
        return
    collection = db[LEDGER_COLLECTION]
    await collection.create_index(
        "created_at", expireAfterSeconds=settings.orch_ledger_ttl_days * 86400
    )
    await collection.create_index([("user_id", 1), ("day", 1), ("asked_at", 1)])
    await collection.create_index("thread_id")
    _indexed_dbs.add(key)


def _warn_if_missing(result, turn_id: str, method: str) -> None:
    """Ghi gọi sau `start()` không bao giờ nên trật — không upsert (sẽ tạo
    doc thiếu `user_id`/`thread_id`/`day`), chỉ cảnh báo để lộ lỗi gọi sai
    thứ tự."""
    if result.matched_count == 0:
        logger.warning("ledger_turn_missing", extra={"turn_id": turn_id, "method": method})


class TurnLedger:
    """Repository `salon_turns` — không giữ trạng thái ngoài `db` (stateless,
    Ruling R3: tool của Task 11 tự dựng `TurnLedger(db)` khi cần)."""

    def __init__(self, db: AsyncIOMotorDatabase):
        self._db = db
        self._collection = db[LEDGER_COLLECTION]

    async def start(
        self, *, turn_id: str, user_id: str, thread_id: str, asked_at: datetime, kind: str
    ) -> None:
        await _ensure_indexes(self._db)
        day = to_local(asked_at).date().isoformat()
        await self._collection.update_one(
            {"turn_id": turn_id},
            {
                "$setOnInsert": {
                    "turn_id": turn_id,
                    "user_id": user_id,
                    "thread_id": thread_id,
                    "day": day,
                    "asked_at": asked_at,
                    "kind": kind,
                    "facts": [],
                    "decision": None,
                    "ended_by": None,
                    "answer_source": None,
                    "announced": False,
                    "created_at": asked_at,
                }
            },
            upsert=True,
        )

    async def add_facts(self, turn_id: str, facts: Sequence[TurnFact]) -> None:
        if not facts:
            return
        result = await self._collection.update_one(
            {"turn_id": turn_id},
            {"$push": {"facts": {"$each": [_FactAdapter.dump_python(f, mode="json") for f in facts]}}},
        )
        _warn_if_missing(result, turn_id, "add_facts")

    async def set_decision(self, turn_id: str, outcome: PendingOutcome, *, session=None) -> None:
        """`session`: `decide.apply_decision` (Task 11) ghi outcome CHUNG
        transaction với lệnh ghi lịch/đề xuất — cả hai cùng có hoặc cùng
        không (abort transaction = quyết định cũng biến mất)."""
        result = await self._collection.update_one(
            {"turn_id": turn_id},
            {"$set": {"decision": outcome.model_dump(mode="json")}},
            session=session,
        )
        _warn_if_missing(result, turn_id, "set_decision")

    async def finish(self, turn_id: str, *, ended_by: EndedBy, answer_source: str) -> None:
        result = await self._collection.update_one(
            {"turn_id": turn_id},
            {"$set": {"ended_by": ended_by, "answer_source": answer_source}},
        )
        _warn_if_missing(result, turn_id, "finish")

    async def mark_announced(self, turn_id: str) -> None:
        result = await self._collection.update_one(
            {"turn_id": turn_id}, {"$set": {"announced": True}}
        )
        _warn_if_missing(result, turn_id, "mark_announced")

    async def unmark_announced(self, turn_id: str) -> None:
        """Hoàn lại `mark_announced` khi câu báo chưa tới khách (lượt bị huỷ
        trước `complete`) — để R0 báo lại ở lượt sau (Ruling R14)."""
        result = await self._collection.update_one(
            {"turn_id": turn_id}, {"$set": {"announced": False}}
        )
        _warn_if_missing(result, turn_id, "unmark_announced")

    async def unannounced(self, user_id: str, *, today: date) -> UnannouncedDecision | None:
        doc = await self._collection.find_one(
            {
                "user_id": user_id,
                "day": today.isoformat(),
                "decision.outcome": {"$in": ["approved", "failed"]},
                "announced": False,
            },
            sort=[("asked_at", -1)],
        )
        if doc is None:
            return None
        decision = PendingOutcome.model_validate(doc["decision"])
        return UnannouncedDecision(
            turn_id=doc["turn_id"],
            outcome=decision.outcome,  # type: ignore[arg-type]  # đã lọc bằng query ở trên
            proposal_kind=decision.kind,
            at=decision.at,
            error=decision.error,
        )

    async def record(self, turn_id: str) -> TurnRecord | None:
        doc = await self._collection.find_one({"turn_id": turn_id})
        if doc is None:
            return None
        return TurnRecord.model_validate(
            {**doc, "day": date.fromisoformat(doc["day"])}
        )

    async def service_facts(self, *, thread_id: str) -> list[ServiceFact]:
        """`ServiceFact` của mọi lượt trong thread, mỗi `service_id` một bản
        mới nhất — duyệt theo thứ tự `asked_at` tăng dần nên bản ghi sau đè
        bản trước."""
        cursor = self._collection.find({"thread_id": thread_id}).sort("asked_at", 1)
        latest: dict[str, ServiceFact] = {}
        async for doc in cursor:
            for raw in doc.get("facts", []):
                if raw.get("kind") != "service":
                    continue
                fact = ServiceFact.model_validate(raw)
                latest[fact.service_id] = fact
        return list(latest.values())
