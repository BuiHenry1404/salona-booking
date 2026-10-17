"""Tạo client Claude dùng cho judge và khách giả lập (hộp đen — không `import app`).

Một hàm duy nhất tạo client: nếu sau này đổi nền tảng (vd: gọi Anthropic
trực tiếp thay vì qua Microsoft Foundry), chỉ cần sửa ở đây — chỗ gọi khác
(`judge.py`, `simulate.py`) không đổi.
"""

from __future__ import annotations

import anthropic

from bench.config import BenchSettings


def make_client(settings: BenchSettings) -> anthropic.AsyncAnthropicFoundry:
    """Tạo `AsyncAnthropicFoundry` từ `BenchSettings` (R3: xác nhận tên lớp
    trong SDK đã cài ở `bench/.venv` trước khi dùng — không giả định)."""
    return anthropic.AsyncAnthropicFoundry(
        api_key=settings.foundry_api_key,
        resource=settings.foundry_resource,
    )
