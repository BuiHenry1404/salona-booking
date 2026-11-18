"""Nạp danh mục dịch vụ mock vào collection `services`. Chạy lại nhiều lần an
toàn (upsert theo `id`), như `seed_dev_users.py`.

    PYTHONPATH=. python scripts/seed_services.py

Dữ liệu sai (ví dụ `duration_minutes` không phải bội số 15) thoát mã khác 0 và
KHÔNG ghi gì — validate toàn bộ trước, chỉ kết nối Mongo sau khi dữ liệu sạch.
"""
import asyncio
import json
import sys
from pathlib import Path

from pydantic import ValidationError

from app.core.config import settings
from app.infrastructure.database import create_mongodb_connection, ensure_indexes
from app.models.service import Service
from app.services.service_catalog import ServiceCatalogService

DATA_PATH = Path(__file__).resolve().parent.parent / "data" / "mock" / "services.json"


async def main() -> None:
    try:
        raw = json.loads(DATA_PATH.read_text(encoding="utf-8"))
        services = [Service(**row) for row in raw]
    except (OSError, json.JSONDecodeError, ValidationError) as exc:
        print(f"Dữ liệu dịch vụ sai, không ghi gì: {exc}", file=sys.stderr)
        sys.exit(1)

    conn = await create_mongodb_connection(str(settings.mongo_uri), settings.mongo_db_name)
    db = conn.get_database()
    await ensure_indexes(db)

    count = await ServiceCatalogService(db).upsert_many(services)
    print(f"Đã nạp {count} dịch vụ")

    await conn.disconnect()


if __name__ == "__main__":
    asyncio.run(main())
