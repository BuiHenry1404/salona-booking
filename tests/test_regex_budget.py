"""Hàng rào số lượng regex (`re.compile`) trong tầng AI Agent (`app/agents/booking_graph/`).

Spec `2026-10-27-regex-reduction-design.md`: cắt từ 45 xuống còn đúng 3 regex:
- `timeparse.py`: 1 (`_FULL` theo bẫy #11 CONTEXT.md)
- `guard.py`: 2 (`_TOOL_GROUP`, `_TOOL_BARE` trong `strip_tool_names`)
- Mọi module khác trong `app/agents/booking_graph/`: 0 regex.
"""
import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parents[1] / "app" / "agents" / "booking_graph"

EXPECTED_BUDGET = {
    "timeparse.py": 1,
    "guard.py": 2,
}


def test_agent_regex_budget_is_at_most_three():
    actual = {}
    for path in sorted(ROOT.rglob("*.py")):
        rel = str(path.relative_to(ROOT))
        count = len(re.findall(r"re\.compile\(", path.read_text(encoding="utf-8")))
        if count:
            actual[rel] = count
    assert actual == EXPECTED_BUDGET, f"Vượt ngân sách regex tầng agent: {actual}"
