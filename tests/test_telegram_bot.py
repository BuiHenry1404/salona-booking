import asyncio

import pytest

from app.telegram.bot import TelegramBot, start_bot

pytestmark = pytest.mark.asyncio


class FakeClient:
    """Trả về vài lô update rồi rỗng mãi."""

    def __init__(self, batches):
        self.batches = list(batches)
        self.offsets = []
        self.closed = False

    async def get_updates(self, offset, timeout=25):
        self.offsets.append(offset)
        await asyncio.sleep(0)
        return self.batches.pop(0) if self.batches else []

    async def aclose(self):
        self.closed = True


class SpyHandlers:
    def __init__(self):
        self.seen = []

    async def handle_update(self, update):
        self.seen.append(update["update_id"])


async def run_briefly(bot, seconds=0.2):
    task = asyncio.create_task(bot.run())
    await asyncio.sleep(seconds)
    bot.stop()
    await asyncio.wait_for(task, timeout=2)


async def test_every_update_reaches_the_handlers(test_db):
    client = FakeClient([[{"update_id": 1}, {"update_id": 2}], [{"update_id": 3}]])
    handlers = SpyHandlers()
    bot = TelegramBot(test_db, client=client, handlers=handlers, idle_sleep=0.01)

    await run_briefly(bot)
    assert handlers.seen == [1, 2, 3]


async def test_offset_advances_past_the_highest_update_id(test_db):
    """Không tăng offset thì Telegram gửi lại cùng update mãi mãi."""
    client = FakeClient([[{"update_id": 10}], [{"update_id": 12}]])
    bot = TelegramBot(test_db, client=client, handlers=SpyHandlers(), idle_sleep=0.01)

    await run_briefly(bot)
    assert client.offsets[0] == 0
    assert 11 in client.offsets
    assert 13 in client.offsets


async def test_a_failing_handler_does_not_kill_the_loop(test_db):
    class Broken:
        def __init__(self):
            self.count = 0

        async def handle_update(self, update):
            self.count += 1
            raise RuntimeError("hỏng")

    client = FakeClient([[{"update_id": 1}], [{"update_id": 2}]])
    handlers = Broken()
    bot = TelegramBot(test_db, client=client, handlers=handlers, idle_sleep=0.01)

    await run_briefly(bot)
    assert handlers.count == 2


async def test_stop_closes_the_client(test_db):
    client = FakeClient([])
    bot = TelegramBot(test_db, client=client, handlers=SpyHandlers(), idle_sleep=0.01)
    await run_briefly(bot)
    assert client.closed is True


async def test_start_bot_returns_none_when_not_configured(test_db, monkeypatch):
    monkeypatch.setattr("app.telegram.bot.is_configured", lambda: False)
    assert start_bot(test_db) is None
