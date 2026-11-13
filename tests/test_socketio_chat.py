import asyncio

import pytest

from app.agents import salon
from app.agents.salon import TURN_ERROR_MESSAGE, AgentEvent, ButtonReply
from app.agents.salon.turn.events import TOOL_LABELS
from app.services import socketio_service
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


async def test_chat_is_served_by_the_salon_package():
    """Task 16: Socket.IO chạy lượt của gói `salon`, không còn gói cũ."""
    assert socketio_service.run_turn is salon.run_turn
    assert socketio_service.ButtonReply is salon.ButtonReply
    assert socketio_service.TURN_ERROR_MESSAGE == salon.TURN_ERROR_MESSAGE


async def test_send_message_runs_the_salon_turn(service, test_db, monkeypatch):
    """Câu gõ (đã `strip`) đi thẳng vào `run_turn`; mọi sự kiện của lượt — kể cả
    `label` tiếng Việt của tool — tới đúng socket, đúng thứ tự, không thêm bớt."""
    user = await AuthService(test_db).create_user("0912345678", "matkhau123", "Cô Lan")
    received = []
    label = TOOL_LABELS["find_free_slots"]
    script = [
        AgentEvent("turn_started"),
        AgentEvent("tool_started", {"name": "find_free_slots", "label": label}),
        AgentEvent("tool_finished", {"name": "find_free_slots", "label": label, "ok": True}),
        AgentEvent("complete", {"answer": "Dạ được ạ", "proposal": None}),
    ]

    async def fake_turn(db, u, question):
        received.append((db, str(u.id), question))
        for event in script:
            yield event

    monkeypatch.setattr("app.services.socketio_service.run_turn", fake_turn)

    await service.handle_message(sid="s1", user=user, message="  mai 3h được không  ")

    assert received == [(test_db, str(user.id), "mai 3h được không")]
    assert service.sio.sent == [(event.type, event.data, "s1") for event in script]
    assert "token" not in service.sio.events()


async def test_every_event_goes_only_to_that_users_socket(service, test_db, monkeypatch):
    user = await AuthService(test_db).create_user("0912345678", "matkhau123", "Cô Lan")

    async def fake_turn(db, u, question):
        yield AgentEvent("turn_started")
        yield AgentEvent("complete", {"answer": "riêng tư", "proposal": None})

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


class TestConfirmProposalEvent:
    """Nút Đồng ý/Không: đường riêng, không qua trần chat (Task 5)."""

    async def test_confirm_proposal_runs_a_button_turn_without_rate_limit(
        self, service, test_db, monkeypatch
    ):
        user = await AuthService(test_db).create_user("0912345678", "matkhau123", "Cô Lan")
        received = {}

        async def fake_turn(db, u, reply):
            received["reply"] = reply
            yield AgentEvent("complete", {"answer": "Dạ xong rồi ạ", "proposal": None})

        monkeypatch.setattr("app.services.socketio_service.run_turn", fake_turn)

        rate_limit_calls = []
        monkeypatch.setattr(
            service.rate_limit, "check_and_hit",
            lambda *a, **kw: rate_limit_calls.append((a, kw)),
        )

        await service.handle_button(sid="s1", user=user,
                                    data={"proposal_id": "p1", "decision": "yes"})

        assert received["reply"] == ButtonReply(proposal_id="p1", decision="yes")
        assert rate_limit_calls == []
        assert ("complete", {"answer": "Dạ xong rồi ạ", "proposal": None}, "s1") in service.sio.sent

    async def test_confirm_proposal_rejects_bad_payload(
        self, service, test_db, monkeypatch
    ):
        user = await AuthService(test_db).create_user("0912345678", "matkhau123", "Cô Lan")
        called = []

        async def fake_turn(db, u, reply):
            called.append(reply)
            yield AgentEvent("complete", {"answer": "x", "proposal": None})

        monkeypatch.setattr("app.services.socketio_service.run_turn", fake_turn)

        await service.handle_button(sid="s1", user=user,
                                    data={"proposal_id": "p1", "decision": "maybe"})

        assert called == []
        assert service.sio.sent == [("error", {"message": TURN_ERROR_MESSAGE}, "s1")]

    async def test_confirm_proposal_rejects_an_unhashable_decision(
        self, service, test_db, monkeypatch
    ):
        """`decision` sai kiểu nặng (dict/list, không chỉ chuỗi lạ) không được
        làm nổ `.get()` trước khi kịp trả `error`."""
        user = await AuthService(test_db).create_user("0912345678", "matkhau123", "Cô Lan")
        called = []

        async def fake_turn(db, u, reply):
            called.append(reply)
            yield AgentEvent("complete", {"answer": "x", "proposal": None})

        monkeypatch.setattr("app.services.socketio_service.run_turn", fake_turn)

        await service.handle_button(sid="s1", user=user,
                                    data={"proposal_id": "p1", "decision": ["yes"]})

        assert called == []
        assert service.sio.sent == [("error", {"message": TURN_ERROR_MESSAGE}, "s1")]

    async def test_registered_confirm_proposal_event_rejects_a_non_dict_payload(
        self, test_db, monkeypatch
    ):
        """Đi qua đúng handler ĐÃ ĐĂNG KÝ `confirm_proposal` (không phải
        `handle_button` trực tiếp): payload không phải dict (vd. client gửi
        nhầm một mảng) không được làm nổ `.get()` trước khi tới nhánh `error`
        — review finding sau Task 5 (lấy handler từ `AsyncServer` thật TRƯỚC
        khi thay `sio` bằng `Recorder`, vì đăng ký xảy ra trong `__init__`)."""
        svc = SocketIOService(test_db)
        handler = svc.sio.handlers["/"]["confirm_proposal"]
        svc.sio = Recorder()

        user = await AuthService(test_db).create_user("0912345678", "matkhau123", "Cô Lan")
        svc.sid_to_user["s1"] = str(user.id)
        called = []

        async def fake_turn(db, u, reply):
            called.append(reply)
            yield AgentEvent("complete", {"answer": "x", "proposal": None})

        monkeypatch.setattr("app.services.socketio_service.run_turn", fake_turn)

        await handler("s1", ["x"])

        assert called == []
        assert svc.sio.sent == [("error", {"message": TURN_ERROR_MESSAGE}, "s1")]


