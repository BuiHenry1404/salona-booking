"""Giới hạn LLM (A-04): timeout mỗi lần gọi, retry có giới hạn, trần thời gian lượt.

Hết lượt thử hay hết giờ thì khách nhận câu lỗi tiếng Việt y như A, không lộ
lỗi kỹ thuật.
"""
import asyncio
from types import SimpleNamespace

import pytest
from langchain.agents.middleware import ModelRetryMiddleware
from langchain_core.messages import AIMessage

from app.agents import llm
from app.agents.booking_graph import agents, events, phrase, rewrite, supervisor
from app.core.config import settings
from app.services.auth import AuthService
from tests.fakes import Hang, scripted

BUSY = "Máy đang bận chút xíu, anh chị nhắn lại giúp em nhé."


@pytest.fixture
def azure(monkeypatch):
    from pydantic import SecretStr
    monkeypatch.setattr(settings, "llm_provider", "azure")
    monkeypatch.setattr(settings, "azure_openai_api_key", SecretStr("khoa-gia"))
    monkeypatch.setattr(settings, "azure_openai_endpoint", "https://gia.openai.azure.com")


def test_chat_model_has_a_timeout_and_no_sdk_retries(azure):
    model = llm.build_chat_model(tags=["respond"], max_retries=0)
    assert model.request_timeout == llm.LLM_TIMEOUT_SECONDS == 20
    assert model.max_retries == 0


def test_other_callers_keep_the_sdk_retries(azure):
    """F11: chỉ model agent (có ModelRetryMiddleware bọc) tắt retry SDK;
    supervisor/rewrite/phrase/timeparse/classify giữ mặc định của SDK như A."""
    model = llm.build_chat_model(tags=["supervisor"])
    assert model.request_timeout == llm.LLM_TIMEOUT_SECONDS
    assert model.max_retries is None
    assert model.root_async_client.max_retries == 2          # mặc định openai SDK


def test_agent_model_is_built_without_sdk_retries(monkeypatch):
    seen = []

    def fake_build(**kw):
        seen.append(kw)
        return scripted([], tags=kw.get("tags") or ())

    monkeypatch.setattr(agents, "build_chat_model", fake_build)
    agents.build_agent("p", [])._get()
    assert seen == [{"tags": ["respond"], "temperature": 0.2, "max_retries": 0}]


def test_agent_retries_the_model_once_and_then_raises():
    mw = agents.model_retry_middleware()
    assert isinstance(mw, ModelRetryMiddleware)
    assert mw.max_retries == 1 and mw.on_failure == "error" and mw.initial_delay == 0.5


# --- đầu-cuối qua run_turn ---------------------------------------------------

@pytest.fixture
async def user(test_db):
    return await AuthService(test_db).create_user("0912345678", "matkhau123", "Cô Lan")


@pytest.fixture
def models(monkeypatch):
    sup = scripted([], tags=("supervisor",))
    agent = scripted([])
    monkeypatch.setattr(supervisor, "build_chat_model", lambda **kw: sup)
    monkeypatch.setattr(agents, "build_chat_model", lambda **kw: agent)
    monkeypatch.setattr(phrase, "build_chat_model", lambda **kw: scripted([]))
    monkeypatch.setattr(rewrite, "build_chat_model", lambda **kw: scripted([AIMessage("")]))
    monkeypatch.setattr(events, "schedule_compaction", lambda db, user_id: None)
    monkeypatch.setattr(events, "get_callbacks", lambda: [])
    return SimpleNamespace(sup=sup, agent=agent)


async def turn(test_db, user, text):
    return [(e.type, e.data) async for e in events.run_turn(test_db, user, text)]


async def test_one_retry_then_vietnamese_error(test_db, user, models):
    models.sup.replies += [AIMessage("social")]
    models.agent.replies += [RuntimeError("azure 500"), RuntimeError("azure 500 lần hai"),
                             AIMessage("không bao giờ tới")]
    out = await turn(test_db, user, "chào em")
    assert out[-1] == ("error", {"message": BUSY})
    assert len(models.agent.calls) == 2                   # 1 lần đầu + đúng 1 lần thử lại
    assert "azure" not in str(out)


async def test_one_failure_is_retried_transparently(test_db, user, models):
    models.sup.replies += [AIMessage("social")]
    models.agent.replies += [RuntimeError("azure 500"), AIMessage("Dạ em chào chị Lan ạ.")]
    out = await turn(test_db, user, "chào em")
    assert out[-1] == ("complete", {"answer": "Dạ em chào chị Lan ạ."})
    assert ("error", {"message": BUSY}) not in out


