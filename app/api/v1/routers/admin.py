from fastapi import APIRouter, Depends
from motor.motor_asyncio import AsyncIOMotorDatabase

from app.api.deps import get_db, require_admin
from app.api.v1.schemas import MonthlyCustomerStatsResponse
from app.models.user import User
from app.services.stats import StatsService

router = APIRouter(prefix="/admin", tags=["admin"])


@router.get("/stats/customers-by-month", response_model=MonthlyCustomerStatsResponse)
async def customers_by_month(
    _: User = Depends(require_admin), db: AsyncIOMotorDatabase = Depends(get_db)
):
    """Số khách duy nhất CÓ LỊCH mỗi tháng, 12 tháng gần nhất.

    Chưa có trạng thái "đã phục vụ" trong DB, nên đây là "khách có lịch
    booked" chứ không phải "khách đã đến tiệm" — đừng đọc thành doanh thu.
    """
    return MonthlyCustomerStatsResponse(months=await StatsService(db).customers_by_month())
