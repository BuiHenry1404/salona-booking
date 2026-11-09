from typing import List, Literal

from pydantic import BaseModel, Field, model_validator

from app.core.slots import SLOT_MINUTES

ServiceCategory = Literal["nail", "toc"]


class Service(BaseModel):
    """Một dòng trong danh mục dịch vụ. Không phải `BaseDocument` — khoá tra
    cứu là `id` (chuỗi ổn định do người viết chọn, vd "toc-nhuom"), không phải
    `_id` của Mongo, để lịch cũ tham chiếu `service_ids` không phụ thuộc
    ObjectId nội bộ."""

    id: str
    category: ServiceCategory
    name: str
    aliases: List[str] = Field(default_factory=list)
    price_min: int
    price_max: int
    duration_minutes: int
    active: bool = True

    @model_validator(mode="after")
    def _validate_ranges(self) -> "Service":
        if self.duration_minutes <= 0 or self.duration_minutes % SLOT_MINUTES:
            raise ValueError(f"duration_minutes phải là bội số dương của {SLOT_MINUTES}")
        if not (0 < self.price_min <= self.price_max):
            raise ValueError("price_min phải dương và không lớn hơn price_max")
        return self
