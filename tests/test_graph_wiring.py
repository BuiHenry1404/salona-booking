"""Hình dạng graph khoá cứng: mọi câu LLM qua guard đúng một lần; rewrite tối đa một lần."""
import inspect
from unittest.mock import MagicMock

from app.agents.booking_graph import confirm, guard, phrase, reply, tools
from app.agents.booking_graph.middleware import confirm as confirm_middleware
from app.services import proposal as proposal_service
from app.agents.booking_graph.graph import build_graph
from app.models.user import User


def edges():
    g = build_graph(MagicMock(), User(phone="0912345678", hashed_password="x", full_name="Cô Lan")).get_graph()
    return {(e.source, e.target) for e in g.edges}


def test_every_llm_node_feeds_the_guard():
    e = edges()
    for node in ("booking", "shop", "social"):
        assert (node, "guard") in e, node
        assert (node, "__end__") not in e, node


def test_guard_goes_to_rewrite_or_end_and_rewrite_returns_to_guard():
    """"end" của guard đi qua `record` (ghi câu đáp vào thread) rồi tới confirm."""
    e = edges()
    assert ("guard", "rewrite") in e and ("guard", "record") in e
    assert ("guard", "__end__") not in e
    assert ("rewrite", "guard") in e
    assert ("rewrite", "__end__") not in e


def test_record_then_confirm_after_guard():
    """Câu hỏi xác nhận phải qua guard và nằm trong thread TRƯỚC khi interrupt."""
    e = edges()
    assert ("record", "confirm") in e
    assert ("record", "__end__") not in e


def test_confirm_goes_to_phrase_booking_supervisor_or_end():
    e = edges()
    for target in ("phrase", "booking", "supervisor", "guard", "__end__"):
        assert ("confirm", target) in e, target
    assert ("confirm", "shop") not in e and ("confirm", "social") not in e
    assert ("phrase", "guard") in e


def test_there_is_no_confirm_shortcut_from_start():
    """Câu trả lời xác nhận vào confirm bằng resume, không bằng cạnh từ START."""
    e = edges()
    assert ("__start__", "confirm") not in e
    assert ("__start__", "supervisor") in e


def test_booking_node_has_the_two_shop_tools_but_no_write_tool():
    from unittest.mock import MagicMock

    from app.agents.booking_graph.graph import booking_tools
    from app.models.user import User
    names = {t.name for t in booking_tools(MagicMock(), User(phone="0912345678", hashed_password="x", full_name="Cô Lan"))}
    assert names == {"parse_time", "find_free_slots", "propose_appointment", "list_my_appointments",
                     "cancel_appointment", "get_shop_hours", "get_shop_status"}
    assert "create_appointment" not in names


def test_no_code_path_books_from_slots():
    """Anh em với test_there_is_NO_tool_that_writes_an_appointment.

    Slots do LLM sinh. Chúng chỉ được in thành chữ trong prompt (render_slots).
    Giây phút confirm/guard/phrase/tools đọc chúng là giá trị model gõ ra đi
    thẳng vào DB — và phrase.py sinh CÂU XÁC NHẬN LỊCH với khách, nên số sai
    lẻn vào đó là đi thẳng tới tai khách — phá đúng chốt 'giá trị lấy từ DB, không từ chuỗi model gõ lại'.

    Không cấm chữ "slots" trần trụi: tools.py có find_free_slots (khung giờ
    trống trong lịch), trùng chữ nhưng không trùng khái niệm với
    ConversationSlots (bản nén hội thoại). Cấm đúng các định danh của khái
    niệm thứ hai.
    """
    forbidden = ("ConversationSlots", "sanitize_slots", "render_slots",
                 'state["slots"]', 'state.get("slots")')
    # F9: mọi module trên đường ghi/chốt lịch, kể cả đường mới của M3.
    for module in (confirm, guard, phrase, tools, reply, confirm_middleware, proposal_service):
        source = inspect.getsource(module)
        for needle in forbidden:
            assert needle not in source, (
                f"{module.__name__} có chuỗi '{needle}' — đọc slots hội thoại "
                f"trong code, xem CONTEXT.md bẫy #22"
            )


