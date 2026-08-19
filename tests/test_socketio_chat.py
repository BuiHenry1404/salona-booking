import pytest

from app.agents.booking_graph.events import AgentEvent
from app.services.auth import AuthService
from app.services.socketio_service import SocketIOService

pytestmark = pytest.mark.asyncio


class Recorder:
    """Ghi lại mọi emit thay vì gửi qua mạng."""

    def __init__(self):
        self.sent = []

    async def emit(self, event, data=None, room=None, **kwargs):
        self.sent.append((event, data, room))

    def events(self):
        return [e for e, _, _ in self.sent]


@pytest.fixture
def service(test_db):
    svc = SocketIOService(test_db)
    svc.sio = Recorder()
    return svc


async def test_events_are_forwarded_in_order(service, test_db, monkeypatch):
    user = await AuthService(test_db).create_user("0912345678", "matkhau123", "Cô Lan")

    async def fake_turn(db, u, question):
        yield AgentEvent("turn_started")
        yield AgentEvent("tool_started", {"name": "find_free_slots"})
        yield AgentEvent("tool_finished", {"name": "find_free_slots", "ok": True})
        yield AgentEvent("token", {"text": "Dạ "})
        yield AgentEvent("token", {"text": "được ạ"})
        yield AgentEvent("complete", {"answer": "Dạ được ạ"})

    monkeypatch.setattr("app.services.socketio_service.run_turn", fake_turn)

    await service.handle_message(sid="s1", user=user, message="mai 3h được không")

    assert service.sio.events() == [
        "turn_started", "tool_started", "tool_finished", "token", "token", "complete",
    ]


async def test_every_event_goes_only_to_that_users_socket(service, test_db, monkeypatch):
    user = await AuthService(test_db).create_user("0912345678", "matkhau123", "Cô Lan")

    async def fake_turn(db, u, question):
        yield AgentEvent("token", {"text": "riêng tư"})

    monkeypatch.setattr("app.services.socketio_service.run_turn", fake_turn)
    await service.handle_message(sid="s1", user=user, message="hỏi gì đó")

    assert {room for _, _, room in service.sio.sent} == {"s1"}


async def test_agent_failure_emits_error_not_a_crash(service, test_db, monkeypatch):
    user = await AuthService(test_db).create_user("0912345678", "matkhau123", "Cô Lan")

    async def broken(db, u, question):
        raise RuntimeError("hỏng")
        yield  # pragma: no cover

    monkeypatch.setattr("app.services.socketio_service.run_turn", broken)
    await service.handle_message(sid="s1", user=user, message="gì đó")

    assert "error" in service.sio.events()


async def test_empty_message_is_ignored(service, test_db):
    user = await AuthService(test_db).create_user("0912345678", "matkhau123", "Cô Lan")
    await service.handle_message(sid="s1", user=user, message="   ")
    assert service.sio.events() == []
