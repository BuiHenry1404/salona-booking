"""Bộ nhớ của Orchestrator (spec §3.5): prompt nén lịch sử ngắn hạn
(`SummarizationMiddleware`, Task 13); bộ nhớ dài hạn về khách qua `store.py`
(singleton `BaseStore`) và `long_term.py` (`CustomerMemory`, `read_memory`,
`render_memory`, `sanitize_memory`, bước gom `consolidate_memory` — Task 14)."""
