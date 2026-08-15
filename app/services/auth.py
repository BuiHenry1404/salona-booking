import hashlib
import secrets
from datetime import timedelta
from typing import Optional, Tuple

from motor.motor_asyncio import AsyncIOMotorDatabase

from app.core.clock import now_utc
from app.core.errors import (AppError, InvalidRefreshTokenError, NotFoundError,
                             PhoneTakenError)
from app.core.logging import get_logger
from app.core.config import settings
from app.core.phone import InvalidPhoneError, normalize_phone
from app.core.security import get_password_hash, verify_password
from app.models.user import Role, User
from app.repositories.refresh_token import RefreshTokenRepository
from app.repositories.user import UserRepository
from app.services.rate_limit import RateLimitService

logger = get_logger(__name__)

RESET_LIMIT = 5
RESET_WINDOW_SECONDS = 3600

# Token đã xoay vẫn được chấp nhận thêm chừng này giây.
#
# Không có cửa sổ này thì xoay vòng tự tạo ra lỗi: điện thoại mạng chập chờn bắn
# hai request song song đúng lúc access token hết hạn, cả hai cùng trình một
# refresh token. Cái thứ hai trông y hệt một vụ phát lại token bị đánh cắp, và
# khách bị đăng xuất dù không ai tấn công.
#
# 30 giây là mặc định của Okta (chỉnh được 0-60); Auth0 gọi cùng thứ này là
# "rotation overlap period". Mạng 3G ở VN có thể chậm hơn 10 giây thật.
REFRESH_GRACE_SECONDS = 30


def _hash_token(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()


class AuthService:
    def __init__(self, db: AsyncIOMotorDatabase):
        self.user_repo = UserRepository(db)
        self.refresh_repo = RefreshTokenRepository(db)
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
        # `token_version` chỉ giết access token. Không xoá refresh token thì kẻ
        # chiếm tài khoản vẫn tự cấp access token mới sau khi nạn nhân đổi mật
        # khẩu — thủng đúng lỗ vừa vá.
        await self.revoke_all_refresh_tokens(str(user.id))
        logger.warning("password_reset", extra={"phone": normalised, "ip": ip})

    async def issue_refresh_token(self, user_id: str, family_id: Optional[str] = None) -> str:
        """Sinh token thô, lưu hash, trả token thô về cho client.

        Không truyền `family_id` = mở phiên mới (một lần đăng nhập). Truyền vào
        = xoay tiếp trong phiên đang có.
        """
        raw = secrets.token_urlsafe(32)
        await self.refresh_repo.create_token(
            user_id=user_id,
            family_id=family_id or secrets.token_urlsafe(16),
            token_hash=_hash_token(raw),
            expires_at=now_utc() + timedelta(days=settings.refresh_token_days),
        )
        return raw

    async def consume_refresh_token(self, raw: str) -> Tuple[User, str]:
        """Đổi refresh token lấy một cái mới. Trả về (user, token mới).

        Mọi nguyên nhân hỏng đều ném cùng một lỗi: phân biệt "không tồn tại" với
        "đã bị thu hồi" là nói cho kẻ tấn công biết nó đoán trúng tới đâu.
        """
        record = await self.refresh_repo.get_by_hash(_hash_token(raw))
        if record is None or record.expires_at <= now_utc():
            raise InvalidRefreshTokenError()

        if record.used_at is not None:
            if (now_utc() - record.used_at).total_seconds() > REFRESH_GRACE_SECONDS:
                # Ngoài cửa sổ ân hạn thì đây là token bị đánh cắp đem phát lại.
                # Xoá cả family: kẻ cắp mất đường, khách đăng nhập lại.
                await self.refresh_repo.delete_family(record.family_id)
                logger.warning(
                    "refresh_token_reuse_detected", user_id=record.user_id,
                    family_id=record.family_id,
                )
                raise InvalidRefreshTokenError()

        user = await self.user_repo.get_by_id(record.user_id)
        if not user or not user.is_active:
            raise InvalidRefreshTokenError()

        await self.refresh_repo.mark_used(str(record.id))
        new_raw = await self.issue_refresh_token(record.user_id, family_id=record.family_id)
        return user, new_raw

    async def revoke_refresh_family(self, raw: str) -> None:
        """Đăng xuất một phiên. Token lạ thì im lặng bỏ qua — nút đăng xuất
        không bao giờ được báo lỗi cho khách."""
        record = await self.refresh_repo.get_by_hash(_hash_token(raw))
        if record:
            await self.refresh_repo.delete_family(record.family_id)

    async def revoke_all_refresh_tokens(self, user_id: str) -> None:
        await self.refresh_repo.delete_for_user(user_id)

    async def get_user_by_id(self, user_id: str) -> Optional[User]:
        return await self.user_repo.get_by_id(user_id)
