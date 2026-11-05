"""Test cho Shop Info Agent (Task 2.5, §8.2) — `run_task` đối chiếu `data`
model trả với dữ liệu THẬT đọc lại từ `ShopService`, không tin chữ model.
Dùng model giả (không gọi Azure thật)."""
import json
import uuid
from datetime import datetime, timedelta

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatResult

import pytest

from app.agents.booking.schemas import ShopHoursOut, ShopOpenOut, ShopStatusOut
from app.agents.booking.specialists.shop_info import ALLOWLIST, check_facts, run_task
from app.agents.booking.prompts.shop_info import SHOP_INFO_PROMPT
from app.core.clock import TZ, now_utc
from app.core.vi_format import format_vi_datetime, format_vi_hhmm
from app.models.shop import ShopHours, ShopStatusView
from app.services.auth import AuthService
from app.services.shop import ShopService
from tests.factories import make_ctx, make_spec
from tests.fakes import scripted, tool_call


def _patch_build_chat_model(monkeypatch, model):
    monkeypatch.setattr(
        "app.agents.booking.specialists.base.build_chat_model", lambda **kwargs: model
    )


async def a_user(db, phone=None, name="Co Lan"):
    if phone is None:
        phone = f"09{uuid.uuid4().int % 100000000:08d}"
    await db["users"].delete_many({"phone": phone})
    return await AuthService(db).create_user(phone, "matkhau123", name)


def today_at(hour: int, minute: int = 0) -> datetime:
    local = datetime.now(TZ)
    return local.replace(hour=hour, minute=minute, second=0, microsecond=0)


class _EchoShopOpen(BaseChatModel):
    """Model giả: gọi đúng chuỗi get_shop_hours → get_shop_status → is_open_at(at),
    rồi CHÉP JSON của is_open_at thẳng vào `data` — mô phỏng model "chỉ chép"."""

    at: str

    @property
    def _llm_type(self) -> str:
        return "echo_shop_open"

    def bind_tools(self, tools, **kwargs):
        return self

    def bind(self, **kwargs):
        return self

    def _reply(self, messages) -> AIMessage:
        ai_rounds = sum(1 for m in messages if isinstance(m, AIMessage))
        if ai_rounds == 0:
            return tool_call("get_shop_hours", {})
        if ai_rounds == 1:
            return tool_call("get_shop_status", {})
        if ai_rounds == 2:
            return tool_call("is_open_at", {"at": self.at})
        tool_json = json.loads(messages[-1].content)
        output = {"status": "ok", "data": tool_json, "missing": [], "error_code": None}
        return AIMessage(content=json.dumps(output, ensure_ascii=False))

    def _generate(self, messages, stop=None, run_manager=None, **kw):
        return ChatResult(generations=[ChatGeneration(message=self._reply(messages))])

    async def _agenerate(self, messages, stop=None, run_manager=None, **kw):
        return ChatResult(generations=[ChatGeneration(message=self._reply(messages))])


async def test_get_hours_returns_hours_out_matching_the_shop(test_db, monkeypatch):
    user = await a_user(test_db)
    hours = await ShopService(test_db).get_hours()
    output = {
        "status": "ok",
        "data": {
            "open": {"iso": hours.open_time, "spoken": format_vi_hhmm(hours.open_time)},
            "close": {"iso": hours.close_time, "spoken": format_vi_hhmm(hours.close_time)},
            "closed_days": [],
        },
        "missing": [], "error_code": None,
    }
    model = scripted([
        tool_call("get_shop_hours", {}),
        AIMessage(content=json.dumps(output, ensure_ascii=False)),
    ], tags=["shop_info"])
    _patch_build_chat_model(monkeypatch, model)
    spec = make_spec(agent="shop_info", action="get_hours", task_id="t1.0")
    ctx = make_ctx()

    result = await run_task(test_db, user, spec, ctx, "may gio tiem mo cua")

    assert result.status == "ok"
    assert isinstance(result.data, ShopHoursOut)
    assert result.data.open.iso == hours.open_time
    assert result.data.close.iso == hours.close_time
    assert result.data.closed_days == []


async def test_is_open_at_combines_hours_and_status(test_db, monkeypatch):
    user = await a_user(test_db)
    at = today_at(12)
    model = _EchoShopOpen(at=at.isoformat())
    _patch_build_chat_model(monkeypatch, model)
    spec = make_spec(agent="shop_info", action="is_open_at", task_id="t1.0")
    ctx = make_ctx()

    result = await run_task(test_db, user, spec, ctx, "12h trua tiem con mo khong")

    assert result.status == "ok"
    assert isinstance(result.data, ShopOpenOut)
    assert result.data.open is True
    assert result.data.reason == "open"


