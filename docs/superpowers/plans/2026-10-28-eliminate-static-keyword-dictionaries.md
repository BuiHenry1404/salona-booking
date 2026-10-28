# Eliminate Static Keyword Dictionaries Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Loại bỏ triệt để 100% các bộ từ điển từ khóa chuỗi tĩnh (`_DEFER_PHRASES_*`, `_REQUEST_PHRASES_*`, `_NEG_PHRASES_*`, `_GO_VERBS`, `veto_reason`) trong `relatives.py` và `reply.py`, ủy thác hoàn toàn việc hiểu ngữ nghĩa tự do cho `TurnAnalysis` (Structured Output LLM, `temperature=0.0`) với cơ chế fail-closed an toàn.

**Architecture:** Giữ nguyên tắc rạch ròi: Code chịu trách nhiệm 100% về State Machine, Hàng rào Write Fence (`ProposalService.approve`), kiểm tra hạn 10 phút, và xử lý nút bấm UI; LLM chịu trách nhiệm 100% về hiểu ngữ nghĩa tự do. Node `confirm` và `supervisor` không dùng heuristic so khớp chuỗi mà đọc trực tiếp kết quả có cấu trúc từ `TurnAnalysis`.

**Tech Stack:** Python 3.12, FastAPI, LangGraph, Pydantic v2, Motor (MongoDB), Pytest, Azure OpenAI (`gpt-5.4-mini`).

## Global Constraints

- **Single worker:** Đúng 1 process worker, không `--workers`.
- **Config source:** `.env` là nguồn cấu hình duy nhất.
- **Write fence:** Agent tools KHÔNG BAO GIỜ ghi Appointment trực tiếp. Chỉ `ProposalService.approve(user, proposal_id)` ghi sau khi có quyết định `approve`.
- **Regex budget:** Tối đa 2 regex trên toàn bộ agent codebase (nằm trong `guard.py`).
- **Fail-closed:** Nếu LLM lỗi/timeout ở bước xác nhận, fallback là `unclear` (hỏi lại khách 1 câu rõ ràng), tuyệt đối không tự ý duyệt hay ghi đè DB.
- **Language convention:** Mã nguồn, chú thích, tài liệu dùng tiếng Việt; commit message bằng tiếng Anh ngắn gọn.

---

### Task 1: Dọn dẹp `relatives.py` & Cập nhật Test Suite liên quan

**Files:**
- Modify: `app/agents/booking_graph/relatives.py`
- Modify: `tests/test_relatives.py`

**Interfaces:**
- Consumes: `TurnAnalysis` từ `app.agents.booking_graph.analyzer`
- Produces: 
  - `aside_line(text: str, analysis: Optional[Mapping[str, Any]] = None) -> str`
  - `deferral_reply(text: str, address: str, previous_replies: Sequence[str], analysis: Optional[Mapping[str, Any]] = None) -> Optional[str]`

- [ ] **Step 1: Viết test failing cho `relatives.py` dựa trên `analysis.aside`**

Trong `tests/test_relatives.py`, đảm bảo `aside_line` và `deferral_reply` hoạt động đúng theo nhãn `aside` của `analysis` và loại bỏ các bài test gọi hàm chuỗi cũ (`defers`, `has_request`, `asks_if_relative_free`):

```python
def test_aside_line_purely_reads_analysis():
    assert "[relative]" in aside_line("câu bất kỳ", analysis={"aside": "relative_free"})
    assert "[deferral]" in aside_line("câu bất kỳ", analysis={"aside": "defer_only"})
    assert aside_line("câu bất kỳ", analysis={"aside": "none"}) == ""
    assert aside_line("câu bất kỳ", analysis=None) == ""

def test_deferral_reply_purely_reads_analysis():
    reply = deferral_reply("câu bất kỳ", "chị Lan", [], analysis={"aside": "defer_only"})
    assert reply and "chị Lan" in reply and "nhắn" in reply
    assert deferral_reply("câu bất kỳ", "chị Lan", [], analysis={"aside": "defer_with_request"}) is None
    assert deferral_reply("câu bất kỳ", "chị Lan", [], analysis={"aside": "none"}) is None
```

