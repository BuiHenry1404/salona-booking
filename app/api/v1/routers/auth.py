from datetime import timedelta
from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordRequestForm
from motor.motor_asyncio import AsyncIOMotorDatabase
from app.api.deps import get_db, get_current_active_user
from app.api.v1.schemas import Token, UserCreate, UserResponse, LoginRequest
from app.core.config import settings
from app.core.security import create_access_token, verify_password, get_password_hash
from app.models.user import CurrentUser
from app.repositories.user import UserRepository
from app.services.auth import AuthService

router = APIRouter(prefix="/auth", tags=["authentication"])


@router.post("/register", response_model=UserResponse, status_code=201, summary="Register new user")
async def register(
    user_data: UserCreate,
    db: AsyncIOMotorDatabase = Depends(get_db)
):
    # TODO(task 11): rewrite for phone-based auth
    raise NotImplementedError


@router.post("/login", response_model=Token, summary="Login user")
async def login(
    login_data: LoginRequest,
    db: AsyncIOMotorDatabase = Depends(get_db)
):
    # TODO(task 11): rewrite for phone-based auth
    raise NotImplementedError


@router.post("/token", response_model=Token, summary="OAuth2 compatible login")
async def login_for_access_token(
    form_data: OAuth2PasswordRequestForm = Depends(),
    db: AsyncIOMotorDatabase = Depends(get_db)
):
    # TODO(task 11): rewrite for phone-based auth
    raise NotImplementedError


@router.get("/me", response_model=UserResponse, summary="Get current user")
async def read_users_me(
    current_user: CurrentUser = Depends(get_current_active_user)
):
    """Get current authenticated user information."""
    return UserResponse(**current_user.model_dump()) 