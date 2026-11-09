"""TDD cho bộ nhớ dài hạn (Task 14, spec §3.5): store (`memory/store.py`,
`InMemoryStore` thay cho `MongoDBStore` thật qua fixture `memory_store`
autouse) và bước gom (`memory/long_term.py::consolidate_memory`).

Không bao giờ gọi Azure thật (Global Constraints): model của `consolidate_memory`
luôn là `ScriptedChatModel` (`tests/fakes.py`), monkeypatch vào
`app.agents.salon.memory.long_term.build_chat_model`.
"""
from __future__ import annotations

import uuid
from datetime import date, datetime, timezone
from typing import Annotated, TypedDict

from langchain_core.messages import AIMessage, HumanMessage
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages

from app.agents.salon.memory import long_term as long_term_module
from app.agents.salon.memory.long_term import (
    MEMORY_NAMESPACE,
    CustomerMemory,
    LastVisit,
    consolidate_memory,
    read_memory,
    render_memory,
    sanitize_memory,
)
from app.agents.salon.shared.context import MEMORY_LABEL, render_context
from app.models.user import User
from tests.fakes import scripted
from tests.salon.shared.test_context import _make_user, _snapshot


def _user(phone="0901234560") -> User:
    return User(phone=phone, hashed_password="x", full_name="Nguyễn Thị Lan")


class _S(TypedDict):
    messages: Annotated[list, add_messages]


async def _seed_thread(memory_checkpointer, thread_id: str, messages: list) -> None:
    """Dựng một checkpoint ĐƠN GIẢN cho `thread_id`, mang `messages` — như
    `tests/test_checkpoint.py`, graph một node không làm gì khác ngoài giữ
    state qua checkpointer."""
    graph = StateGraph(_S)
    graph.add_node("n", lambda s: {})
    graph.add_edge(START, "n")
    graph.add_edge("n", END)
    compiled = graph.compile(checkpointer=memory_checkpointer)
    await compiled.ainvoke({"messages": messages}, {"configurable": {"thread_id": thread_id}})


# ---------------------------------------------------------------------------
# read_memory
# ---------------------------------------------------------------------------


async def test_read_returns_empty_memory_for_new_customer(memory_store):
    memory = await read_memory(memory_store, "khach-moi")
    assert memory == CustomerMemory()


async def test_read_is_fail_soft_against_a_malformed_record(memory_store):
    """Review round 1 (controller re-chấm): một bản ghi hỏng (ở đây `day`
    không parse được thành `date`) không được ném — `read_memory` phải trả
    bộ nhớ rỗng, để `ContextMiddleware` vẫn dựng được khối bối cảnh."""
    namespace = (MEMORY_NAMESPACE, "khach-hong")
    await memory_store.aput(namespace, "last_visit", {
        "data": {"day": "khong-phai-ngay", "lines": ["làm tóc"]},
        "updated_at": "2026-10-01T00:00:00+00:00",
    })

    memory = await read_memory(memory_store, "khach-hong")
    assert memory == CustomerMemory()


# ---------------------------------------------------------------------------
# sanitize_memory
# ---------------------------------------------------------------------------


def test_sanitize_drops_times_prices_ids_and_caps_length():
    dirty = CustomerMemory(
        preferences=[
            "thích làm nail buổi chiều",            # sạch, giữ lại
            "hẹn lúc 3 giờ chiều mai",               # có giờ -> bỏ
            "gói 200k mỗi lần",                      # có tiền -> bỏ
            "mã lịch 64e2f1a2b3c4d5e6f7a8b9c0",       # 24 ký tự hex -> bỏ
            "gọi propose_appointment cho khách",      # tên tool (orchestrator) -> bỏ
            "model gọi parse_time rồi mới đặt",       # tên tool (subagent) -> bỏ
        ],
        notes=["thích ngồi gần cửa"],
        last_visit=LastVisit(day=date(2026, 11, 1), lines=["làm tóc, hẹn giờ 15:30"]),
    )
    cleaned = sanitize_memory(dirty, max_chars=1200)
    assert cleaned.preferences == ["thích làm nail buổi chiều"]
    assert cleaned.notes == ["thích ngồi gần cửa"]
    # "làm tóc, hẹn giờ 15:30" mang giờ -> cả last_visit rỗng lines -> None
    assert cleaned.last_visit is None

    long_memory = CustomerMemory(
        preferences=["a" * 100, "b" * 100],
        notes=["c" * 100],
        last_visit=LastVisit(day=date(2026, 11, 1), lines=["d" * 100, "e" * 100]),
    )
    capped = sanitize_memory(long_memory, max_chars=250)
    total = (sum(len(p) for p in capped.preferences) + sum(len(n) for n in capped.notes)
             + (sum(len(line) for line in capped.last_visit.lines) if capped.last_visit else 0))
    assert total <= 250


