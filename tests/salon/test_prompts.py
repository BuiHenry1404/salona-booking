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
from app.agents.salon.orchestrator.prompt import ORCHESTRATOR_PROMPT
from app.agents.salon.subagents.appointment.prompt import APPOINTMENT_PROMPT

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


def _flat(prompt: str) -> str:
    """Prompts are hard-wrapped; compare phrases on one line."""
    return " ".join(prompt.split())


def test_orchestrator_prompt_forbids_markdown():
    assert "no Markdown" in _flat(ORCHESTRATOR_PROMPT)


def test_appointment_prompt_never_reports_a_taken_time_as_ok():
    flat = _flat(APPOINTMENT_PROMPT)
    assert "or the information asked for is in the tool results" not in flat
    assert "turned out taken and no proposal was created, the result is never ok" in flat


def test_orchestrator_prompt_has_no_segment_rules():
    flat = _flat(ORCHESTRATOR_PROMPT)
    assert "segment" not in flat
    assert "Reply facts" not in flat
    assert "ref and role" not in flat


def test_orchestrator_prompt_names_plan_keys():
    flat = _flat(ORCHESTRATOR_PROMPT)
    for token in ("<reply_plan>", "<context>", "taken_told_by_code", "`acts`", "`ask`",
                  "remember_address"):
        assert token in flat, token


def test_orchestrator_prompt_allows_context_facts_beyond_the_plan():
    flat = _flat(ORCHESTRATOR_PROMPT)
    assert "in neither the reply plan nor the context block" in flat


def test_orchestrator_prompt_asks_which_offered_time_suits():
    flat = _flat(ORCHESTRATOR_PROMPT)
    assert "`offer`" in flat
    assert "ask the customer which one suits them" in flat


def _descriptions(schema) -> list[str]:
    """Every `description` string in a JSON schema (tool and field descriptions).
    Enum VALUES (e.g. remember_address forms) are turn data, not descriptions."""
    if isinstance(schema, dict):
        found = [schema["description"]] if isinstance(schema.get("description"), str) else []
        return found + [d for value in schema.values() for d in _descriptions(value)]
    if isinstance(schema, list):
        return [d for value in schema for d in _descriptions(value)]
    return []


def _accented(text: str) -> list[str]:
    return sorted({ch for ch in text.lower() if ch in _VIETNAMESE})


def test_orchestrator_tool_descriptions_are_english():
    """Review F5: every Orchestrator tool description is model-facing text."""
    from app.agents.salon.orchestrator.tools import make_orchestrator_tools

    tools = make_orchestrator_tools(None, None, None)
    assert {tool.name for tool in tools} >= {"appointment_task", "remember_address"}
    for tool in tools:
        texts = [tool.description, *_descriptions(tool.tool_call_schema.model_json_schema())]
        for text in texts:
            assert not _accented(text), (tool.name, text)


def test_guard_hints_are_english():
    """Review F5: every rewrite hint is model-facing text."""
    from app.agents.salon.guard.validator import CODES
    from app.agents.salon.orchestrator.middleware.reply_guard import HINTS

    assert set(HINTS) == set(CODES)
    for code, hint in HINTS.items():
        assert not _accented(hint), (code, hint)


def test_taken_notes_leave_the_taken_sentence_to_the_code():
    """Review F1: the appointment notes to the Orchestrator must not ask it to
    tell the customer about the taken time (the code does, `taken_mentioned`)."""
    from app.agents.salon.subagents.appointment.agent import TAKEN_ASK_OFFERED, TAKEN_ASK_OTHER

    for note in (TAKEN_ASK_OFFERED, TAKEN_ASK_OTHER):
        assert "Tell the customer" not in note
        assert "the code tells the customer" in note
        assert not _accented(note)
    assert TAKEN_ASK_OFFERED.endswith("Ask which of the offered times they want.")
    assert TAKEN_ASK_OTHER.endswith("Ask which other time they want.")


def test_orchestrator_prompt_limits_services_and_prices_to_plan_and_tool_results():
    """Review F2: the validator allows only this turn's services and prices."""
    flat = _flat(ORCHESTRATOR_PROMPT)
    assert ("name a service or price only if it is in the reply plan or this turn's tool "
            "results (the `Facts this turn` lines)") in flat
    assert "add no time, price or service that is in neither" not in flat


def test_orchestrator_prompt_checks_every_named_time_with_appointment_task():
    """Live try 15/11 (L2): a time the customer names is checked this turn."""
    flat = _flat(ORCHESTRATOR_PROMPT)
    assert ("When the customer names a time they want to book, always call appointment_task "
            "in that turn, even if they also ask something else") in flat
    assert "never offer or promise a time that appointment_task did not check in this turn" in flat


def test_orchestrator_prompt_lets_the_customers_own_form_of_address_win():
    """Live try 15/11 (L3a): the customer's own self-reference beats the Address line."""
    flat = _flat(ORCHESTRATOR_PROMPT)
    assert ("When the customer refers to themselves with a form of address different from the "
            "Address line, their own words win: call remember_address with that form") in flat