- [ ] **Step 2: Chạy test để thấy test fail**

Run: `.venv/bin/pytest tests/test_relatives.py -v`
Expected: FAIL hoặc phát hiện các tham chiếu cũ cần loại bỏ.

- [ ] **Step 3: Cập nhật `app/agents/booking_graph/relatives.py`**

Xóa bỏ hoàn toàn:
- `_PERSONS_ACC`, `_PERSONS_BARE`, `_SHOP_WORDS`
- `_DEFER_VERBS_ACC`, `_DEFER_VERBS_BARE`, `_DEFER_PHRASES_ACC`, `_DEFER_PHRASES_BARE`
- `_REQUEST_PHRASES_ACC`, `_REQUEST_VERBS_BARE`, `_REQUEST_OBJS_BARE`, `_REQUEST_PHRASES_BARE`
- `_person_near_free`, `asks_if_relative_free`, `_has_de_verb`, `defers`, `has_request`

Giữ lại các hằng số dòng bối cảnh và hàm sinh câu đáp code:
```python
from typing import Any, Mapping, Optional, Sequence
from app.agents.booking_graph import guard

RELATIVE_LINE = ("Khách vừa hỏi NGƯỜI NHÀ/NGƯỜI QUEN của khách có rảnh không — không phải "
                 "hỏi chủ tiệm bận hay rảnh, không phải hỏi lịch của khách khác. [relative]")
DEFERRAL_LINE = ("Khách nói sẽ hỏi lại/tính lại rồi nhắn em sau — đây là chuyện lịch của "
                 "chính khách; chỉ đáp một câu ngắn, mời khách nhắn lại. [deferral]")

_DEFERRAL_REPLIES = (
    "Dạ vâng, {address} cứ thong thả, xong thì nhắn em nhé.",
    "Dạ, khi nào {address} sẵn sàng thì nhắn lại em là được ạ.",
    "Dạ không sao ạ, có gì {address} cứ nhắn em nhé.",
)

def aside_line(text: str, analysis: Optional[Mapping[str, Any]] = None) -> str:
    aside = (analysis or {}).get("aside") if isinstance(analysis, Mapping) else getattr(analysis, "aside", None)
    lines = []
    if aside == "relative_free":
        lines.append(RELATIVE_LINE)
    elif aside in ("defer_only", "defer_with_request"):
        lines.append(DEFERRAL_LINE)
    return " ".join(lines)

def deferral_reply(text: str, address: str, previous_replies: Sequence[str],
                   analysis: Optional[Mapping[str, Any]] = None) -> Optional[str]:
    aside = (analysis or {}).get("aside") if isinstance(analysis, Mapping) else getattr(analysis, "aside", None)
    if aside != "defer_only":
        return None
    options = [r.format(address=address) for r in _DEFERRAL_REPLIES]
    previous = [p for p in previous_replies or [] if p]
    return next((o for o in options if o not in previous and not guard.repeats(o, previous)), options[-1])
```

- [ ] **Step 4: Chạy test để xác nhận pass**

Run: `.venv/bin/pytest tests/test_relatives.py tests/test_regex_budget.py -v`
Expected: PASS (48/48 passed, regex budget $\le 2$).

- [ ] **Step 5: Commit Task 1**

```bash
git add app/agents/booking_graph/relatives.py tests/test_relatives.py
git commit -m "refactor(relatives): eliminate static keyword dictionaries and string matching"
```

---

### Task 2: Tinh giản `reply.py` & Chuẩn hoá Quyết định tại Node `confirm`

**Files:**
- Modify: `app/agents/booking_graph/reply.py`
- Modify: `app/agents/booking_graph/confirm.py`
- Modify: `tests/test_reply_veto.py`

**Interfaces:**
- Consumes: `TurnAnalysis` từ `analyzer.py`, `Proposal` từ `app.models.proposal`
- Produces: Quyết định tại Node `confirm` (`approve`, `reject`, `change`, `unclear`) dựa trên semantic label thay vì heuristic veto.

- [ ] **Step 1: Viết test failing cho quyết định Node `confirm` thuần semantic**