# ---------------------------------------------------------------------------
# render_memory / khối bối cảnh
# ---------------------------------------------------------------------------


def test_render_memory_is_none_when_empty():
    assert render_memory(CustomerMemory()) is None


async def test_render_memory_sits_under_label_in_context(test_db):
    user = await _make_user(test_db)
    snapshot = await _snapshot(test_db, user)
    memory = CustomerMemory(
        preferences=["thích sơn gel"],
        last_visit=LastVisit(day=date(2026, 10, 20), lines=["làm tóc, còn muốn nhuộm"]),
    )

    text = render_context(
        snapshot, audience="orchestrator", turn_facts=[], memory_text=render_memory(memory),
        now=snapshot.now_local,
    )

    lines = text.splitlines()
    idx = lines.index(MEMORY_LABEL)
    block = "\n".join(lines[idx + 1:])
    assert "thích sơn gel" in block
    assert "2026-10-20" in block
    assert "còn muốn nhuộm" in block


# ---------------------------------------------------------------------------
# consolidate_memory
# ---------------------------------------------------------------------------


async def test_consolidate_writes_three_keys_from_previous_thread(
        test_db, memory_checkpointer, memory_store, monkeypatch):
    user = _user()
    thread_id = f"thread-{uuid.uuid4().hex}"
    await _seed_thread(memory_checkpointer, thread_id, [
        HumanMessage("chị muốn làm nail buổi chiều"),
        AIMessage("Dạ, anh chị muốn đặt lúc mấy giờ ạ?"),
    ])

    fresh_memory = CustomerMemory(
        preferences=["thích làm nail buổi chiều"],
        notes=["muốn được nhắc trước khi tới"],
        last_visit=LastVisit(day=date(2026, 11, 1), lines=["hỏi giá nail, chưa chốt"]),
    )
    model = scripted([fresh_memory], tags=("memory",))
    monkeypatch.setattr(long_term_module, "build_chat_model", lambda **kwargs: model)

    ok = await consolidate_memory(
        test_db, memory_store, user, previous_thread_id=thread_id,
        now=datetime(2026, 11, 2, 9, 0, tzinfo=timezone.utc), turn_id="turn-moi",
    )
    assert ok is True

    saved = await read_memory(memory_store, str(user.id))
    assert saved.preferences == fresh_memory.preferences
    assert saved.notes == fresh_memory.notes
    assert saved.last_visit.day == fresh_memory.last_visit.day
    assert saved.last_visit.lines == fresh_memory.last_visit.lines

    namespace = (MEMORY_NAMESPACE, str(user.id))
    for key in ("preferences", "notes", "last_visit"):
        assert await memory_store.aget(namespace, key) is not None