def test_tools_return_data_not_instructions():
    """Tool là chỗ cấp DỮ KIỆN. Trước 2026-09-20 nó nhét cả mệnh lệnh vào kết
    quả trả về ("Hãy nhắc lại đầy đủ ngày giờ và hỏi khách xác nhận.") — chỉ
    dẫn cho model trá hình thành dữ liệu.

    Hậu quả: cùng một luật tồn tại hai bản, một ở prompts.py một ở tools.py,
    rồi trôi lệch nhau mà không ai rà được. Chỉ dẫn thuộc system prompt —
    ổn định và được cache; kết quả tool thì đổi mỗi lần gọi.
    """
    import inspect

    from app.agents.booking_graph import tools

    source = inspect.getsource(tools)
    # Chỉ soi phần THÂN HÀM, không soi docstring: docstring của tool ĐÚNG là
    # chỗ để chỉ dẫn, nó đi vào định nghĩa tool chứ không vào kết quả trả về.
    bodies = "\n".join(
        line for line in source.splitlines()
        if not line.lstrip().startswith(('"""', "#", "'''"))
    )
    for cam in ("Hãy ", "hãy "):
        assert cam not in bodies, (
            f"tools.py còn mệnh lệnh {cam!r} trong thân hàm — chuyển sang prompts.py"
        )


def test_the_live_slots_scenario_can_actually_reach_compaction():
    """Kịch bản LLM-32 tồn tại để nghiệm thu tầng slots qua chạy thật. Nó chỉ
    chạm tới slots khi lượt nén NỔ, mà lượt nén chỉ nổ khi phần ngoài cửa sổ 8
    tin vượt COMPACT_THRESHOLD_TOKENS.

    Hai lần liên tiếp kịch bản này tụt xuống dưới ngưỡng mà không ai biết:
    - lần một: bản 12 lượt chỉ đạt ~300 token, `state` là undefined;
    - lần hai: bản 17 lượt đủ ngưỡng NHỜ câu đáp của AI dài dòng — sửa xong lỗi
      lặp thì câu ngắn lại, cả cuộc còn 787/800 và lượt nén lại không nổ.

    Cả hai lần, kịch bản vẫn "chạy xanh" và vẫn bị đọc là đã nghiệm thu. Nên
    hàng rào phải là: CÂU CỦA KHÁCH tự gánh đủ ngưỡng, không tính câu của AI —
    độ dài câu AI là thứ ta sẽ còn sửa nữa.
    """
    import re

    from app.services.conversation import CHARS_PER_TOKEN
    from app.services.conversation_state import (COMPACT_THRESHOLD_TOKENS,
                                                 KEEP_RECENT_TURNS)

    src = open("scripts/llm_scenarios.py", encoding="utf-8").read()
    block = src[src.index('("LLM-32"'):src.index("\n]\n", src.index('("LLM-32"'))]
    lines = re.findall(r'"([^"]{80,})"', block)
    assert lines, "không đọc được câu khách của LLM-32"

    # 4 lượt cuối luôn nằm trong cửa sổ nguyên văn, không bị nén.
    older = lines[:-KEEP_RECENT_TURNS]
    tokens = sum(max(1, len(m) // CHARS_PER_TOKEN) for m in older)
    assert tokens > COMPACT_THRESHOLD_TOKENS, (
        f"LLM-32 chỉ còn {tokens} token ngoài cửa sổ, dưới ngưỡng "
        f"{COMPACT_THRESHOLD_TOKENS} — lượt nén sẽ KHÔNG nổ và kịch bản sẽ "
        f"chạy xanh mà không hề chạm tới slots."
    )


def test_graph_builds_without_llm_keys(monkeypatch):
    """Dựng graph KHÔNG được cần key LLM: model chỉ dựng lúc node chạy (như A).
    Thiếu key thì nhánh confirm→phrase... vẫn tới được node; test cũng không
    được đòi key thật trong .env."""
    from app.agents.llm import build_chat_model
    from app.core.config import settings

    monkeypatch.setattr(settings, "llm_provider", "azure")
    monkeypatch.setattr(settings, "azure_openai_api_key", None)
    monkeypatch.setattr(settings, "azure_openai_endpoint", None)
    import pytest
    with pytest.raises(RuntimeError):
        build_chat_model(tags=["respond"])           # tiền đề: dựng model sẽ nổ

    g = build_graph(MagicMock(), User(phone="0912345678", hashed_password="x", full_name="Cô Lan"))
    assert {"booking", "shop", "social"} <= set(g.get_graph().nodes)


def test_build_graph_constructs_no_chat_model(monkeypatch):
    """Dựng model là ~25 ms đồng bộ trên event loop; build_graph chạy mỗi lượt
    nên không được dựng model nào (kể cả qua đường dò subgraph của compile)."""
    calls = []
    monkeypatch.setattr("app.agents.booking_graph.agents.build_chat_model",
                        lambda **kw: calls.append(kw))
    build_graph(MagicMock(), User(phone="0912345678", hashed_password="x", full_name="Cô Lan"))
    assert calls == []
