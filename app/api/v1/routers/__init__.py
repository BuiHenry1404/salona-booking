from fastapi import APIRouter

from app.api.v1.routers import appointments, auth, health, shop

api_router = APIRouter()
api_router.include_router(health.router)
api_router.include_router(auth.router)
api_router.include_router(appointments.router)
api_router.include_router(shop.router)
