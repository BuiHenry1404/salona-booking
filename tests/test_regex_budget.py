"""Hàng rào regex trong tầng AI Agent: gói `salon` không `import re` ở đâu cả
và không có `re.compile` nào (spec 2026-11-08-subagents-rebuild). Gói agent cũ
(có ngân sách 2 regex) đã xoá ở Task 16 — chỉ còn một gói để quét.
"""
import ast
import pathlib
import re

ROOT_SALON = pathlib.Path(__file__).resolve().parents[1] / "app" / "agents" / "salon"

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


def test_salon_package_has_no_regex():
    actual, re_importers = _scan(ROOT_SALON)
    assert actual == EXPECTED_BUDGET_SALON, f"Vượt ngân sách regex gói salon: {actual}"
    assert re_importers == set(EXPECTED_BUDGET_SALON.keys()), (
        f"Module trong gói salon import re: {re_importers}"
    )
