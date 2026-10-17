"""Test cho `bench/seed.py`. Chỉ `test_seed_against_running_ref` là @live: nó tự
dựng và dọn một RefWorkspace thật trên HEAD."""

from __future__ import annotations

import shutil
import subprocess
import uuid
from datetime import datetime
from pathlib import Path

import pytest

from bench.config import BenchSettings
from bench.dbread import snapshot
from bench.scenario import VN_TZ, ApptSpec, resolve_at
from bench.seed import Customer, ensure_admin
from bench.workspace import RefWorkspace


def test_customer_is_a_plain_value_object():
    c = Customer(id="u1", phone="0987654321", name="Khách", token="tok")

    assert c.id == "u1"
    assert c.phone == "0987654321"
    assert c.token == "tok"


@pytest.mark.live
@pytest.mark.asyncio
async def test_seed_against_running_ref(tmp_path):
    from bench.seed import Seeder

    settings = BenchSettings(
        repo_root=Path(__file__).resolve().parent.parent.parent,
        worktrees_dir=tmp_path / "worktrees",
    )
    run_id = f"seedlive{uuid.uuid4().hex[:6]}"
    ws = RefWorkspace(settings, ref="HEAD", port=8198, run_id=run_id, azure_endpoint=None)

    try:
        await ws.prepare()
        await ensure_admin(ws)
        ok = await ws.start(timeout_s=90)
        assert ok is True, ws.error

        seeder = Seeder(ws.base_url, settings.admin_phone, settings.admin_password)
        await seeder.set_shop(open_time="08:00", close_time="19:00", closed_days=())
        await seeder.set_busy(None)

        customer = await seeder.new_customer("Khách Test Bench")
        assert customer.token

        now_vn = datetime.now(VN_TZ)
        at = resolve_at(ApptSpec(day="tomorrow", time="10:00"), now_vn)

        appt_id = await seeder.book_for(customer.id, at, note="cắt tóc")
        assert appt_id

        rows = snapshot(settings.mongo_uri, ws.db_name)
        matching = [r for r in rows if r.id == appt_id]
        assert len(matching) == 1
        row = matching[0]
        assert row.user_id == customer.id
        assert row.status == "booked"
        assert row.start_at.hour == 10
        assert row.start_at.date() == at.date()
    finally:
        await ws.stop()
        await ws.drop_db()
        subprocess.run(
            ["git", "-C", str(settings.repo_root), "worktree", "remove", "--force", str(ws.path)],
            check=False,
        )
        shutil.rmtree(ws.path, ignore_errors=True)
