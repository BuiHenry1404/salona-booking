from typing import List, Optional, Sequence, Tuple

from motor.motor_asyncio import AsyncIOMotorDatabase

from app.core.config import settings
from app.core.errors import UnknownServiceError
from app.core.text import normalize_for_match, strip_diacritics
from app.models.service import Service, ServiceCategory
from app.repositories.service import ServiceRepository


def _match_keys(text: str) -> Tuple[str, str]:
    """Chuỗi chuẩn hoá có dấu và bỏ dấu, dùng để so khớp theo cả hai kiểu gõ."""
    normalized = normalize_for_match(text)
    return normalized, strip_diacritics(normalized)


def _candidates_match(query: str, candidates: Sequence[str]) -> bool:
    q_accented, q_bare = _match_keys(query)
    for candidate in candidates:
        c_accented, c_bare = _match_keys(candidate)
        if q_accented in c_accented or c_accented in q_accented:
            return True
        if q_bare in c_bare or c_bare in q_bare:
            return True
    return False


class ServiceCatalogService:
    """Danh mục dịch vụ — mọi logic tra cứu/giá/thời lượng nằm ở đây, tool và
    REST chỉ bọc lại (AGENTS.md: mọi logic nghiệp vụ trong `app/services/`)."""

    def __init__(self, db: AsyncIOMotorDatabase):
        self.repo = ServiceRepository(db)

    async def get(self, service_id: str) -> Optional[Service]:
        """Đọc một dịch vụ kể cả đã ngừng (`active=False`)."""
        return await self.repo.find_by_service_id(service_id)

    async def get_many(self, ids: Sequence[str]) -> List[Service]:
        """Giữ đúng thứ tự `ids`; id lạ ném `UnknownServiceError` ngay, không
        âm thầm bỏ qua — gọi nơi khác tính sai giá/thời lượng nếu bỏ qua."""
        if not ids:
            return []
        found = await self.repo.find_by_ids(ids)
        result = []
        for service_id in ids:
            service = found.get(service_id)
            if service is None:
                raise UnknownServiceError()
            result.append(service)
        return result

    async def search(self, text: str) -> List[Service]:
        """Chỉ dịch vụ đang hoạt động; khớp tên/alias theo cả hai chiều chứa
        nhau, sau khi chuẩn hoá NFC + casefold + bỏ khoảng trắng thừa, có dấu
        lẫn không dấu. Không phải từ điển từ khoá ý định — chỉ tra tên."""
        services = await self.repo.find_active()
        return [
            service for service in services
            if _candidates_match(text, [service.name, *service.aliases])
        ]

    async def list(self, category: Optional[ServiceCategory] = None) -> List[Service]:
        return await self.repo.find_active(category=category)

    async def duration_for(self, ids: Sequence[str]) -> int:
        """Tổng thời lượng các dịch vụ; khách không nhắc dịch vụ (ids rỗng)
        thì giữ hành vi cũ — `settings.booking_slot_minutes`."""
        if not ids:
            return settings.booking_slot_minutes
        services = await self.get_many(ids)
        return sum(service.duration_minutes for service in services)

    async def price_range(self, ids: Sequence[str]) -> Tuple[int, int]:
        services = await self.get_many(ids)
        return (
            sum(service.price_min for service in services),
            sum(service.price_max for service in services),
        )

    async def upsert_many(self, services: Sequence[Service]) -> int:
        return await self.repo.upsert_many(services)
