"""Workspace của một ref được đo: worktree git riêng, venv riêng, `.env` riêng,
và vòng đời server uvicorn (start/stop) cho ref đó.

Hộp đen: module này KHÔNG `import app` — chỉ gọi `git`, `uv`, và một subprocess
uvicorn qua HTTP. Xem AGENTS.md và global-constraints.md.
"""

from __future__ import annotations

import asyncio
import shutil
import subprocess
import time
from pathlib import Path

import httpx
from pymongo import MongoClient

from bench.config import BenchSettings

_TELEGRAM_RE_PREFIX = "TELEGRAM_BOT_TOKEN="


def rewrite_env(src_text: str, db_name: str, azure_endpoint: str | None) -> str:
    """Ghi đè đúng 3 khoá trong nội dung `.env` gốc, giữ nguyên mọi dòng khác.

    - `MONGO_DB_NAME` được đặt thành `db_name` (thêm dòng mới ở cuối nếu
      chưa có trong file gốc).
    - Mọi dòng bắt đầu bằng `TELEGRAM_BOT_TOKEN=` bị comment lại (một kết nối
      `getUpdates` mỗi token — không được chạy nhiều ref cùng polling).
    - `AZURE_OPENAI_ENDPOINT` được thay bằng `azure_endpoint` nếu được truyền
      (khác None); nếu None thì giữ nguyên giá trị gốc.
    """
    lines = src_text.splitlines()
    out: list[str] = []
    saw_db_name = False

    for line in lines:
        stripped = line.strip()

        if stripped.startswith("MONGO_DB_NAME="):
            out.append(f"MONGO_DB_NAME={db_name}")
            saw_db_name = True
            continue

        if stripped.startswith(_TELEGRAM_RE_PREFIX):
            out.append(f"# {line}")
            continue

        if azure_endpoint is not None and stripped.startswith("AZURE_OPENAI_ENDPOINT="):
            out.append(f"AZURE_OPENAI_ENDPOINT={azure_endpoint}")
            continue

        out.append(line)

    if not saw_db_name:
        out.append(f"MONGO_DB_NAME={db_name}")

    result = "\n".join(out)
    if src_text.endswith("\n") and not result.endswith("\n"):
        result += "\n"
    return result


def _tail_lines(path: Path, n: int = 50) -> str:
    if not path.exists():
        return ""
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    return "\n".join(lines[-n:])


