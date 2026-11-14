"""Tên tool kỹ thuật của gói — một nguồn duy nhất cho mọi nơi cần biết chúng:
luật tool của Orchestrator (`orchestrator/tools.py` re-export `CONTROL_TOOLS`)
và bộ lọc chữ của guard (`guard/checks.py::strip_tool_text`, xoá tên tool lọt
vào câu tới khách). Ở `shared/` vì `guard/` không được import `orchestrator/`
(luật phụ thuộc của gói).

`SUBAGENT_TOOLS` phải phủ mọi tool agent con dựng ra —
`tests/salon/guard/test_checks.py::test_tool_name_set_covers_every_subagent_tool`
canh, thêm tool mới thì thêm tên vào đây.
"""

# Tool điều khiển của Orchestrator — luật tool và bộ lọc chữ dùng chung một tập.
CONTROL_TOOLS = frozenset({
    "appointment_task", "shop_info_task", "service_task", "decide_pending", "remember_address",
})

SUBAGENT_TOOLS = frozenset({
    # dùng chung (subagents/time_tool.py)
    "parse_time",
    # agent Đặt lịch
    "find_free_slots", "remember_next_request", "list_my_appointments",
    "propose_appointment", "propose_cancel",
    # agent Thông tin tiệm
    "get_shop_hours", "get_shop_status", "is_open_at",
    # agent Dịch vụ & giá
    "search_services", "get_service", "list_services",
})
