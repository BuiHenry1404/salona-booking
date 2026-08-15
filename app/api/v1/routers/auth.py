from fastapi import APIRouter, Depends, HTTPException, Request, status
from motor.motor_asyncio import AsyncIOMotorDatabase

from app.api.deps import get_current_user, get_db, require_admin
from app.api.v1.schemas import (CreateUserRequest, LoginRequest, RefreshRequest,
                                ResetPasswordRequest, TokenResponse,
                                UserResponse)
from app.core.security import create_access_token_for
from app.models.user import User
from app.repositories.user import UserRepository
from app.services.auth import AuthService

router = APIRouter(prefix="/auth", tags=["auth"])


def _to_response(user: User) -> UserResponse:
    return UserResponse(
        id=str(user.id), phone=user.phone, full_name=user.full_name,
        role=user.role, is_active=user.is_active,
    )


@router.post("/login", response_model=TokenResponse)
async def login(
    payload: LoginRequest, request: Request, db: AsyncIOMotorDatabase = Depends(get_db)
):
    ip = request.client.host if request.client else "unknown"
    svc = AuthService(db)
    user = await svc.authenticate(payload.phone, payload.password, ip=ip)
    if not user:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Số điện thoại hoặc mật khẩu không đúng")
    return TokenResponse(
        access_token=create_access_token_for(user),
        refresh_token=await svc.issue_refresh_token(str(user.id)),
        role=user.role,
    )


@router.post("/refresh", response_model=TokenResponse)
async def refresh(payload: RefreshRequest, db: AsyncIOMotorDatabase = Depends(get_db)):
    """Đổi refresh token lấy cặp token mới. Không cần Authorization —
    chính refresh token là bằng chứng."""
    svc = AuthService(db)
    user, new_refresh = await svc.consume_refresh_token(payload.refresh_token)
    return TokenResponse(
        access_token=create_access_token_for(user),
        refresh_token=new_refresh,
        role=user.role,
    )


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(payload: RefreshRequest, db: AsyncIOMotorDatabase = Depends(get_db)):
    await AuthService(db).revoke_refresh_family(payload.refresh_token)


@router.post("/reset-password", status_code=status.HTTP_204_NO_CONTENT)
async def reset_password(
    payload: ResetPasswordRequest, request: Request, db: AsyncIOMotorDatabase = Depends(get_db)
):
    ip = request.client.host if request.client else "unknown"
    await AuthService(db).reset_password(payload.phone, payload.new_password, ip=ip)


@router.post("/users", response_model=UserResponse, status_code=status.HTTP_201_CREATED)
async def create_user(
    payload: CreateUserRequest,
    _: User = Depends(require_admin),
    db: AsyncIOMotorDatabase = Depends(get_db),
):
    user = await AuthService(db).create_user(
        payload.phone, payload.password, payload.full_name, payload.role
    )
    return _to_response(user)


@router.get("/users", response_model=list[UserResponse])
async def list_users(
    _: User = Depends(require_admin), db: AsyncIOMotorDatabase = Depends(get_db)
):
    return [_to_response(u) for u in await UserRepository(db).list_all()]


@router.get("/me", response_model=UserResponse)
async def me(user: User = Depends(get_current_user)):
    return _to_response(user)
