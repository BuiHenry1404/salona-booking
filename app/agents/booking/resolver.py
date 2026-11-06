"""State Resolver — node đầu tiên mỗi lượt (§4.3, Task 1.5).

Code thuần: không gọi LLM, không ghi DB, không có danh sách từ khoá ý định
("ừ", "không", "đồng ý" ...). Chỉ nhìn CẤU TRÚC của `TurnContext` (có
`pending`? có `open_question`? có hàng chờ? có việc chưa báo?) cộng các
scanner SỰ KIỆN của `tokens` (giờ/ngày/thứ) — đó là dữ kiện, không phải ý
định, nên được phép dùng ở đây.

Thứ tự luật (không đổi): R1 nút thắng mọi thứ, kể cả khi có `pending`. R2
còn `pending` + câu chữ. R3 có `open_question` và câu ngắn chỉ nói giờ/ngày.
R5: còn lại — kể cả "lượt sạch" (không pending/open_question/hàng chờ/việc
chưa báo, câu không nhắc số/giờ/ngày/thứ nào); Orchestrator tự quyết xã giao/
không hỗ trợ qua `finish(kind_hint=...)` (quyết định chủ dự án 06/11, bỏ
`classify_only`/R4 — xem Task 4.4b).

`R0` không phải một nhánh riêng: `unannounced` khác None chỉ gắn thêm cờ
`must_announce=True` vào bất kỳ kết quả nào ở trên.
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel

from app.agents.booking.context import TurnContext
from app.agents.booking_graph.button import ButtonReply
from app.agents.booking_graph.tokens import (
    has_slash_date,
    names_a_clock,
    names_a_day,
    scan_day_offsets,
    scan_times,
    scan_weekdays,
    words,
)

Route = Literal["button", "orchestrate"]
Rule = Literal["R0", "R1", "R2", "R3", "R5"]

# R3: câu trả lời ngắn cho một câu hỏi mở — tối đa ngần này từ.
_SHORT_ANSWER_MAX_WORDS = 6


class ResolveResult(BaseModel):
    """Quyết định của resolver cho một lượt — không chạm DB, không mang chữ khách."""

    route: Route
    rule: Rule
    must_announce: bool
    answers_open_question_hint: bool = False


def is_short_time_answer(text: str) -> bool:
    """R3: câu ≤6 từ (`tokens.words`) và chứa ít nhất một mốc giờ/ngày/thứ."""
    if len(words(text)) > _SHORT_ANSWER_MAX_WORDS:
        return False
    return bool(
        scan_times(text)
        or scan_weekdays(text)
        or scan_day_offsets(text)
        or has_slash_date(text)
        or names_a_day(text)
        or names_a_clock(text)
    )


def resolve(ctx: TurnContext, question: str | ButtonReply) -> ResolveResult:
    """Quyết định route cho một lượt. Thuần, không mutate `ctx`, không ghi DB."""
    must_announce = ctx.unannounced is not None

    # R1: nút Đồng ý/Không thắng mọi nhánh khác, kể cả khi còn `pending`.
    if isinstance(question, ButtonReply):
        return ResolveResult(route="button", rule="R1", must_announce=must_announce)

    # R2: còn đề xuất chờ, khách gõ chữ → orchestrator tự quyết (middleware chặn ghi).
    if ctx.pending is not None:
        return ResolveResult(route="orchestrate", rule="R2", must_announce=must_announce)

    # R3: đang có câu hỏi mở và khách trả lời ngắn gọn bằng giờ/ngày.
    if ctx.open_question is not None and is_short_time_answer(question):
        return ResolveResult(
            route="orchestrate",
            rule="R3",
            must_announce=must_announce,
            answers_open_question_hint=True,
        )

    # R5: còn lại.
    return ResolveResult(route="orchestrate", rule="R5", must_announce=must_announce)
