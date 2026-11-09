import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.core.config import settings
from app.core.errors import UnknownServiceError
from app.models.service import Service
from app.services.service_catalog import ServiceCatalogService

DATA_PATH = Path(__file__).resolve().parent.parent / "data" / "mock" / "services.json"


def _service(**overrides) -> Service:
    base = dict(
        id="svc-test",
        category="nail",
        name="Dịch vụ test",
        aliases=[],
        price_min=10000,
        price_max=10000,
        duration_minutes=30,
        active=True,
    )
    base.update(overrides)
    return Service(**base)


async def _seeded(test_db) -> ServiceCatalogService:
    raw = json.loads(DATA_PATH.read_text(encoding="utf-8"))
    services = [Service(**row) for row in raw]
    catalog = ServiceCatalogService(test_db)
    await catalog.upsert_many(services)
    return catalog


async def test_service_rejects_duration_not_multiple_of_15():
    with pytest.raises(ValidationError):
        _service(duration_minutes=40)


async def test_get_many_keeps_order_and_rejects_unknown(test_db):
    catalog = await _seeded(test_db)

    result = await catalog.get_many(["toc-nhuom", "nail-son-gel"])

    assert [s.id for s in result] == ["toc-nhuom", "nail-son-gel"]
    with pytest.raises(UnknownServiceError):
        await catalog.get_many(["x"])


async def test_duration_for_sums_and_defaults(test_db):
    catalog = await _seeded(test_db)

    assert await catalog.duration_for(["nail-son-gel", "toc-goi-say"]) == 75
    assert await catalog.duration_for([]) == settings.booking_slot_minutes


async def test_price_range_sums(test_db):
    catalog = await _seeded(test_db)

    assert await catalog.price_range(["nail-son-gel", "toc-goi-say"]) == (170000, 250000)


@pytest.mark.parametrize("text, ids", [
    ("sơn gel", ["nail-son-gel"]),
    ("son gel", ["nail-son-gel"]),
    ("nhuộm tóc", ["toc-nhuom"]),
    ("NHUỘM", ["toc-nhuom"]),
    ("xăm", []),
])
async def test_search_table(test_db, text, ids):
    catalog = await _seeded(test_db)

    result = await catalog.search(text)

    assert sorted(s.id for s in result) == sorted(ids)


async def test_inactive_service_hidden_from_search_but_readable_by_get(test_db):
    catalog = await _seeded(test_db)
    inactive = _service(
        id="nail-tay-trang",
        name="Tẩy trắng móng",
        aliases=["tẩy trắng"],
        active=False,
    )
    await catalog.upsert_many([inactive])

    assert await catalog.search("tẩy trắng") == []
    got = await catalog.get("nail-tay-trang")
    assert got is not None
    assert got.active is False


async def test_seed_is_idempotent(test_db):
    await _seeded(test_db)
    first_count = await test_db["services"].count_documents({})

    await _seeded(test_db)
    second_count = await test_db["services"].count_documents({})

    assert first_count == second_count == 10