class RefWorkspace:
    """Worktree + venv + .env + vòng đời server uvicorn cho một git ref."""

    def __init__(
        self,
        settings: BenchSettings,
        ref: str,
        port: int,
        run_id: str,
        azure_endpoint: str | None,
    ) -> None:
        self.settings = settings
        self.ref = ref
        self.port = port
        self.run_id = run_id
        self.azure_endpoint = azure_endpoint

        self.sha = self._resolve_sha(ref)
        self.sha7 = self.sha[:7]
        self.path: Path = settings.worktrees_dir / self.sha7
        self.db_name = f"bench_{self.sha7}_{run_id}"
        self.base_url = f"http://127.0.0.1:{port}"
        self.log_path: Path = self.path / "bench_server.log"

        self.status: str = "pending"
        self.error: str | None = None

        self._process: subprocess.Popen | None = None
        self._log_file = None

    def _resolve_sha(self, ref: str) -> str:
        result = subprocess.run(
            ["git", "-C", str(self.settings.repo_root), "rev-parse", ref],
            capture_output=True,
            text=True,
            check=True,
        )
        return result.stdout.strip()

    def _venv_python(self) -> Path:
        return self.path / ".venv" / "bin" / "python"

    async def prepare(self) -> None:
        """Tạo (hoặc tái dùng) worktree, dựng venv, cài deps, ghi `.env`."""
        await asyncio.to_thread(self._prepare_sync)

    def _prepare_sync(self) -> None:
        self.settings.worktrees_dir.mkdir(parents=True, exist_ok=True)

        if self.path.exists():
            existing_sha = self._worktree_sha(self.path)
            if existing_sha != self.sha:
                subprocess.run(
                    ["git", "-C", str(self.settings.repo_root), "worktree", "remove", "--force", str(self.path)],
                    check=False,
                )
                if self.path.exists():
                    shutil.rmtree(self.path, ignore_errors=True)
                self._create_worktree()
        else:
            self._create_worktree()

        self._build_venv()
        self._write_env()

    def _worktree_sha(self, path: Path) -> str | None:
        try:
            result = subprocess.run(
                ["git", "-C", str(path), "rev-parse", "HEAD"],
                capture_output=True,
                text=True,
                check=True,
            )
            return result.stdout.strip()
        except subprocess.CalledProcessError:
            return None

    def _create_worktree(self) -> None:
        subprocess.run(
            [
                "git",
                "-C",
                str(self.settings.repo_root),
                "worktree",
                "add",
                "--detach",
                str(self.path),
                self.sha,
            ],
            check=True,
            capture_output=True,
            text=True,
        )

    def _build_venv(self) -> None:
        venv_dir = self.path / ".venv"
        if not (venv_dir / "bin" / "python").exists():
            subprocess.run(
                ["uv", "venv", str(venv_dir), "--python", "3.12"],
                check=True,
                capture_output=True,
                text=True,
            )
        subprocess.run(
            [
                "uv",
                "pip",
                "install",
                "-p",
                str(self._venv_python()),
                "-r",
                str(self.path / "requirements.txt"),
            ],
            check=True,
            capture_output=True,
            text=True,
        )

    def _write_env(self) -> None:
        src_path = self.settings.repo_root / ".env"
        src_text = src_path.read_text(encoding="utf-8")
        new_text = rewrite_env(src_text, self.db_name, self.azure_endpoint)
        (self.path / ".env").write_text(new_text, encoding="utf-8")

    def _server_cmd(self) -> list[str]:
        return [
            str(self._venv_python()),
            "-m",
            "uvicorn",
            "main:app",
            "--host",
            "127.0.0.1",
            "--port",
            str(self.port),
        ]

    async def start(self, timeout_s: float = 90) -> bool:
        """Khởi động uvicorn và chờ `/api/v1/health/ready` trả 200.

        Không bao giờ raise: thất bại (crash, port bận, timeout) được ghi vào
        `self.status = "failed_to_start"` và `self.error` (50 dòng log cuối),
        trả về False.
        """
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        self._log_file = open(self.log_path, "wb")

        try:
            self._process = subprocess.Popen(
                self._server_cmd(),
                cwd=str(self.path),
                stdout=self._log_file,
                stderr=subprocess.STDOUT,
            )
        except OSError as exc:
            self.status = "failed_to_start"
            self.error = str(exc)
            return False

        ready_url = f"{self.base_url}/api/v1/health/ready"
        deadline = time.monotonic() + timeout_s

        async with httpx.AsyncClient() as client:
            while time.monotonic() < deadline:
                exit_code = self._process.poll()
                if exit_code is not None:
                    self.status = "failed_to_start"
                    self.error = _tail_lines(self.log_path, 50)
                    self._close_log()
                    return False

                try:
                    resp = await client.get(ready_url, timeout=2.0)
                    if resp.status_code == 200:
                        self.status = "ready"
                        return True
                except httpx.HTTPError:
                    pass

                await asyncio.sleep(0.5)

        self.status = "failed_to_start"
        self.error = _tail_lines(self.log_path, 50)
        await self.stop()
        return False

    def _close_log(self) -> None:
        if self._log_file is not None:
            try:
                self._log_file.close()
            except OSError:
                pass
            self._log_file = None

    async def stop(self) -> None:
        """Dừng server (nếu đang chạy) sạch sẽ, không để lại process."""
        if self._process is not None and self._process.poll() is None:
            self._process.terminate()
            try:
                await asyncio.to_thread(self._process.wait, 10)
            except subprocess.TimeoutExpired:
                self._process.kill()
                await asyncio.to_thread(self._process.wait)
        self._process = None
        self._close_log()

    async def drop_db(self) -> None:
        """Xoá database Mongo riêng của ref này (dùng pymongo trực tiếp)."""

        def _drop() -> None:
            client: MongoClient = MongoClient(self.settings.mongo_uri)
            try:
                client.drop_database(self.db_name)
            finally:
                client.close()

        await asyncio.to_thread(_drop)