Trong `tests/test_confirm.py` hoặc `tests/test_reply_veto.py`, kiểm tra các tình huống khách gõ chữ tự do:
- Khách gõ câu có từ "xem lại" như *"chị xem lại lịch hẹn của chị với"* $\rightarrow$ Không bị veto hoãn, chuyển tiếp phân tích ngữ nghĩa chính xác.
- Khách gõ câu đồng ý gián tiếp $\rightarrow$ Nhận diện `approve`.
- Khách gõ câu đổi giờ $\rightarrow$ Nhận diện `change`.

- [ ] **Step 2: Chạy test để xác nhận fail**

Run: `.venv/bin/pytest tests/test_reply_veto.py -v`
Expected: FAIL trước khi cập nhật `reply.py` và `confirm.py`.

- [ ] **Step 3: Loại bỏ từ điển tĩnh trong `reply.py`**

Trong `app/agents/booking_graph/reply.py`:
- Xóa bỏ `_NEG_PHRASES_*`, `_NEG_WORDS_*`, `_GO_VERBS`, `_GO_AGREE`, `_GO_FILLER`, `_QUESTION_WORDS`, `_QUESTION_TAILS`.
- Xóa bỏ `_has_negation`, `_has_deferral`, `_is_go_ahead`, `_is_question`, `veto_reason`.
- Giữ lại các tiện ích cấu trúc thời gian thuần logic: `mentions_other_time`, `_describe`, `_as_datetime`.
- Tinh giản hàm `decide`: Gọi trực tiếp `label_fn` (chạy `analyzer.analyze_turn`) để lấy quyết định ngữ nghĩa.

- [ ] **Step 4: Cập nhật Node `confirm.py` để sử dụng trực tiếp `TurnAnalysis`**

Trong `app/agents/booking_graph/confirm.py`:
- Nếu resume từ nút bấm (`isinstance(text, dict)`): Xử lý bằng code thuần qua `_handle_button`.
- Nếu resume từ chữ gõ tự do:
  - Gọi `analyzer.analyze_turn(proposal_facts=facts, bot_question=question, customer_text=text)`.
  - Phân nhánh:
    - Nếu `analysis.aside == "defer_only"`: `decision = "reject"` (hoãn quyết định, bot đáp câu hoãn lịch sự qua `deferral_reply`).
    - Nếu `analysis.reply_to_proposal in ("approve", "reject", "change")`: Dùng trực tiếp nhãn này.
    - Nếu `analysis.reply_to_proposal == "unclear"` (hoặc LLM timeout/fallback): `decision = "unclear"`, hỏi lại 1 câu rõ ràng.

- [ ] **Step 5: Chạy test để xác nhận pass**

Run: `.venv/bin/pytest tests/test_confirm.py tests/test_reply_veto.py tests/test_mw_confirm.py -v`
Expected: PASS.

- [ ] **Step 6: Commit Task 2**

```bash
git add app/agents/booking_graph/reply.py app/agents/booking_graph/confirm.py tests/test_reply_veto.py
git commit -m "refactor(confirm): remove static string veto and use TurnAnalysis directly"
```

---

### Task 3: Dọn dẹp `supervisor.py` & `make_agent_node`

**Files:**
- Modify: `app/agents/booking_graph/supervisor.py`
- Modify: `app/agents/booking_graph/agents.py`

**Interfaces:**
- Consumes: `TurnAnalysis` từ `analyzer.py`
- Produces: Routing thuần túy dựa trên `TurnAnalysis.route` và `TurnAnalysis.aside`.

- [ ] **Step 1: Viết test failing cho `supervisor` không dùng heuristic string**

Trong `tests/test_supervisor.py`:
- Đảm bảo supervisor route hoàn toàn dựa vào kết quả của mô hình phân tích `TurnAnalysis.route` mà không cần gọi các hàm quét chuỗi cũ `asks_if_relative_free` hay `defers`.

- [ ] **Step 2: Chạy test để xác nhận fail**

Run: `.venv/bin/pytest tests/test_supervisor.py -v`
Expected: FAIL hoặc báo lỗi import nếu hàm cũ đã bỏ.

- [ ] **Step 3: Cập nhật `supervisor.py` & `agents.py`**