async def test_consolidate_failure_returns_false_and_keeps_old_memory(
        test_db, memory_checkpointer, memory_store, monkeypatch):
    user = _user("0901234561")
    thread_id = f"thread-{uuid.uuid4().hex}"
    await _seed_thread(memory_checkpointer, thread_id, [
        HumanMessage("chị muốn cắt tóc"),
        AIMessage("Dạ, anh chị muốn ngày nào ạ?"),
    ])

    namespace = (MEMORY_NAMESPACE, str(user.id))
    await memory_store.aput(namespace, "preferences",
                            {"data": ["hay cắt tóc ngắn"], "updated_at": "2026-10-01T00:00:00+00:00"})

    model = scripted([RuntimeError("model lỗi")], tags=("memory",))
    monkeypatch.setattr(long_term_module, "build_chat_model", lambda **kwargs: model)

    ok = await consolidate_memory(
        test_db, memory_store, user, previous_thread_id=thread_id,
        now=datetime(2026, 11, 2, 9, 0, tzinfo=timezone.utc),
    )
    assert ok is False

    kept = await read_memory(memory_store, str(user.id))
    assert kept.preferences == ["hay cắt tóc ngắn"]


class _RaisingPutStore:
    """Store giả: `aget` đi thẳng tới store thật, `aput` luôn ném — giả một
    Mongo lỗi ĐÚNG lúc ghi (review round 1: giai đoạn ghi từng nằm ngoài
    `try` của `consolidate_memory`, nên lỗi ở đây từng thoát ra ngoài hàm)."""

    def __init__(self, inner):
        self._inner = inner

    async def aget(self, namespace, key, **kwargs):
        return await self._inner.aget(namespace, key, **kwargs)

    async def aput(self, namespace, key, value, **kwargs):
        raise RuntimeError("store lỗi lúc ghi")


async def test_consolidate_returns_false_when_the_store_write_fails(
        test_db, memory_checkpointer, memory_store, monkeypatch):
    user = _user("0901234565")
    thread_id = f"thread-{uuid.uuid4().hex}"
    await _seed_thread(memory_checkpointer, thread_id, [
        HumanMessage("chị muốn làm nail"),
        AIMessage("Dạ, anh chị muốn ngày nào ạ?"),
    ])

    model = scripted([CustomerMemory(preferences=["thích làm nail"])], tags=("memory",))
    monkeypatch.setattr(long_term_module, "build_chat_model", lambda **kwargs: model)

    ok = await consolidate_memory(
        test_db, _RaisingPutStore(memory_store), user, previous_thread_id=thread_id,
        now=datetime(2026, 11, 2, 9, 0, tzinfo=timezone.utc),
    )
    assert ok is False


async def test_consolidate_with_missing_checkpoint_returns_false(test_db, memory_store):
    """Giới hạn đã biết: checkpoint hết TTL (hoặc chưa từng tồn tại) — không
    có gì để gom, trả `False` mà không ném."""
    user = _user("0901234562")
    ok = await consolidate_memory(
        test_db, memory_store, user, previous_thread_id="thread-khong-ton-tai",
        now=datetime(2026, 11, 2, 9, 0, tzinfo=timezone.utc),
    )
    assert ok is False


# ---------------------------------------------------------------------------
# cách ly giữa khách
# ---------------------------------------------------------------------------


async def test_memory_is_isolated_per_customer(
        test_db, memory_checkpointer, memory_store, monkeypatch):
    user_a = _user("0901234563")
    user_b = _user("0901234564")
    thread_id = f"thread-{uuid.uuid4().hex}"
    await _seed_thread(memory_checkpointer, thread_id, [
        HumanMessage("chị muốn làm nail"),
        AIMessage("Dạ, anh chị muốn ngày nào ạ?"),
    ])

    memory_a = CustomerMemory(preferences=["thích làm nail"])
    model = scripted([memory_a], tags=("memory",))
    monkeypatch.setattr(long_term_module, "build_chat_model", lambda **kwargs: model)

    ok = await consolidate_memory(
        test_db, memory_store, user_a, previous_thread_id=thread_id,
        now=datetime(2026, 11, 2, 9, 0, tzinfo=timezone.utc),
    )
    assert ok is True

    saved_a = await read_memory(memory_store, str(user_a.id))
    saved_b = await read_memory(memory_store, str(user_b.id))
    assert saved_a.preferences == ["thích làm nail"]
    assert saved_b == CustomerMemory()
