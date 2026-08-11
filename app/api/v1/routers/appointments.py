from datetime import date

from fastapi import APIRouter, Depends, status
from motor.motor_asyncio import AsyncIOMotorDatabase

from app.api.deps import get_current_user, get_db, require_admin
from app.api.v1.schemas import (AppointmentCreateRequest, AppointmentResponse,
                                FreeSlotsResponse)
from app.models.appointment import Appointment
from app.models.user import User
from app.services.appointment import AppointmentService

router = APIRouter(prefix="/appointments", tags=["appointments"])


def _to_response(appt: Appointment) -> AppointmentResponse:
    return AppointmentResponse(
        id=str(appt.id), start_at=appt.start_at, duration_minutes=appt.duration_minutes,
        note=appt.note, status=appt.status, user_name=appt.user_name, phone=appt.phone,
    )


@router.post("", response_model=AppointmentResponse, status_code=status.HTTP_201_CREATED)
async def create_appointment(
    payload: AppointmentCreateRequest,
    user: User = Depends(get_current_user),
    db: AsyncIOMotorDatabase = Depends(get_db),
):
    created_via = "admin" if user.role == "admin" else "chat"
    appt = await AppointmentService(db).create(user, payload.start_at, payload.note, created_via)
    return _to_response(appt)


@router.get("/mine", response_model=list[AppointmentResponse])
async def my_appointments(
    user: User = Depends(get_current_user), db: AsyncIOMotorDatabase = Depends(get_db)
):
    return [_to_response(a) for a in await AppointmentService(db).upcoming_for(user)]


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
    return [_to_response(a) for a in await AppointmentService(db).day_schedule(day)]


@router.get("/free-slots/{day}", response_model=FreeSlotsResponse)
async def free_slots(
    day: date, _: User = Depends(get_current_user), db: AsyncIOMotorDatabase = Depends(get_db)
):
    return FreeSlotsResponse(slots=await AppointmentService(db).find_free_slots(day))
