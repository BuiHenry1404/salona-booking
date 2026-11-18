from fastapi import APIRouter, Depends, HTTPException, status
from motor.motor_asyncio import AsyncIOMotorDatabase

from app.api.deps import get_db, require_admin
from app.api.v1.schemas import MonthlyCustomerStatsResponse, SetStaffNoteRequest
from app.models.user import User
from app.repositories.user import UserRepository
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


@router.patch("/users/{user_id}/note", status_code=status.HTTP_204_NO_CONTENT)
async def set_staff_note(
    user_id: str,
    payload: SetStaffNoteRequest,
    _: User = Depends(require_admin),
    db: AsyncIOMotorDatabase = Depends(get_db),
):
    """Ghi chú riêng của tiệm về khách (Task 13) — hiện trong khối bối cảnh
    là "Ghi chú tiệm: …", chỉ admin thấy và sửa được, chưa từng từ khách.
    """
    ok = await UserRepository(db).set_staff_note(user_id, payload.staff_note)
    if not ok:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Không tìm thấy khách")
