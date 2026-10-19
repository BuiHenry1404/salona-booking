"""Hàng rào đường ghi (M3): trong `app/agents`, CHỈ node `confirm` được gọi
`ProposalService.approve`, và không chỗ nào gọi thẳng phương thức ghi của
`AppointmentService` (create / reschedule / cancel). Tool chỉ được TẠO đề xuất.

Quét AST mọi lời gọi `.create(` / `.cancel(` / `.reschedule(` / `.approve(`
trong `app/agents` và so với danh sách cho phép tường minh. Thêm một lời gọi mới
là test đỏ — người thêm phải giải thích vì sao nó không phải đường ghi.
"""
import ast
import pathlib

ROOT = pathlib.Path(__file__).resolve().parents[1]
WRITE_METHODS = {"create", "cancel", "reschedule", "approve"}

# (file, chuỗi receiver, method) — mỗi dòng là một lời gọi đã duyệt.
ALLOWED = {
    # Tool tạo ĐỀ XUẤT (không phải lịch) — ProposalService.create.
    ("app/agents/booking_graph/tools.py", "proposals", "create"),
    # Cổng ghi duy nhất của chat, chỉ sau khi lưới + bộ phân loại cùng approve.
    ("app/agents/booking_graph/confirm.py", "ProposalService(db)", "approve"),
    # Huỷ asyncio.Task của lượt cũ (supersede), không phải huỷ lịch.
    ("app/agents/booking_graph/events.py", "previous", "cancel"),
}


def write_calls():
    found = set()
    for path in (ROOT / "app" / "agents").rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                    and node.func.attr in WRITE_METHODS):
                found.add((str(path.relative_to(ROOT)), ast.unparse(node.func.value),
                           node.func.attr))
    return found


def test_only_the_confirm_node_reaches_the_write_gate():
    assert write_calls() == ALLOWED


def test_the_fence_actually_sees_calls():
    """Tự kiểm: nếu bộ quét hỏng (0 lời gọi) thì test trên vẫn có thể xanh giả."""
    assert ("app/agents/booking_graph/confirm.py", "ProposalService(db)", "approve") in write_calls()


def test_services_approve_is_the_only_appointment_writer_in_the_proposal_module():
    """ProposalService chỉ ghi lịch bên trong `approve` (qua `_execute`)."""
    source = (ROOT / "app" / "services" / "proposal.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    writers = set()
    for fn in ast.walk(tree):
        if isinstance(fn, ast.AsyncFunctionDef):
            for node in ast.walk(fn):
                if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                        and ast.unparse(node.func.value) == "self.appointments"
                        and node.func.attr in {"create", "cancel", "reschedule"}):
                    writers.add(fn.name)
    assert writers == {"_execute"}
