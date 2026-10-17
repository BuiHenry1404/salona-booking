"""Test cho `bench/workspace.py`: rewrite_env (thuần) và vòng đời RefWorkspace."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from bench.config import BenchSettings
from bench.workspace import RefWorkspace, rewrite_env

SAMPLE_ENV = """APP_NAME=chatbot-api
MONGO_URI=mongodb://localhost:27017
MONGO_DB_NAME=chatbot_db
TELEGRAM_BOT_TOKEN=abc
AZURE_OPENAI_ENDPOINT=https://x
OTHER_KEY=unchanged
"""


def test_rewrite_env():
    out = rewrite_env(SAMPLE_ENV, db_name="bench_x", azure_endpoint="http://127.0.0.1:9100")

    assert "MONGO_DB_NAME=bench_x" in out
    assert "# TELEGRAM_BOT_TOKEN=abc" in out
    assert "TELEGRAM_BOT_TOKEN=abc" not in out.replace("# TELEGRAM_BOT_TOKEN=abc", "")
    assert "AZURE_OPENAI_ENDPOINT=http://127.0.0.1:9100" in out
    assert "OTHER_KEY=unchanged" in out
    assert "APP_NAME=chatbot-api" in out
    assert "MONGO_URI=mongodb://localhost:27017" in out


def test_rewrite_env_keeps_azure_endpoint_when_none():
    out = rewrite_env(SAMPLE_ENV, db_name="bench_x", azure_endpoint=None)

    assert "AZURE_OPENAI_ENDPOINT=https://x" in out


def test_rewrite_env_adds_missing_db_name():
    src = "APP_NAME=chatbot-api\nOTHER_KEY=unchanged\n"
    out = rewrite_env(src, db_name="bench_y", azure_endpoint=None)

    lines = out.splitlines()
    assert "MONGO_DB_NAME=bench_y" in lines
    assert lines[-1] == "MONGO_DB_NAME=bench_y"


@pytest.mark.asyncio
async def test_start_failure_is_reported_not_raised(tmp_path, monkeypatch):
    settings = BenchSettings(
        repo_root=Path(".").resolve(),
        worktrees_dir=tmp_path / "worktrees",
    )
    ws = RefWorkspace(settings, ref="HEAD", port=8199, run_id="t1", azure_endpoint=None)

    # Bỏ qua prepare() thật; chỉ cần path tồn tại để ghi log.
    ws.path = tmp_path / "fake_workspace"
    ws.path.mkdir(parents=True, exist_ok=True)
    ws.log_path = ws.path / "bench_server.log"

    monkeypatch.setattr(
        ws,
        "_server_cmd",
        lambda: ["python3", "-c", "import sys; print('boom'); sys.exit(1)"],
    )

    ok = await ws.start(timeout_s=3)

    assert ok is False
    assert ws.status == "failed_to_start"
    assert ws.error is not None
    assert "boom" in ws.error


@pytest.mark.asyncio
async def test_is_alive_tracks_server_process(tmp_path):
    """I2: runner dùng is_alive()/log_tail() để phát hiện server chết giữa chừng."""
    settings = BenchSettings(repo_root=Path(".").resolve(), worktrees_dir=tmp_path / "wt")
    ws = RefWorkspace(settings, ref="HEAD", port=8199, run_id="t2", azure_endpoint=None)
    ws.path = tmp_path / "ws"
    ws.path.mkdir()
    ws.log_path = ws.path / "bench_server.log"
    assert ws.is_alive() is False  # chưa start

    ws._process = subprocess.Popen(
        ["python3", "-c", "print('dying'); import time; time.sleep(0.2)"],
        stdout=open(ws.log_path, "wb"),
        stderr=subprocess.STDOUT,
    )
    assert ws.is_alive() is True
    ws._process.wait(5)
    assert ws.is_alive() is False
    assert "dying" in ws.log_tail()
    await ws.stop()


_HTTP_200_SERVER = """
import http.server, sys
class H(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200); self.end_headers(); self.wfile.write(b'ok')
    def log_message(self, *a):
        pass
