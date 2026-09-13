"""Tầng digest trong phiên: nén phần cũ của hội thoại hôm nay thành dữ kiện.

Spec: docs/superpowers/specs/2026-09-14-conversation-digest-design.md.
"""
from typing import List

from pydantic import BaseModel, field_validator

from app.core.text import single_line

KEEP_RECENT_TURNS = 4                 # 8 tin gần nhất luôn nguyên văn
COMPACT_THRESHOLD_TOKENS = 800        # phần ngoài cửa sổ vượt mức này mới nén
MAX_BULLETS = 8
BULLET_MAX_CHARS = 120
MAX_FAILURES = 3                      # hỏng liên tiếp ngần này thì thôi tới hết ngày
DIGEST_TIMEOUT_SECONDS = 8            # cùng mốc với parser (CONTEXT.md bẫy #10)


class DigestBullets(BaseModel):
    """Đầu ra có cấu trúc của lượt nén. Validator ÉP về giới hạn thay vì ném
    lỗi: lớp gọi fail-soft, ném lỗi là vứt luôn phần model đã nén đúng."""

    bullets: List[str]

    @field_validator("bullets")
    @classmethod
    def _coerce(cls, value: List[str]) -> List[str]:
        cleaned = []
        for raw in value:
            line = single_line((raw or "").lstrip("-•* ").strip(), BULLET_MAX_CHARS)
            if line:
                cleaned.append(line)
        return cleaned[:MAX_BULLETS]
