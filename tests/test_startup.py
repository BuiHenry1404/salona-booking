"""App phải khởi động được với đúng .env mà README bảo người ta tạo.

Cả bộ test còn lại không bao giờ chạy lifespan: async_client dùng
AsyncClient(app=...) (không kích hoạt startup), nên một app không boot nổi vẫn
cho 87 test xanh. Test này đóng đúng khe hở đó.
"""
from fastapi.testclient import TestClient

from main import app


def test_app_starts_and_serves_health():
    """Vào lifespan thật: kết nối Mongo, ensure_indexes, mount Socket.IO."""
    with TestClient(app) as client:
        assert client.get("/api/v1/health").status_code == 200


def test_app_starts_without_ai_credentials(monkeypatch):
    """Thiếu key AI không được làm chết app — đặt lịch không cần LLM."""
    async def _die():
        raise ValueError("Azure endpoint and deployment are required for Azure OpenAI")

    monkeypatch.setattr("main.initialize_llm_clients", _die)

    with TestClient(app) as client:
        assert client.get("/api/v1/health").status_code == 200
    assert app.state.llm_manager is None


def test_readiness_returns_503_when_mongo_is_unreachable():
    """Probe phải fail bằng STATUS CODE. Trả 200 kèm "not ready" trong body thì
    k8s/LB vẫn đẩy traffic vào instance hỏng — đúng kịch bản mongod chết sau
    khi app đã khởi động."""
    from pymongo.errors import ServerSelectionTimeoutError

    from app.api.deps import get_db

    class _DeadDb:
        async def command(self, *_args, **_kwargs):
            raise ServerSelectionTimeoutError("no primary available")

    app.dependency_overrides[get_db] = lambda: _DeadDb()
    try:
        with TestClient(app) as client:
            resp = client.get("/api/v1/health/ready")
        assert resp.status_code == 503
        assert "ServerSelection" not in resp.text
    finally:
        app.dependency_overrides.clear()


def test_readiness_returns_200_when_mongo_is_up():
    with TestClient(app) as client:
        assert client.get("/api/v1/health/ready").status_code == 200


def test_socketio_is_reachable_at_the_default_client_path():
    """Client gọi `io()` đi vào /socket.io/ — phải có handshake ở đúng đó.

    `socketio.ASGIApp` mặc định tự phục vụ dưới tiền tố "socket.io"; mount nó
    vào /socket.io nữa là địa chỉ thật thành /socket.io/socket.io/ và mọi client
    mặc định nhận 404. Test đơn vị gọi thẳng `handle_message` nên không thấy.
    """
    with TestClient(app) as client:   # phải vào lifespan, mount xảy ra ở đó
        resp = client.get("/socket.io/", params={"EIO": "4", "transport": "polling"})

    assert resp.status_code == 200, resp.text
    assert resp.text.lstrip("0").startswith("{"), resp.text[:80]

def test_lifespan_registers_socket_notifier_channel():
    """Vào lifespan thật: SocketNotifier phải là kênh duy nhất, bọc đúng
    socketio_service vừa tạo."""
    from app.services.notifications import notifications
    from app.services.socket_notifier import SocketNotifier

    notifications.clear()
    try:
        with TestClient(app):
            assert len(notifications.channels) == 1
            assert isinstance(notifications.channels[0], SocketNotifier)
            assert notifications.channels[0].socketio is app.state.socketio_service
    finally:
        notifications.clear()


def test_rerunning_lifespan_does_not_duplicate_channels():
    """Reload/test chạy lifespan nhiều lần không được đăng ký trùng kênh."""
    from app.services.notifications import notifications

    notifications.clear()
    try:
        with TestClient(app):
            pass
        with TestClient(app):
            assert len(notifications.channels) == 1
    finally:
        notifications.clear()


def test_socketio_failure_leaves_no_channels_but_app_still_boots(monkeypatch):
    """Socket.IO chết là fail-soft: app vẫn khởi động, và không có kênh rác."""
    from app.services.notifications import notifications

    class _Broken:
        def __init__(self, db):
            raise RuntimeError("socket hỏng")

    monkeypatch.setattr("main.SocketIOService", _Broken)
    notifications.clear()
    try:
        with TestClient(app) as client:
            assert client.get("/api/v1/health").status_code == 200
        assert app.state.socketio_service is None
        assert notifications.channels == []
    finally:
        notifications.clear()
