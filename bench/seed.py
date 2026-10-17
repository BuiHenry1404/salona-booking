"""Seed một ref đang chạy qua REST API thật (hộp đen — không `import app`):
tạo tài khoản admin dev (idempotent, qua script của chính worktree), đặt giờ
tiệm/trạng thái bận, tạo khách mới, và đặt lịch hộ khách.

Hộp đen: chỉ dùng `httpx` (HTTP) và subprocess (chạy script seed của worktree
bằng venv của chính worktree đó) — không `import app`.
"""

from __future__ import annotations

import asyncio
import random
from dataclasses import dataclass
from datetime import datetime

import httpx

from bench.workspace import RefWorkspace

_SEED_SCRIPT = "scripts/seed_dev_users.py"
_MAX_PHONE_ATTEMPTS = 10


@dataclass(frozen=True)
class Customer:
    id: str
    phone: str
    name: str
    token: str


def _random_phone() -> str:
    """SĐT khách giả: `0987` + 6 số ngẫu nhiên — tránh đụng tài khoản dev seed
    sẵn (0901234567, 0912345678, ...) và duy nhất đủ dùng cho một lần chạy."""
    return "0987" + "".join(str(random.randint(0, 9)) for _ in range(6))


async def ensure_admin(ws: RefWorkspace) -> None:
    """Chạy `scripts/seed_dev_users.py` của worktree bằng venv của chính nó.

    Script đọc `.env` của worktree (Task 3 đã ghi đè `MONGO_DB_NAME` thành DB
    riêng của ref này) và nối thẳng Mongo — không cần server đã `start()`.
    Idempotent: chạy lại không lỗi nếu tài khoản đã có.
    """
    python = ws.path / ".venv" / "bin" / "python"
    env = {"PYTHONPATH": str(ws.path)}

    proc = await asyncio.create_subprocess_exec(
        str(python),
        _SEED_SCRIPT,
        cwd=str(ws.path),
        env=env,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    stdout, _ = await proc.communicate()
    if proc.returncode != 0:
        raise RuntimeError(
            f"ensure_admin thất bại (exit {proc.returncode}) cho {ws.path}:\n"
            f"{stdout.decode(errors='replace')}"
        )


class Seeder:
    """Thao tác seed qua REST trên một ref đang chạy ở `base_url`."""

    def __init__(self, base_url: str, admin_phone: str, admin_password: str) -> None:
        self.base_url = base_url
        self._admin_phone = admin_phone
        self._admin_password = admin_password
        self._admin_token: str | None = None

    async def _admin_headers(self) -> dict[str, str]:
        if self._admin_token is None:
            async with httpx.AsyncClient(base_url=self.base_url) as client:
                resp = await client.post(
                    "/api/v1/auth/login",
                    json={"phone": self._admin_phone, "password": self._admin_password},
                )
                resp.raise_for_status()
                self._admin_token = resp.json()["access_token"]
        return {"Authorization": f"Bearer {self._admin_token}"}

    async def set_shop(
        self,
        open_time: str = "08:00",
        close_time: str = "19:00",
        closed_days: tuple[int, ...] = (),
    ) -> None:
        headers = await self._admin_headers()
        async with httpx.AsyncClient(base_url=self.base_url) as client:
            resp = await client.put(
                "/api/v1/shop/hours",
                headers=headers,
                json={
                    "open_time": open_time,
                    "close_time": close_time,
                    "closed_days": list(closed_days),
                },
            )
            resp.raise_for_status()

    async def set_busy(self, minutes: int | None) -> None:
        headers = await self._admin_headers()
        async with httpx.AsyncClient(base_url=self.base_url) as client:
            if minutes is None:
                resp = await client.post("/api/v1/shop/free", headers=headers)
            else:
                resp = await client.post(
                    "/api/v1/shop/busy", headers=headers, json={"minutes": minutes}
                )
            resp.raise_for_status()

    async def new_customer(self, name: str) -> Customer:
        """Tạo khách mới (SĐT ngẫu nhiên `0987xxxxxx`, thử lại nếu trùng) rồi
        đăng nhập luôn để lấy access token."""
        headers = await self._admin_headers()
        password = "bench1234"

        last_error: httpx.HTTPStatusError | None = None
        for _ in range(_MAX_PHONE_ATTEMPTS):
            phone = _random_phone()
            async with httpx.AsyncClient(base_url=self.base_url) as client:
                resp = await client.post(
                    "/api/v1/auth/users",
                    headers=headers,
                    json={
                        "phone": phone,
                        "password": password,
                        "full_name": name,
                        "role": "user",
                    },
                )
            if resp.status_code in (400, 409):
                last_error = httpx.HTTPStatusError(
                    "số điện thoại trùng, thử lại", request=resp.request, response=resp
                )
                continue
            resp.raise_for_status()
            user_id = resp.json()["id"]

            async with httpx.AsyncClient(base_url=self.base_url) as client:
                login_resp = await client.post(
                    "/api/v1/auth/login", json={"phone": phone, "password": password}
                )
                login_resp.raise_for_status()
                token = login_resp.json()["access_token"]

            return Customer(id=user_id, phone=phone, name=name, token=token)

        assert last_error is not None
        raise last_error

    async def book_for(self, customer_id: str, at: datetime, note: str | None) -> str:
        """Đặt hộ khách (dùng quyền admin để truyền `for_user_id`). Trả về id lịch."""
        headers = await self._admin_headers()
        async with httpx.AsyncClient(base_url=self.base_url) as client:
            resp = await client.post(
                "/api/v1/appointments",
                headers=headers,
                json={
                    "start_at": at.isoformat(),
                    "note": note,
                    "for_user_id": customer_id,
                },
            )
            resp.raise_for_status()
            return resp.json()["id"]
