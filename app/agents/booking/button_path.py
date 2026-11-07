"""Đường nút Đồng ý/Không (`confirm_proposal`), Task 4.5 — §5.3 lifecycle.

Khác đường chat thường (`send_message`): không đi qua Orchestrator, không gọi
model nào cả — nút bấm là quyết định CODE đọc thẳng, chỉ còn `apply_decision`
(cổng ghi duy nhất, M3) quyết việc ghi lịch. Vẫn đi `brief -> respond` như
lượt thường (Conversation Agent dựng câu trả lời từ `ReplyBrief`), trừ nhánh
`stale` dùng thẳng câu mẫu cố định `BUTTON_STALE_REPLY`, không qua Conversation
Agent (không có gì để kể, không cần rủi ro model bịa)."""
from __future__ import annotations

from app.agents.booking.orchestrator.decide import apply_decision
from app.agents.booking.orchestrator.tools import OrchestratorDeps
from app.agents.booking.reply_brief import BUTTON_STALE_REPLY, ButtonOutcome
from app.agents.booking.button import ButtonReply


def stale_reply(address: str) -> str:
    """Câu cố định khi đề xuất trong nút bấm đã hết hạn/không còn khớp —
    không qua Conversation Agent (§5.3)."""
    return BUTTON_STALE_REPLY.replace("anh chị", address)


async def run_button(deps: OrchestratorDeps, button: ButtonReply) -> ButtonOutcome:
    """Áp quyết định của nút bấm lên đề xuất đang chờ của lượt này.

    `ctx.pending` KHÁC `button.proposal_id` (đã bị đóng/đổi ở nơi khác, hoặc
    nút bấm cho một đề xuất cũ) -> `stale`, KHÔNG chạm DB/ledger — không có gì
    để ghi, và ghi outcome cho một đề xuất không còn là đề xuất "của lượt
    này" sẽ sai `turn_ledger`. Đề xuất ĐÃ HẾT HẠN nhưng vẫn đúng id thì KHÔNG
    coi là stale ở đây — phải đi qua `apply_decision` như bình thường để nó tự
    rơi vào `not_approvable` (reason="expired") và GHI LẠI outcome đó vào
    ledger, như mọi nhánh not_approvable khác."""
    pending = deps.ctx.pending
    if pending is None or pending.id != button.proposal_id:
        return ButtonOutcome(kind="stale", outcome=None)

    label = "approve" if button.decision == "yes" else "reject"
    outcome = await apply_decision(deps, label)
    return ButtonOutcome(kind=outcome.outcome, outcome=outcome)  # type: ignore[arg-type]
