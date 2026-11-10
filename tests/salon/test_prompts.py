"""Hàng rào cho prompt của gói `app/agents/salon` (thay `tests/test_prompts.py`
của gói cũ — CONTEXT.md bẫy 17-19, mục Xưng hô).

Prompt được TÌM TỰ ĐỘNG: mọi hằng `*_PROMPT` (chuỗi) ở mọi module của gói —
prompt mới (Task 14, 17...) tự vào lưới, không phải nhớ thêm vào danh sách.
"""
from __future__ import annotations

import importlib
import pkgutil

import pytest

import app.agents.salon as salon_package

# Bộ chữ có dấu tiếng Việt — một ký tự là đủ để biết prompt có câu mẫu tiếng Việt.
_VIETNAMESE = set(
    "àáảãạăắằẳẵặâấầẩẫậèéẻẽẹêếềểễệìíỉĩịòóỏõọôốồổỗộơớờởỡợùúủũụưứừửữựỳýỷỹỵđ"
)
# Cách gọi cấm ở mọi nơi khách thấy (CONTEXT.md mục Xưng hô); cô/chú/bác đã có
# dấu nên lọt lưới `_VIETNAMESE`, còn "con" thì không.
_FORBIDDEN_ADDRESS = {"con", "cô", "chú", "bác"}
# Prompt viết câu TỚI KHÁCH phải dặn gọi khách theo dòng `Address` của bối cảnh.
_CUSTOMER_FACING = {"ORCHESTRATOR_PROMPT"}


def _all_prompts() -> dict[str, str]:
    """Mỗi prompt MỘT lần, theo module định nghĩa nó (`prompt.py` / `prompts.py`)
    — module chỉ import lại hằng số thì bỏ qua."""
    found: dict[str, str] = {}
    for info in pkgutil.walk_packages(salon_package.__path__, prefix="app.agents.salon."):
        module = importlib.import_module(info.name)
        for name, value in vars(module).items():
            if not (name.endswith("_PROMPT") and isinstance(value, str)):
                continue
            same = [key for key, seen in found.items() if seen is value]
            if same and not info.name.rsplit(".", 1)[1].startswith("prompt"):
                continue
            for key in same:
                del found[key]
            found[f"{info.name}.{name}"] = value
    return found


PROMPTS = _all_prompts()


def _words(text: str) -> set[str]:
    cleaned = "".join(ch.lower() if ch.isalpha() else " " for ch in text)
    return set(cleaned.split())


def test_every_known_prompt_is_discovered():
    names = {key.rsplit(".", 1)[1] for key in PROMPTS}
    assert {"ORCHESTRATOR_PROMPT", "SUMMARY_PROMPT", "APPOINTMENT_PROMPT",
            "SHOP_INFO_PROMPT", "SERVICE_PROMPT"} <= names


@pytest.mark.parametrize("key", sorted(PROMPTS), ids=sorted(PROMPTS))
def test_prompts_are_english_without_vietnamese_samples(key):
    prompt = PROMPTS[key]
    accented = sorted({ch for ch in prompt.lower() if ch in _VIETNAMESE})
    assert not accented, (key, accented)
    assert not (_words(prompt) & _FORBIDDEN_ADDRESS), (key, _words(prompt) & _FORBIDDEN_ADDRESS)
    if key.rsplit(".", 1)[1] in _CUSTOMER_FACING:
        assert "Address" in prompt, key
