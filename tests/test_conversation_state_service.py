"""Không gọi LLM: build_chat_model được thay bằng model giả ghi lại đầu vào."""
import asyncio
from datetime import datetime, timezone

import pytest

from app.services.conversation import ConversationService
from app.services.conversation_state import (COMPACT_THRESHOLD_TOKENS, KEEP_RECENT_TURNS,
                                 MAX_FAILURES, StateOutput, ConversationStateService,
                                 split_window)

pytestmark = pytest.mark.asyncio

LONG = ("câu khá dài để tốn token " * 8).strip()  # ~65 token theo CHARS_PER_TOKEN=3
# .strip(): single_line() trong ConversationStateService gộp khoảng trắng khi đưa vào
# prompt — nếu LONG có dấu cách cuối, content gốc sẽ không còn khớp y hệt
# chuỗi trong prompt (bẫy phát hiện khi chạy test, không phải lỗi logic nén).


class FakeStructured:
    def __init__(self, reply, fail=False):
        self.reply, self.fail, self.prompts = reply, fail, []

    async def ainvoke(self, prompt):
        self.prompts.append(prompt)
        if self.fail:
            raise RuntimeError("azure down")
        return self.reply


class FakeModel:
    def __init__(self, structured):
        self.structured = structured
        self.kwargs = None

    def with_structured_output(self, schema):
        assert schema is StateOutput
        return self.structured


@pytest.fixture
def patch_model(monkeypatch):
    def _install(reply=None, fail=False):
        structured = FakeStructured(reply or StateOutput(bullets=["Khách muốn làm tóc."]), fail)
        model = FakeModel(structured)

        def _build(**kwargs):
            model.kwargs = kwargs
            return model
        monkeypatch.setattr("app.services.conversation_state.build_chat_model", _build)
        return model
    return _install


async def _seed(db, turns, text=LONG):
    svc = ConversationService(db)
    # offset theo số tin đã có: gọi _seed hai lần trong cùng test (mô phỏng
    # phiên dài dần) không được sinh ra nội dung trùng ký tự với lần trước,
    # nếu không assert "content not in prompt" ở dưới sẽ dương tính giả.
    offset = len(await svc._all_messages("u1"))
    for j in range(turns):
        i = offset + j
        # "[i]" thay vì "i " để tránh trùng lặp chuỗi con giữa các chỉ số
        # (vd "0 ..." vốn là chuỗi con của "10 ...") làm sai lệch assert
        # "content not in prompt" ở các test dưới.
        await svc.append("u1", "user", f"[{i}] {text}")
        await asyncio.sleep(0.002)
        await svc.append("u1", "assistant", f"[{i}] đáp {text}")
        await asyncio.sleep(0.002)
    return await svc._all_messages("u1")


def test_split_window_keeps_the_last_turns_verbatim():
    msgs = list(range(20))          # thay ChatMessage bằng số cho gọn; hàm chỉ cắt lát
    older, recent = split_window(msgs, covers_until=None)
    assert recent == list(range(20 - 2 * KEEP_RECENT_TURNS, 20))
    assert older == list(range(0, 20 - 2 * KEEP_RECENT_TURNS))


def test_split_window_boundary_exactly_the_keep_window():
    keep = 2 * KEEP_RECENT_TURNS
    msgs = list(range(keep))
    older, recent = split_window(msgs, covers_until=None)
    assert older == []
    assert recent == msgs


def test_split_window_boundary_one_more_than_the_keep_window():
    keep = 2 * KEEP_RECENT_TURNS
    msgs = list(range(keep + 1))
    older, recent = split_window(msgs, covers_until=None)
    assert older == [0]
    assert recent == msgs[1:]


async def test_short_session_does_not_call_the_model(test_db, patch_model):
    model = patch_model()
    await _seed(test_db, 3)
    assert await ConversationStateService(test_db).maybe_compact("u1") is False
    assert model.kwargs is None


async def test_older_messages_under_threshold_do_not_call_the_model(test_db, patch_model):
    """5 lượt = 10 tin, giữ 8 tin gần nhất (KEEP_RECENT_TURNS=4) → older chỉ
    còn 2 tin. Với nội dung NGẮN, older không rỗng nhưng chưa vượt ngưỡng
    token — phải bỏ qua nhánh estimate_tokens(older) < COMPACT_THRESHOLD_TOKENS
    mà không gọi model."""
    model = patch_model()
    await _seed(test_db, 5, text="ok")
    assert await ConversationStateService(test_db).maybe_compact("u1") is False
    assert model.kwargs is None


