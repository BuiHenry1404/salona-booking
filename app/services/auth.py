from typing import Optional

from motor.motor_asyncio import AsyncIOMotorDatabase

from app.core.errors import AppError, NotFoundError
from app.core.logging import get_logger
from app.core.phone import normalize_phone
from app.core.security import get_password_hash, verify_password
from app.models.user import Role, User
from app.repositories.user import UserRepository
from app.services.rate_limit import RateLimitService

logger = get_logger(__name__)

RESET_LIMIT = 5
RESET_WINDOW_SECONDS = 3600


class AuthService:
    def __init__(self, db: AsyncIOMotorDatabase):
        self.user_repo = UserRepository(db)
        self.rate_limit = RateLimitService(db)

    async def authenticate(self, phone: str, password: str) -> Optional[User]:
        user = await self.user_repo.get_by_phone(phone)
        if not user or not user.is_active:
            return None
        if not verify_password(password, user.hashed_password):
            return None
        return user

    async def create_user(
        self, phone: str, password: str, full_name: Optional[str], role: Role = "user"
    ) -> User:
        """Chỉ admin gọi. Không có đăng ký tự do."""
        normalised = normalize_phone(phone)
        if await self.user_repo.get_by_phone(normalised):
            raise AppError("Số điện thoại này đã có tài khoản")
        return await self.user_repo.create_user(
            phone=normalised,
            hashed_password=get_password_hash(password),
            full_name=full_name,
            role=role,
        )

    async def reset_password(self, phone: str, new_password: str, ip: str) -> None:
        """Đúng luồng trong spec: có SĐT trong DB thì cho đổi.

        Rủi ro đã được chủ dự án cân nhắc và chấp nhận (xem README spec): ai biết
        SĐT cũng đổi được mật khẩu. Giảm thiểu bằng rate limit theo cả SĐT lẫn IP,
        và ghi log mọi lần đổi.
        """
        normalised = normalize_phone(phone)
        await self.rate_limit.check_and_hit(f"pwreset:phone:{normalised}", RESET_LIMIT, RESET_WINDOW_SECONDS)
        await self.rate_limit.check_and_hit(f"pwreset:ip:{ip}", RESET_LIMIT, RESET_WINDOW_SECONDS)

        user = await self.user_repo.get_by_phone(normalised)
        if not user:
            raise NotFoundError("Không tìm thấy tài khoản với số này")

        await self.user_repo.set_password(str(user.id), get_password_hash(new_password))
        logger.warning("password_reset", extra={"phone": normalised, "ip": ip})

    async def get_user_by_id(self, user_id: str) -> Optional[User]:
        return await self.user_repo.get_by_id(user_id)