class TestMessageDedupe:
    """Task 10 — Socket.IO có thể gửi lại cùng tin (client retry/reconnect);
    server chỉ chạy MỘT lượt cho mỗi `(user_id, message_id)`."""

    async def test_duplicate_message_id_replays_complete_without_running_a_turn(
        self, service, test_db, monkeypatch
    ):
        user = await AuthService(test_db).create_user("0912345678", "matkhau123", "Cô Lan")
        calls = []

        async def fake_turn(db, u, question):
            calls.append(question)
            yield AgentEvent("complete", {"answer": "Dạ được ạ", "proposal": None})

        monkeypatch.setattr("app.services.socketio_service.run_turn", fake_turn)

        await service.handle_message(sid="s1", user=user, message="mai 3h",
                                     message_id="m1")
        await service.handle_message(sid="s1", user=user, message="mai 3h",
                                     message_id="m1")

        # Lượt THỨ HAI không chạm agent — chỉ phát lại đúng payload đã lưu.
        assert calls == ["mai 3h"]
        completes = [d for e, d, _ in service.sio.sent if e == "complete"]
        assert completes == [
            {"answer": "Dạ được ạ", "proposal": None},
            {"answer": "Dạ được ạ", "proposal": None},
        ]

    async def test_different_message_id_still_supersedes(
        self, service, test_db, monkeypatch
    ):
        """Khác `message_id` không bao giờ bị dedupe chặn, kể cả khi lượt
        trước (cùng khách) còn đang "treo" chưa xong."""
        user = await AuthService(test_db).create_user("0912345678", "matkhau123", "Cô Lan")
        calls = []
        gate = asyncio.Event()
        entered = {"a": asyncio.Event(), "b": asyncio.Event()}

        async def fake_turn(db, u, question):
            calls.append(question)
            entered[question].set()
            await gate.wait()
            yield AgentEvent("complete", {"answer": "xong", "proposal": None})

        monkeypatch.setattr("app.services.socketio_service.run_turn", fake_turn)

        # Chờ bằng sự kiện, không bằng `sleep(0)`: `dedupe.begin` đi Mongo thật,
        # một vòng event loop không đủ để lượt đầu chắc chắn đã vào `run_turn`.
        first = asyncio.create_task(
            service.handle_message(sid="s1", user=user, message="a", message_id="m1")
        )
        await asyncio.wait_for(entered["a"].wait(), 5)  # lượt đầu còn treo ở gate

        second = asyncio.create_task(
            service.handle_message(sid="s1", user=user, message="b", message_id="m2")
        )
        await asyncio.wait_for(entered["b"].wait(), 5)

        gate.set()
        await asyncio.gather(first, second)

        assert calls == ["a", "b"]
        assert service.sio.events().count("complete") == 2

    async def test_duplicate_while_running_is_ignored_not_rerun(
        self, service, test_db, monkeypatch
    ):
        """Cùng `message_id` TRONG LÚC lượt đầu còn chạy — bỏ qua, không chạy
        agent lần hai, không gửi gì cho client (client vẫn đang chờ lượt đầu)."""
        user = await AuthService(test_db).create_user("0912345678", "matkhau123", "Cô Lan")
        calls = []
        gate = asyncio.Event()
        entered = asyncio.Event()

        async def fake_turn(db, u, question):
            calls.append(question)
            entered.set()
            await gate.wait()
            yield AgentEvent("complete", {"answer": "xong", "proposal": None})

        monkeypatch.setattr("app.services.socketio_service.run_turn", fake_turn)

        first = asyncio.create_task(
            service.handle_message(sid="s1", user=user, message="a", message_id="m1")
        )
        await asyncio.wait_for(entered.wait(), 5)  # lượt đầu đã giữ khoá "đang chạy"

        await service.handle_message(sid="s1", user=user, message="a", message_id="m1")

        gate.set()
        await first

        assert calls == ["a"]
        assert service.sio.events().count("complete") == 1

    async def test_a_duplicate_does_not_consume_a_rate_limit_hit(
        self, service, test_db, monkeypatch
    ):
        """Dedupe phải được kiểm TRƯỚC `check_and_hit` — tin gửi lại không
        được tính thêm một dấu hạn mức."""
        from app.core.config import settings

        monkeypatch.setattr(settings, "chat_max_per_hour", 1)
        user = await AuthService(test_db).create_user("0912345678", "matkhau123", "Cô Lan")

        async def fake_turn(db, u, question):
            yield AgentEvent("complete", {"answer": "dạ", "proposal": None})

        monkeypatch.setattr("app.services.socketio_service.run_turn", fake_turn)

        await service.handle_message(sid="s1", user=user, message="a", message_id="m1")
        # Gửi lại CÙNG message_id nhiều lần — không được đụng trần 1 tin/giờ.
        for _ in range(5):
            await service.handle_message(sid="s1", user=user, message="a",
                                         message_id="m1")

        assert "error" not in service.sio.events()


