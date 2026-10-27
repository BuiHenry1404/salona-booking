"""Fixtures dùng chung cho test của bench/.

Không khởi tạo Mongo hay server thật ở đây — các test không đánh dấu `live`
phải chạy được mà không cần gì ngoài venv của bench/.
"""

from __future__ import annotations

from pathlib import Path

import pytest

BENCH_ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture
def bench_root() -> Path:
    return BENCH_ROOT
