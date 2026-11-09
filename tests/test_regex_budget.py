"""Hàng rào số lượng regex (`re.compile`) trong tầng AI Agent.

Spec `2026-10-29-llm-orchestrator-design.md`: `app/agents/booking/` cắt xuống
còn đúng 2 regex (`_TOOL_GROUP`, `_TOOL_BARE` trong `strip_tool_names`, dời từ
`guard.py` của gói cũ ở Task 6.1 sang `draft_checks.py`). Mọi module khác
(kể cả `timeparse.py`) — 0 regex. Task 6.3 (M6-cutover) xoá hẳn gói
supervisor graph cũ — chỉ còn một gói để quét.
"""
import ast
import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parents[1] / "app" / "agents" / "booking"
ROOT_SALON = pathlib.Path(__file__).resolve().parents[1] / "app" / "agents" / "salon"

EXPECTED_BUDGET = {
    "draft_checks.py": 2,
}
EXPECTED_BUDGET_SALON: dict[str, int] = {}


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


def test_salon_package_has_no_regex():
    actual, re_importers = _scan(ROOT_SALON)
    assert actual == EXPECTED_BUDGET_SALON, f"Vượt ngân sách regex gói salon: {actual}"
    assert re_importers == set(EXPECTED_BUDGET_SALON.keys()), (
        f"Module trong gói salon import re: {re_importers}"
    )
