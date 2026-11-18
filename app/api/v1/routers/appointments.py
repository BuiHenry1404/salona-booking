from datetime import date
from typing import Sequence

from fastapi import APIRouter, Depends, status
from motor.motor_asyncio import AsyncIOMotorDatabase

from app.api.deps import get_current_user, get_db, require_admin
from app.api.v1.schemas import (AppointmentCreateRequest, AppointmentResponse,
                                FreeSlotsResponse)
from app.core.errors import ForbiddenError, NotFoundError
from app.models.appointment import Appointment
from app.models.user import User
from app.services.appointment import AppointmentService
from app.services.auth import AuthService

router = APIRouter(prefix="/appointments", tags=["appointments"])


async def _to_responses(
    svc: AppointmentService, appointments: Sequence[Appointment]
) -> list[AppointmentResponse]:
    """Mỏng — toàn bộ việc tra tên dịch vụ / tính giờ xong nằm trong
    `AppointmentService.describe`, router chỉ đổi hình sang schema REST."""
    return [AppointmentResponse(**detail.model_dump()) for detail in await svc.describe(appointments)]


@router.post("", response_model=AppointmentResponse, status_code=status.HTTP_201_CREATED)
async def create_appointment(
    payload: AppointmentCreateRequest,
    user: User = Depends(get_current_user),
    db: AsyncIOMotorDatabase = Depends(get_db),
):
    # created_via nói ai BẤM nút đặt, nên tính theo CALLER chứ không theo
    # người được đặt hộ.
    created_via = "admin" if user.role == "admin" else "chat"

    # Khách gọi điện, chủ tiệm bấm hộ: đặt cho `for_user_id` thay cho caller.
    # Kiểm quyền Ở ĐÂY, không dựa vào việc React ẩn nút.
    booking_for = user
    if payload.for_user_id and payload.for_user_id != str(user.id):
        if user.role != "admin":
            raise ForbiddenError("Không có quyền đặt lịch hộ người khác")
        booking_for = await AuthService(db).get_user_by_id(payload.for_user_id)
        if not booking_for:
            raise NotFoundError("Không tìm thấy khách này")

    svc = AppointmentService(db)
    appt = await svc.create(
        booking_for, payload.start_at, payload.note, created_via,
        service_ids=payload.service_ids,
    )
    [response] = await _to_responses(svc, [appt])
    return response


@router.get("/mine", response_model=list[AppointmentResponse])
async def my_appointments(
    user: User = Depends(get_current_user), db: AsyncIOMotorDatabase = Depends(get_db)
):
    svc = AppointmentService(db)
    return await _to_responses(svc, await svc.upcoming_for(user))


@router.delete("/{appointment_id}", status_code=status.HTTP_204_NO_CONTENT)
async def cancel_appointment(
    appointment_id: str,
    user: User = Depends(get_current_user),
    db: AsyncIOMotorDatabase = Depends(get_db),
):
    await AppointmentService(db).cancel(user, appointment_id)


@router.get("/day/{day}", response_model=list[AppointmentResponse])
async def day_schedule(
    day: date, _: User = Depends(require_admin), db: AsyncIOMotorDatabase = Depends(get_db)
):
    svc = AppointmentService(db)
    return await _to_responses(svc, await svc.day_schedule(day))


@router.get("/free-slots/{day}", response_model=FreeSlotsResponse)
async def free_slots(
    day: date, _: User = Depends(get_current_user), db: AsyncIOMotorDatabase = Depends(get_db)
):
    return FreeSlotsResponse(slots=await AppointmentService(db).find_free_slots(day))