srv = http.server.HTTPServer(('127.0.0.1', int(sys.argv[1])), H)
srv.handle_request() if len(sys.argv) > 2 else srv.serve_forever()
"""


def _free_port() -> int:
    import socket

    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _fake_ws(tmp_path, port: int, run_id: str = "t3") -> RefWorkspace:
    settings = BenchSettings(repo_root=Path(".").resolve(), worktrees_dir=tmp_path / "wt")
    ws = RefWorkspace(settings, ref="HEAD", port=port, run_id=run_id, azure_endpoint=None)
    ws.path = tmp_path / f"ws-{run_id}"
    ws.path.mkdir(parents=True, exist_ok=True)
    ws.log_path = ws.path / "bench_server.log"
    return ws


@pytest.mark.asyncio
async def test_start_refuses_busy_port(tmp_path, monkeypatch):
    # I1: một server cũ (mồ côi) đang giữ cổng và trả 200 cho /ready — không
    # được nhận nhầm là server của mình.
    port = _free_port()
    squatter = subprocess.Popen(["python3", "-c", _HTTP_200_SERVER, str(port)])
    try:
        import time as _t

        _t.sleep(0.5)
        ws = _fake_ws(tmp_path, port)
        monkeypatch.setattr(
            ws, "_server_cmd", lambda: ["python3", "-c", "import time; time.sleep(3)"]
        )
        ok = await ws.start(timeout_s=3)
        assert ok is False
        assert ws.status == "failed_to_start"
        assert "port busy" in (ws.error or "")
        assert ws.is_alive() is False
    finally:
        squatter.kill()
        squatter.wait()


@pytest.mark.asyncio
async def test_start_fails_if_server_dies_right_after_ready(tmp_path, monkeypatch):
    import bench.workspace as wsmod

    monkeypatch.setattr(wsmod, "_READY_SETTLE_S", 1.0)
    port = _free_port()
    ws = _fake_ws(tmp_path, port)
    # Trả 200 đúng một lần rồi thoát.
    monkeypatch.setattr(
        ws, "_server_cmd", lambda: ["python3", "-c", _HTTP_200_SERVER, str(port), "once"]
    )
    ok = await ws.start(timeout_s=5)
    assert ok is False
    assert ws.status == "failed_to_start"


def test_worktree_lock_blocks_second_user(tmp_path):
    ws1 = _fake_ws(tmp_path, 8199, "a")
    ws2 = _fake_ws(tmp_path, 8199, "b")
    ws1.acquire_lock()
    try:
        with pytest.raises(RuntimeError, match="đang được"):
            ws2.acquire_lock()
    finally:
        ws1.release_lock()
    ws2.acquire_lock()
    ws2.release_lock()


@pytest.mark.live
@pytest.mark.asyncio
async def test_start_real_ref(tmp_path):
    settings = BenchSettings(
        repo_root=Path(__file__).resolve().parent.parent.parent,
        worktrees_dir=tmp_path / "worktrees",
    )
    ws = RefWorkspace(settings, ref="HEAD", port=8199, run_id="live1", azure_endpoint=None)

    try:
        await ws.prepare()
        ok = await ws.start(timeout_s=90)
        assert ok is True
        assert ws.status == "ready"

        import httpx

        async with httpx.AsyncClient() as client:
            resp = await client.get(f"{ws.base_url}/api/v1/health/ready", timeout=5.0)
            assert resp.status_code == 200
    finally:
        await ws.stop()
        await ws.drop_db()
        subprocess.run(
            ["git", "-C", str(settings.repo_root), "worktree", "remove", "--force", str(ws.path)],
            check=False,
        )
        shutil.rmtree(ws.path, ignore_errors=True)
