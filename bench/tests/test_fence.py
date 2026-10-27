"""Hộp đen: không module nào trong bench/ được import `app`."""

import ast
from pathlib import Path

BENCH_ROOT = Path(__file__).resolve().parent.parent


def _imported_top_level_names(tree: ast.Module) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                names.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                names.add(node.module.split(".")[0])
    return names


def test_no_bench_module_imports_app():
    offenders = []
    for path in BENCH_ROOT.rglob("*.py"):
        if ".venv" in path.parts or ".worktrees" in path.parts:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        names = _imported_top_level_names(tree)
        if any(name == "app" or name.startswith("app") for name in names):
            offenders.append(str(path))

    assert not offenders, f"Các file sau import 'app' (vi phạm hộp đen): {offenders}"
