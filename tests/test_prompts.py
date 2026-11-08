"""Hàng rào cho prompt tiếng Anh của gói mới (`app.agents.booking.prompts`).

Task 6.3 (M6-cutover): 70+ test đọc chữ của bốn prompt CŨ (`BOOKING_PROMPT`,
`SHOP_PROMPT`, `SOCIAL_PROMPT`, `SUPERVISOR_PROMPT`) bị xoá cùng gói supervisor
graph cũ — không prompt nào trong số đó còn chạy trên traffic thật. Giữ lại:

- Hai test DIGEST_PROMPT (T0.4: tiếng Anh/không câu mẫu tiếng Việt, và yêu
  cầu `day` dạng ISO) + test tham số hoá các `DIGEST_CAPS` (T4) — cả ba đều
  kiểm `app.agents.booking.prompts.digest.DIGEST_PROMPT`, vẫn chạy thật.
- Test `REWRITE_PROMPT` đòi tiếng Việt ở câu trả khách — vẫn chạy thật qua
  `app.agents.booking.rewrite.rewrite_once`.
- MỘT hàm tham số hoá mới, `test_every_prompt_is_english_without_vietnamese_samples`,
  trên các prompt khách-hàng-đối-diện/model-đọc của gói mới. M6-cutover.md ghi
  "7 prompt mới"; thực tế `app/agents/booking/prompts/` chỉ có SÁU hằng
  `..._PROMPT` (`appointment`, `conversation`, `digest`, `orchestrator`,
  `rewrite`, `shop_info`) — đi theo thực tế, không theo con số cũ. Bốn trong
  sáu prompt đã có test ASCII-only riêng ở file specialist/agent tương ứng
  (`test_specialist_appointment.py`, `test_orchestrator_agent.py`,
  `test_conversation_agent.py`, `test_specialist_shop_info.py`); hàm ở đây
  kiểm cả sáu bằng MỘT luật thống nhất để không prompt nào lọt lưới sau này.
"""
import re

import pytest

from app.agents.booking.prompts.appointment import APPOINTMENT_PROMPT
from app.agents.booking.prompts.conversation import CONVERSATION_PROMPT
from app.agents.booking.prompts.digest import DIGEST_PROMPT
from app.agents.booking.prompts.orchestrator import ORCHESTRATOR_PROMPT
from app.agents.booking.prompts.rewrite import REWRITE_PROMPT
from app.agents.booking.prompts.shop_info import SHOP_INFO_PROMPT
from app.models.appointment import NOTE_MAX
from app.services.conversation_state import BULLET_MAX_CHARS, DIGEST_CAPS

ALL_PROMPTS = {
    "APPOINTMENT_PROMPT": APPOINTMENT_PROMPT,
    "CONVERSATION_PROMPT": CONVERSATION_PROMPT,
    "DIGEST_PROMPT": DIGEST_PROMPT,
    "ORCHESTRATOR_PROMPT": ORCHESTRATOR_PROMPT,
    "REWRITE_PROMPT": REWRITE_PROMPT,
    "SHOP_INFO_PROMPT": SHOP_INFO_PROMPT,
}

_VIETNAMESE_CHAR = re.compile(
    r"[àáảãạăắằẳẵặâấầẩẫậèéẻẽẹêếềểễệìíỉĩịòóỏõọôốồổỗộơớờởỡợùúủũụưứừửữựỳýỷỹỵđ]", re.I)


@pytest.mark.parametrize("name", sorted(ALL_PROMPTS), ids=sorted(ALL_PROMPTS))
def test_every_prompt_is_english_without_vietnamese_samples(name):
    """Chỉ dẫn gửi cho model là tiếng Anh thuần — không câu mẫu tiếng Việt
    (bẫy #15/#16 của `CONTEXT.md`: luật kèm ví dụ thì model bắt chước cả ví
    dụ, luật chung chung thì bị bỏ qua)."""
    prompt = ALL_PROMPTS[name]
    assert not _VIETNAMESE_CHAR.search(prompt), (name, _VIETNAMESE_CHAR.search(prompt).group())


# --- Task 12c: DIGEST_PROMPT (nén cuốn chiếu 5 mục) ------------------------

def test_digest_prompt_is_english_with_no_vietnamese_samples():
    """Spec mục 3 "Nén giữ lại, đổi cách tóm": prompt nén viết bằng tiếng Anh,
    không câu mẫu tiếng Việt (bẫy #15/#16) — nội dung tiếng Việt chỉ nằm
    trong dữ kiện mà model/khách tạo ra lúc chạy thật, không trong luật.
    Gộp với bất biến "today ở dòng đầu" (CONTEXT.md bẫy #6: model không có
    mốc nào thì neo vào ngày huấn luyện)."""
    assert not _VIETNAMESE_CHAR.search(DIGEST_PROMPT), _VIETNAMESE_CHAR.search(DIGEST_PROMPT).group()
    assert DIGEST_PROMPT.splitlines()[0].startswith("Today is {today}")


def test_digest_prompt_tells_the_model_to_fill_slots_with_iso_days():
    """CONTEXT.md bẫy #6: không đòi `day` dạng YYYY-MM-DD thì model neo vào ngày huấn luyện."""
    assert "slots" in DIGEST_PROMPT.lower()
    assert "YYYY-MM-DD" in DIGEST_PROMPT


@pytest.mark.parametrize("key", sorted(DIGEST_CAPS), ids=sorted(DIGEST_CAPS))
def test_digest_prompt_names_every_cap_the_code_enforces(key):
    """Mỗi khoá `DIGEST_CAPS` phải đổ đúng vào mục cùng tên của prompt đã
    `.format` — chệch khoá (vd. gán nhầm `wants_max` cho `settled`) từng là
    lỗi thật (xem `test_the_prompt_promises_the_same_cap_the_code_enforces`
    ở `test_conversation_state_service.py` cho `service`/`NOTE_MAX`)."""
    formatted = DIGEST_PROMPT.format(
        today="2026-01-01", max_chars=BULLET_MAX_CHARS,
        wants_max=DIGEST_CAPS["wants"], settled_max=DIGEST_CAPS["settled"],
        declined_max=DIGEST_CAPS["declined"], constraints_max=DIGEST_CAPS["constraints"],
        open_max=DIGEST_CAPS["open"], service_max=NOTE_MAX,
        prev_wants="(none)", prev_settled="(none)", prev_declined="(none)",
        prev_constraints="(none)", prev_open="(none)", messages="",
    )
    section_line = next(l for l in formatted.splitlines() if l.strip().startswith(f"- {key}"))
    assert f"at most {DIGEST_CAPS[key]} items" in section_line


class TestRewritePromptDemandsVietnamese:
    """F6: `REWRITE_PROMPT` viết lại câu trả lời cho khách — nó phải mang cùng
    luật ngôn ngữ như các prompt khách-hàng-đối-diện khác, không thì bản viết
    lại có thể lạc tiếng."""

    def test_rewrite_prompt_demands_vietnamese(self):
        assert "MUST be Vietnamese" in REWRITE_PROMPT
