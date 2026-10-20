# Harness cho mỗi lượt chat của agent — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Gom mọi luật quanh `pending_confirmation` vào một `TurnHarness` mỗi lượt, thay `is_affirmative` bằng bộ phân loại ba trạng thái tất định, và cho supervisor trả `intent` — để agent không còn ghi/hủy/dời lịch trái ý khách.

**Architecture:** Hai module mới trong `app/services/`: `consent.py` (hàm thuần `classify_reply`) và `turn_harness.py` (`TurnHarness` — chỗ DUY NHẤT phía agent được ghi pending và gọi `create`/`cancel`/`reschedule`). `run_turn` tạo harness mỗi lượt, truyền vào `build_graph`; tool, `confirm`, `guard` và node supervisor nhận nó qua closure như cách `user` đang được truyền. Cuối lượt `harness.finish(answer)` chỉ giữ pending nếu câu trả lời nhắc đúng giờ đang giữ.

**Tech Stack:** Python 3.12, LangGraph, LangChain tools, Motor (MongoDB), pytest + pytest-asyncio (`asyncio_mode = "auto"`).

**Spec:** `docs/superpowers/specs/2026-10-04-agent-turn-harness-design.md` — đọc trước khi bắt đầu. Review gốc: `docs/AGENT_GRAPH_REVIEW.md` (mã G-xx).

## Global Constraints

- Nhánh: `feat/agent-turn-harness`, rẽ từ `docs/arch-security-review` (nhánh này chứa spec và 10 commit của `fix/clock-and-dead-data` chưa vào `henry/develop`). Xong việc **không** tự merge; merge vào `henry/develop` chỉ khi người dùng bảo. Không push.
- **Cấm `git add -A` và `git add .`** — stage đúng đường dẫn. Các file `hoi-thoai-dai-*.txt` ở gốc repo **không thuộc việc này**, đừng chạm.
- Mongo phải chạy: `docker compose up -d mongo`. Chạy test: `PYTHONPATH=. .venv/bin/python -m pytest -q` từ gốc repo. **Chỉ MỘT tiến trình pytest một lúc** (hai tiến trình tranh cùng `chatbot_test_db` ra lỗi pymongo giả). Trước Task 1 chạy cả bộ một lần và ghi mốc (khoảng 805 collected, 14 deselected; một test `tests/test_telegram_handlers.py` tự skip sau 3 giờ chiều giờ VN — bình thường).
- **Không gọi LLM thật.** `pytest -m llm` và `scripts/probe_supervisor.py` chỉ chạy tay khi người dùng cho phép (tốn tiền).
- **Giữ nguyên:** không có tool ghi lịch; lịch ghi từ giá trị trong Mongo; START → `confirm` đi tắt khi có pending; pending hết hạn 10 phút (`get_pending`); chỉ stream token tag `respond`; thứ tự khối prompt theo `CONTEXT.md` bẫy #9 (system → state → lịch sử → bối cảnh → câu khách). `AppError.message` không đổi. `AppointmentService` không đổi.
- Câu gốc của khách đi nguyên văn tới agent. `consent` và `timeparse` chỉ đọc câu gốc.
- Xưng hô: "em"/"anh"/"chị". Cấm "con", "cô", "chú", "bác" ở mọi chuỗi khách thấy.
- Prompt và docstring tool viết **tiếng Anh**, không câu mẫu tiếng Việt (`tests/test_prompts.py` canh). Comment code viết **tiếng Việt**, giải thích *tại sao*.
- Commit message tiếng Anh, ngắn, kết thúc bằng dòng trống rồi `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.
- Mỗi task: viết test → chạy cho ĐỎ → sửa code → chạy XANH → chạy cả bộ → commit.
- **Một lệch nhỏ có chủ ý so với spec mục 3:** câu "ừ không đổi gì đâu" ra `unclear` (vì "gì" là từ hỏi, bước 2 đứng trước bước 3), không phải `no`. Cả hai đều chuyển sang booking và không ghi lịch; `unclear` giữ pending để booking hỏi lại.

## Review Focus

Những đầu vào spec ngụ ý nhưng dễ cắn người dùng thật — mỗi dòng đã có test ở task sở hữu:

1. **Ngày "5/10" trùng chuỗi con của "15/10"** trong `finish`: câu trả lời nhắc 15/10 không được giữ pending 5/10 → Task 3, `test_day_inside_a_longer_date_does_not_count`.
2. **Lời đồng ý dài kiểu đọc giọng nói** (> 8 từ toàn từ đồng ý/đệm) → `unclear`, không `yes` → Task 2, dòng `"dạ được rồi em ơi đặt giúp chị luôn nha em"` trong bảng.
3. **Lượt lỗi SAU khi đã ghi lịch** (phrase/guard nổ): `finish("")` không được đụng gì, lịch vẫn còn → Task 3, `test_finish_after_commit_touches_nothing`.
4. **Rewrite làm mất giờ khỏi câu hỏi xác nhận**: `finish` phải xét câu CUỐI (sau rewrite), nên pending bị xoá → Task 10, `run_turn` gọi `finish` với `final_state["answer"]`, có test.
5. **Model gọi `propose` lại cùng một giờ trong lượt `unclear`** (pending kế thừa vẫn còn): `hold` vẫn là lần đầu của lượt nên được giữ, pending mới thay pending cũ → Task 3, `test_hold_replaces_an_inherited_pending`.

## File Structure

| File | Trách nhiệm |
|---|---|
| `app/services/consent.py` (mới) | `classify_reply` + `strip_diacritics` — hiểu câu trả lời có/không/chưa rõ |
| `app/services/turn_harness.py` (mới) | `TurnHarness`, `CommitResult`, `mentions`, `claims_done` — 4 luật của lượt |
| `app/services/conversation.py` (sửa) | `set_pending` trả giá trị đã lưu; `take_pending` xoá có điều kiện |
| `app/agents/booking_graph/context.py` (sửa) | `spoken_parts`, `render_turn_lines` |
| `app/agents/booking_graph/supervisor.py` (sửa) | `parse_intent`, `INTENT_ROUTES`; `supervise` trả `route` + `intent` |
| `app/agents/booking_graph/tools.py` (sửa) | `propose`/`cancel` gọi `harness.hold` |
| `app/agents/booking_graph/confirm.py` (sửa) | ba nhánh theo `classify_reply`; xoá `is_affirmative` |
| `app/agents/booking_graph/guard.py` (sửa) | mã vi phạm `claim` |
| `app/agents/booking_graph/agents.py` (sửa) | dòng lượt sau khối bối cảnh; cờ `gave_up` |
| `app/agents/booking_graph/graph.py`, `events.py`, `state.py` (sửa) | nối harness; `finish` trong mọi nhánh |
| `app/agents/booking_graph/prompts.py` (sửa) | nhãn intent; mã lỗi mới; `cancel_failed`; gợi ý `claim` |
| `tests/test_consent.py`, `tests/test_turn_harness.py` (mới) | test tầng 1 và 2 |
| `CONTEXT.md`, `AGENTS.md`, `scripts/probe_supervisor.py` (sửa) | tài liệu và probe |

---

### Task 1: `ConversationService` — pending lưu được so khớp

**Files:**
- Modify: `app/services/conversation.py` (`set_pending`, thêm `take_pending`)
- Test: `tests/test_conversation.py`

**Interfaces:**
- Produces: `ConversationService.set_pending(user_id: str, payload: Optional[dict]) -> Optional[dict]` — trả đúng dict đã lưu (kèm `asked_at` đã cắt về mili giây), `None` khi xoá. `ConversationService.take_pending(user_id: str, asked_at: datetime) -> bool` — xoá pending **chỉ khi** `asked_at` khớp; `True` nếu đã xoá.

- [ ] **Step 1: Viết test**

Thêm vào cuối `tests/test_conversation.py` (file đã có `ConversationService`, `now_utc`, `timedelta`):

```python
async def test_set_pending_returns_what_was_stored(test_db):
    svc = ConversationService(test_db)
    stored = await svc.set_pending("u1", {"start_at": "x", "note": None})
    read_back = await svc.get_pending("u1")
    # Mongo chỉ giữ mili giây: giá trị trả về phải bằng ĐÚNG giá trị đọc lại,
    # không thì take_pending so khớp hụt ở micro giây.
    assert read_back["asked_at"] == stored["asked_at"]
    assert stored["asked_at"].microsecond % 1000 == 0


async def test_take_pending_clears_only_once(test_db):
    svc = ConversationService(test_db)
    stored = await svc.set_pending("u1", {"start_at": "x", "note": None})
    assert await svc.take_pending("u1", stored["asked_at"]) is True
    assert await svc.get_pending("u1") is None
    assert await svc.take_pending("u1", stored["asked_at"]) is False


async def test_take_pending_leaves_a_newer_pending_alone(test_db):
    svc = ConversationService(test_db)
    old = await svc.set_pending("u1", {"start_at": "x", "note": None})
    await test_db["conversations"].update_one(
        {"user_id": "u1"},
        {"$set": {"pending_confirmation.asked_at": old["asked_at"] + timedelta(seconds=1)}},
    )
    assert await svc.take_pending("u1", old["asked_at"]) is False
    assert (await svc.get_pending("u1"))["start_at"] == "x"


async def test_set_pending_none_returns_none(test_db):
    svc = ConversationService(test_db)
    assert await svc.set_pending("u1", None) is None
