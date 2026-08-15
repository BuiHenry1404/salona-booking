from typing import Optional

from motor.motor_asyncio import AsyncIOMotorDatabase

from app.core.errors import AppError, NotFoundError, PhoneTakenError
from app.core.logging import get_logger
from app.core.config import settings
from app.core.phone import InvalidPhoneError, normalize_phone
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

    async def authenticate(self, phone: str, password: str, ip: str) -> Optional[User]:
        """Trả về `None` nếu sai, ném `RateLimitedError` nếu đã bị khoá tạm.

        Kiểm hạn mức TRƯỚC khi so mật khẩu, và chỉ ghi dấu khi sai — xem
        `RateLimitService.check`.

        SĐT không đúng định dạng vẫn tính vào hạn mức theo IP, nếu không kẻ dò
        chỉ cần đổi SĐT mỗi lần là thoát giới hạn.
        """
        keys = [f"login:ip:{ip}"]
        try:
            keys.append(f"login:phone:{normalize_phone(phone)}")
        except InvalidPhoneError:
            pass

        for key in keys:
            await self.rate_limit.check(
                key, settings.login_max_attempts, settings.login_window_seconds
            )

        user = await self.user_repo.get_by_phone(phone)
        if user and user.is_active and verify_password(password, user.hashed_password):
            return user

        for key in keys:
            await self.rate_limit.hit(key, settings.login_window_seconds)
        return None

    async def create_user(
        self, phone: str, password: str, full_name: Optional[str], role: Role = "user"
    ) -> User:
        """Chỉ admin gọi. Không có đăng ký tự do."""
        normalised = normalize_phone(phone)
        if await self.user_repo.get_by_phone(normalised):
            raise PhoneTakenError()
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