class TestCompleteProposalContract:
    """`complete.proposal` qua ĐÚNG đường Socket.IO thật với `run_turn` THẬT của
    gói `salon` (không giả lượt): khác `None` khi và chỉ khi lượt kết thúc với
    một đề xuất còn `pending` và chưa hết hạn (spec §6). Chỉ MODEL là giả
    (`tests/fakes.py`, không Azure); Orchestrator, agent con, tool, Mongo thật."""

    @pytest.mark.parametrize("decision", ["yes", "no"])
    async def test_complete_proposal_follows_the_pending_proposal_rule(
        self, service, test_db, monkeypatch, decision
    ):
        from datetime import datetime

        from langchain_core.messages import AIMessage

        from app.agents.salon.memory import long_term
        from app.agents.salon.orchestrator import agent as agent_module
        from app.agents.salon.orchestrator import middleware as middleware_pkg
        from app.core.clock import now_utc, to_local
        from app.services.proposal import ProposalService
        from tests.fakes import cite, reply, scripted, tool_call
        from tests.salon.subagents import (a_user, default_hours, moment, result,
                                           tomorrow_at, use_model)

        iso15, s15 = moment(tomorrow_at(15))["iso"], moment(tomorrow_at(15))["spoken"]
        # Nhãn ngắn do code dựng cho mốc "mai" (`catalog.short_label`, live run 5
        # fix) — khác `s15` (ngày đầy đủ), dùng riêng cho `when_spoken` dưới.
        s15_short = "3 giờ chiều mai"
        # Câu trả lời cuối là JSON các đoạn trích dẫn (level C); khách nhận chữ đã ghép.
        offer_text = f"Dạ vâng, em giữ chỗ {s15_short} cho chị Lan, chị Lan đồng ý không ạ?"
        offer = reply("Dạ vâng, em giữ chỗ ", cite("held", s15_short),
                      " cho chị Lan, chị Lan đồng ý không ạ?")
        hours_text = "Dạ tiệm mở cửa từ 8 giờ sáng tới 7 giờ tối ạ."
        hours = reply("Dạ tiệm mở cửa từ ", cite("hours", "8 giờ sáng"), " tới ",
                      cite("hours", "7 giờ tối"), " ạ.")
        orchestrator = scripted([
            # Lượt 1: giao agent Đặt lịch → đề xuất 15:00.
            tool_call("appointment_task", {"request": "Book tomorrow at 3pm.",
                                           "continue_session": False, "service_ids": None}),
            offer,
            # Lượt 2: hỏi giờ mở cửa — lượt gõ chữ khi có đề xuất chờ phải
            # `decide_pending` trước (nhãn `none`: nói chuyện khác), đề xuất
            # vẫn chờ, nút vẫn hiện.
            tool_call("decide_pending", {"label": "none", "aside": None,
                                         "reason": "The customer asks about opening hours."}),
            tool_call("shop_info_task", {"request": "Opening hours.", "continue_session": False}),
            hours,
        ], tags=("orchestrate",))
        subagent = scripted([
            tool_call("parse_time", {"day_offset": 1, "hour": 3, "part_of_day": "chieu"}),
            tool_call("propose_appointment", {"start_at": iso15}),
            result(status="ok"),
            tool_call("get_shop_hours", {}),
            result(status="ok"),
        ], tags=("subagent",))
        monkeypatch.setattr(agent_module, "build_chat_model", lambda **kw: orchestrator)
        use_model(monkeypatch, subagent)
        summary = scripted([AIMessage("The customer chatted.")] * 10, tags=("summary",))
        monkeypatch.setattr(middleware_pkg, "build_chat_model", lambda **kw: summary)
        monkeypatch.setattr(long_term, "build_chat_model",
                            lambda **kw: scripted([], tags=("memory",)))

        await default_hours(test_db)
        user = await a_user(test_db)

        def last_complete():
            assert service.sio.events().count("error") == 0, service.sio.sent
            return [d for e, d, _ in service.sio.sent if e == "complete"][-1]

        # Lượt 1: tạo đề xuất → nút hiện.
        await service.handle_message(sid="s1", user=user, message="đặt cho chị 3 giờ chiều mai")
        pending = await ProposalService(test_db).latest(user)
        assert pending is not None and pending.status == "pending"
        assert to_local(pending.start_at) == tomorrow_at(15)
        first = last_complete()
        assert first["answer"] == offer_text
        proposal = first["proposal"]
        assert set(proposal) == {"id", "kind", "expires_at", "when_spoken"}
        assert proposal["id"] == str(pending.id)
        assert proposal["kind"] == "book"
        assert proposal["when_spoken"] == s15
        assert datetime.fromisoformat(proposal["expires_at"]) > now_utc()

        # Lượt 2: hỏi chuyện khác → đề xuất vẫn chờ, nút vẫn hiện.
        await service.handle_message(sid="s1", user=user, message="tiệm mở cửa mấy giờ vậy em")
        assert last_complete() == {"answer": hours_text, "proposal": proposal}

        # Lượt 3: bấm nút → đề xuất đã duyệt/từ chối, nút biến mất.
        await service.handle_button(sid="s1", user=user,
                                    data={"proposal_id": proposal["id"], "decision": decision})
        assert service.sio.events()[-1] == "complete"
        assert last_complete()["proposal"] is None
        decided = await ProposalService(test_db).get(user, proposal["id"])
        assert decided.status == ("approved" if decision == "yes" else "rejected")
        assert orchestrator.replies == [] and subagent.replies == []
        # Mỗi lượt mở bằng `turn_started`, đóng bằng đúng một `complete`; không
        # còn `token` — câu tới khách chỉ đi qua `complete`.
        events = service.sio.events()
        assert events.count("turn_started") == 3 and events.count("complete") == 3
        assert "token" not in events
        # Tool lên màn hình luôn kèm nhãn tiếng Việt, không bao giờ tên kỹ thuật.
        shown = [d for e, d, _ in service.sio.sent if e in ("tool_started", "tool_finished")]
        assert "propose_appointment" in {d["name"] for d in shown}
        for data in shown:
            assert data["label"] == TOOL_LABELS[data["name"]]
            assert "_" not in data["label"]
