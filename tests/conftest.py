import pytest
import pytest_asyncio
from httpx import AsyncClient
from motor.motor_asyncio import AsyncIOMotorClient
from fastapi.testclient import TestClient

from main import app
from app.core.config import settings
from app.api.deps import get_db
from app.infrastructure.database import ensure_indexes

pytest_plugins = ("pytest_asyncio",)

# Test database settings
TEST_MONGO_URI = "mongodb://localhost:27017"
TEST_DB_NAME = "chatbot_test_db"


@pytest_asyncio.fixture
async def test_db():
    """Fresh Motor connection per test — avoids event-loop mismatch with pytest-asyncio 0.23."""
    client = AsyncIOMotorClient(TEST_MONGO_URI, tz_aware=True)
    db = client[TEST_DB_NAME]
    yield db
    client.close()


@pytest.fixture
def override_get_db(test_db):
    """Override the get_db dependency for testing."""
    def _override_get_db():
        return test_db
    return _override_get_db


@pytest.fixture
def test_app(override_get_db):
    """Create test FastAPI application."""
    app.dependency_overrides[get_db] = override_get_db
    yield app
    app.dependency_overrides.clear()


@pytest.fixture
def client(test_app):
    """Create test client."""
    return TestClient(test_app)


@pytest_asyncio.fixture
async def async_client(test_app):
    """Create async test client."""
    async with AsyncClient(app=test_app, base_url="http://test") as ac:
        yield ac


@pytest_asyncio.fixture(autouse=True)
async def clean_db(test_db):
    """Mỗi test bắt đầu từ DB trống nhưng đã có index."""
    for name in await test_db.list_collection_names():
        await test_db[name].drop()
    await ensure_indexes(test_db)
    yield


@pytest.fixture(autouse=True)
def memory_checkpointer():
    """Graph trong test KHÔNG bao giờ ghi checkpoint vào Mongo thật.

    `.env` trỏ `MONGO_DB_NAME` về DB dev; `get_checkpointer()` tự tạo
    `MongoDBSaver` trên DB đó nếu chưa có gì được đặt. Mỗi test nhận một
    `InMemorySaver` mới, xong thì trả lại giá trị cũ.
    """
    from langgraph.checkpoint.memory import InMemorySaver

    from app.agents import checkpoint
    previous = checkpoint._checkpointer
    saver = InMemorySaver()
    checkpoint.set_checkpointer(saver)
    yield saver
    checkpoint.set_checkpointer(previous)