```

- [ ] **Step 2: Chạy cho đỏ**

Run: `PYTHONPATH=. .venv/bin/python -m pytest tests/test_conversation.py -q -k "pending"`
Expected: FAIL — `set_pending` trả `None`, `take_pending` không tồn tại.

- [ ] **Step 3: Sửa `app/services/conversation.py`**

Thay toàn bộ `set_pending` bằng:

```python
    async def set_pending(
        self, user_id: str, payload: Optional[Dict[str, Any]]
    ) -> Optional[Dict[str, Any]]:
        """Lưu (hoặc xoá) pending và trả về ĐÚNG giá trị đã lưu.

        `asked_at` cắt về mili giây vì BSON chỉ giữ tới mili giây: giá trị trong
        bộ nhớ phải bằng giá trị đọc lại thì `take_pending` mới so khớp được.
        """
        value = None
        if payload:
            now = now_utc()
            value = {**payload,
                     "asked_at": now.replace(microsecond=now.microsecond // 1000 * 1000)}
        await self.collection.update_one(
            {"user_id": user_id},
            {"$set": {"pending_confirmation": value, "updated_at": now_utc()},
             "$setOnInsert": {"user_id": user_id, "messages": [], "created_at": now_utc()}},
            upsert=True,
        )
        return value

    async def take_pending(self, user_id: str, asked_at: datetime) -> bool:
        """Xoá pending NẾU nó vẫn là cái có `asked_at` này. Nguyên tử.

        Hai lượt chat của cùng một khách chạy song song (REL-01) cùng đọc một
        pending: chỉ lượt giành được mới được ghi lịch, và lượt này không bao
        giờ xoá nhầm một pending mới hơn do lượt kia vừa giữ.
        """
        result = await self.collection.update_one(
            {"user_id": user_id, "pending_confirmation.asked_at": asked_at},
            {"$set": {"pending_confirmation": None, "updated_at": now_utc()}},
        )
        return result.modified_count == 1
```

- [ ] **Step 4: Chạy cho xanh**

Run: `PYTHONPATH=. .venv/bin/python -m pytest tests/test_conversation.py -q`
Expected: PASS. Rồi chạy cả bộ — phải xanh như mốc.

- [ ] **Step 5: Commit**

```bash
git add app/services/conversation.py tests/test_conversation.py
git commit -m "feat: return stored pending and add compare-and-clear take_pending

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 2: `consent.classify_reply` — có / không / chưa rõ

**Files:**
- Create: `app/services/consent.py`
- Test: `tests/test_consent.py`

**Interfaces:**
- Produces: `classify_reply(text: str, cancelling: bool = False) -> ConsentVerdict`, với `ConsentVerdict(NamedTuple)` gồm `verdict: str` (`"yes" | "no" | "unclear"`) và `reason: str` (`"empty"`, `"question"`, `"negative:<từ>"`, `"extra_words:<từ>"`, `"too_long"`, `"no_yes_word"`, `"yes"`). `strip_diacritics(text: str) -> str` (chuyển từ `confirm.py`, giữ y nguyên thân hàm).

- [ ] **Step 1: Viết test `tests/test_consent.py`**

```python
"""Bảng câu thật cho bước chốt. Thêm câu mới = thêm một dòng.

Nguồn: docs/AGENT_GRAPH_REVIEW.md (G-01, G-02, G-03, G-09) và bug
`is_affirmative` cũ trong CONTEXT.md. Spec mục 3.
"""
import pytest

from app.services.consent import classify_reply

CASES = [
    # --- không: phủ định ở bất kỳ đâu thắng mọi từ đồng ý (G-01) ---
    ("dạ ko em", False, "no"),
    ("dạ hông", False, "no"),
    ("dạ khoan", False, "no"),
    ("dc ko", False, "no"),
    ("ừ dời qua thứ Sáu", False, "no"),
    ("ừ dời qua thứ Sáu", True, "no"),
    ("thôi khỏi", False, "no"),
    ("à khoan, để chị xem lại", False, "no"),
    ("dung roi nhung doi gio khac", False, "no"),
    ("thôi hủy đi", False, "no"),
    ("ừ hủy đi em", False, "no"),            # đang chốt ĐẶT: "hủy" là không
    ("không hủy nữa", True, "no"),
    ("thôi khoan, giữ lại đi", True, "no"),
    # --- chưa rõ: câu hỏi, giờ mới, nội dung khác ---
    ("dạ 4 giờ nha em", False, "unclear"),   # G-03
    ("dạ 9 giờ", False, "unclear"),          # G-09a
    ("dạ sơn gel", False, "unclear"),        # G-09b
    ("hủy cái nào vậy em?", True, "unclear"),           # G-02
    ("để chị hỏi thằng Huy đã", True, "unclear"),        # G-02
    ("ừ không đổi gì đâu", False, "unclear"),            # "gì" là từ hỏi — xem Global Constraints
    ("Mà làm móng bột mất bao lâu hả chị, việc gì cũng phải tính trước cho chắc",
     False, "unclear"),                                   # bug is_affirmative cũ
    ("cho cô dùng thử", False, "unclear"),
    ("để mai đi", False, "unclear"),
    ("đúng không em?", False, "unclear"),
    ("em ơi", False, "unclear"),
    ("", False, "unclear"),
    ("dạ được rồi em ơi đặt giúp chị luôn nha em", False, "unclear"),  # > 8 từ
    # --- có: cả câu chỉ là từ đồng ý + từ đệm ---
    ("ừ", False, "yes"),
    ("Ừ", False, "yes"),
    ("dạ được em", False, "yes"),
    ("ok chị chốt", False, "yes"),
    ("OK ạ", False, "yes"),
    ("u dc", False, "yes"),
    ("👍", False, "yes"),
    ("đúng rồi", False, "yes"),
    ("chính xác", False, "yes"),
    ("ừ chốt luôn nha", False, "yes"),
    ("duoc do", False, "yes"),
    ("duoc nha con", False, "yes"),
    ("vang a", False, "yes"),
    ("ừ hủy đi em", True, "yes"),
    ("dạ hủy", True, "yes"),
    ("u huy di", True, "yes"),
]


@pytest.mark.parametrize("text,cancelling,expected", CASES)
def test_reply_table(text, cancelling, expected):
    assert classify_reply(text, cancelling=cancelling).verdict == expected


@pytest.mark.parametrize("variants", [
    ("dạ được em", "da duoc em", "dạ duoc em"),
    ("dạ ko em", "da ko em", "dạ không em"),
    ("dạ 4 giờ nha em", "da 4 gio nha em", "dạ 4 gio nha em"),
])
def test_accents_never_change_the_verdict(variants):
    """Có dấu, không dấu, gõ trộn: cùng một kết quả. Trước đây hai bộ luật có
    dấu/không dấu cho kết quả ngược nhau với cùng một câu."""
    verdicts = {classify_reply(v).verdict for v in variants}
    assert len(verdicts) == 1, variants


def test_reason_names_the_word_that_decided():
    assert classify_reply("dạ ko em").reason == "negative:ko"
    assert classify_reply("dạ 4 giờ nha em").reason == "extra_words:4"
    assert classify_reply("hả em").reason == "question"
```

- [ ] **Step 2: Chạy cho đỏ**

Run: `PYTHONPATH=. .venv/bin/python -m pytest tests/test_consent.py -q`
Expected: FAIL — `ModuleNotFoundError: app.services.consent`.

- [ ] **Step 3: Viết `app/services/consent.py`**

```python
"""Bước chốt: khách vừa được hỏi "chốt giờ này nhé?" — họ nói có, không, hay chưa rõ.

Spec: docs/superpowers/specs/2026-10-04-agent-turn-harness-design.md, mục 3.

Chỉ trả "yes" khi CẢ CÂU chỉ gồm từ đồng ý và từ đệm. Mọi thứ khác là "no" hoặc
"unclear" — sai hướng này chỉ tốn một câu hỏi lại, sai hướng kia là ghi lịch
trái ý khách (G-01, G-02, G-03 và bug `is_affirmative` cũ).

Một bộ luật KHÔNG DẤU duy nhất. Bộ luật cũ phải tách có dấu / không dấu vì
"đúng" và "dùng" bỏ dấu cùng ra "dung"; với luật "cả câu", "cho cô dùng thử" có
chữ "thu" lạ nên tự rơi vào "unclear".

Không LLM, không DB. Thêm từ mới khi log `consent_verdict` cho thấy khách thật nói gì.
"""
import re
import unicodedata
from typing import NamedTuple

MAX_YES_WORDS = 8

# Cụm nhiều chữ được nối thành một từ trước khi so.
_MULTI = ("nhat tri", "dong y", "chinh xac", "de sau", "bao nhieu")

_QUESTION = {"nao", "sao", "ha", "gi", "may", "bao_nhieu", "vay"}
# "doi" là đổi / đợi / dời cùng lúc — cả ba đều là chưa chốt giờ đang giữ.
_NEGATIVE = {"khong", "ko", "k", "hong", "hem", "thoi", "khoi", "chua", "khoan",
             "doi", "khac", "lui", "chuyen", "de_sau"}
_YES = {"u", "ua", "o", "uh", "uhm", "ok", "oke", "okie", "okay", "vang", "da",
        "dung", "phai", "duoc", "dc", "chuan", "chot", "dat", "nhat_tri", "dong_y",
        "chinh_xac", "👍"}
_FILLER = {"em", "chi", "anh", "co", "chu", "bac", "con", "minh", "roi", "nhe", "nha",
           "a", "oi", "di", "luon", "giup", "cho", "the", "do", "lich"}

_TOKEN = re.compile(r"[a-z0-9]+|👍")


class ConsentVerdict(NamedTuple):
    verdict: str   # "yes" | "no" | "unclear"
    reason: str


def strip_diacritics(text: str) -> str:
    """Bỏ dấu tiếng Việt. 'đúng rồi' và 'dung roi' phải cùng ra một chuỗi.

    NFD tách dấu thành ký tự riêng để lọc, nhưng đ/Đ không phải d kèm dấu — nó
    là một ký tự độc lập nên phải thay tay.
    """
    text = (text or "").replace("đ", "d").replace("Đ", "D")
    return "".join(
        c for c in unicodedata.normalize("NFD", text)
        if unicodedata.category(c) != "Mn"
    )


def _words(text: str) -> list:
    joined = " ".join(_TOKEN.findall(strip_diacritics(text).lower()))
    for phrase in _MULTI:
        joined = re.sub(rf"\b{phrase}\b", phrase.replace(" ", "_"), joined)
    return joined.split()


def classify_reply(text: str, cancelling: bool = False) -> ConsentVerdict:
    """`cancelling`: câu đang chờ là "hủy lịch này nhé?" — khi đó "hủy" là đồng ý,
    không phải phủ định (bẫy #19, chỉ khi cả câu là "hủy" + từ đệm)."""
    raw = (text or "").strip()
    words = _words(raw)
    if not words:
        return ConsentVerdict("unclear", "empty")
    if "?" in raw or any(w in _QUESTION for w in words):
        return ConsentVerdict("unclear", "question")

    negative = _NEGATIVE if cancelling else _NEGATIVE | {"huy"}
    hit = next((w for w in words if w in negative), None)
    if hit:
        return ConsentVerdict("no", f"negative:{hit}")

    yes = _YES | {"huy"} if cancelling else _YES
    extra = next((w for w in words if w not in yes and w not in _FILLER), None)
    if extra:
        return ConsentVerdict("unclear", f"extra_words:{extra}")
    if len(words) > MAX_YES_WORDS:
        return ConsentVerdict("unclear", "too_long")
    if not any(w in yes for w in words):
        return ConsentVerdict("unclear", "no_yes_word")
    return ConsentVerdict("yes", "yes")
```

- [ ] **Step 4: Chạy cho xanh**

Run: `PYTHONPATH=. .venv/bin/python -m pytest tests/test_consent.py -q`
Expected: PASS. Nếu một dòng trong `CASES` đỏ, sửa bộ từ trong `consent.py`, **không** sửa kỳ vọng — trừ khi kỳ vọng mâu thuẫn spec mục 3 (khi đó báo lại, đừng tự đổi).

- [ ] **Step 5: Commit**

```bash
git add app/services/consent.py tests/test_consent.py
git commit -m "feat: add deterministic three-way consent classifier

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 3: `TurnHarness` — giữ chỗ, nhả, và kiểm cuối lượt (luật 1, 2, 3b)

**Files:**
- Modify: `app/agents/booking_graph/context.py` (thêm `spoken_parts`)
- Create: `app/services/turn_harness.py`
- Test: `tests/test_turn_harness.py`

**Interfaces:**
- Consumes: `ConversationService.set_pending`, `take_pending` (Task 1).
- Produces:
  - `spoken_parts(dt) -> tuple[str, str]` trong `context.py`: `("5/10", "3 giờ chiều")`.
  - `mentions(answer: str, pending: dict) -> bool` trong `turn_harness.py`.
  - `TurnHarness(db, user, pending: Optional[dict] = None)` với thuộc tính `pending`, `held`, `committed: Optional[str]`, `released: bool`, `intent: Optional[str]`; phương thức `current() -> Optional[dict]`, `async hold(payload: dict) -> Optional[str]` (trả `None` khi giữ được, hoặc mã lỗi `"one_at_a_time"` / `"reschedule_needs_id"`), `async release(pending: Optional[dict]) -> None`, `async finish(answer: str) -> None`.

- [ ] **Step 1: Viết test `tests/test_turn_harness.py`**

```python
"""TurnHarness — 4 luật của một lượt chat. Spec mục 2, 4, 5."""
from datetime import datetime, timedelta

import pytest

from app.core.clock import TZ
from app.services.auth import AuthService
from app.services.conversation import ConversationService
from app.services.turn_harness import TurnHarness, mentions


def tomorrow_at(hour):
    local = datetime.now(TZ) + timedelta(days=1)
    return local.replace(hour=hour, minute=0, second=0, microsecond=0)


async def a_user(db, name="Cô Lan"):
    return await AuthService(db).create_user("0912345678", "matkhau123", name)


def said(start):
    """Câu hỏi xác nhận đúng như tool cho model đọc lại."""
    from app.agents.booking_graph.context import format_vi_datetime
    return f"Em giữ {format_vi_datetime(start)} cho chị, chị chốt giúp em nhé?"


# --- mentions -----------------------------------------------------------------

def test_mentions_needs_both_day_and_clock():
    start = tomorrow_at(15)
    pending = {"start_at": start.isoformat()}
    assert mentions(said(start), pending) is True
    assert mentions("Em giữ 3 giờ chiều cho chị nhé?", pending) is False
    assert mentions(f"Ngày {start.day}/{start.month} chị đến nhé?", pending) is False


def test_day_inside_a_longer_date_does_not_count():
    """Review Focus #1: "5/10" không được khớp bên trong "15/10"."""
    start = datetime(2026, 10, 5, 15, 0, tzinfo=TZ)
    pending = {"start_at": start.isoformat()}
    assert mentions("Thứ Năm 15/10, 3 giờ chiều được không chị?", pending) is False


def test_mentions_survives_a_broken_payload():
    assert mentions("bất kỳ", {"start_at": "rác"}) is False
    assert mentions("bất kỳ", {}) is False


# --- hold (luật 1, 3b) --------------------------------------------------------

async def test_second_hold_in_a_turn_is_refused(test_db):
    user = await a_user(test_db)
    harness = TurnHarness(test_db, user)
    assert await harness.hold({"start_at": tomorrow_at(15).isoformat(), "note": None}) is None
    assert await harness.hold({"start_at": tomorrow_at(16).isoformat(), "note": None}) == "one_at_a_time"
    stored = await ConversationService(test_db).get_pending(str(user.id))
    assert stored["start_at"] == tomorrow_at(15).isoformat()


async def test_reschedule_intent_needs_the_old_id(test_db):
    user = await a_user(test_db)
    harness = TurnHarness(test_db, user)
    harness.intent = "reschedule"
    assert await harness.hold({"start_at": tomorrow_at(15).isoformat(), "note": None}) == "reschedule_needs_id"
    assert await ConversationService(test_db).get_pending(str(user.id)) is None
    ok = {"start_at": tomorrow_at(15).isoformat(), "note": None, "replaces_appointment_id": "abc"}
    assert await harness.hold(ok) is None


async def test_reschedule_intent_does_not_block_a_cancel(test_db):
    user = await a_user(test_db)
    harness = TurnHarness(test_db, user)
    harness.intent = "reschedule"
    payload = {"cancel_appointment_id": "abc", "start_at": tomorrow_at(15).isoformat()}
    assert await harness.hold(payload) is None


async def test_hold_replaces_an_inherited_pending(test_db):
    """Review Focus #5: lượt `unclear` kế thừa pending 15h, model giữ 16h — được."""
    user = await a_user(test_db)
    svc = ConversationService(test_db)
    inherited = await svc.set_pending(str(user.id), {"start_at": tomorrow_at(15).isoformat(), "note": None})
    harness = TurnHarness(test_db, user, inherited)
    assert await harness.hold({"start_at": tomorrow_at(16).isoformat(), "note": None}) is None
    assert harness.current()["start_at"] == tomorrow_at(16).isoformat()


# --- finish (luật 2) ----------------------------------------------------------

async def test_finish_keeps_a_pending_the_answer_names(test_db):
    user = await a_user(test_db)
    harness = TurnHarness(test_db, user)
    await harness.hold({"start_at": tomorrow_at(15).isoformat(), "note": None})
    await harness.finish(said(tomorrow_at(15)))
    assert await ConversationService(test_db).get_pending(str(user.id)) is not None


@pytest.mark.parametrize("answer", [
    "Dạ em chưa tra được, anh chị gọi trực tiếp cho tiệm giúp em nhé ạ.",   # G-07
    "Cả 9 giờ và 10 giờ đều còn, chị chọn giờ nào ạ?",                        # G-09a
    "",                                                                       # lượt lỗi
])
async def test_finish_drops_a_pending_the_answer_does_not_name(test_db, answer):
    user = await a_user(test_db)
    harness = TurnHarness(test_db, user)
    await harness.hold({"start_at": tomorrow_at(15).isoformat(), "note": None})
    await harness.finish(answer)
    assert await ConversationService(test_db).get_pending(str(user.id)) is None


async def test_finish_checks_an_inherited_pending_too(test_db):
    """Câu `unclear` → booking trả lời chuyện khác mà không hỏi lại giờ đang giữ."""
    user = await a_user(test_db)
    inherited = await ConversationService(test_db).set_pending(
        str(user.id), {"start_at": tomorrow_at(15).isoformat(), "note": None})
    harness = TurnHarness(test_db, user, inherited)
    await harness.finish("Dạ tiệm mở tới 7 giờ tối ạ.")
    assert await ConversationService(test_db).get_pending(str(user.id)) is None


async def test_finish_never_clears_a_newer_pending(test_db):
    """Lượt song song vừa giữ cái mới: lượt này không được xoá nó."""
    user = await a_user(test_db)
    svc = ConversationService(test_db)
    inherited = await svc.set_pending(str(user.id), {"start_at": tomorrow_at(15).isoformat(), "note": None})
    await test_db["conversations"].update_one(
        {"user_id": str(user.id)},
        {"$set": {"pending_confirmation.asked_at": inherited["asked_at"] + timedelta(seconds=1)}})
    await TurnHarness(test_db, user, inherited).finish("")
    assert await svc.get_pending(str(user.id)) is not None


async def test_release_clears_the_inherited_pending(test_db):
    user = await a_user(test_db)
    inherited = await ConversationService(test_db).set_pending(
        str(user.id), {"start_at": tomorrow_at(15).isoformat(), "note": None})
    harness = TurnHarness(test_db, user, inherited)
    await harness.release(inherited)
    assert harness.current() is None
    assert await ConversationService(test_db).get_pending(str(user.id)) is None


async def test_finish_without_any_pending_is_a_no_op(test_db):
    user = await a_user(test_db)
    await TurnHarness(test_db, user).finish("")
```

- [ ] **Step 2: Chạy cho đỏ**

Run: `PYTHONPATH=. .venv/bin/python -m pytest tests/test_turn_harness.py -q`
Expected: FAIL — `ModuleNotFoundError: app.services.turn_harness`.

- [ ] **Step 3: Thêm `spoken_parts` vào `app/agents/booking_graph/context.py`**

Ngay sau hàm `format_vi_datetime`:

```python
def spoken_parts(dt) -> tuple:
    """('5/10', '3 giờ chiều') — hai mẩu mà câu trả lời phải chứa để pending còn
    sống (`TurnHarness.finish`). Dựng bằng đúng hàm của `format_vi_datetime` nên
    không thể lệch với câu tool cho model đọc lại."""
    local = to_local(dt)
    return f"{local.day}/{local.month}", _clock_phrase(local.hour, local.minute)
```

- [ ] **Step 4: Viết `app/services/turn_harness.py`**

```python
"""Harness cho MỘT lượt chat của agent — chỗ duy nhất phía agent được ghi
pending và gọi create/cancel/reschedule.

Spec: docs/superpowers/specs/2026-10-04-agent-turn-harness-design.md.

Trước đây luật quanh `pending_confirmation` nằm rải ở tools.py, agents.py,
confirm.py, events.py, và không chỗ nào biết LƯỢT NÀY đã giữ gì, ghi gì, khách
đã thật sự được hỏi gì (G-07, G-08, G-09, A-07).

Module này KHÔNG import `app.agents...` ở đầu file: gói `app.agents.booking_graph`
nạp `events` → `graph` → `confirm`, mà `confirm` import module này — vòng import.
Hàm định dạng giờ được nhập muộn bên trong hàm.
"""
import re
from datetime import datetime
from typing import Any, Dict, NamedTuple, Optional

from motor.motor_asyncio import AsyncIOMotorDatabase

from app.core.errors import AppError
from app.core.logging import get_logger
from app.models.user import User
from app.services.appointment import AppointmentService
from app.services.conversation import ConversationService

logger = get_logger(__name__)


def mentions(answer: str, pending: Dict[str, Any]) -> bool:
    """Câu trả lời có nhắc đúng giờ đang giữ không — đủ cả ngày lẫn giờ.

    Luật 2: pending chỉ sống nếu khách vừa được hỏi đúng nó. Ngày so có biên
    số hai bên để "5/10" không khớp bên trong "15/10".
    """
    from app.agents.booking_graph.context import spoken_parts  # nhập muộn, xem docstring module

    try:
        start = datetime.fromisoformat(pending["start_at"])
    except (KeyError, TypeError, ValueError):
        return False
    day, clock = spoken_parts(start)
    text = " ".join((answer or "").lower().split())
    return (re.search(rf"(?<!\d){re.escape(day)}(?!\d)", text) is not None
            and clock.lower() in text)


class TurnHarness:
    def __init__(self, db: AsyncIOMotorDatabase, user: User,
                 pending: Optional[Dict[str, Any]] = None):
        self.user = user
        self.user_id = str(user.id)
        self.conversations = ConversationService(db)
        self.appointments = AppointmentService(db)
        self.pending = pending          # đọc lúc đầu lượt (load_context)
        self.held: Optional[Dict[str, Any]] = None
        self.committed: Optional[str] = None
        self.released = False
        self.intent: Optional[str] = None

    def current(self) -> Optional[Dict[str, Any]]:
        """Pending mà lượt này đang chịu trách nhiệm."""
        if self.held is not None:
            return self.held
        if self.committed or self.released:
            return None
        return self.pending

    async def hold(self, payload: Dict[str, Any]) -> Optional[str]:
        """Luật 1 + 3b. Trả None khi đã giữ, hoặc mã lỗi cho model."""
        if self.held is not None:
            return "one_at_a_time"
        if (self.intent == "reschedule"
                and "cancel_appointment_id" not in payload
                and not payload.get("replaces_appointment_id")):
            # Lưới tất định cho G-10/BUG-1: khách muốn DỜI mà model quên id lịch
            # cũ thì sẽ ra hai lịch. Supervisor đoán nhầm thì khách chỉ bị hỏi
            # "dời lịch nào ạ?" — không ghi sai gì.
            return "reschedule_needs_id"
        self.held = await self.conversations.set_pending(self.user_id, payload)
        return None

    async def release(self, pending: Optional[Dict[str, Any]]) -> None:
        """Khách nói KHÔNG: xoá đúng pending đó (so `asked_at`)."""
        self.released = True
        asked_at = (pending or {}).get("asked_at")
        if asked_at is not None:
            await self.conversations.take_pending(self.user_id, asked_at)

    async def finish(self, answer: str) -> None:
        """Luật 2, gọi ở cuối MỌI lượt kể cả lượt lỗi (answer rỗng)."""
        current = self.current()
        if not current or mentions(answer, current):
            return
        logger.info("pending_dropped_unmentioned", extra={
            "user_id": self.user_id, "start_at": current.get("start_at"),
            "held_this_turn": self.held is not None,
        })
        asked_at = current.get("asked_at")
        if asked_at is not None:
            await self.conversations.take_pending(self.user_id, asked_at)
```

(Các import `AppError`, `NamedTuple` dùng ở Task 4 — để sẵn.)

- [ ] **Step 5: Chạy cho xanh**

Run: `PYTHONPATH=. .venv/bin/python -m pytest tests/test_turn_harness.py -q`
Expected: PASS. Rồi chạy cả bộ.

- [ ] **Step 6: Commit**

```bash
git add app/services/turn_harness.py app/agents/booking_graph/context.py tests/test_turn_harness.py
git commit -m "feat: add TurnHarness hold/release/finish rules

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 4: `TurnHarness.commit` — ghi thật, giành pending nguyên tử

**Files:**
- Modify: `app/services/turn_harness.py`
- Test: `tests/test_turn_harness.py`

**Interfaces:**
- Consumes: `take_pending` (Task 1), `AppointmentService.create/cancel/reschedule` (không đổi).
- Produces: `CommitResult(NamedTuple)`: `kind: str` (`"booked" | "moved" | "cancelled" | "failed" | "cancel_failed" | "bad_payload" | "lost"`), `start_at: Optional[datetime] = None`, `note: Optional[str] = None`, `error: Optional[str] = None`. `async TurnHarness.commit(pending: dict) -> CommitResult`; khi thành công đặt `self.committed = kind`.

- [ ] **Step 1: Thêm test vào `tests/test_turn_harness.py`**

```python
from app.services.appointment import AppointmentService


async def stored(db, user, payload):
    return await ConversationService(db).set_pending(str(user.id), payload)


async def test_commit_books_the_time_stored_in_mongo(test_db):
    user = await a_user(test_db)
    pending = await stored(test_db, user, {"start_at": tomorrow_at(15).isoformat(), "note": "làm tóc"})
    result = await TurnHarness(test_db, user, pending).commit(pending)
    assert result.kind == "booked"
    booked = await AppointmentService(test_db).upcoming_for(user)
    assert [a.start_at.astimezone(TZ) for a in booked] == [tomorrow_at(15)]
    assert await ConversationService(test_db).get_pending(str(user.id)) is None


async def test_two_commits_of_one_pending_book_once(test_db):
    """Khách "ừ" hai lượt liền / hai lượt song song: đúng một lịch."""
    user = await a_user(test_db)
    pending = await stored(test_db, user, {"start_at": tomorrow_at(15).isoformat(), "note": None})
    first = await TurnHarness(test_db, user, pending).commit(pending)
    second = await TurnHarness(test_db, user, pending).commit(pending)
    assert (first.kind, second.kind) == ("booked", "lost")
    assert len(await AppointmentService(test_db).upcoming_for(user)) == 1


async def test_a_pending_never_stored_is_lost(test_db):
    user = await a_user(test_db)
    result = await TurnHarness(test_db, user).commit({"start_at": tomorrow_at(15).isoformat()})
    assert result.kind == "lost"
    assert await AppointmentService(test_db).upcoming_for(user) == []


async def test_replaced_proposal_books_the_new_time(test_db):
    """Lượt `unclear` đổi 15h → 16h, rồi "ừ": ghi 16h, không ghi 15h."""
    user = await a_user(test_db)
    old = await stored(test_db, user, {"start_at": tomorrow_at(15).isoformat(), "note": None})
    harness = TurnHarness(test_db, user, old)
    await harness.hold({"start_at": tomorrow_at(16).isoformat(), "note": None})
    await harness.finish(said(tomorrow_at(16)))

    new = await ConversationService(test_db).get_pending(str(user.id))
    assert (await TurnHarness(test_db, user, old).commit(old)).kind == "lost"
    assert (await TurnHarness(test_db, user, new).commit(new)).kind == "booked"
    booked = await AppointmentService(test_db).upcoming_for(user)
    assert [a.start_at.astimezone(TZ) for a in booked] == [tomorrow_at(16)]


async def test_slot_taken_while_waiting_fails_and_clears(test_db):
    user = await a_user(test_db)
    other = await AuthService(test_db).create_user("0938111222", "x", "Cô Hoa")
    pending = await stored(test_db, user, {"start_at": tomorrow_at(15).isoformat(), "note": None})
    await AppointmentService(test_db).create(other, tomorrow_at(15), None)

    result = await TurnHarness(test_db, user, pending).commit(pending)
    assert result.kind == "failed" and result.error
    assert await AppointmentService(test_db).upcoming_for(user) == []
    assert await ConversationService(test_db).get_pending(str(user.id)) is None


async def test_cancel_of_a_vanished_appointment_is_cancel_failed(test_db):
    user = await a_user(test_db)
    appt = await AppointmentService(test_db).create(user, tomorrow_at(15), None)
    await AppointmentService(test_db).cancel(user, str(appt.id))
    pending = await stored(test_db, user, {"cancel_appointment_id": str(appt.id),
                                           "start_at": appt.start_at.isoformat()})
    result = await TurnHarness(test_db, user, pending).commit(pending)
    assert result.kind == "cancel_failed"


async def test_commit_cancels_and_marks_committed(test_db):
    user = await a_user(test_db)
    appt = await AppointmentService(test_db).create(user, tomorrow_at(15), None)
    pending = await stored(test_db, user, {"cancel_appointment_id": str(appt.id),
                                           "start_at": appt.start_at.isoformat()})
    harness = TurnHarness(test_db, user, pending)
    result = await harness.commit(pending)
    assert result.kind == "cancelled" and harness.committed == "cancelled"
    assert await AppointmentService(test_db).upcoming_for(user) == []


async def test_bad_payload_is_reported_not_raised(test_db):
    user = await a_user(test_db)
    pending = await stored(test_db, user, {"start_at": "rác", "note": None})
    assert (await TurnHarness(test_db, user, pending).commit(pending)).kind == "bad_payload"


async def test_finish_after_commit_touches_nothing(test_db):
    """Review Focus #3: lượt nổ SAU khi đã ghi — lịch vẫn còn, không lỗi."""
    user = await a_user(test_db)
    pending = await stored(test_db, user, {"start_at": tomorrow_at(15).isoformat(), "note": None})
    harness = TurnHarness(test_db, user, pending)
    await harness.commit(pending)
    await harness.finish("")
    assert len(await AppointmentService(test_db).upcoming_for(user)) == 1
```

- [ ] **Step 2: Chạy cho đỏ**

Run: `PYTHONPATH=. .venv/bin/python -m pytest tests/test_turn_harness.py -q`
Expected: FAIL — `AttributeError: 'TurnHarness' object has no attribute 'commit'`.

- [ ] **Step 3: Thêm vào `app/services/turn_harness.py`**

Trên `class TurnHarness`:

```python
class CommitResult(NamedTuple):
    kind: str   # booked | moved | cancelled | failed | cancel_failed | bad_payload | lost
    start_at: Optional[datetime] = None
    note: Optional[str] = None
    error: Optional[str] = None
```

Trong `TurnHarness`, sau `release`:

```python
    async def commit(self, pending: Dict[str, Any]) -> CommitResult:
        """Khách nói CÓ: ghi / hủy / dời từ pending trong DB.

        Giành pending TRƯỚC khi ghi: lượt nào giành hụt (pending đã bị lượt
        khác dùng, đã thay, đã hết hạn rồi bị xoá) thì không ghi gì — cùng một
        pending không bao giờ thành hai lịch.
        """
        asked_at = pending.get("asked_at")
        if asked_at is None or not await self.conversations.take_pending(self.user_id, asked_at):
            logger.info("pending_lost", extra={"user_id": self.user_id})
            return CommitResult("lost")

        cancel_id = pending.get("cancel_appointment_id")
        if cancel_id:
            try:
                await self.appointments.cancel(self.user, cancel_id)
            except AppError as exc:
                return CommitResult("cancel_failed", error=exc.message)
            self.committed = "cancelled"
            try:
                start = datetime.fromisoformat(pending["start_at"])
            except (KeyError, ValueError):
                start = None
            return CommitResult("cancelled", start_at=start)

        try:
            start = datetime.fromisoformat(pending["start_at"])
            replaces = pending.get("replaces_appointment_id")
            if replaces:
                appointment = await self.appointments.reschedule(
                    self.user, replaces, start, pending.get("note"))
                kind = "moved"
            else:
                appointment = await self.appointments.create(self.user, start, pending.get("note"))
                kind = "booked"
        except AppError as exc:
            return CommitResult("failed", error=exc.message)
        except (KeyError, TypeError, ValueError):
            logger.warning("bad_pending_payload", extra={"payload": str(pending)[:120]})
            return CommitResult("bad_payload")

        self.committed = kind
        return CommitResult(kind, start_at=appointment.start_at, note=appointment.note)
```

- [ ] **Step 4: Chạy cho xanh**

Run: `PYTHONPATH=. .venv/bin/python -m pytest tests/test_turn_harness.py -q`
Expected: PASS. Rồi cả bộ.

- [ ] **Step 5: Commit**

```bash
git add app/services/turn_harness.py tests/test_turn_harness.py
git commit -m "feat: commit pending atomically through TurnHarness

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 5: Supervisor trả `intent` (spec mục 3b)

**Files:**
- Modify: `app/agents/booking_graph/supervisor.py`, `app/agents/booking_graph/prompts.py` (`SUPERVISOR_PROMPT`), `app/agents/booking_graph/state.py`, `scripts/probe_supervisor.py`
- Test: `tests/test_supervisor.py`

**Interfaces:**
- Produces: `INTENT_ROUTES: dict[str, str]`, `parse_intent(raw: str) -> str` (một nhãn trong `INTENT_ROUTES` hoặc `"unknown"`), `supervise(state) -> {"route": str, "intent": str}`. `GraphState` thêm `intent: Optional[str]` và `gave_up: bool`.

- [ ] **Step 1: Sửa test `tests/test_supervisor.py`**

Thay các test dùng nhãn route cũ. Cụ thể: trong `test_booking_intent_routes_to_booking` đổi `patch_model("booking")` → `patch_model("book")`; `test_small_talk_routes_to_social` giữ `"social"`; `test_shop_question_routes_to_shop` đổi `"shop"` → `"shop_info"`; `test_model_reply_is_case_and_space_insensitive` đổi `"  SHOP \n"` → `"  SHOP_INFO \n"`; `test_supervisor_is_not_given_the_tool_schemas` đổi `"booking"` → `"book"`. Rồi thêm:

```python
from app.agents.booking_graph.supervisor import INTENT_ROUTES, parse_intent


@pytest.mark.parametrize("raw,intent", [
    ("Reschedule.", "reschedule"),
    ("**shop_info**", "shop_info"),
    (" BOOK ", "book"),
    ("my_appointments\n", "my_appointments"),
    ("booking", "unknown"),       # nhãn route cũ không còn là intent
    ("tôi không chắc", "unknown"),
    ("", "unknown"),
])
def test_parse_intent_normalises(raw, intent):
    assert parse_intent(raw) == intent


def test_every_intent_maps_to_a_live_route():
    assert set(INTENT_ROUTES.values()) == {"booking", "shop", "social"}
    assert {"book", "reschedule", "cancel", "my_appointments", "availability",
            "shop_info", "social"} == set(INTENT_ROUTES)


@pytest.mark.asyncio
async def test_supervise_returns_route_and_intent(patch_model):
    patch_model("reschedule")
    assert await supervise(a_state("chuyển giùm chị qua 2 giờ")) == {
        "route": "booking", "intent": "reschedule"}


@pytest.mark.asyncio
async def test_unknown_reply_still_routes_to_booking(patch_model):
    patch_model("**hmm**")
    assert await supervise(a_state()) == {"route": "booking", "intent": "unknown"}
```

- [ ] **Step 2: Chạy cho đỏ**

Run: `PYTHONPATH=. .venv/bin/python -m pytest tests/test_supervisor.py -q`
Expected: FAIL — `ImportError: cannot import name 'INTENT_ROUTES'`.

- [ ] **Step 3: Sửa `app/agents/booking_graph/supervisor.py`**

Thay `VALID_ROUTES = {...}` và thân `supervise` bằng:

```python
import re

# Model chỉ trả intent; route do code suy ra — một nguồn sự thật, không có cặp
# route/intent mâu thuẫn ("shop" + "cancel").
INTENT_ROUTES = {
    "book": "booking",
    "reschedule": "booking",
    "cancel": "booking",
    "my_appointments": "booking",
    "availability": "booking",
    "shop_info": "shop",
    "social": "social",
}
VALID_ROUTES = set(INTENT_ROUTES.values())

# Bỏ mọi thứ không phải chữ thường / gạch dưới: "Reschedule." và "**shop_info**"
# là cùng một nhãn (G-27). Không dùng JSON để khỏi thêm một kiểu lỗi phân tích.
_NOT_LABEL = re.compile(r"[^a-z_]")


def parse_intent(raw: str) -> str:
    word = _NOT_LABEL.sub("", (raw or "").strip().lower())
    return word if word in INTENT_ROUTES else "unknown"
```

```python
async def supervise(state: GraphState) -> dict:
    """Phân loại ý định.

    Supervisor KHÔNG nhận memory, KHÔNG nhận schema tool, KHÔNG nhận lịch sắp tới —
    chỉ vài lượt chat cuối. Nó chỉ cần biết khách đang muốn gì.
    """
    model = build_chat_model(tags=["supervisor"], temperature=0.0)
    recent = state["messages"][-SUPERVISOR_HISTORY_TURNS:]

    reply = await model.ainvoke([SystemMessage(content=SUPERVISOR_PROMPT), *recent])
    intent = parse_intent(reply.content)
    if intent == "unknown":
        logger.info("supervisor_fallback", extra={"raw": (reply.content or "")[:80]})
    # Thà cho vào luồng đặt lịch còn hơn từ chối nhầm khách hợp lệ.
    return {"route": INTENT_ROUTES.get(intent, "booking"), "intent": intent}
```

- [ ] **Step 4: Thay `SUPERVISOR_PROMPT` trong `prompts.py`**

```python
SUPERVISOR_PROMPT = """Classify the customer's intent at a Vietnamese nail and
hair salon.

Reply with EXACTLY ONE label. No punctuation, no explanation, no quotes:
- book            : book a new appointment, or just name the service they want —
                    wanting a service IS wanting an appointment
- reschedule      : move an appointment they already have to another time
- cancel          : cancel an appointment
- my_appointments : ask about their own appointments
- availability    : ask which times are free
- shop_info       : whether the owner is busy or free, when they finish, whether
                    the salon is open right now, what time it opens or closes,
                    which days it is closed
- social          : everything else — greetings, thanks, goodbyes, small talk,
                    and requests outside the salon's business

A message that mixes a shop question with anything about appointments is about
appointments: pick the appointment label.
If torn between an appointment label and social, pick the appointment label."""
```

- [ ] **Step 5: `state.py` thêm hai trường**

Trong `GraphState`, sau `route: str`:

```python
    intent: Optional[str]            # nhãn của supervisor; None khi đi tắt qua confirm
    gave_up: bool                    # node trả FALLBACK_ANSWER (hết vòng tool / câu rỗng)
```

- [ ] **Step 6: Cập nhật `scripts/probe_supervisor.py`**

Đổi cột kỳ vọng sang intent và so bằng `parse_intent`:

```python
from app.agents.booking_graph.supervisor import parse_intent

PROBES = [
    ("book", "chị muốn làm tóc"),
    ("book", "mai 3h chiều được không em"),
    ("my_appointments", "chị có lịch lúc nào vậy em"),
    ("cancel", "hủy giùm chị lịch mai"),
    ("reschedule", "em ơi chuyển giùm cô qua 2 giờ chiều nha"),
    ("reschedule", "dời lịch mai của chị sang thứ Sáu được không"),
    ("availability", "chiều nay còn giờ nào trống không em"),
    ("shop_info", "tiệm đang bận không em"),
    ("shop_info", "chừng nào chủ tiệm xong vậy"),
    ("shop_info", "tiệm mình mở cửa mấy giờ vậy em"),
    ("shop_info", "mấy giờ tiệm đóng cửa"),
    ("shop_info", "chủ nhật tiệm có làm không em"),
    ("shop_info", "tiệm còn mở không em"),
    ("social", "chào em"),
    ("social", "cảm ơn em nhé"),
    ("social", "thôi chị đi nha"),
    ("social", "cho tôi công thức nấu phở"),
    ("social", "dịch giùm tôi đoạn tiếng Anh này"),
    ("availability", "mấy giờ đóng cửa vậy, chiều nay anh qua cắt tóc được không"),
    ("availability", "tiệm còn làm không em, còn giờ nào trống chiều nay"),
    ("book", "chủ tiệm đang bận hả, vậy mai 9 giờ đặt được không"),
]
```

và trong `main()` thay `actual = (reply.content or "").strip().lower()` bằng `actual = parse_intent(reply.content)`; đổi định dạng in `{actual:10s}` → `{actual:16s}`, `{expected:8s}` → `{expected:16s}`. **Không chạy script** (tốn LLM).

- [ ] **Step 7: Chạy cho xanh**

Run: `PYTHONPATH=. .venv/bin/python -m pytest tests/test_supervisor.py tests/test_prompts.py -q`
Expected: PASS. Rồi cả bộ.

- [ ] **Step 8: Commit**

```bash
git add app/agents/booking_graph/supervisor.py app/agents/booking_graph/prompts.py app/agents/booking_graph/state.py scripts/probe_supervisor.py tests/test_supervisor.py
git commit -m "feat: supervisor returns an intent, route derived in code

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 6: Tool giữ chỗ qua `harness.hold` + prompt booking

**Files:**
- Modify: `app/agents/booking_graph/tools.py` (`make_booking_tools`, `propose_appointment`, `cancel_appointment`), `app/agents/booking_graph/prompts.py` (`BOOKING_PROMPT`)
- Test: `tests/test_tools.py`, `tests/test_prompts.py`

**Interfaces:**
- Consumes: `TurnHarness(db, user)`, `TurnHarness.hold` (Task 3).
- Produces: `make_booking_tools(db, user, harness: Optional[TurnHarness] = None) -> List[BaseTool]` — `None` thì tự tạo một harness riêng (giữ test cũ chạy được). Tool trả `{"error": "one_at_a_time"}` hoặc `{"error": "reschedule_needs_id"}` khi `hold` từ chối.

- [ ] **Step 1: Thêm test vào `tests/test_tools.py`**

```python
from app.services.turn_harness import TurnHarness


async def test_second_propose_in_one_turn_is_refused(test_db):
    """G-08: hai lần giữ trong một lượt ghi đè nhau — giờ bị từ chối."""
    user = await a_user(test_db)
    tools = make_booking_tools(test_db, user, TurnHarness(test_db, user))
    propose = by_name(tools, "propose_appointment")
    first = json.loads(await propose.ainvoke({"start_at": tomorrow_at(15).isoformat(), "note": None}))
    second = json.loads(await propose.ainvoke({"start_at": tomorrow_at(16).isoformat(), "note": None}))
    assert "held" in first
    assert second == {"error": "one_at_a_time"}
    pending = await ConversationService(test_db).get_pending(str(user.id))
    assert pending["start_at"] == tomorrow_at(15).isoformat()


async def test_cancel_after_propose_in_one_turn_is_refused(test_db):
    user = await a_user(test_db)
    appt = await AppointmentService(test_db).create(user, tomorrow_at(9), None)
    tools = make_booking_tools(test_db, user, TurnHarness(test_db, user))
    await by_name(tools, "propose_appointment").ainvoke(
        {"start_at": tomorrow_at(15).isoformat(), "note": None})
    out = json.loads(await by_name(tools, "cancel_appointment").ainvoke(
        {"appointment_id": str(appt.id)}))
    assert out == {"error": "one_at_a_time"}


async def test_reschedule_intent_without_id_is_refused(test_db):
    user = await a_user(test_db)
    harness = TurnHarness(test_db, user)
    harness.intent = "reschedule"
    out = json.loads(await by_name(make_booking_tools(test_db, user, harness),
                                   "propose_appointment").ainvoke(
        {"start_at": tomorrow_at(15).isoformat(), "note": None}))
    assert out == {"error": "reschedule_needs_id"}
    assert await ConversationService(test_db).get_pending(str(user.id)) is None
```

(Kiểm `a_user`, `tomorrow_at`, `by_name`, `json`, `ConversationService`, `AppointmentService` đã import ở đầu `tests/test_tools.py`; thiếu cái nào thì thêm.)

Thêm vào `tests/test_prompts.py`:

```python
def test_booking_prompt_explains_the_harness_error_codes():
    for code in ("one_at_a_time", "reschedule_needs_id"):
        assert code in BOOKING_PROMPT, code


def test_booking_prompt_gathers_several_services_into_one_hold():
    """τ-bench: yêu cầu gộp nhiều việc là ~19% ca hỏng. Luật một-lần-giữ không
    được biến "làm móng rồi gội đầu luôn" thành chỉ làm một việc."""
    assert "ONE `note`" in BOOKING_PROMPT
```

- [ ] **Step 2: Chạy cho đỏ**

Run: `PYTHONPATH=. .venv/bin/python -m pytest tests/test_tools.py tests/test_prompts.py -q`
Expected: FAIL — `make_booking_tools()` không nhận đối số thứ ba; prompt thiếu mã lỗi.

- [ ] **Step 3: Sửa `tools.py`**

Thêm import: `from app.services.turn_harness import TurnHarness`.

Đổi chữ ký và đầu hàm:

```python
def make_booking_tools(
    db: AsyncIOMotorDatabase, user: User, harness: Optional[TurnHarness] = None
) -> List[BaseTool]:
    """Tool đóng kín `user` trong closure.

    `user_id` KHÔNG phải tham số của tool: AI không thể bị dụ thao tác lịch của
    người khác, kể cả khi khách gõ "hủy lịch của bà Lan".

    Ghi pending CHỈ qua `harness.hold` — luật "mỗi lượt giữ một lịch" sống ở đó.
    """
    service = AppointmentService(db)
    harness = harness or TurnHarness(db, user)
```

Xoá dòng `conversations = ConversationService(db)` trong `make_booking_tools` và import `ConversationService` nếu không còn chỗ nào dùng (kiểm bằng `grep -n ConversationService app/agents/booking_graph/tools.py`).

Trong `propose_appointment`, thay

```python
        await conversations.set_pending(str(user.id), payload)
```

bằng

```python
        refused = await harness.hold(payload)
        if refused:
            return json.dumps({"error": refused})
```

Trong `cancel_appointment`, thay khối `await conversations.set_pending(str(user.id), {...})` bằng

```python
        refused = await harness.hold(
            {"cancel_appointment_id": str(appt.id), "start_at": appt.start_at.isoformat()})
        if refused:
            return json.dumps({"error": refused})
```

Trong docstring `propose_appointment`, thêm câu cuối: `When several services are wanted for the same visit, put them all in ONE note of ONE call.`

- [ ] **Step 4: Sửa `BOOKING_PROMPT`**

Trong rule 1, ngay sau câu `Never hold a second appointment for a customer who is moving one.` thêm:

```
   Hold at most ONE appointment per turn. When the customer wants several
   services in the same visit, put them all in ONE `note` of ONE
   propose_appointment call.
```

Trong danh sách mã lỗi của rule 2, sau dòng `- slot_unavailable: ...` thêm:

```
   - one_at_a_time: you already held one appointment this turn. Ask the
     customer about that one first; hold another only after they answer.
   - reschedule_needs_id: the customer wants to MOVE an appointment. Call
     propose_appointment again with `replaces_appointment_id` copied from the
     context block, or ask which appointment they mean.
```

- [ ] **Step 5: Chạy cho xanh**

Run: `PYTHONPATH=. .venv/bin/python -m pytest tests/test_tools.py tests/test_prompts.py tests/test_confirm.py -q`
Expected: PASS. Nếu một test cũ dùng CÙNG một danh sách tool để gọi `propose` hai lần, giờ lần hai trả `one_at_a_time` — đó là hành vi đúng; sửa test cho mỗi lần gọi dùng `make_booking_tools(...)` mới (một lượt chat mới). Rồi cả bộ.

- [ ] **Step 6: Commit**

```bash
git add app/agents/booking_graph/tools.py app/agents/booking_graph/prompts.py tests/test_tools.py tests/test_prompts.py
git commit -m "feat: tools hold pending through the turn harness

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 7: `confirm` ba nhánh qua harness, xoá `is_affirmative`

**Files:**
- Modify: `app/agents/booking_graph/confirm.py`, `app/agents/booking_graph/prompts.py` (`PHRASE_PROMPT`, `PHRASE_KIND_SENTENCES`), `app/agents/booking_graph/guard.py` (`_guard_phrase`)
- Test: `tests/test_confirm.py`

**Interfaces:**
- Consumes: `classify_reply` (Task 2), `TurnHarness.release/commit`, `CommitResult` (Task 3, 4).
- Produces: `make_confirm_node(db, user, harness: Optional[TurnHarness] = None)`. Nhánh `no` trả `{"route": "booking", "pending_confirmation": None}`; nhánh `unclear` trả `{"route": "booking"}`; nhánh `lost` trả `{"route": "booking", "pending_confirmation": None}`; nhánh ghi trả `{"confirm_fact": {...}, "fallback": str}` với `kind` thêm giá trị `"cancel_failed"`. `route_after_confirm` giữ nguyên.

- [ ] **Step 1: Sửa `tests/test_confirm.py`**

1. Xoá `is_affirmative` khỏi dòng import. Xoá hẳn `test_affirmative_phrases`, `test_negative_phrases`, `class TestAffirmativeVariants`, `class TestIsAffirmativeWhileCancelling` (đã chuyển sang `tests/test_consent.py` ở Task 2).
2. Thêm helper ngay dưới `a_state`:

```python
async def run_confirm(db, user, text, payload):
    """Pending phải NẰM TRONG DB (có `asked_at`) — harness giành nó trước khi
    ghi. Pending dựng tay mà không lưu là pending mà lượt khác đã dùng mất."""
    stored = await ConversationService(db).set_pending(str(user.id), payload)
    return await make_confirm_node(db, user)(a_state(user, text, stored))
```

3. Mọi chỗ gọi node với pending **dựng tay** (dict literal hoặc `self._pending(appt)`) đổi sang `await run_confirm(test_db, user, <câu>, <payload>)`. Các test cần đổi: ba test trong `TestConfirmUsesTheRightHonorific`, hai test trong `TestHonorificInTheOtherBranches`, hai test trong `TestNotAffirmativeGoesToBooking`, mọi test trong `TestConfirmReschedules` và `TestConfirmCancels`. Các test dùng `_setup` + `get_pending` thì giữ nguyên (pending đó đã nằm trong DB).
4. Đổi kỳ vọng của nhánh KHÔNG: `assert result == {"route": "booking"}` → `assert result == {"route": "booking", "pending_confirmation": None}` (trong `test_no_clears_the_flag_without_booking`, `test_the_decline_branch_hands_over_instead_of_answering`, `test_no_keeps_the_appointment_and_hands_over`).
5. Đổi docstring của `test_u_huy_di_counts_as_yes_when_cancelling`: bỏ câu nhắc `is_affirmative`, thay bằng "consent.classify_reply(cancelling=True) coi 'hủy' là đồng ý khi cả câu chỉ có 'hủy' + từ đệm".
6. Thêm test mới cuối file:

```python
class TestThreeWayVerdict:
    """G-01, G-02, G-03: không / chưa rõ không bao giờ ghi lịch."""

    async def test_short_negative_does_not_book(self, test_db):
        user = await AuthService(test_db).create_user("0912345678", "matkhau123", "Cô Lan")
        out = await run_confirm(test_db, user, "dạ ko em",
                                {"start_at": tomorrow_at(15).isoformat(), "note": None})
        assert out == {"route": "booking", "pending_confirmation": None}
        assert await test_db["appointments"].count_documents({}) == 0
        assert await ConversationService(test_db).get_pending(str(user.id)) is None

    async def test_unclear_keeps_the_pending_and_hands_over(self, test_db):
        user = await AuthService(test_db).create_user("0912345678", "matkhau123", "Cô Lan")
        out = await run_confirm(test_db, user, "dạ 4 giờ nha em",
                                {"start_at": tomorrow_at(15).isoformat(), "note": None})
        assert out == {"route": "booking"}
        assert await test_db["appointments"].count_documents({}) == 0
        assert await ConversationService(test_db).get_pending(str(user.id)) is not None

    async def test_a_question_while_cancelling_cancels_nothing(self, test_db):
        user = await AuthService(test_db).create_user("0912345678", "matkhau123", "Cô Lan")
        appt = await AppointmentService(test_db).create(user, tomorrow_at(15), None)
        out = await run_confirm(test_db, user, "hủy cái nào vậy em?",
                                {"cancel_appointment_id": str(appt.id),
                                 "start_at": appt.start_at.isoformat()})
        assert out == {"route": "booking"}
        assert len(await AppointmentService(test_db).upcoming_for(user)) == 1

    async def test_yes_twice_books_once(self, test_db):
        user = await AuthService(test_db).create_user("0912345678", "matkhau123", "Cô Lan")
        stored = await ConversationService(test_db).set_pending(
            str(user.id), {"start_at": tomorrow_at(15).isoformat(), "note": None})
        node = make_confirm_node(test_db, user)
        first = await node(a_state(user, "ừ", stored))
        second = await node(a_state(user, "ừ", stored))
        assert first["confirm_fact"]["kind"] == "booked"
        assert second == {"route": "booking", "pending_confirmation": None}
        assert await test_db["appointments"].count_documents({}) == 1

    async def test_cancel_of_a_vanished_appointment_says_so(self, test_db):
        user = await AuthService(test_db).create_user("0912345678", "matkhau123", "Cô Lan")
        appt = await AppointmentService(test_db).create(user, tomorrow_at(15), None)
        await AppointmentService(test_db).cancel(user, str(appt.id))
        out = await run_confirm(test_db, user, "ừ hủy đi",
                                {"cancel_appointment_id": str(appt.id),
                                 "start_at": appt.start_at.isoformat()})
        assert out["confirm_fact"]["kind"] == "cancel_failed"
        assert "chọn giờ khác" not in out["fallback"]
```

Lưu ý: `test_already_gone_appointment_gets_a_friendly_answer` (trong `TestConfirmCancels`) vẫn giữ kỳ vọng `"không tìm thấy" in out["fallback"].lower()` — đúng với câu cứng mới.

- [ ] **Step 2: Chạy cho đỏ**

Run: `PYTHONPATH=. .venv/bin/python -m pytest tests/test_confirm.py -q`
Expected: FAIL — các test mới (`TestThreeWayVerdict`) và kỳ vọng `pending_confirmation: None`.

- [ ] **Step 3: Viết lại `app/agents/booking_graph/confirm.py`**

Thay toàn bộ file bằng:

```python
"""Nhánh tắt cho câu trả lời xác nhận.

Khách nói "ừ" sau khi AI đã hỏi "3h chiều Thứ Năm đúng không chị?" thì đi thẳng
vào thực thi — không quay lại supervisor, không qua LLM. Hiểu câu trả lời là
việc của `consent.classify_reply`; ghi lịch là việc của `TurnHarness.commit`.
Node này chỉ nối hai thứ đó và viết câu cứng dự phòng.
"""
from typing import Awaitable, Callable, Optional

from motor.motor_asyncio import AsyncIOMotorDatabase

from app.agents.booking_graph.context import address_phrase, format_vi_datetime
from app.agents.booking_graph.state import GraphState
from app.core.logging import get_logger
from app.models.user import User
from app.services.consent import classify_reply
from app.services.turn_harness import TurnHarness

logger = get_logger(__name__)


def _sentence_start(phrase: str) -> str:
    """Viết hoa chữ đầu mà KHÔNG hạ chữ tên: str.capitalize() biến "anh Hùng"
    thành "Anh hùng"."""
    return phrase[:1].upper() + phrase[1:]


def make_confirm_node(
    db: AsyncIOMotorDatabase, user: User, harness: Optional[TurnHarness] = None
) -> Callable[[GraphState], Awaitable[dict]]:
    harness = harness or TurnHarness(db, user)

    async def node(state: GraphState) -> dict:
        pending = state.get("pending_confirmation") or {}
        last_message = state["messages"][-1].content if state["messages"] else ""
        cancelling = bool(pending.get("cancel_appointment_id"))

        verdict = classify_reply(last_message, cancelling=cancelling)
        logger.info("consent_verdict", extra={
            "verdict": verdict.verdict, "reason": verdict.reason, "cancelling": cancelling,
        })

        if verdict.verdict == "no":
            await harness.release(pending)
            return {"route": "booking", "pending_confirmation": None}
        if verdict.verdict == "unclear":
            # GIỮ pending: booking thấy dòng "Đang chờ khách chốt" trong khối
            # bối cảnh và hỏi lại / giữ giờ mới. Luật 2 (`finish`) xoá nó nếu
            # câu trả lời cuối không nhắc lại giờ đó.
            return {"route": "booking"}

        result = await harness.commit(pending)
        if result.kind == "lost":
            return {"route": "booking", "pending_confirmation": None}

        # Suy từ full_name bằng code, không nhận từ model: model quên truyền là
        # câu chốt mất lời gọi.
        address = address_phrase(user.full_name)
        when = format_vi_datetime(result.start_at) if result.start_at else None

        def done(kind, fallback, error=None):
            return {"confirm_fact": {"kind": kind, "when": when, "note": result.note,
                                     "address": address, "error": error},
                    "fallback": fallback}

        if result.kind == "cancelled":
            if not when:
                return done("cancelled", f"Em hủy lịch xong rồi ạ, {address} cần gì cứ nhắn em nhé.")
            return done("cancelled",
                        f"Em hủy lịch {when} cho {address} xong rồi ạ. Cần đặt lại thì cứ nhắn em nhé.")
        if result.kind == "cancel_failed":
            return done("cancel_failed",
                        f"Dạ {result.error} ạ. {_sentence_start(address)} xem lại danh sách lịch giúp em nhé.",
                        error=result.error)
        if result.kind == "failed":
            return done("failed",
                        f"Dạ {result.error} ạ. {_sentence_start(address)} chọn giờ khác giúp em nhé.",
                        error=result.error)
        if result.kind == "bad_payload":
            return done("failed",
                        f"Dạ em nhầm mất rồi, {address} nhắc lại ngày giờ giúp em ạ.",
                        error="em nhầm mất rồi")
        if result.kind == "moved":
            return done("moved", f"Em dời lịch xong rồi ạ. Hẹn gặp {address} {when} nhé.")
        return done("booked", f"Xong rồi ạ. Hẹn gặp {address} {when} nhé.")

    return node


def route_after_confirm(state: GraphState) -> str:
    """Chưa chốt → booking; đã ghi (có confirm_fact) → phrase viết câu chốt."""
    return "booking" if state.get("route") == "booking" else "phrase"
```

Kiểm `grep -rn "strip_diacritics\|is_affirmative" app scripts tests` — chỗ nào còn import `strip_diacritics` từ `confirm` thì đổi sang `from app.services.consent import strip_diacritics`.

- [ ] **Step 4: `cancel_failed` trong prompt và guard**

`prompts.py`, trong `PHRASE_PROMPT` thay

```
Do not add any other fact. Do not ask a new question unless the booking
failed, in which case ask them to pick another time.
```

bằng

```
Do not add any other fact. Do not ask a new question unless the sentence
above tells you to.
```

và thêm vào `PHRASE_KIND_SENTENCES`:

```python
    "cancel_failed": "The salon could NOT cancel the appointment. The reason, in Vietnamese, is: {error}. Say so and ask them to check their list of appointments; do not ask them to pick another time.",
```

`guard.py`, trong `_guard_phrase` đổi `if kind == "failed" and error ...` thành `if kind in ("failed", "cancel_failed") and error ...`.

- [ ] **Step 5: Chạy cho xanh**

Run: `PYTHONPATH=. .venv/bin/python -m pytest tests/test_confirm.py tests/test_phrase.py tests/test_prompts.py -q`
Expected: PASS. Rồi `grep -rn "is_affirmative" app tests scripts` — phải rỗng. Rồi cả bộ.

- [ ] **Step 6: Commit**

```bash
git add app/agents/booking_graph/confirm.py app/agents/booking_graph/prompts.py app/agents/booking_graph/guard.py tests/test_confirm.py
git commit -m "feat: three-way confirm through consent and the turn harness

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 8: Guard chặn câu nhận vơ "đã xong" (luật 4)

**Files:**
- Modify: `app/services/turn_harness.py` (`claims_done`, `TurnHarness.check_claim`), `app/agents/booking_graph/guard.py` (`make_guard_node`), `app/agents/booking_graph/prompts.py` (`VIOLATION_HINTS`)
- Test: `tests/test_turn_harness.py`, `tests/test_guard.py`

**Interfaces:**
- Produces: `claims_done(text: str) -> bool`; `TurnHarness.check_claim(answer: str) -> bool` (= `committed is None and claims_done(answer)`); `make_guard_node(user, harness: Optional[TurnHarness] = None)`; mã vi phạm `"claim"`; hằng `CLAIM_FALLBACK` trong `guard.py` (chuỗi có `{address}` và `{Address}`).

- [ ] **Step 1: Viết test**

Thêm vào `tests/test_turn_harness.py`:

```python
from app.services.turn_harness import claims_done


@pytest.mark.parametrize("text,claimed", [
    ("Dạ em đặt xong rồi ạ.", True),
    ("Xong rồi ạ, hẹn gặp chị.", True),
    ("Em đã hủy lịch rồi nhé chị.", True),
    ("Em dời xong rồi ạ.", True),
    ("Dạ chị đã có lịch 4 giờ chiều rồi ạ.", False),
    ("Dạ em chưa đặt lịch nào cho chị ạ.", False),
    ("Em giữ 3 giờ chiều cho chị, chị chốt giúp em nhé?", False),
    ("Dạ việc này em chưa làm xong đâu ạ.", False),
])
def test_claims_done(text, claimed):
    assert claims_done(text) is claimed


async def test_check_claim_is_off_once_something_was_written(test_db):
    user = await a_user(test_db)
    pending = await stored(test_db, user, {"start_at": tomorrow_at(15).isoformat(), "note": None})
    harness = TurnHarness(test_db, user, pending)
    assert harness.check_claim("Dạ em đặt xong rồi ạ.") is True
    await harness.commit(pending)
    assert harness.check_claim("Dạ em đặt xong rồi ạ.") is False
```

Thêm vào `tests/test_guard.py` (dựng `User` và state theo cách các test guard hiện có trong file đang dựng; tên khách "Cô Lan" → `address` là "chị Lan"):

```python
from unittest.mock import MagicMock

from app.agents.booking_graph.guard import CLAIM_FALLBACK, make_guard_node
from app.models.user import User
from app.services.turn_harness import TurnHarness

LAN = User(phone="0912345678", hashed_password="x", full_name="Cô Lan")


def claim_state(draft, **extra):
    return {"draft": draft, "previous_replies": [], "customer_text": "ừ được em",
            "context_block": "Gọi khách là: chị Lan.", **extra}


async def test_a_false_claim_goes_to_rewrite():
    node = make_guard_node(LAN, TurnHarness(MagicMock(), LAN))
    out = await node(claim_state("Dạ em đặt xong rồi ạ."))
    assert "claim" in out["violations"] and "answer" not in out


async def test_a_claim_that_survives_rewrite_becomes_the_canned_sentence():
    node = make_guard_node(LAN, TurnHarness(MagicMock(), LAN))
    out = await node(claim_state("Dạ em đặt xong cho chị rồi ạ.", rewritten=True,
                                 original_draft="Dạ em đặt xong rồi ạ."))
    assert out["answer"] == CLAIM_FALLBACK.format(address="chị Lan", Address="Chị Lan")
    assert out["answer_source"] == "code"


async def test_without_a_harness_the_guard_behaves_as_before():
    out = await make_guard_node(LAN)(claim_state("Dạ em đặt xong rồi ạ."))
    assert "claim" not in (out.get("violations") or [])
```

- [ ] **Step 2: Chạy cho đỏ**

Run: `PYTHONPATH=. .venv/bin/python -m pytest tests/test_turn_harness.py tests/test_guard.py -q`
Expected: FAIL — `ImportError: claims_done`, `CLAIM_FALLBACK`.

- [ ] **Step 3: `turn_harness.py`**

Trên `class CommitResult`:

```python
# Luật 4: chỉ câu NGÔI THỨ NHẤT của bot nói đã hoàn tất. "chị đã có lịch 4 giờ
# chiều rồi ạ" là câu đúng (khách có lịch thật) — không được bắt.
_CLAIM = re.compile(
    r"\bem\s+(?:đã\s+)?(?:đặt|hủy|dời|ghi)(?:\s+lịch)?(?:\s+\w+){0,2}?\s+(?:xong|rồi)\b"
    r"|\bxong\s+rồi\s+ạ\b|\bđặt\s+xong\b",
    re.IGNORECASE,
)


def claims_done(text: str) -> bool:
    for match in _CLAIM.finditer(text or ""):
        if "chưa" in (text[max(0, match.start() - 12):match.start()]).lower():
            continue
        return True
    return False
```

Trong `TurnHarness`, thêm:

```python
    def check_claim(self, answer: str) -> bool:
        """Câu nói đã đặt/hủy/dời xong mà lượt này chưa ghi gì."""
        return self.committed is None and claims_done(answer)
```

- [ ] **Step 4: `guard.py` và `prompts.py`**

`guard.py` — thêm hằng ngay dưới `PHRASE_TIMEOUT_SECONDS`:

```python
# Câu cứng khi rewrite vẫn nhận vơ "đã xong". Không chứa mẫu `claims_done` nào.
CLAIM_FALLBACK = ("Dạ việc này em chưa làm xong đâu ạ. {Address} nói lại giúp em "
                  "muốn đặt, dời hay hủy giờ nào nhé.")
```

Đổi `make_guard_node(user: User)` thành `make_guard_node(user: User, harness=None)`. Trong `node`, ngay sau khối `codes = find_violations(...)` thêm:

```python
        if harness is not None and harness.check_claim(draft):
            codes.append("claim")
```

Trong nhánh `if state.get("rewritten"):`, ngay sau `if codes:` (trước dòng `extra = {...}`) thêm:

```python
                if "claim" in codes:
                    logger.warning("guard_claim_fallback", extra={"codes": codes})
                    return {"answer": CLAIM_FALLBACK.format(
                                address=address, Address=address[:1].upper() + address[1:]),
                            "answer_source": "code", "violations": codes}
```

`prompts.py` — thêm vào `VIOLATION_HINTS`:

```python
    "claim": "It says an appointment was already booked, moved or cancelled, but nothing was written this turn. Never claim it is done; say what you will do next or ask what they want.",
```

- [ ] **Step 5: Chạy cho xanh**

Run: `PYTHONPATH=. .venv/bin/python -m pytest tests/test_turn_harness.py tests/test_guard.py tests/test_phrase.py tests/test_rewrite.py -q`
Expected: PASS. Rồi cả bộ.

- [ ] **Step 6: Commit**

```bash
git add app/services/turn_harness.py app/agents/booking_graph/guard.py app/agents/booking_graph/prompts.py tests/test_turn_harness.py tests/test_guard.py
git commit -m "feat: guard rewrites replies that claim an unwritten booking

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 9: Dòng lượt sau khối bối cảnh + cờ `gave_up`

**Files:**
- Modify: `app/agents/booking_graph/context.py` (`render_turn_lines`), `app/agents/booking_graph/agents.py`, `app/agents/booking_graph/prompts.py` (`BOOKING_PROMPT`)
- Test: `tests/test_context_block.py`, `tests/test_subagents.py`

**Interfaces:**
- Consumes: `TurnHarness` + `make_booking_tools(db, user, harness)` (Task 3, 6).
- Produces: `render_turn_lines(pending: Optional[dict], intent: Optional[str]) -> str`; `NO_PENDING_LINE`. Node của `make_subagent_node` gửi `context_block + "\n" + render_turn_lines(...)` làm message áp chót, và trả `{"draft": FALLBACK_ANSWER, "gave_up": True}` ở cả hai nhánh fallback.

- [ ] **Step 1: Viết test**

Thêm vào `tests/test_context_block.py`:

```python
from datetime import datetime

from app.agents.booking_graph.context import NO_PENDING_LINE, render_turn_lines
from app.core.clock import TZ


def test_turn_lines_say_when_nothing_is_waiting():
    assert render_turn_lines(None, None) == NO_PENDING_LINE


def test_turn_lines_name_the_waiting_booking():
    start = datetime(2026, 10, 8, 15, 0, tzinfo=TZ)
    text = render_turn_lines({"start_at": start.isoformat(), "note": "làm móng"}, "book")
    assert "Đang chờ khách chốt: Thứ Năm 8/10, 3 giờ chiều, làm móng." in text
    assert "Lượt này khách muốn: đặt lịch mới." in text


def test_turn_lines_name_a_waiting_cancel():
    start = datetime(2026, 10, 8, 15, 0, tzinfo=TZ)
    text = render_turn_lines({"cancel_appointment_id": "x", "start_at": start.isoformat()}, None)
    assert text == "Đang chờ khách chốt HỦY lịch: Thứ Năm 8/10, 3 giờ chiều."


def test_turn_lines_skip_social_and_unknown_intents():
    assert "Lượt này" not in render_turn_lines(None, "social")
    assert "Lượt này" not in render_turn_lines(None, "unknown")


def test_turn_lines_survive_a_broken_pending():
    assert render_turn_lines({"start_at": "rác"}, None) == NO_PENDING_LINE
```

Thêm vào `tests/test_subagents.py`:

```python
@pytest.mark.asyncio
async def test_turn_lines_follow_the_context_block(patch_model):
    model = patch_model([AIMessage(content="xong")])
    node = make_subagent_node("prompt", [fake_lookup], tag="respond")
    await node({**a_state("còn không em"), "intent": "availability"})
    context = str(model.calls[0][-2].content)
    assert context.startswith("Bạn đang nói chuyện với")
    assert "Không có giờ nào đang chờ khách chốt." in context
    assert "Lượt này khách muốn: hỏi giờ còn trống." in context


@pytest.mark.asyncio
async def test_exhausted_tool_loop_sets_gave_up(patch_model):
    patch_model([tool_call_message() for _ in range(MAX_TOOL_ROUNDS)])
    node = make_subagent_node("prompt", [fake_lookup], tag="respond")
    out = await node(a_state())
    assert out["gave_up"] is True


@pytest.mark.asyncio
async def test_g07_hold_on_the_last_round_does_not_survive(test_db, patch_model):
    """G-07: vòng 4 giữ được chỗ, rồi hết vòng → câu fallback → pending phải mất."""
    from datetime import datetime, timedelta

    from app.agents.booking_graph.tools import make_booking_tools
    from app.core.clock import TZ
    from app.services.auth import AuthService
    from app.services.conversation import ConversationService
    from app.services.turn_harness import TurnHarness

    user = await AuthService(test_db).create_user("0912345678", "matkhau123", "Cô Lan")
    start = (datetime.now(TZ) + timedelta(days=1)).replace(hour=15, minute=0, second=0, microsecond=0)
    harness = TurnHarness(test_db, user)
    call = AIMessage(content="", tool_calls=[{"name": "propose_appointment",
                                              "args": {"start_at": start.isoformat()},
                                              "id": "c1"}])
    patch_model([call for _ in range(MAX_TOOL_ROUNDS)])
    node = make_subagent_node("prompt", make_booking_tools(test_db, user, harness), tag="respond")

    out = await node(a_state())
    await harness.finish(out["draft"])

    assert out["gave_up"] is True
    assert await ConversationService(test_db).get_pending(str(user.id)) is None
```

- [ ] **Step 2: Chạy cho đỏ**

Run: `PYTHONPATH=. .venv/bin/python -m pytest tests/test_context_block.py tests/test_subagents.py -q`
Expected: FAIL — `ImportError: render_turn_lines`; thiếu `gave_up`.

- [ ] **Step 3: `context.py`**

Ngay dưới `render_slots`:

```python
NO_PENDING_LINE = "Không có giờ nào đang chờ khách chốt."

_TURN_INTENT_VI = {
    "book": "đặt lịch mới", "reschedule": "dời lịch", "cancel": "hủy lịch",
    "my_appointments": "xem lịch của mình", "availability": "hỏi giờ còn trống",
    "shop_info": "hỏi về tiệm",
}


def render_turn_lines(pending, intent) -> str:
    """Hai dòng của RIÊNG lượt này, dựng bằng code, nối sau khối bối cảnh.

    Dòng pending LUÔN có: câu `unclear` của confirm được chuyển cho booking, và
    booking phải biết đang giữ chỗ nào để hỏi lại (spec mục 2, luật 3). Không
    nằm trong `build_context_block` vì confirm có thể nhả pending GIỮA lượt —
    dòng này đọc `state["pending_confirmation"]` sau confirm.
    """
    when = None
    if pending:
        try:
            when = format_vi_datetime(datetime.fromisoformat(pending["start_at"]))
        except (KeyError, TypeError, ValueError):
            when = None
    if when and pending.get("cancel_appointment_id"):
        lines = [f"Đang chờ khách chốt HỦY lịch: {when}."]
    elif when:
        note = f", {pending['note']}" if pending.get("note") else ""
        lines = [f"Đang chờ khách chốt: {when}{note}."]
    else:
        lines = [NO_PENDING_LINE]
    if intent in _TURN_INTENT_VI:
        lines.append(f"Lượt này khách muốn: {_TURN_INTENT_VI[intent]}.")
    return "\n".join(lines)
```

- [ ] **Step 4: `agents.py`**

Import: `from app.agents.booking_graph.context import render_slots, render_turn_lines`.

Trong `messages = [...]` thay `HumanMessage(content=state.get("context_block", "")),` bằng:

```python
            HumanMessage(content=(state.get("context_block", "") + "\n" + render_turn_lines(
                state.get("pending_confirmation"), state.get("intent")))),
```

Thay `return {"draft": reply.content or FALLBACK_ANSWER}` bằng:

```python
            if not calls:
                if reply.content:
                    return {"draft": reply.content}
                return {"draft": FALLBACK_ANSWER, "gave_up": True}
```

(giữ dòng `calls = getattr(...)` ngay trên) và `return {"draft": FALLBACK_ANSWER}` cuối hàm thành `return {"draft": FALLBACK_ANSWER, "gave_up": True}`.

- [ ] **Step 5: `BOOKING_PROMPT`**

Cuối rule 1 (sau câu `answer both halves of a mixed question in one reply.`) thêm:

```
   The context block says which time, if any, is waiting for the customer's
   confirmation. If one is waiting and their reply is not a clear yes or no,
   answer what they said, then ask again about that time, saying its weekday,
   date and clock time. The line saying what the customer wants this turn is
   newer than the conversation state block; when they differ, follow it.
```

- [ ] **Step 6: Chạy cho xanh**

Run: `PYTHONPATH=. .venv/bin/python -m pytest tests/test_context_block.py tests/test_subagents.py tests/test_prompts.py tests/test_phrase.py -q`
Expected: PASS. Rồi cả bộ.

- [ ] **Step 7: Commit**

```bash
git add app/agents/booking_graph/context.py app/agents/booking_graph/agents.py app/agents/booking_graph/prompts.py tests/test_context_block.py tests/test_subagents.py
git commit -m "feat: show the waiting confirmation and turn intent to the agent

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 10: Nối harness vào graph và `run_turn`

**Files:**
- Modify: `app/agents/booking_graph/graph.py`, `app/agents/booking_graph/events.py`
- Test: `tests/test_graph_events.py`, `tests/test_graph_wiring.py`

**Interfaces:**
- Consumes: mọi thứ ở Task 3–9.
- Produces: `build_graph(db, user, harness: Optional[TurnHarness] = None)`; `booking_tools(db, user, harness)`. `run_turn` tạo `TurnHarness(db, user, context["pending_confirmation"])`, gọi `await harness.finish(answer)` TRƯỚC khi lưu lịch sử và phát `complete`, và `await harness.finish("")` trong nhánh lỗi.

- [ ] **Step 1: Viết test**

Trong `tests/test_graph_events.py`, đổi mọi `lambda db, u: FakeGraph()` thành `lambda db, u, h=None: FakeGraph()`. Thêm:

```python
async def test_run_turn_finishes_the_harness_with_the_final_answer(test_db, monkeypatch):
    """Review Focus #4: luật 2 xét câu CUỐI (sau guard/rewrite)."""
    from app.agents.booking_graph import events
    from app.services.auth import AuthService
    from app.services.turn_harness import TurnHarness

    user = await AuthService(test_db).create_user("0912345678", "matkhau123", "Cô Lan")
    seen = []

    async def fake_finish(self, answer):
        seen.append(answer)

    class FakeGraph:
        async def astream_events(self, state, config=None, version=None):
            yield {"event": "on_chain_end", "data": {"output": {"answer": "câu cuối"}}}

    monkeypatch.setattr(TurnHarness, "finish", fake_finish)
    monkeypatch.setattr(events, "build_graph", lambda db, u, h=None: FakeGraph())
    monkeypatch.setattr(events, "schedule_compaction", lambda db, uid: None)

    [e async for e in events.run_turn(test_db, user, "ừ")]
    assert seen == ["câu cuối"]


async def test_run_turn_finishes_the_harness_when_the_graph_fails(test_db, monkeypatch):
    from app.agents.booking_graph import events
    from app.services.auth import AuthService
    from app.services.turn_harness import TurnHarness

    user = await AuthService(test_db).create_user("0912345678", "matkhau123", "Cô Lan")
    seen = []

    async def fake_finish(self, answer):
        seen.append(answer)

    class BrokenGraph:
        async def astream_events(self, state, config=None, version=None):
            raise RuntimeError("azure down")
            yield  # pragma: no cover

    monkeypatch.setattr(TurnHarness, "finish", fake_finish)
    monkeypatch.setattr(events, "build_graph", lambda db, u, h=None: BrokenGraph())

    out = [e async for e in events.run_turn(test_db, user, "ừ")]
    assert out[-1].type == "error"
    assert seen == [""]
```

(`AgentEvent` là dataclass có `type: str` và `data: dict` — `events.py:31`.)

Thêm vào `tests/test_graph_wiring.py`:

```python
def test_only_the_harness_writes_pending_or_appointments():
    """Spec: TurnHarness là chỗ DUY NHẤT phía agent ghi pending và gọi
    create/cancel/reschedule. Lớp phụ — lớp chính là tests/test_turn_harness.py."""
    import inspect

    from app.agents.booking_graph import agents, confirm, events, graph, guard, tools

    for module in (agents, confirm, events, graph, guard, tools):
        source = inspect.getsource(module)
        for needle in ("set_pending(", "take_pending(", ".create(", ".cancel(", ".reschedule("):
            assert needle not in source, f"{module.__name__} gọi {needle} — đi qua TurnHarness"


def test_build_graph_accepts_a_harness():
    from unittest.mock import MagicMock

    from app.services.turn_harness import TurnHarness

    user = User(phone="0912345678", hashed_password="x", full_name="Cô Lan")
    g = build_graph(MagicMock(), user, TurnHarness(MagicMock(), user)).get_graph()
    assert "supervisor" in set(g.nodes)
```

- [ ] **Step 2: Chạy cho đỏ**

Run: `PYTHONPATH=. .venv/bin/python -m pytest tests/test_graph_events.py tests/test_graph_wiring.py -q`
Expected: FAIL — `build_graph()` không nhận harness; `finish` chưa được gọi.

- [ ] **Step 3: `graph.py`**

Import: `from typing import Optional` và `from app.services.turn_harness import TurnHarness`.

```python
def booking_tools(db: AsyncIOMotorDatabase, user: User, harness: TurnHarness):
    """5 tool lịch + 2 tool đọc của shop. Câu kép ("mấy giờ đóng cửa, chiều nay
    còn giờ nào") vào booking và cần cả hai nhóm. Vẫn KHÔNG có tool ghi."""
    return make_booking_tools(db, user, harness) + make_shop_tools(db, user)


def build_graph(db: AsyncIOMotorDatabase, user: User, harness: Optional[TurnHarness] = None):
    """Đồ thị cho một khách cụ thể, MỘT lượt.

    Dựng lại mỗi lượt vì tool đóng kín `user` trong closure — đó là cách user_id
    không bao giờ trở thành tham số mà AI có thể điều khiển. `harness` cũng của
    riêng lượt này: tool, confirm, guard và supervisor cùng nhìn một trạng thái.
    """
    harness = harness or TurnHarness(db, user)
    graph = StateGraph(GraphState)

    async def supervisor_node(state: GraphState) -> dict:
        out = await supervise(state)
        harness.intent = out["intent"]     # luật 3b đọc ở hold()
        return out

    graph.add_node("supervisor", supervisor_node)
    graph.add_node("confirm", make_confirm_node(db, user, harness))
```

Đổi tiếp: `booking_tools(db, user)` → `booking_tools(db, user, harness)`; `make_guard_node(user)` → `make_guard_node(user, harness)`. Phần còn lại của `build_graph` giữ nguyên.

- [ ] **Step 4: `events.py`**

Import: `from app.services.turn_harness import TurnHarness`.

Trong `run_turn`: trước `try:` thêm `harness = None`. Ngay sau `context = await load_context(...)` thêm `harness = TurnHarness(db, user, context["pending_confirmation"])`. Đổi `graph = build_graph(db, user)` → `graph = build_graph(db, user, harness)`. Ngay sau `answer = final_state.get("answer", "")` thêm:

```python
        # Luật 2, TRƯỚC khi lưu lịch sử và phát complete: khách thấy complete
        # thì DB đã đúng — pending chỉ còn nếu câu cuối nhắc đúng giờ đang giữ.
        await harness.finish(answer)
```

Trong `except Exception as exc:`, trước `yield AgentEvent("error", ...)` thêm:

```python
        if harness is not None:
            # Lượt lỗi: khách không được hỏi gì cả, pending giữ trong lượt (hoặc
            # kế thừa) không được sống sang lượt sau (A-07).
            try:
                await harness.finish("")
            except Exception as finish_exc:
                logger.warning("harness_finish_failed", extra={
                    "user_id": user_id, "error": str(finish_exc)})
```

- [ ] **Step 5: Chạy cho xanh**

Run: `PYTHONPATH=. .venv/bin/python -m pytest tests/test_graph_events.py tests/test_graph_wiring.py tests/test_socketio_chat.py -q`
Expected: PASS. Rồi **cả bộ** — phải xanh hết, số test tăng so với mốc.

- [ ] **Step 6: Commit**

```bash
git add app/agents/booking_graph/graph.py app/agents/booking_graph/events.py tests/test_graph_events.py tests/test_graph_wiring.py
git commit -m "feat: wire the turn harness through the graph and run_turn

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 11: Tài liệu và kiểm cuối

**Files:**
- Modify: `CONTEXT.md`, `AGENTS.md`

- [ ] **Step 1: `CONTEXT.md`**

1. Xoá cả mục `### VIỆC TIẾP THEO — bug \`is_affirmative\`, nặng nhất đang mở` (tới trước tiêu đề kế tiếp). Thay bằng một dòng trong phần đã xong: `- 2026-10-04: bug is_affirmative → thay bằng app/services/consent.py (ba trạng thái, chỉ "yes" khi cả câu là từ đồng ý + từ đệm). Spec: docs/superpowers/specs/2026-10-04-agent-turn-harness-design.md.`
2. Bẫy #19: thay đoạn cuối `**Hủy cũng qua \`confirm\`**; khi đang chốt hủy thì chữ "hủy" trong "ừ hủy đi" là ĐỒNG Ý (\`is_affirmative(..., cancelling=True)\`) — dùng chung bộ từ phủ định là khách không bao giờ hủy được.` bằng: `**Hủy cũng qua \`confirm\`**; khi đang chốt hủy thì "hủy" là ĐỒNG Ý **chỉ khi cả câu chỉ có "hủy" + từ đệm** ("ừ hủy đi em") — \`consent.classify_reply(..., cancelling=True)\`. Câu hỏi có chữ "hủy" ("hủy cái nào vậy em?") là chưa rõ, không hủy gì (G-02).`
3. Thêm bẫy mới cuối danh sách bẫy (số kế tiếp): `**Pending chỉ sống nếu câu trả lời cuối nhắc đúng giờ đang giữ** (cả ngày "5/10" lẫn giờ "3 giờ chiều") — \`TurnHarness.finish\`. Đừng "nới" luật này để giảm số lần hỏi lại: nó là thứ chặn G-07 (hết vòng tool → "gọi tiệm giúp em" → "dạ vâng" ghi lịch ma) và G-09. Mọi ghi pending / create / cancel / reschedule phía agent đi qua \`TurnHarness\` — có fence trong tests/test_graph_wiring.py.`

- [ ] **Step 2: `AGENTS.md`**

- Dòng `- **Agent has NO create/write tool.** Flow: \`propose_appointment\` holds a slot; a separate` → `- **Agent has NO create/write tool.** Flow: \`propose_appointment\` stores a pending *intent* (nothing is held in the appointments collection); a separate`. Thêm ngay sau gạch đầu dòng đó: `- **Every agent-side write goes through \`TurnHarness\`** (\`app/services/turn_harness.py\`): one hold per turn, pending survives only if the final reply names its time, \`confirm\` uses \`app/services/consent.py\` (yes/no/unclear). Don't call \`set_pending\`/\`create\`/\`cancel\`/\`reschedule\` from \`app/agents/\`.`
- Dòng `app/agents/booking_graph/  LangGraph: supervisor → respond/confirm nodes, 5 read-only tools` → `app/agents/booking_graph/  LangGraph: supervisor (intent) → booking/shop/social/confirm → guard; 7 tools, none write`.

- [ ] **Step 3: Kiểm cuối**

```bash
grep -rn "is_affirmative" app tests scripts          # phải rỗng
PYTHONPATH=. .venv/bin/python -m pytest -q             # phải xanh toàn bộ
git status --short                                     # chỉ còn hoi-thoai-dai-*.txt chưa track
```

Ghi số test trước/sau vào tin nhắn báo cáo. **Không** chạy `pytest -m llm`, `scripts/probe_supervisor.py`, `scripts/chat_scenarios.py` — báo người dùng rằng ba lệnh này nên chạy tay (tốn LLM) để đo intent và hội thoại thật.

- [ ] **Step 4: Commit**

```bash
git add CONTEXT.md AGENTS.md
git commit -m "docs: record the turn harness rules in CONTEXT and AGENTS

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```
