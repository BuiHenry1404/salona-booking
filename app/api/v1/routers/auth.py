from typing import Optional

from fastapi import APIRouter, Cookie, Depends, HTTPException, Request, Response, status
from motor.motor_asyncio import AsyncIOMotorDatabase

from app.api.deps import get_current_user, get_db, require_admin
from app.api.v1.schemas import (CreateUserRequest, LoginRequest,
                                ResetPasswordRequest, TokenResponse,
                                UserResponse)
from app.core.config import settings
from app.core.errors import InvalidRefreshTokenError
from app.core.security import create_access_token_for
from app.models.user import User
from app.repositories.user import UserRepository
from app.services.auth import AuthService

router = APIRouter(prefix="/auth", tags=["auth"])

REFRESH_COOKIE = "refresh_token"
# Cookie chỉ được gửi kèm khi gọi đúng nhóm endpoint này, không đính vào mọi
# request tới API — bề mặt lộ ra càng hẹp càng tốt.
COOKIE_PATH = "/api/v1/auth"


def _set_refresh_cookie(response: Response, raw: str) -> None:
    """HttpOnly để JavaScript không đọc được — đó là toàn bộ lý do dùng cookie
    thay vì trả token trong body. SameSite=strict chặn gửi kèm từ site khác."""
    response.set_cookie(
        REFRESH_COOKIE,
        raw,
        max_age=settings.refresh_token_days * 24 * 3600,
        httponly=True,
        secure=settings.cookie_secure,
        samesite="strict",
        path=COOKIE_PATH,
    )


def _to_response(user: User) -> UserResponse:
    return UserResponse(
        id=str(user.id), phone=user.phone, full_name=user.full_name,
        role=user.role, is_active=user.is_active,
    )


@router.post("/login", response_model=TokenResponse)
async def login(
    payload: LoginRequest,
    request: Request,
    response: Response,
    db: AsyncIOMotorDatabase = Depends(get_db),
):
    ip = request.client.host if request.client else "unknown"
    svc = AuthService(db)
    user = await svc.authenticate(payload.phone, payload.password, ip=ip)
    if not user:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Số điện thoại hoặc mật khẩu không đúng")
    _set_refresh_cookie(response, await svc.issue_refresh_token(str(user.id)))
    return TokenResponse(access_token=create_access_token_for(user), role=user.role)


@router.post("/refresh", response_model=TokenResponse)
async def refresh(
    response: Response,
    refresh_token: Optional[str] = Cookie(default=None, alias=REFRESH_COOKIE),
    db: AsyncIOMotorDatabase = Depends(get_db),
):
    """Đổi refresh token lấy cặp mới. Không cần `Authorization` — chính cookie
    là bằng chứng, và nó chỉ được gửi tới đúng đường dẫn này."""
    if not refresh_token:
        raise InvalidRefreshTokenError()

    user, new_refresh = await AuthService(db).consume_refresh_token(refresh_token)
    _set_refresh_cookie(response, new_refresh)
    return TokenResponse(access_token=create_access_token_for(user), role=user.role)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(
    response: Response,
    refresh_token: Optional[str] = Cookie(default=None, alias=REFRESH_COOKIE),
    db: AsyncIOMotorDatabase = Depends(get_db),
):
    """Không có cookie cũng trả 204 — nút đăng xuất không bao giờ báo lỗi."""
    if refresh_token:
        await AuthService(db).revoke_refresh_family(refresh_token)
    response.delete_cookie(REFRESH_COOKIE, path=COOKIE_PATH)


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
