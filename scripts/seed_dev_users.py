"""Tạo tài khoản dev để test tay: 1 chủ tiệm + 4 khách. Chạy lại nhiều lần được.

    PYTHONPATH=. python scripts/seed_dev_users.py

Không có đăng ký tự do trong app, nên tài khoản đầu tiên phải seed bằng tay.
Mật khẩu ở đây là mật khẩu dev — đừng chạy script này lên database thật.
"""
import asyncio

from app.core.config import settings
from app.core.errors import PhoneTakenError
from app.infrastructure.database import create_mongodb_connection, ensure_indexes
from app.services.auth import AuthService

# Bốn khách để thử những thứ cần nhiều người cùng lúc: giành slot (hai người
# đặt trùng giờ), hạn mức chat tính riêng từng người, và thông báo realtime chỉ
# về đúng socket của chủ nhân. Mật khẩu để giống nhau cho dễ gõ.
DEV_USERS = [
    ("0901234567", "chutiem123", "Chủ tiệm", "admin"),
    ("0912345678", "matkhau123", "Cô Lan", "user"),
    ("0923456789", "matkhau123", "Chú Hùng", "user"),
    ("0934567890", "matkhau123", "Bác Ba", "user"),
    ("0945678901", "matkhau123", "Cô Tám", "user"),
]


async def main() -> None:
    conn = await create_mongodb_connection(str(settings.mongo_uri), settings.mongo_db_name)
    db = conn.get_database()
    await ensure_indexes(db)

    for phone, password, full_name, role in DEV_USERS:
        try:
            await AuthService(db).create_user(phone, password, full_name, role=role)
            print(f"tạo mới  {phone}  {role:<5}  {full_name}")
        except PhoneTakenError:
            print(f"đã có    {phone}  {role:<5}  {full_name}")

    await conn.disconnect()


if __name__ == "__main__":
    asyncio.run(main())
