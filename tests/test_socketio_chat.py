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


class TestChatRateLimit:
    """Trần chi phí LLM cho chat.

    Mỗi lượt chạy ít nhất 2 lượt gọi LLM (supervisor + subagent), thêm timeparse
    khi khách nhắc giờ. Một tài khoản hợp lệ giữ socket và bắn liên tục sẽ nhân
    chi phí theo ý muốn, và vì hệ chạy đúng 1 worker, tải nặng còn làm chậm cả
    Telegram lẫn khách khác.
    """

    @staticmethod
    def _counting_turn(calls):
        async def fake_turn(db, u, question):
            calls.append(question)
            yield AgentEvent("complete", {"answer": "dạ"})
        return fake_turn

    async def test_a_normal_conversation_is_never_touched(
        self, service, test_db, monkeypatch
    ):
        """15 tin là một cuộc đặt lịch thật. Hàng rào không được cản người dùng
        thật — chạm phải nó là hỏng sản phẩm, không phải bảo vệ sản phẩm."""
        user = await AuthService(test_db).create_user("0912345678", "matkhau123", "Cô Lan")
        calls = []
        monkeypatch.setattr(
            "app.services.socketio_service.run_turn", self._counting_turn(calls)
        )

        for i in range(15):
            await service.handle_message(sid="s1", user=user, message=f"tin {i}")

        assert len(calls) == 15
        assert "error" not in service.sio.events()

    async def test_over_the_limit_the_agent_is_never_called(
        self, service, test_db, monkeypatch
    ):
        """Đây là chỗ tiết kiệm tiền: vượt trần thì KHÔNG chạm LLM."""
        from app.core.config import settings

        monkeypatch.setattr(settings, "chat_max_per_hour", 3)
        user = await AuthService(test_db).create_user("0912345678", "matkhau123", "Cô Lan")
        calls = []
        monkeypatch.setattr(
            "app.services.socketio_service.run_turn", self._counting_turn(calls)
        )

        for i in range(6):
            await service.handle_message(sid="s1", user=user, message=f"tin {i}")

        assert len(calls) == 3
        assert service.sio.events().count("error") == 3

    async def test_the_limit_message_does_not_blame_the_machine(
        self, service, test_db, monkeypatch
    ):
        """Nếu RateLimitedError rơi vào `except Exception` chung thì khách nhận
        "Máy đang bận chút xíu" — đổ lỗi cho máy trong khi thật ra là họ nhắn
        quá nhanh. Hai câu phải khác nhau."""
        from app.core.config import settings

        monkeypatch.setattr(settings, "chat_max_per_hour", 1)
        user = await AuthService(test_db).create_user("0912345678", "matkhau123", "Cô Lan")
        monkeypatch.setattr(
            "app.services.socketio_service.run_turn", self._counting_turn([])
        )

        await service.handle_message(sid="s1", user=user, message="tin 1")
        await service.handle_message(sid="s1", user=user, message="tin 2")

        errors = [d for e, d, _ in service.sio.sent if e == "error"]
        assert len(errors) == 1
        assert "Máy đang bận" not in errors[0]["message"]
        assert "nhanh" in errors[0]["message"]

    async def test_the_customer_is_told_how_long_to_wait(
        self, service, test_db, monkeypatch
    ):
        """Chuẩn IETF (Retry-After) khuyên nói rõ chờ bao lâu để client tự điều
        tiết. Không có con số thì cụ già bấm hoài, mà mỗi lần bấm lại tính thêm
        một dấu — tự khoá mình lâu hơn."""
        from app.core.config import settings

        monkeypatch.setattr(settings, "chat_max_per_hour", 1)
        user = await AuthService(test_db).create_user("0912345678", "matkhau123", "Cô Lan")
        monkeypatch.setattr(
            "app.services.socketio_service.run_turn", self._counting_turn([])
        )

        await service.handle_message(sid="s1", user=user, message="tin 1")
        await service.handle_message(sid="s1", user=user, message="tin 2")

        payload = [d for e, d, _ in service.sio.sent if e == "error"][0]
        assert 0 < payload["retry_after_seconds"] <= 3600
        # Câu phải mang một mốc thời gian; cách diễn đạt do TestTooFastWording lo.
        assert "phút" in payload["message"] or "tiếng" in payload["message"]

    async def test_the_limit_follows_the_user_not_the_socket(
        self, service, test_db, monkeypatch
    ):
        """Mở thêm tab là có sid mới. Tính theo sid thì nhân đôi hạn mức bằng
        một cú Ctrl+T."""
        from app.core.config import settings

        monkeypatch.setattr(settings, "chat_max_per_hour", 2)
        user = await AuthService(test_db).create_user("0912345678", "matkhau123", "Cô Lan")
        calls = []
        monkeypatch.setattr(
            "app.services.socketio_service.run_turn", self._counting_turn(calls)
        )

        await service.handle_message(sid="tab1", user=user, message="a")
        await service.handle_message(sid="tab2", user=user, message="b")
        await service.handle_message(sid="tab2", user=user, message="c")

        assert len(calls) == 2
        assert service.sio.events().count("error") == 1

    async def test_two_customers_do_not_share_a_quota(
        self, service, test_db, monkeypatch
    ):
        """Khách này spam không được làm khách kia câm."""
        from app.core.config import settings

        monkeypatch.setattr(settings, "chat_max_per_hour", 1)
        lan = await AuthService(test_db).create_user("0912345678", "matkhau123", "Cô Lan")
        tu = await AuthService(test_db).create_user("0987654321", "matkhau123", "Cô Tư")
        calls = []
        monkeypatch.setattr(
            "app.services.socketio_service.run_turn", self._counting_turn(calls)
        )

        await service.handle_message(sid="s1", user=lan, message="a")
        await service.handle_message(sid="s1", user=lan, message="b")
        await service.handle_message(sid="s2", user=tu, message="c")

        assert len(calls) == 2

    async def test_an_empty_message_costs_no_quota(self, service, test_db, monkeypatch):
        """Gõ hụt rồi bấm Gửi không được tiêu hạn mức: không có lượt LLM nào chạy."""
        from app.core.config import settings

        monkeypatch.setattr(settings, "chat_max_per_hour", 1)
        user = await AuthService(test_db).create_user("0912345678", "matkhau123", "Cô Lan")
        calls = []
        monkeypatch.setattr(
            "app.services.socketio_service.run_turn", self._counting_turn(calls)
        )

        for _ in range(5):
            await service.handle_message(sid="s1", user=user, message="   ")
        await service.handle_message(sid="s1", user=user, message="thật")

        assert len(calls) == 1
        assert "error" not in service.sio.events()


class TestTooFastWording:
    """Câu báo phải đọc lên nghe được. Người nhận là cô chú lớn tuổi."""

    def test_a_full_hour_is_said_as_an_hour_not_sixty_minutes(self):
        from app.services.socketio_service import SocketIOService

        assert "1 tiếng" in SocketIOService._too_fast_message(3600)
        assert "60 phút" not in SocketIOService._too_fast_message(3600)

    def test_under_a_minute_still_rounds_up_to_one_minute(self):
        """Không bao giờ nói "0 phút nữa" — nghe như đùa."""
        from app.services.socketio_service import SocketIOService

        assert "1 phút" in SocketIOService._too_fast_message(5)

    def test_a_normal_wait_is_said_in_minutes(self):
        from app.services.socketio_service import SocketIOService

        assert "12 phút" in SocketIOService._too_fast_message(720)