async def test_long_session_compacts_once_and_records_covers_until(test_db, patch_model):
    model = patch_model()
    all_msgs = await _seed(test_db, 12)

    assert await ConversationStateService(test_db).maybe_compact("u1") is True

    state = await ConversationService(test_db).get_state("u1")
    assert state.summary == ["Khách muốn làm tóc."]
    assert state.covers_until == all_msgs[-(2 * KEEP_RECENT_TURNS) - 1].created_at
    assert state.failures == 0
    assert model.kwargs["tags"] == ["state"]
    assert model.kwargs["streaming"] is False
    assert model.kwargs["temperature"] == 0.0


async def test_recent_turns_are_not_in_the_prompt(test_db, patch_model):
    model = patch_model()
    all_msgs = await _seed(test_db, 12)
    await ConversationStateService(test_db).maybe_compact("u1")
    prompt = model.structured.prompts[0]
    for m in all_msgs[-(2 * KEEP_RECENT_TURNS):]:
        assert m.content not in prompt
    assert all_msgs[0].content in prompt


async def test_second_run_only_feeds_new_messages_and_old_bullets(test_db, patch_model):
    model = patch_model()
    svc = ConversationStateService(test_db)
    first = await _seed(test_db, 12)
    await svc.maybe_compact("u1")

    # thêm 12 lượt nữa → phần chưa phủ ngoài cửa sổ lại vượt ngưỡng
    more = await _seed(test_db, 12)
    await svc.maybe_compact("u1")

    prompt = model.structured.prompts[1]
    assert "Khách muốn làm tóc." in prompt          # bullets cũ đi vào lần nén sau
    assert first[0].content not in prompt          # tin đã phủ không gửi lại


async def test_already_covered_session_does_not_recompact(test_db, patch_model):
    model = patch_model()
    await _seed(test_db, 12)
    svc = ConversationStateService(test_db)
    await svc.maybe_compact("u1")
    assert await svc.maybe_compact("u1") is False
    assert len(model.structured.prompts) == 1


async def test_model_failure_bumps_the_fuse_and_keeps_the_old_state(test_db, patch_model):
    ok = patch_model()
    await _seed(test_db, 12)
    svc = ConversationStateService(test_db)
    await svc.maybe_compact("u1")

    patch_model(fail=True)
    await _seed(test_db, 12)
    assert await svc.maybe_compact("u1") is False

    state = await ConversationService(test_db).get_state("u1")
    assert state.failures == 1
    assert state.summary == ["Khách muốn làm tóc."]


async def test_three_failures_stop_further_attempts(test_db, patch_model):
    model = patch_model(fail=True)
    await _seed(test_db, 12)
    svc = ConversationStateService(test_db)
    for _ in range(MAX_FAILURES):
        await svc.maybe_compact("u1")
    calls_before = len(model.structured.prompts)
    await svc.maybe_compact("u1")
    assert len(model.structured.prompts) == calls_before == MAX_FAILURES


async def test_empty_state_does_not_advance_covers_until(test_db, patch_model):
    """Model trả về 0 bullet hợp lệ → coi như hỏng: covers_until KHÔNG dời,
    state cũ (nếu có) giữ nguyên, failures tăng, và context_window vẫn trả về
    TOÀN BỘ tin hôm nay nguyên văn (không mất gì)."""
    patch_model(reply=StateOutput(bullets=["", "-", "  "]))
    all_msgs = await _seed(test_db, 12)

    assert await ConversationStateService(test_db).maybe_compact("u1") is False

    conv = ConversationService(test_db)
    state = await conv.get_state("u1")
    assert state.summary == []
    assert state.failures == 1

    summary, msgs = await conv.context_window("u1")
    assert summary == []
    # token_budget lớn để tách riêng khỏi cắt-theo-ngân-sách: điều test này
    # canh là covers_until KHÔNG dời, không phải chuyện cắt bớt vì dài.
    full = await conv.history("u1", after=state.covers_until, token_budget=10**6)
    assert [m.content for m in full] == [m.content for m in all_msgs]


async def test_empty_state_keeps_the_old_state_and_bumps_the_fuse(test_db, patch_model):
    patch_model()
    all_first = await _seed(test_db, 12)
    svc = ConversationStateService(test_db)
    assert await svc.maybe_compact("u1") is True

    conv = ConversationService(test_db)
    before = await conv.get_state("u1")

    patch_model(reply=StateOutput(bullets=["", "-", "  "]))
    await _seed(test_db, 12)
    assert await ConversationStateService(test_db).maybe_compact("u1") is False

    after = await conv.get_state("u1")
    assert after.summary == before.summary
    assert after.covers_until == before.covers_until
    assert after.failures == before.failures + 1


async def test_never_raises(test_db, monkeypatch):
    """Chạy nền sau complete — lỗi gì cũng phải nuốt và log."""
    async def boom(*a, **k):
        raise RuntimeError("mongo down")
    monkeypatch.setattr(ConversationService, "get_state", boom)
    await _seed(test_db, 12)
    assert await ConversationStateService(test_db).maybe_compact("u1") is False
