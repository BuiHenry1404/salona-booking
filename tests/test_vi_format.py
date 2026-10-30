import ast
import pathlib


def test_vi_format_matches_legacy_context_helpers():
    from app.core import vi_format
    from app.agents.booking_graph import context

    assert vi_format.format_vi_datetime is context.format_vi_datetime
    assert vi_format.derive_address is context.derive_address
    assert vi_format._WEEKDAYS[0] == "Thứ Hai"


def test_only_agents_and_the_three_known_services_import_app_agents():
    allowed = {
        "app/services/socketio_service.py",
        "app/services/conversation_state.py",
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
