"""Không gọi LLM: build_chat_model được thay bằng model giả ghi lại đầu vào."""
import asyncio
from datetime import datetime, timezone

import pytest

from app.services.conversation import ConversationService
from app.services.digest import (COMPACT_THRESHOLD_TOKENS, KEEP_RECENT_TURNS,
                                 MAX_FAILURES, DigestBullets, DigestService,
                                 split_window)

pytestmark = pytest.mark.asyncio

LONG = ("câu khá dài để tốn token " * 8).strip()  # ~65 token theo CHARS_PER_TOKEN=3
# .strip(): single_line() trong DigestService gộp khoảng trắng khi đưa vào
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
        assert schema is DigestBullets
        return self.structured


@pytest.fixture
def patch_model(monkeypatch):
    def _install(reply=None, fail=False):
        structured = FakeStructured(reply or DigestBullets(bullets=["Khách muốn làm tóc."]), fail)
        model = FakeModel(structured)

        def _build(**kwargs):
            model.kwargs = kwargs
            return model
        monkeypatch.setattr("app.services.digest.build_chat_model", _build)
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


async def test_short_session_does_not_call_the_model(test_db, patch_model):
    model = patch_model()
    await _seed(test_db, 3)
    assert await DigestService(test_db).maybe_compact("u1") is False
    assert model.kwargs is None


async def test_long_session_compacts_once_and_records_covers_until(test_db, patch_model):
    model = patch_model()
    all_msgs = await _seed(test_db, 12)

    assert await DigestService(test_db).maybe_compact("u1") is True

    digest = await ConversationService(test_db).get_digest("u1")
    assert digest.bullets == ["Khách muốn làm tóc."]
    assert digest.covers_until == all_msgs[-(2 * KEEP_RECENT_TURNS) - 1].created_at
    assert digest.failures == 0
    assert model.kwargs["tags"] == ["digest"]
    assert model.kwargs["streaming"] is False
    assert model.kwargs["temperature"] == 0.0


async def test_recent_turns_are_not_in_the_prompt(test_db, patch_model):
    model = patch_model()
    all_msgs = await _seed(test_db, 12)
    await DigestService(test_db).maybe_compact("u1")
    prompt = model.structured.prompts[0]
    for m in all_msgs[-(2 * KEEP_RECENT_TURNS):]:
        assert m.content not in prompt
    assert all_msgs[0].content in prompt


async def test_second_run_only_feeds_new_messages_and_old_bullets(test_db, patch_model):
    model = patch_model()
    svc = DigestService(test_db)
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
    svc = DigestService(test_db)
    await svc.maybe_compact("u1")
    assert await svc.maybe_compact("u1") is False
    assert len(model.structured.prompts) == 1


async def test_model_failure_bumps_the_fuse_and_keeps_the_old_digest(test_db, patch_model):
    ok = patch_model()
    await _seed(test_db, 12)
    svc = DigestService(test_db)
    await svc.maybe_compact("u1")

    patch_model(fail=True)
    await _seed(test_db, 12)
    assert await svc.maybe_compact("u1") is False

    digest = await ConversationService(test_db).get_digest("u1")
    assert digest.failures == 1
    assert digest.bullets == ["Khách muốn làm tóc."]


async def test_three_failures_stop_further_attempts(test_db, patch_model):
    model = patch_model(fail=True)
    await _seed(test_db, 12)
    svc = DigestService(test_db)
    for _ in range(MAX_FAILURES):
        await svc.maybe_compact("u1")
    calls_before = len(model.structured.prompts)
    await svc.maybe_compact("u1")
    assert len(model.structured.prompts) == calls_before == MAX_FAILURES


async def test_never_raises(test_db, monkeypatch):
    """Chạy nền sau complete — lỗi gì cũng phải nuốt và log."""
    async def boom(*a, **k):
        raise RuntimeError("mongo down")
    monkeypatch.setattr(ConversationService, "get_digest", boom)
    await _seed(test_db, 12)
    assert await DigestService(test_db).maybe_compact("u1") is False
