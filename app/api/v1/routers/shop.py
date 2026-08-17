from fastapi import APIRouter, Depends
from motor.motor_asyncio import AsyncIOMotorDatabase

from app.api.deps import get_current_user, get_db, require_admin
from app.api.v1.schemas import (SetBusyRequest, ShopHoursRequest,
                                ShopStatusResponse)
from app.models.shop import ShopHours
from app.models.user import User
from app.services.notifications import notifications
from app.services.shop import ShopService

router = APIRouter(prefix="/shop", tags=["shop"])


@router.get("/status", response_model=ShopStatusResponse)
async def get_status(
    _: User = Depends(get_current_user), db: AsyncIOMotorDatabase = Depends(get_db)
):
    return ShopStatusResponse(**(await ShopService(db).get_status()).model_dump())


@router.post("/busy", response_model=ShopStatusResponse)
async def set_busy(
    payload: SetBusyRequest,
    _: User = Depends(require_admin),
    db: AsyncIOMotorDatabase = Depends(get_db),
):
    status = await ShopService(db).set_busy(payload.minutes)
    await notifications.shop_status_changed(status)
    return ShopStatusResponse(**status.model_dump())


@router.post("/free", response_model=ShopStatusResponse)
async def set_free(
    _: User = Depends(require_admin), db: AsyncIOMotorDatabase = Depends(get_db)
):
    status = await ShopService(db).set_free()
    await notifications.shop_status_changed(status)
    return ShopStatusResponse(**status.model_dump())


@router.get("/hours", response_model=ShopHours)
async def get_hours(
    _: User = Depends(get_current_user), db: AsyncIOMotorDatabase = Depends(get_db)
):
    return await ShopService(db).get_hours()


@router.put("/hours", response_model=ShopHours)
async def set_hours(
    payload: ShopHoursRequest,
    _: User = Depends(require_admin),
    db: AsyncIOMotorDatabase = Depends(get_db),
):
    return await ShopService(db).set_hours(
        payload.open_time, payload.close_time, payload.closed_days
    )
