"""Hàng rào số lượng regex (`re.compile`) trong tầng AI Agent (`app/agents/booking_graph/`).

Spec `2026-10-28-llm-context-input-design.md`: cắt xuống còn đúng 2 regex:
- `guard.py`: 2 (`_TOOL_GROUP`, `_TOOL_BARE` trong `strip_tool_names`)
- Mọi module khác trong `app/agents/booking_graph/` (kể cả `timeparse.py`): 0 regex.
"""
import ast
import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parents[1] / "app" / "agents" / "booking_graph"

EXPECTED_BUDGET = {
    "guard.py": 2,
}


def test_agent_regex_budget_is_at_most_two():
    actual = {}
    re_importers = set()
    for path in sorted(ROOT.rglob("*.py")):
        rel = str(path.relative_to(ROOT))
        src = path.read_text(encoding="utf-8")
        count = len(re.findall(r"re\.compile\(", src))
        if count:
            actual[rel] = count
        tree = ast.parse(src, filename=rel)
        for node in ast.walk(tree):
            if isinstance(node, ast.Import) and any(a.name == "re" for a in node.names):
                re_importers.add(rel)
            elif isinstance(node, ast.ImportFrom) and node.module == "re":
                re_importers.add(rel)
    assert actual == EXPECTED_BUDGET, f"Vượt ngân sách regex tầng agent: {actual}"
    assert re_importers == set(EXPECTED_BUDGET.keys()), (
        f"Module ngoài ngân sách import re: {re_importers}"
    )