Trong `app/agents/booking_graph/supervisor.py`:
- Loại bỏ các import `asks_if_relative_free`, `defers`.
- Định tuyến trực tiếp:
```python
route = (analysis or {}).get("route") or "booking"
return {"route": route}
```

Trong `app/agents/booking_graph/agents.py`:
- Trong `make_agent_node`: Cập nhật logic xử lý `aside` để đọc trực tiếp từ `state.get("analysis")` thay vì quét chuỗi `customer_text`.

- [ ] **Step 4: Chạy test để xác nhận pass**

Run: `.venv/bin/pytest tests/test_supervisor.py tests/test_agents.py tests/test_graph_wiring.py -v`
Expected: PASS.

- [ ] **Step 5: Commit Task 3**

```bash
git add app/agents/booking_graph/supervisor.py app/agents/booking_graph/agents.py tests/test_supervisor.py
git commit -m "refactor(supervisor): route purely via TurnAnalysis without string heuristics"
```

---

### Task 4: Bổ sung Kiểm thử Ngữ nghĩa Toàn diện trong `tests/test_analyzer.py`

**Files:**
- Modify: `tests/test_analyzer.py`

**Interfaces:**
- Kiểm tra tính đúng đắn và độ bền của prompt và schema `TurnAnalysis` đối với tất cả các ngữ cảnh trước đây từng dùng từ điển tĩnh.

- [ ] **Step 1: Viết các test case kiểm tra phân loại ngữ nghĩa**

Thêm vào `tests/test_analyzer.py`:
1. **Câu đồng ý đa dạng**: *"ừ em", "chốt cho chị", "ok làm đi", "đặt giờ đó nha"* $\rightarrow$ `reply_to_proposal == "approve"`.
2. **Câu từ chối**: *"thôi bận rồi", "không làm nữa", "hủy đi em"* $\rightarrow$ `reply_to_proposal == "reject"`.
3. **Câu đổi giờ**: *"đổi sang 4h chiều mai", "thôi 5h đi em"* $\rightarrow$ `reply_to_proposal == "change"`.
4. **Câu hoãn**: *"để chị xem lại đã", "để cô hỏi thằng Huy đã"* $\rightarrow$ `aside == "defer_only"`.
5. **Câu hỏi người nhà**: *"thằng cháu có rảnh không em"* $\rightarrow$ `aside == "relative_free"`.
6. **Câu tra cứu lịch (Edge Case)**: *"chị xem lại lịch hẹn của chị với"* $\rightarrow$ `route == "booking"`, `aside == "none"`, không bị nhầm thành hoãn.

- [ ] **Step 2: Chạy test xác nhận**

Run: `.venv/bin/pytest tests/test_analyzer.py -v`
Expected: PASS.

- [ ] **Step 3: Commit Task 4**

```bash
git add tests/test_analyzer.py
git commit -m "test(analyzer): add comprehensive semantic test coverage for intents and asides"
```

---

### Task 5: End-to-End Regression & Kiểm thử Trực tiếp 10 Lượt LLM

**Files:**
- Run: Full test suite (`pytest`)
- Run: `scripts/test_live_10_turns.py`

- [ ] **Step 1: Chạy kiểm thử hàng rào an toàn Write Fence**

Run: `.venv/bin/pytest tests/test_write_fence.py -v`
Expected: PASS (0 writes without approval).

- [ ] **Step 2: Chạy kiểm thử toàn bộ test suite**

Run: `.venv/bin/pytest`
Expected: PASS (toàn bộ 1682 tests xanh).

- [ ] **Step 3: Chạy kịch bản 10 lượt LLM trực tiếp trên DB sạch**

Run: `.venv/bin/python scripts/test_live_10_turns.py`
Expected: 10/10 lượt thành công (0 errors), xác nhận lượt 10 *"chị xem lại lịch hẹn"* tra cứu đúng lịch hẹn mà không bị kích hoạt câu hoãn.

- [ ] **Step 4: Commit hoàn tất Plan**

```bash
git add docs/superpowers/plans/2026-10-28-eliminate-static-keyword-dictionaries.md
git commit -m "docs(plan): complete plan to eliminate static keyword dictionaries"
```
