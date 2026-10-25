"""Review fix (Task 10, finding 4): `ping_redis()` lỗi lúc lifespan phải tắt
Redis hẳn cho tiến trình, và client phải có timeout socket ngắn (một lượt
chat có trần 60s — Redis treo không được kéo cả lượt theo)."""
import pytest

from app.core.config import settings
from app.infrastructure import redis as redis_module

pytestmark = pytest.mark.asyncio


@pytest.fixture(autouse=True)
def _reset_redis_module():
    redis_module.reset_redis_for_tests()
    yield
    redis_module.reset_redis_for_tests()


def test_get_redis_client_has_short_socket_timeouts(monkeypatch):
    monkeypatch.setattr(settings, "redis_url", "redis://localhost:6399/0")

    client = redis_module.get_redis()

    assert client is not None
    kwargs = client.connection_pool.connection_kwargs
    assert kwargs.get("socket_connect_timeout") == redis_module._SOCKET_CONNECT_TIMEOUT
    assert kwargs.get("socket_timeout") == redis_module._SOCKET_TIMEOUT


async def test_failed_ping_disables_redis_for_the_rest_of_the_process(monkeypatch):
    # Cổng 1 không ai lắng nghe — connection refused gần như ngay lập tức,
    # không cần chờ hết timeout để test nhanh.
    monkeypatch.setattr(settings, "redis_url", "redis://localhost:1/0")

    ok = await redis_module.ping_redis()

    assert ok is False
    # Tắt hẳn: KHÔNG thử tạo lại client ở lần gọi get_redis() sau, dù
    # `settings.redis_url` vẫn còn đó.
    assert redis_module.get_redis() is None


async def test_successful_ping_keeps_redis_enabled(monkeypatch):
    class FakeClient:
        async def ping(self):
            return True

    monkeypatch.setattr(redis_module, "get_redis", lambda: FakeClient())

    ok = await redis_module.ping_redis()

    assert ok is True


def test_unset_redis_url_returns_none_without_building_a_client(monkeypatch):
    monkeypatch.setattr(settings, "redis_url", None)

    assert redis_module.get_redis() is None
