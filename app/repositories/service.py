from typing import Dict, List, Optional, Sequence

from motor.motor_asyncio import AsyncIOMotorDatabase

from app.models.service import Service, ServiceCategory
from app.repositories.base import BaseRepository


class ServiceRepository(BaseRepository[Service]):
    """`id` (chuỗi nghiệp vụ) là khoá tra cứu, không phải `_id` của Mongo —
    các hàm `get_by_id`/`update`/`delete` của `BaseRepository` (dùng `_id`)
    không áp dụng ở đây, repo này tự định nghĩa truy vấn theo `id`."""

    def __init__(self, db: AsyncIOMotorDatabase):
        super().__init__(db, Service, "services")

    async def find_by_service_id(self, service_id: str) -> Optional[Service]:
        """Đọc kể cả dịch vụ đã `active=False` — lịch cũ còn cần đọc tên."""
        doc = await self.collection.find_one({"id": service_id})
        return Service(**doc) if doc else None

    async def find_by_ids(self, ids: Sequence[str]) -> Dict[str, Service]:
        cursor = self.collection.find({"id": {"$in": list(ids)}})
        docs = await cursor.to_list(length=None)
        return {doc["id"]: Service(**doc) for doc in docs}

    async def find_active(self, category: Optional[ServiceCategory] = None) -> List[Service]:
        query: Dict = {"active": True}
        if category is not None:
            query["category"] = category
        cursor = self.collection.find(query)
        docs = await cursor.to_list(length=None)
        return [Service(**doc) for doc in docs]

    async def upsert_many(self, services: Sequence[Service]) -> int:
        """Ghi theo `id`; chạy lại nhiều lần an toàn (ghi đè cùng giá trị)."""
        count = 0
        for service in services:
            await self.collection.update_one(
                {"id": service.id},
                {"$set": service.model_dump()},
                upsert=True,
            )
            count += 1
        return count
