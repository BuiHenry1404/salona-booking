from datetime import datetime
from typing import List, Optional

from bson import ObjectId
from motor.motor_asyncio import AsyncIOMotorDatabase
from pymongo.errors import DuplicateKeyError

from app.core.errors import SlotTakenError
from app.core.slots import slot_keys_for
from app.models.appointment import Appointment, CreatedVia
from app.repositories.base import BaseRepository


class AppointmentRepository(BaseRepository[Appointment]):
    def __init__(self, db: AsyncIOMotorDatabase):
        super().__init__(db, Appointment, "appointments")

    async def insert_booked(
        self,
        user_id: str,
        user_name: Optional[str],
        phone: Optional[str],
        start_at: datetime,
        duration_minutes: int,
        note: Optional[str],
        created_via: CreatedVia,
    ) -> Appointment:
        """Ghi lịch mới. Chặn trùng do chính MongoDB làm qua unique partial index
        trên slot_keys — nguyên tử, không cần transaction, không cần khóa."""
        try:
            return await self.create({
                "user_id": user_id,
                "user_name": user_name,
                "phone": phone,
                "start_at": start_at,
                "duration_minutes": duration_minutes,
                "slot_keys": slot_keys_for(start_at, duration_minutes),
                "note": note,
                "status": "booked",
                "created_via": created_via,
            })
        except DuplicateKeyError as exc:
            raise SlotTakenError() from exc

    async def find_active_at(self, user_id: str, start_at: datetime) -> Optional[Appointment]:
        doc = await self.collection.find_one(
            {"user_id": user_id, "start_at": start_at, "status": "booked"}
        )
        return Appointment(**doc) if doc else None

    async def cancel(self, appointment_id: str) -> bool:
        """Chỉ đổi status. Document tự rơi khỏi partial index nên slot được giải
        phóng ngay, còn slot_keys giữ nguyên để tra cứu lịch sử."""
        if not ObjectId.is_valid(appointment_id):
            return False
        result = await self.collection.update_one(
            {"_id": ObjectId(appointment_id), "status": "booked"},
            {"$set": {"status": "cancelled"}},
        )
        return result.modified_count == 1

    async def upcoming_for_user(self, user_id: str, now: datetime) -> List[Appointment]:
        docs = await self.collection.find({
            "user_id": user_id, "status": "booked", "start_at": {"$gte": now},
        }).sort("start_at", 1).to_list(length=100)
        return [Appointment(**d) for d in docs]

    async def booked_between(self, start: datetime, end: datetime) -> List[Appointment]:
        docs = await self.collection.find({
            "status": "booked", "start_at": {"$gte": start, "$lt": end},
        }).sort("start_at", 1).to_list(length=500)
        return [Appointment(**d) for d in docs]
