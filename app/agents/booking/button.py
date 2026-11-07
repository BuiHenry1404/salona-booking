"""`ButtonReply`: câu trả lời đề xuất bằng nút bấm (Đồng ý/Không), Task 5.

Khác câu khách gõ chữ: nguồn là Socket.IO event `confirm_proposal`, không phải
`send_message`. `label` là câu hiển thị — ghi vào `conversations` và thread
đúng như một câu khách gõ ("customer_text") — để lịch sử trò chuyện cho thấy
khách đã bấm gì, dù không gõ chữ nào. `decision`/`proposal_id` là cặp resume
CODE dùng thẳng ở node `confirm` (`_handle_button`), không qua `reply.decide`.
"""
from dataclasses import dataclass
from typing import Literal

# Nhãn hiển thị cho từng quyết định — khớp đúng chữ trên nút giao diện.
BUTTON_LABELS = {"yes": "Đồng ý", "no": "Không"}


@dataclass(frozen=True)
class ButtonReply:
    decision: Literal["yes", "no"]
    proposal_id: str
    label: str
