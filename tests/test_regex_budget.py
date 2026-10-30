"""Hàng rào số lượng regex (`re.compile`) trong tầng AI Agent.

Spec `2026-10-28-llm-context-input-design.md`: `app/agents/booking_graph/` cắt
xuống còn đúng 2 regex:
- `guard.py`: 2 (`_TOOL_GROUP`, `_TOOL_BARE` trong `strip_tool_names`)
- Mọi module khác (kể cả `timeparse.py`): 0 regex.

`app/agents/booking/` (gói mới thay thế dần `booking_graph`, Task 1.1+) bắt
đầu với ngân sách 0 — không module nào được import `re`.
"""
import ast
import pathlib
import re

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1] / "app" / "agents"

ROOT = REPO_ROOT / "booking_graph"
ROOT2 = REPO_ROOT / "booking"

EXPECTED_BUDGET = {
    "guard.py": 2,
}

EXPECTED_BUDGET_BOOKING: dict[str, int] = {}


def _scan(root: pathlib.Path) -> tuple[dict[str, int], set[str]]:
    """Đếm `re.compile(` và thu module import `re`, ở mọi file `.py` dưới `root`."""
    actual: dict[str, int] = {}
    re_importers: set[str] = set()
    for path in sorted(root.rglob("*.py")):
        rel = str(path.relative_to(root))
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
    return actual, re_importers


def test_agent_regex_budget_is_at_most_two():
    actual, re_importers = _scan(ROOT)
    assert actual == EXPECTED_BUDGET, f"Vượt ngân sách regex tầng agent: {actual}"
    assert re_importers == set(EXPECTED_BUDGET.keys()), (
        f"Module ngoài ngân sách import re: {re_importers}"
    )


def test_new_package_has_no_regex():
    """`app/agents/booking/` (gói orchestrator mới) không dùng regex chút nào."""
    actual, re_importers = _scan(ROOT2)
    assert actual == EXPECTED_BUDGET_BOOKING, f"Vượt ngân sách regex gói mới: {actual}"
    assert re_importers == set(), f"Module trong gói mới import re: {re_importers}"
