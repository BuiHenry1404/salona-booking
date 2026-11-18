import ast
import pathlib


def test_only_socketio_and_main_import_app_agents():
    allowed = {
        "app/services/socketio_service.py",
        "main.py",
    }
    root = pathlib.Path(__file__).resolve().parents[1]
    hits = set()
    for p in list((root / "app").rglob("*.py")) + [root / "main.py"]:
        rel = str(p.relative_to(root))
        if rel.startswith("app/agents/"):
            continue
        for node in ast.walk(ast.parse(p.read_text(encoding="utf-8"))):
            if isinstance(node, ast.ImportFrom) and (node.module or "").startswith("app.agents"):
                hits.add(rel)
    assert hits == allowed
