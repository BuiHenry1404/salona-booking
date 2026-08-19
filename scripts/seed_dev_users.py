"""Tạo hai tài khoản dev để test tay. Chạy lại nhiều lần được.

    PYTHONPATH=. python scripts/seed_dev_users.py

Không có đăng ký tự do trong app, nên tài khoản đầu tiên phải seed bằng tay.
Mật khẩu ở đây là mật khẩu dev — đừng chạy script này lên database thật.
"""
import asyncio

from app.core.config import settings
from app.core.errors import PhoneTakenError
from app.infrastructure.database import create_mongodb_connection, ensure_indexes
from app.services.auth import AuthService

DEV_USERS = [
    ("0901234567", "chutiem123", "Chủ tiệm", "admin"),
    ("0912345678", "matkhau123", "Cô Lan", "user"),
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
