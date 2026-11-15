"""Helper dùng chung cho test agent con (Task 9): khách, bối cảnh lượt,
`ToolRuntime` giả của tool cha, và cách thay model bằng `scripted` (không gọi
Azure)."""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta
from pathlib import Path

from langchain.tools import ToolRuntime
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from app.agents.salon.orchestrator.state import SalonContext
from app.agents.salon.shared.context import SnapshotHolder
from app.agents.salon.subagents import dispatch
from app.core.clock import TZ, now_utc, to_local
from app.core.vi_format import format_vi_datetime
from app.models.service import Service
from app.services.auth import AuthService
from app.services.service_catalog import ServiceCatalogService
from app.services.shop import ShopService
from tests.fakes import tool_call

TURN_ID = "turn-1"


async def a_user(db, phone="0912345678", name="Cô Lan"):
    return await AuthService(db).create_user(phone, "matkhau123", name)


class NoLedger:
    """`LedgerReader` giả — không dịch vụ đã biết, không quyết định chưa báo."""

    async def service_facts(self, *, thread_id: str):
        return []

    async def unannounced(self, user_id: str, *, now, exclude_turn_id=None):
        return None


def a_holder(db, user) -> SnapshotHolder:
    return SnapshotHolder(db, user, asked_at=now_utc(), thread_id="thread-1", services=NoLedger())


def a_runtime(user, *, question: str = "", proposals_created: int = 0,
              turn_id: str = TURN_ID) -> ToolRuntime:
    context = SalonContext(
        user_id=str(user.id), turn_id=turn_id, thread_id="thread-1", asked_at=now_utc(),
        question=question, preset=None,
    )
    return ToolRuntime(
        state={"messages": [], "proposals_created": proposals_created},
        context=context, config={}, stream_writer=lambda _: None,
        tool_call_id="call-parent", store=None,
    )


def use_model(monkeypatch, model) -> None:
    monkeypatch.setattr(dispatch, "build_chat_model", lambda **kwargs: model)


def result(**fields) -> AIMessage:
    """Lời gọi `SubagentResult` cuối (ToolStrategy, tên tool = tên class)."""
    fields.setdefault("question_fields", [])
    fields.setdefault("question_text", None)
    fields.setdefault("error_code", None)
    return tool_call("SubagentResult", fields)


def tomorrow_at(hour: int, minute: int = 0) -> datetime:
    day = (to_local(now_utc()) + timedelta(days=1)).date()
    return datetime(day.year, day.month, day.day, hour, minute, tzinfo=TZ)


def moment(dt: datetime) -> dict:
    return {"iso": to_local(dt).isoformat(), "spoken": format_vi_datetime(dt)}


async def default_hours(db) -> None:
    """Giờ tiệm cache trong tiến trình — test khác có thể đã đổi; đặt lại."""
    await ShopService(db).set_hours("08:00", "19:00", [])


async def seed_services(db) -> None:
    await ServiceCatalogService(db).upsert_many([
        Service(id="toc-nhuom", category="toc", name="Nhuộm", price_min=350000,
                price_max=700000, duration_minutes=120),
        Service(id="nail-sua", category="nail", name="Sửa móng", price_min=30000,
                price_max=30000, duration_minutes=15),
        Service(id="blk-60", category="toc", name="Gội hấp", price_min=100000,
                price_max=100000, duration_minutes=60),
        Service(id="blk-45", category="nail", name="Sơn gel", price_min=120000,
                price_max=180000, duration_minutes=45),
    ])


MOCK_SERVICES = Path(__file__).resolve().parents[3] / "data" / "mock" / "services.json"


async def seed_mock_catalogue(db) -> None:
    """Danh mục mock thật (`data/mock/services.json`, như `scripts/seed_services.py`)."""
    rows = json.loads(MOCK_SERVICES.read_text(encoding="utf-8"))
    await ServiceCatalogService(db).upsert_many([Service(**row) for row in rows])


def human_text(model) -> str:
    """Nội dung HumanMessage DUY NHẤT gửi cho agent con ở lần gọi model đầu."""
    humans = [m for m in model.calls[0] if isinstance(m, HumanMessage)]
    assert len(humans) == 1
    return humans[0].content


def tool_json(model, name: str) -> list[dict]:
    """JSON các ToolMessage tên `name` mà model nhận ở lần gọi cuối."""
    out = []
    for message in model.calls[-1]:
        if isinstance(message, ToolMessage) and message.name == name:
            out.append(json.loads(message.content))
    return out


def new_id() -> str:
    return uuid.uuid4().hex