async def test_turn_timeout_emits_the_same_error(test_db, user, models, monkeypatch):
    monkeypatch.setattr(events, "TURN_TIMEOUT_SECONDS", 0.2)
    models.sup.replies += [AIMessage("social")]
    models.agent.replies += [Hang(30, AIMessage("quá muộn"))]
    logged = []
    monkeypatch.setattr(events.logger, "error", lambda msg, **kw: logged.append(msg))

    started = asyncio.get_running_loop().time()
    out = await turn(test_db, user, "chào em")

    assert asyncio.get_running_loop().time() - started < 5
    assert out[0] == ("turn_started", {})
    assert out[-1] == ("error", {"message": BUSY})
    assert logged == ["agent_turn_timeout"]
    # Lượt hết giờ không ghi gì vào conversations.
    assert await test_db["conversations"].count_documents({"messages.0": {"$exists": True}}) == 0


async def test_slow_consumer_never_trips_the_turn_timeout(test_db, user, models, monkeypatch):
    """F12: trần thời gian chỉ bọc lúc KÉO sự kiện từ graph, không bọc lúc
    người nhận (sio.emit) xử lý — nhận chậm không bao giờ cắt lượt."""
    monkeypatch.setattr(events, "TURN_TIMEOUT_SECONDS", 0.3)
    models.sup.replies += [AIMessage("social")]
    models.agent.replies += [AIMessage("Dạ em chào chị Lan ạ.")]
    out = []
    async for event in events.run_turn(test_db, user, "chào em"):
        out.append((event.type, event.data))
        if event.type == "token":
            await asyncio.sleep(0.5)                       # người nhận chậm hơn trần
    assert ("token", {"text": "Dạ em chào chị Lan ạ."}) in out
    assert out[-1] == ("complete", {"answer": "Dạ em chào chị Lan ạ."})


async def test_next_turn_after_a_timeout_runs_normally(test_db, user, models, monkeypatch):
    monkeypatch.setattr(events, "TURN_TIMEOUT_SECONDS", 0.2)
    models.sup.replies += [AIMessage("social")]
    models.agent.replies += [Hang(30, AIMessage("quá muộn"))]
    assert (await turn(test_db, user, "chào em"))[-1][0] == "error"

    monkeypatch.setattr(events, "TURN_TIMEOUT_SECONDS", 60)
    models.sup.replies += [AIMessage("social")]
    models.agent.replies += [AIMessage("Dạ em chào chị Lan ạ.")]
    assert (await turn(test_db, user, "chào em"))[-1] == (
        "complete", {"answer": "Dạ em chào chị Lan ạ."})


# --- ngân sách cả lượt (review M4 I-1) ----------------------------------------

def test_worst_case_llm_budget_of_one_turn_fits_under_the_turn_cap():
    """Một lần gọi treo phải XUỐNG CẤP (fallback/thử lại) chứ không ăn hết trần
    lượt. Mỗi đường của một lượt — tin mới (supervisor) hay resume (bộ phân
    loại) — cộng ngân sách xấu nhất: bước phân loại + một lần gọi model agent
    kèm thử lại + rewrite của guard. Live: supervisor 3 × 20 giây > 60 giây."""
    from app.agents.booking_graph import guard, reply
    agent_call = (llm.LLM_TIMEOUT_SECONDS * (1 + agents.MODEL_RETRIES)
                  + agents.MODEL_RETRY_DELAY_SECONDS * 1.25 * agents.MODEL_RETRIES)
    fresh = supervisor.SUPERVISOR_TIMEOUT_SECONDS + agent_call + guard.REWRITE_TIMEOUT_SECONDS
    resume = reply.CLASSIFY_TIMEOUT_SECONDS + agent_call + guard.REWRITE_TIMEOUT_SECONDS
    assert fresh < events.TURN_TIMEOUT_SECONDS
    assert resume < events.TURN_TIMEOUT_SECONDS


async def test_supervisor_stall_degrades_to_booking_not_to_an_error(test_db, user, models, monkeypatch):
    """Lỗi live 1: supervisor treo → lượt vẫn trả lời (route booking), không
    `error`, và xong sớm hơn hẳn trần lượt."""
    monkeypatch.setattr(supervisor, "SUPERVISOR_TIMEOUT_SECONDS", 0.2)
    models.sup.replies += [Hang(30, AIMessage("social"))]
    models.agent.replies += [AIMessage("Dạ chị muốn đặt ngày nào ạ?")]

    started = asyncio.get_running_loop().time()
    out = await turn(test_db, user, "em ơi mai cô muốn làm móng")

    assert asyncio.get_running_loop().time() - started < 5
    assert out[-1] == ("complete", {"answer": "Dạ chị muốn đặt ngày nào ạ?"})
    assert not any(kind == "error" for kind, _ in out)
