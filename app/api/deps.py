from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from motor.motor_asyncio import AsyncIOMotorDatabase
from app.core.security import verify_token, verify_api_key
from app.models.user import User
from app.services.auth import AuthService
from app.infrastructure.llm import LLMManager

# Một instance duy nhất: mỗi HTTPBearer() sinh thêm một ô "Authorize" trong
# Swagger, hai ô giống hệt nhau thì người dùng không biết điền vào đâu.
security = HTTPBearer()


def get_db(request: Request) -> AsyncIOMotorDatabase:
    """Get database instance from app state."""
    if hasattr(request.app.state, 'db'):
        return request.app.state.db
    else:
        raise RuntimeError("Database not initialized in application state")


def get_llm_client(request: Request) -> LLMManager:
    """Get LLM manager instance from app state."""
    if hasattr(request.app.state, 'llm_manager'):
        return request.app.state.llm_manager
    else:
        raise RuntimeError("LLM manager not initialized in application state")


def get_autogen_llm_client(request: Request):
    """Get raw AutoGen model client for use with AutoGen agents."""
    if hasattr(request.app.state, 'llm_manager'):
        # Get the raw AutoGen client from the manager
        client = request.app.state.llm_manager.get_client()
        if hasattr(client, 'client'):
            return client.client
        else:
            raise RuntimeError("Client does not have underlying AutoGen client")
    else:
        raise RuntimeError("LLM manager not initialized in application state")


async def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(security),
    db: AsyncIOMotorDatabase = Depends(get_db),
) -> User:
    try:
        payload = verify_token(credentials.credentials)
        user_id = payload.get("sub")
    except Exception:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Phiên đăng nhập đã hết hạn")

    user = await AuthService(db).get_user_by_id(user_id) if user_id else None
    if not user or not user.is_active:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Phiên đăng nhập đã hết hạn")

    # Đổi mật khẩu thu hồi mọi token phát trước đó. Không có bước này thì nạn
    # nhân bị chiếm tài khoản, đổi lại mật khẩu, mà token của kẻ chiếm vẫn dùng
    # được tới hết JWT_EXPIRE_MINUTES.
    if payload.get("tv", 0) != user.token_version:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Phiên đăng nhập đã hết hạn")

    return user


async def require_admin(user: User = Depends(get_current_user)) -> User:
    """Chặn ở backend, không chỉ ẩn trên React."""
    if user.role != "admin":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Không có quyền")
    return user


async def get_current_active_user(
    current_user: User = Depends(get_current_user)
) -> User:
    """Get current active user."""
    if not current_user.is_active:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Inactive user"
        )
    return current_user


def verify_api_key_dependency(
    credentials: HTTPAuthorizationCredentials = Depends(security)
) -> bool:
    """Verify API key for internal services."""
    api_key = credentials.credentials
    if not verify_api_key(api_key):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid API key"
        )
    return True


class CommonQueryParams:
    """Common query parameters for list endpoints."""
    
    def __init__(
        self,
        skip: int = 0,
        limit: int = 20,
        sort_by: str = "created_at",
        sort_order: int = -1  # -1 for desc, 1 for asc
    ):
        self.skip = max(0, skip)
        self.limit = min(100, max(1, limit))  # Cap at 100 items
        self.sort_by = sort_by
        self.sort_order = sort_order 