async def test_open_hours_but_owner_busy_is_ok_not_fact_mismatch(test_db, monkeypatch):
    """Fix round 1: `is_open_at` của ShopService chỉ xét giờ mở cửa, không
    biết chủ tiệm đang bận — `run_task` phải tự cộng thêm điều kiện bận
    (đúng luật tool `is_open_at` đã dùng) trước khi so `open_at`, nếu không
    mọi câu TRẢ LỜI ĐÚNG "trong giờ nhưng chủ đang bận" sẽ bị bắt nhầm
    fact_mismatch."""
    user = await a_user(test_db)
    shop = ShopService(test_db)
    # Mở cả ngày để không phụ thuộc giờ chạy test thật.
    await shop.set_hours("00:00", "23:59", [])
    await shop.set_busy(90)
    at = now_utc() + timedelta(minutes=30)

    model = _EchoShopOpen(at=at.isoformat())
    _patch_build_chat_model(monkeypatch, model)
    spec = make_spec(agent="shop_info", action="is_open_at", task_id="t1.0")
    ctx = make_ctx()

    result = await run_task(test_db, user, spec, ctx, "30 phut nua tiem con mo khong")

    assert result.status == "ok"
    assert isinstance(result.data, ShopOpenOut)
    assert result.data.open is False
    assert result.data.reason == "owner_busy"


async def test_fabricated_closing_time_is_fact_mismatch_and_not_retried(test_db, monkeypatch):
    user = await a_user(test_db)
    hours = await ShopService(test_db).get_hours()
    assert hours.close_time != "18:00"
    output = {
        "status": "ok",
        "data": {
            "open": {"iso": hours.open_time, "spoken": format_vi_hhmm(hours.open_time)},
            "close": {"iso": "18:00", "spoken": format_vi_hhmm("18:00")},
            "closed_days": [],
        },
        "missing": [], "error_code": None,
    }
    model = scripted([
        tool_call("get_shop_hours", {}),
        AIMessage(content=json.dumps(output, ensure_ascii=False)),
    ], tags=["shop_info"])
    _patch_build_chat_model(monkeypatch, model)
    spec = make_spec(agent="shop_info", action="get_hours", task_id="t1.0")
    ctx = make_ctx()

    result = await run_task(test_db, user, spec, ctx, "may gio tiem dong cua")

    assert result.status == "failed"
    assert result.error_code == "fact_mismatch"
    assert result.retryable is False
    # Không chạy lại agent sau khi phát hiện bịa — đúng số lần gọi của MỘT
    # lượt (1 vòng tool + 1 câu trả lời cuối), không hơn.
    assert len(model.calls) == 2


async def test_status_tool_not_in_get_hours_allowlist(test_db, monkeypatch):
    user = await a_user(test_db)
    assert "get_shop_status" not in ALLOWLIST["get_hours"]
    model = scripted([
        tool_call("get_shop_status", {}),
        AIMessage(content="khong lam duoc"),
    ], tags=["shop_info"])
    _patch_build_chat_model(monkeypatch, model)
    spec = make_spec(agent="shop_info", action="get_hours", task_id="t1.0")
    ctx = make_ctx()

    result = await run_task(test_db, user, spec, ctx, "chu tiem co ban khong")

    assert result.status == "failed"
    assert result.error_code == "schema_invalid"


@pytest.mark.parametrize(
    "data, hours, status, open_at, expected",
    [
        (
            ShopHoursOut(
                open={"iso": "08:00", "spoken": "8 giờ sáng"},
                close={"iso": "19:00", "spoken": "7 giờ tối"},
                closed_days=[],
            ),
            ShopHours(open_time="08:00", close_time="19:00", closed_days=[]),
            ShopStatusView(is_busy=False),
            None,
            True,
        ),
        (
            ShopHoursOut(
                open={"iso": "08:00", "spoken": "8 giờ sáng"},
                close={"iso": "18:00", "spoken": "6 giờ tối"},
                closed_days=[],
            ),
            ShopHours(open_time="08:00", close_time="19:00", closed_days=[]),
            ShopStatusView(is_busy=False),
            None,
            False,
        ),
        (
            ShopStatusOut(busy=True, until={"iso": "2026-01-01T12:00:00+07:00", "spoken": "12 giờ trưa"}),
            ShopHours(),
            ShopStatusView(is_busy=True, busy_until=datetime(2026, 1, 1, 5, tzinfo=TZ)),
            None,
            True,
        ),
        (
            ShopStatusOut(busy=False, until=None),
            ShopHours(),
            ShopStatusView(is_busy=True, busy_until=datetime(2026, 1, 1, 5, tzinfo=TZ)),
            None,
            False,
        ),
        (
            ShopOpenOut(
                at={"iso": "2026-01-01T12:00:00+07:00", "spoken": "12 giờ trưa"},
                open=True, reason="open", next_change=None,
            ),
            ShopHours(),
            ShopStatusView(is_busy=False),
            True,
            True,
        ),
        (
            ShopOpenOut(
                at={"iso": "2026-01-01T12:00:00+07:00", "spoken": "12 giờ trưa"},
                open=False, reason="owner_busy", next_change=None,
            ),
            ShopHours(),
            ShopStatusView(is_busy=False),
            False,
            False,
        ),
    ],
)
def test_check_facts_pure_cases(data, hours, status, open_at, expected):
    assert check_facts(data, hours=hours, status=status, open_at=open_at) is expected


def test_shop_info_prompt_is_english():
    assert all(ord(c) < 128 for c in SHOP_INFO_PROMPT)
