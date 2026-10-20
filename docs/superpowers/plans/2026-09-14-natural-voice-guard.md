# Lời thoại tự nhiên hơn (guard / rewrite / neo thời gian / phrase) — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Mọi câu trả lời cho khách đi qua một tầng gác tất định (lặp, xưng hô, đại từ, giờ, ngôn ngữ) và được viết lại tối đa một lần; `parse_time` giải phần thiếu từ mốc neo; câu chốt lịch do LLM viết với số liệu từ DB; `booking` trả lời được câu hỏi kép.

**Architecture:** Thêm ba node vào LangGraph hiện có — `guard` (0 LLM, set `answer` duy nhất), `rewrite` (LLM, tag `rewrite`, 1 lần), `phrase` (LLM, tag `respond`, sau `confirm`). Node LLM trả `draft` thay cho `answer`. `ParsedTime` thêm `partial_hour/partial_minute` để `apply_anchor` điền ngày/buổi từ neo bằng code. `booking` nhận thêm 2 tool đọc của shop.

**Tech Stack:** Python 3.12, LangGraph, langchain-openai (`build_chat_model`), Pydantic v2, `difflib` (stdlib), Motor/Mongo, pytest-asyncio (Mongo thật `:27017`).

**Spec:** `docs/superpowers/specs/2026-09-14-natural-voice-guard-design.md`

## Global Constraints

- Chạy test: `PYTHONPATH=. .venv/bin/python -m pytest -q <file>` — cần `docker compose up -d mongo`. Mốc trước plan: **639 test xanh**.
- Nhánh `feat/natural-voice-guard` (đã có, chứa spec). Commit message tiếng Anh, ngắn, kết bằng `Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>`.
- Code/comment/docstring nội bộ: **tiếng Việt**. Prompt gửi LLM: **tiếng Anh**, không câu thoại mẫu tiếng Việt (`tests/test_prompts.py` canh 4 prompt chính; `_PROMPT` của `timeparse.py` là ngoại lệ có sẵn, giữ phong cách của nó).
- Chỉ token mang tag `respond` được stream (bẫy #8). `rewrite` mang tag `rewrite`, `streaming=False`.
- Thứ tự tin nhắn cho LLM: System → [digest] → lịch sử → khối bối cảnh → câu khách — không đổi (bẫy #9).
- Ghi lịch 100 % code ở `confirm`; **không thêm tool ghi**. Hàng rào: `tests/test_tools.py::test_there_is_NO_tool_that_writes_an_appointment`.
- Validator **ép, không từ chối**. Không đụng regex của `timeparse` (bẫy #11). Không đổi enum `MissingPiece`.
- Ngân sách: thường 2 LLM/lượt, tối đa 3 (rewrite); lượt chốt 1 (phrase).
- Hằng số của tầng gác ở `app/agents/booking_graph/guard.py`: `REPEAT_RATIO = 0.85`, `REPEAT_MIN_WORDS = 6`, `REPEAT_LOOKBACK = 3`, `ENGLISH_RATIO = 0.30`, `REWRITE_TIMEOUT_SECONDS = 8`, `PHRASE_TIMEOUT_SECONDS = 8`. Hai ngưỡng repeat là điểm khởi đầu — khoá sau khi duyệt đầu ra `scripts/probe_repeats.py` (Task 1).
- `.env` là nguồn cấu hình duy nhất; plan này không thêm biến cấu hình.

## Cấu trúc file

| File | Trách nhiệm |
|---|---|
| `app/agents/booking_graph/guard.py` (mới) | hằng số, `normalize`, `sentences`, `repeats`, 4 phép kiểm còn lại, `content_kept`, `find_violations`, `make_guard_node`, `route_after_guard` |
| `app/agents/booking_graph/rewrite.py` (mới) | `REWRITE_TAG`, `make_rewrite_node` |
| `app/agents/booking_graph/phrase.py` (mới) | `make_phrase_node` |
| `app/agents/booking_graph/prompts.py` | `VIOLATION_HINTS`, `REWRITE_PROMPT`, `PHRASE_PROMPT`; sửa `SUPERVISOR_PROMPT`, `BOOKING_PROMPT` |
| `app/agents/booking_graph/state.py` | khoá mới |
| `app/agents/booking_graph/agents.py` | node LLM trả `draft` |
| `app/agents/booking_graph/confirm.py` | trả `confirm_fact` + `fallback`; `route_after_confirm` → `phrase` |
| `app/agents/booking_graph/graph.py` | nối node mới |
| `app/agents/booking_graph/events.py` | `previous_replies`, `answer_source` → `append(..., source=)` |
| `app/agents/booking_graph/timeparse.py` | `partial_hour/partial_minute`, `apply_anchor`, `parse_vi_time(anchor, hours)` |
| `app/agents/booking_graph/tools.py` | `parse_time(text, anchor)`, propose trả `(iso: …)`, booking 7 tool |
| `app/agents/booking_graph/context.py` | `[id: …, iso: …]` |
| `app/models/conversation.py`, `app/services/conversation.py` | `ChatMessage.source`, `append(source=)` |
| `scripts/probe_repeats.py` (mới), `scripts/probe_supervisor.py` | công cụ đo |
| tests: `test_guard.py`, `test_rewrite.py`, `test_phrase.py` (mới); `test_graph_wiring.py` (mới); sửa `test_subagents.py`, `test_confirm.py`, `test_timeparse.py`, `test_tools.py`, `test_context_block.py`, `test_conversation.py`, `test_prompts.py`, `test_graph_events.py` | |

---

### Task 1: Tầng gác thuần code — `guard.py` (phép kiểm) + `ChatMessage.source` + probe transcript

**Files:**
- Create: `app/agents/booking_graph/guard.py`, `scripts/probe_repeats.py`
- Modify: `app/models/conversation.py` (ChatMessage), `app/services/conversation.py` (`append`)
- Test: `tests/test_guard.py`, `tests/test_conversation.py`

**Interfaces:**
- Produces (module `guard.py`):
  - `normalize(text: str) -> str`; `sentences(text: str) -> List[str]`
  - `repeats(draft: str, previous_replies: Sequence[str], ratio: float = REPEAT_RATIO, min_words: int = REPEAT_MIN_WORDS) -> bool`
  - `customer_asked_to_repeat(text: str) -> bool`
  - `check_address(draft: str, address: str, name: str) -> bool` — True = vi phạm
  - `check_register(draft: str) -> bool`; `check_clock(draft: str) -> bool`; `check_language(draft: str) -> bool`
  - `content_kept(original: str, rewritten: str) -> bool` — True = còn đủ số liệu
  - `find_violations(draft: str, *, previous_replies: Sequence[str], address: str, name: str, customer_text: str) -> List[str]` — mã: `repeat`, `address`, `register`, `clock`, `language`
- `ChatMessage.source: Literal["llm", "code"] = "llm"`; `ConversationService.append(user_id, role, content, source: str = "llm")`.

- [ ] **Step 1: Viết test đỏ**

```python
# tests/test_guard.py
"""Tầng gác là code tất định — mỗi phép kiểm có ca dương và ca âm."""
import pytest

from app.agents.booking_graph.guard import (check_address, check_clock,
                                            check_language, check_register,
                                            content_kept,
                                            customer_asked_to_repeat,
                                            find_violations, normalize,
                                            repeats, sentences)


class TestNormalizeAndSentences:
    def test_normalize_drops_punctuation_case_and_spacing(self):
        assert normalize("Dạ, anh Tám!  Em   chào ANH.") == "dạ anh tám em chào anh"

    def test_sentences_split_on_terminal_punctuation(self):
        assert sentences("Em giữ chỗ rồi ạ. Anh xác nhận giúp em nhé? Cảm ơn!") == [
            "Em giữ chỗ rồi ạ", "Anh xác nhận giúp em nhé", "Cảm ơn"]


class TestRepeats:
    PREV = ["Em chỉ xem và đặt lịch của chị Thắm thôi. Chị muốn đặt hay kiểm tra lịch của mình ạ?"]

    def test_verbatim_copy_is_a_repeat(self):
        assert repeats(self.PREV[0], self.PREV) is True

    def test_near_copy_with_a_word_added_is_a_repeat(self):
        """Ca thật 2026-09-14: chỉ khác "của chị" → "của chị Thắm"."""
        draft = "Em chỉ xem và đặt lịch của chị Thắm thôi. Chị muốn đặt hay kiểm tra lịch của chị Thắm ạ?"
        assert repeats(draft, self.PREV) is True

    def test_a_repeated_tail_sentence_is_a_repeat(self):
        draft = "Dạ giá thì chủ tiệm sẽ báo ạ. Chị muốn đặt hay kiểm tra lịch của mình ạ?"
        assert repeats(draft, self.PREV) is True

    def test_short_shared_sentence_is_not_a_repeat(self):
        """Câu ngắn dưới 6 từ ("Dạ anh Tám ạ.") lặp là bình thường."""
        assert repeats("Dạ anh Tám ạ. Mai 9 giờ sáng nhé.", ["Dạ anh Tám ạ. Em đã hủy rồi."]) is False

    def test_different_content_is_not_a_repeat(self):
        assert repeats("Tiệm đóng cửa 7 giờ tối ạ.", self.PREV) is False

    def test_only_the_last_three_replies_count(self):
        old = ["Câu này lặp lại y nguyên nhưng đã quá xa trong lịch sử rồi ạ."] + ["khác một"] * 3
        assert repeats("Câu này lặp lại y nguyên nhưng đã quá xa trong lịch sử rồi ạ.", old) is False


@pytest.mark.parametrize("text", ["nhắc lại giùm", "em nói lại đi", "anh quên rồi", "lặp lại giúp anh"])
def test_customer_asking_to_repeat_is_detected(text):
    assert customer_asked_to_repeat(text) is True


def test_ordinary_text_is_not_a_repeat_request():
    assert customer_asked_to_repeat("mai 9 giờ được không") is False


class TestAddress:
    def test_wrong_pronoun_at_sentence_start_for_a_male_customer(self):
        assert check_address("Chị hỏi bên chủ tiệm giúp em nhé.", "anh Tám", "Tám") is True

    def test_wrong_pronoun_before_the_name(self):
        assert check_address("Em giữ chỗ cho chị Tám rồi ạ.", "anh Tám", "Tám") is True

    def test_chi_chu_tiem_is_not_a_violation(self):
        """"Chị chủ" là cách gọi chủ tiệm, không phải gọi khách."""
        assert check_address("Chị chủ sẽ trả lời phần giá ạ. Anh Tám muốn đặt gì ạ?", "anh Tám", "Tám") is False

    def test_correct_pronoun_passes(self):
        assert check_address("Dạ anh Tám, mai 9 giờ sáng ạ.", "anh Tám", "Tám") is False

    def test_female_customer_called_anh_is_a_violation(self):
        assert check_address("Anh Lan muốn đặt giờ nào ạ?", "chị Lan", "Lan") is True

    def test_ambiguous_address_never_flags(self):
        assert check_address("Chị muốn đặt giờ nào ạ?", "anh chị", "") is False


class TestRegister:
    @pytest.mark.parametrize("draft", ["Cô muốn đặt giờ nào ạ?", "Để con xem lịch giúp cô.", "Chú Ba đặt 3 giờ nhé."])
    def test_forbidden_register_is_flagged(self, draft):
        assert check_register(draft) is True

    def test_anh_chi_em_register_passes(self):
        assert check_register("Dạ anh Tám, em xem lịch giúp anh nhé.") is False


class TestClock:
    @pytest.mark.parametrize("draft", ["Hẹn anh 15:00 nhé.", "Em giữ chỗ chín giờ sáng ạ.",
                                       "Thứ Bảy ngày mười chín tháng chín ạ."])
    def test_colon_and_number_words_are_flagged(self, draft):
        assert check_clock(draft) is True

    def test_digits_pass(self):
        assert check_clock("Thứ Bảy 19/9, 9 giờ sáng ạ.") is False

    def test_a_name_that_is_a_number_word_passes(self):
        assert check_clock("Dạ anh Ba, em chào anh.") is False


class TestLanguage:
    def test_english_reply_is_flagged(self):
        assert check_language("Sure, I can book that appointment for you tomorrow.") is True

    def test_vietnamese_passes(self):
        assert check_language("Dạ em giữ chỗ Thứ Ba 15/9, 9 giờ sáng cho anh Tám rồi ạ.") is False

    def test_short_reply_without_diacritics_passes(self):
        assert check_language("Da anh Tam.") is False


class TestContentKept:
    def test_dropping_a_time_is_not_kept(self):
        assert content_kept("Em giữ chỗ Thứ Ba 15/9, 9 giờ sáng ạ.", "Em giữ chỗ cho anh rồi ạ.") is False

    def test_reworded_with_the_same_numbers_is_kept(self):
        assert content_kept("Em giữ chỗ Thứ Ba 15/9, 9 giờ sáng ạ.",
                            "Dạ anh Tám, Thứ Ba 15/9 lúc 9 giờ sáng em đã giữ chỗ rồi nhé.") is True


class TestFindViolations:
    def test_collects_codes_in_fixed_order(self):
        codes = find_violations("Chị hỏi chủ tiệm giúp em, hẹn 15:00 nhé.",
                                previous_replies=[], address="anh Tám", name="Tám", customer_text="giá bao nhiêu")
        assert codes == ["address", "clock"]

    def test_repeat_is_skipped_when_the_customer_asked_for_it(self):
        prev = ["Dạ anh Tám, mai 9 giờ sáng cắt tóc ạ, em đã giữ chỗ rồi."]
        assert find_violations(prev[0], previous_replies=prev, address="anh Tám", name="Tám",
                               customer_text="mai mấy giờ ha, anh quên rồi") == []

    def test_clean_draft_has_no_violations(self):
        assert find_violations("Dạ anh Tám, mai 9 giờ sáng ạ.", previous_replies=[],
                               address="anh Tám", name="Tám", customer_text="mai mấy giờ") == []
```

Thêm vào `tests/test_conversation.py`:

```python
class TestMessageSource:
    async def test_append_defaults_to_llm_and_can_mark_code(self, test_db):
        svc = ConversationService(test_db)
        await svc.append("u1", "assistant", "câu LLM")
        await svc.append("u1", "assistant", "câu code", source="code")
        msgs = await svc._all_messages("u1")
        assert [m.source for m in msgs] == ["llm", "code"]

    async def test_old_documents_without_source_read_as_llm(self, test_db):
        await test_db["conversations"].insert_one({"user_id": "u9", "messages": [
            {"role": "assistant", "content": "cũ", "created_at": now_utc()}]})
        msgs = await ConversationService(test_db)._all_messages("u9")
        assert msgs[0].source == "llm"
```

- [ ] **Step 2: Chạy, thấy đỏ**

Run: `PYTHONPATH=. .venv/bin/python -m pytest -q tests/test_guard.py tests/test_conversation.py -k "Guard or Normalize or Repeats or repeat or Address or Register or Clock or Language or Content or Violations or MessageSource"`
Expected: FAIL — `ModuleNotFoundError: app.agents.booking_graph.guard`; `TypeError: append() got an unexpected keyword argument 'source'`.

- [ ] **Step 3: `ChatMessage.source` và `append(source=)`**

`app/models/conversation.py`:

```python
class ChatMessage(BaseModel):
    role: Role
    content: str
    created_at: datetime = Field(default_factory=now_utc)
    # "code": câu do node confirm / câu lỗi sinh — cố ý giống nhau, không đem so
    # lặp. Document cũ không có trường này → mặc định "llm", không migrate.
    source: Literal["llm", "code"] = "llm"
```

`app/services/conversation.py::append`:

```python
    async def append(self, user_id: str, role: str, content: str, source: str = "llm") -> None:
        await self.collection.update_one(
            {"user_id": user_id},
            {
                "$push": {"messages": ChatMessage(role=role, content=content, source=source).model_dump()},
                "$set": {"updated_at": now_utc()},
                "$setOnInsert": {"user_id": user_id, "created_at": now_utc()},
            },
            upsert=True,
        )
```

- [ ] **Step 4: Viết `app/agents/booking_graph/guard.py` — phần phép kiểm**

```python
"""Tầng gác: kiểm câu trả lời bằng CODE trước khi phát cho khách.

Bốn lỗi đo được ngày 2026-09-14 (lặp, xưng hô trượt, giọng cô/chú, giờ viết
sai) đều không sửa được bằng prompt — kể cả viết hoa, temperature 0.6, hay trích
câu cũ vào khối bối cảnh. Ở đây phát hiện tất định; LLM chỉ dùng để viết lại
(rewrite.py), tối đa một lần. Spec: 2026-09-14-natural-voice-guard-design.md.
"""
import re
from difflib import SequenceMatcher
from typing import List, Sequence

REPEAT_RATIO = 0.85          # điểm khởi đầu — khoá sau khi duyệt scripts/probe_repeats.py
REPEAT_MIN_WORDS = 6
REPEAT_LOOKBACK = 3
ENGLISH_RATIO = 0.30
REWRITE_TIMEOUT_SECONDS = 8
PHRASE_TIMEOUT_SECONDS = 8

_PUNCT = re.compile(r"[^\w\s]", re.UNICODE)
_SENTENCE_END = re.compile(r"(?<=[.!?…])\s+")
_VI_DIACRITIC = re.compile(r"[àáảãạăắằẳẵặâấầẩẫậèéẻẽẹêếềểễệìíỉĩịòóỏõọôốồổỗộơớờởỡợùúủũụưứừửữựỳýỷỹỵđ]", re.I)

# Khách xin nhắc lại thì lặp là đúng — cùng vế miễn trừ của _NO_REPEAT.
_REPEAT_REQUESTS = ("nhắc lại", "nói lại", "lặp lại", "quên rồi", "quên mất")

# Đại từ trái giọng "em — anh/chị". "con" chỉ bắt khi làm chủ ngữ của một
# động từ lễ tân, để "con gái", "con nít" không bị oan.
_REGISTER = re.compile(r"(^|[.!?…]\s*)(cô|chú|bác)\b|\b(cô|chú|bác)\s+\p{Lu}", re.I) if False else None
_REGISTER_START = re.compile(r"(?:^|[.!?…]\s+)(cô|chú|bác)\b", re.I)
_REGISTER_BEFORE_NAME = re.compile(r"\b(cô|chú|bác)\s+[A-ZĐ][a-zà-ỹ]+")
_CON_AS_SUBJECT = re.compile(r"\b(để|giúp|cho)\s+con\b|\bcon\s+(xem|giúp|đặt|hỏi|kiểm|giữ)\b", re.I)

_NUMBER_WORD = (r"(?:mười\s+(?:một|hai|ba|bốn|lăm|sáu|bảy|tám|chín)|"
                r"(?:hai|ba)\s+mươi(?:\s+(?:mốt|hai|ba|bốn|lăm|sáu|bảy|tám|chín))?|"
                r"mười|một|hai|ba|bốn|năm|sáu|bảy|tám|chín)")
_CLOCK_COLON = re.compile(r"\b\d{1,2}:\d{2}\b")
_CLOCK_WORDS = re.compile(rf"\b{_NUMBER_WORD}\s+(?:giờ|tháng)\b|\bngày\s+{_NUMBER_WORD}\b", re.I)

_ENGLISH = {"the", "you", "your", "please", "appointment", "booking", "book", "hello", "thanks",
            "thank", "time", "today", "tomorrow", "we", "is", "are", "can", "hair", "nail",
            "salon", "open", "closed", "at", "for", "and", "sure", "i", "that", "would", "like"}

_DATETIME = re.compile(r"Thứ\s+\w+\s+\d{1,2}/\d{1,2}|Chủ\s+Nhật\s+\d{1,2}/\d{1,2}|\d{1,2}\s+giờ(?:\s+rưỡi|\s+\d{2})?(?:\s+(?:sáng|chiều|tối))?")
_NUMBER = re.compile(r"\d+")


def normalize(text: str) -> str:
    return " ".join(_PUNCT.sub(" ", (text or "").lower()).split())


def sentences(text: str) -> List[str]:
    parts = [p.strip().rstrip(".!?…").strip() for p in _SENTENCE_END.split((text or "").strip())]
    return [p for p in parts if p]


def repeats(draft: str, previous_replies: Sequence[str],
            ratio: float = REPEAT_RATIO, min_words: int = REPEAT_MIN_WORDS) -> bool:
    """Cấp 2 (độ giống cả câu trả lời) + cấp 3 (từng câu ≥ min_words từ), so với
    REPEAT_LOOKBACK câu đáp LLM gần nhất. Không so nguyên văn: ca thật chỉ khác
    "của chị" → "của chị Thắm"."""
    d = normalize(draft)
    d_sents = [normalize(s) for s in sentences(draft)]
    for prev in list(previous_replies)[-REPEAT_LOOKBACK:]:
        p = normalize(prev)
        if SequenceMatcher(None, d, p).ratio() >= ratio:
            return True
        p_sents = [normalize(s) for s in sentences(prev)]
        for sent in d_sents:
            if len(sent.split()) >= min_words and any(
                SequenceMatcher(None, sent, s).ratio() >= ratio for s in p_sents
            ):
                return True
    return False


def customer_asked_to_repeat(text: str) -> bool:
    lowered = (text or "").lower()
    return any(k in lowered for k in _REPEAT_REQUESTS)


def _pronouns(address: str) -> tuple:
    """"anh Tám" → (đúng "anh", sai "chị"); "chị Lan" → ngược lại; "anh chị" → không kiểm."""
    head = (address or "").split()[0].lower() if address else ""
    if head == "anh" and address.strip().lower() != "anh chị":
        return "anh", "chị"
    if head == "chị":
        return "chị", "anh"
    return None, None


def check_address(draft: str, address: str, name: str) -> bool:
    _, wrong = _pronouns(address)
    if not wrong:
        return False
    # Đầu câu, trừ khi nói về chủ tiệm ("Chị chủ ...").
    for sent in sentences(draft):
        first = sent.split()[:2]
        if first and first[0].lower() == wrong and not (len(first) > 1 and first[1].lower() == "chủ"):
            return True
    # Ngay trước tên khách.
    if name and re.search(rf"\b{wrong}\s+{re.escape(name)}\b", draft, re.I):
        return True
    return False


def check_register(draft: str) -> bool:
    return bool(_REGISTER_START.search(draft) or _REGISTER_BEFORE_NAME.search(draft)
                or _CON_AS_SUBJECT.search(draft))


def check_clock(draft: str) -> bool:
    return bool(_CLOCK_COLON.search(draft) or _CLOCK_WORDS.search(draft))


def check_language(draft: str) -> bool:
    words = normalize(draft).split()
    if len(words) <= 6:
        return False
    if not _VI_DIACRITIC.search(draft):
        return True
    english = sum(1 for w in words if w in _ENGLISH)
    return english / len(words) > ENGLISH_RATIO


def content_kept(original: str, rewritten: str) -> bool:
    """Bản viết lại phải còn mọi mốc ngày-giờ và mọi con số của bản gốc."""
    for m in _DATETIME.findall(original):
        if m not in rewritten:
            return False
    return all(n in _NUMBER.findall(rewritten) for n in _NUMBER.findall(original))


def find_violations(draft: str, *, previous_replies: Sequence[str], address: str, name: str,
                    customer_text: str) -> List[str]:
    codes: List[str] = []
    if not customer_asked_to_repeat(customer_text) and repeats(draft, previous_replies):
        codes.append("repeat")
    if check_address(draft, address, name):
        codes.append("address")
    if check_register(draft):
        codes.append("register")
    if check_clock(draft):
        codes.append("clock")
    if check_language(draft):
        codes.append("language")
    return codes
```

Xoá dòng `_REGISTER = ... if False else None` (dòng nháp — không giữ mã chết).

- [ ] **Step 5: Chạy, thấy xanh**

Run: `PYTHONPATH=. .venv/bin/python -m pytest -q tests/test_guard.py tests/test_conversation.py`
Expected: PASS. Nếu `test_a_name_that_is_a_number_word_passes` đỏ: "anh Ba, em" — regex `_CLOCK_WORDS` chỉ khớp khi theo sau là "giờ"/"tháng", nên phải xanh; nếu không, kiểm biên từ `\b` với chữ có dấu (Python `re` với str Unicode coi chữ có dấu là `\w`, ổn).

- [ ] **Step 6: `scripts/probe_repeats.py` — chạy `repeats()` trên transcript sẵn có**

```python
"""Chạy tầng gác `repeats()` trên các transcript đã có để hiệu chỉnh ngưỡng.

    PYTHONPATH=. .venv/bin/python scripts/probe_repeats.py [--ratio 0.85] [--min-words 6] FILE...

In từng cặp (câu đáp trước, câu đáp bị cờ) để người đọc quyết: cờ đúng hay cờ
oan. Không gọi LLM. Định dạng transcript: dòng "BOT   : ..." của
scripts/chat_e2e_transcript.py; mỗi "##### KỊCH BẢN" là một cuộc riêng.
"""
import argparse
import glob

from app.agents.booking_graph.guard import repeats


def bot_lines(path):
    convos, current = [], []
    for line in open(path, encoding="utf-8"):
        if line.startswith("##### KỊCH BẢN"):
            if current:
                convos.append(current)
            current = []
        elif line.startswith("BOT   :"):
            current.append(line.split(":", 1)[1].strip())
    if current:
        convos.append(current)
    return convos


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("files", nargs="*", default=sorted(glob.glob("*.txt")))
    ap.add_argument("--ratio", type=float, default=0.85)
    ap.add_argument("--min-words", type=int, default=6)
    args = ap.parse_args()

    total = flagged = 0
    for path in args.files:
        for convo in bot_lines(path):
            for i, reply in enumerate(convo):
                total += 1
                if repeats(reply, convo[:i], ratio=args.ratio, min_words=args.min_words):
                    flagged += 1
                    print(f"\n[{path}] lượt {i + 1}")
                    for prev in convo[max(0, i - 3):i]:
                        print(f"  trước: {prev}")
                    print(f"  CỜ   : {reply}")
    print(f"\n{flagged}/{total} câu đáp bị cờ (ratio={args.ratio}, min_words={args.min_words})")


if __name__ == "__main__":
    main()
```

Chạy: `PYTHONPATH=. .venv/bin/python scripts/probe_repeats.py baseline-*.txt after-*.txt chat_transcript.txt` và **dán toàn bộ đầu ra vào report** kèm nhận xét từng cặp bị cờ (đúng/oan). Chạy thêm với `--ratio 0.80` và `--ratio 0.90` để so. KHÔNG tự đổi hằng số — chủ dự án quyết theo report.

- [ ] **Step 7: Toàn bộ suite, commit**

Run: `PYTHONPATH=. .venv/bin/python -m pytest -q`
Expected: PASS (≥ 639 + test mới).

```bash
git add app/agents/booking_graph/guard.py scripts/probe_repeats.py app/models/conversation.py app/services/conversation.py tests/test_guard.py tests/test_conversation.py
git commit -m "guard: deterministic reply checks (repeat, address, register, clock, language) and message source

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 2: Nối `guard` + `rewrite` vào graph; node LLM trả `draft`

**Files:**
- Create: `app/agents/booking_graph/rewrite.py`, `tests/test_rewrite.py`, `tests/test_graph_wiring.py`
- Modify: `app/agents/booking_graph/guard.py` (thêm node), `state.py`, `agents.py`, `graph.py`, `events.py`, `prompts.py`
- Test: `tests/test_subagents.py`, `tests/test_graph_events.py`

**Interfaces:**
- Consumes: `find_violations`, `content_kept` (Task 1); `ConversationService.append(source=)`.
- Produces:
  - `GraphState` thêm: `draft: str`, `original_draft: str`, `violations: List[str]`, `rewritten: bool`, `previous_replies: List[str]`, `customer_text: str`, `answer_source: str` (`"llm"|"code"`), `fallback: Optional[str]`, `phrase_fact: Optional[dict]` (dùng ở Task 4).
  - `make_subagent_node(...)` trả `{"draft": ...}` thay cho `{"answer": ...}`.
  - `guard.py`: `make_guard_node(user) -> node`; `route_after_guard(state) -> "rewrite" | "end"`. `guard` là nơi **duy nhất** set `answer` và `answer_source`.
  - `rewrite.py`: `REWRITE_TAG = "rewrite"`; `make_rewrite_node() -> node` trả `{"draft": new, "original_draft": old, "rewritten": True}`; lỗi → `{"rewritten": True}` (draft giữ nguyên).
  - `prompts.py`: `VIOLATION_HINTS: dict[str, str]`, `REWRITE_PROMPT: str` (chứa `{violations}`, `{address}`, `{previous}`, `{draft}`).
  - `events.py`: state có `previous_replies` (câu đáp `source == "llm"` trong `context["history"]`), `customer_text`; sau lượt `append(..., source=final_state.get("answer_source", "llm"))`.
  - `confirm` trong task này vẫn trả `answer` thẳng ra END như hiện tại (đổi ở Task 4); `route_after_confirm` không đổi.

- [ ] **Step 1: Viết test đỏ**

`tests/test_rewrite.py`:

```python
import pytest
from langchain_core.messages import AIMessage

from app.agents.booking_graph.guard import make_guard_node, route_after_guard
from app.agents.booking_graph.rewrite import REWRITE_TAG, make_rewrite_node
from app.models.user import User

pytestmark = pytest.mark.asyncio

USER = User(phone="0912345678", hashed_password="x", full_name="Chú Tám")


class ScriptedModel:
    def __init__(self, reply, fail=False):
        self.reply, self.fail, self.calls, self.kwargs = reply, fail, [], None

    async def ainvoke(self, messages, **kw):
        self.calls.append(messages)
        if self.fail:
            raise RuntimeError("azure down")
        return AIMessage(content=self.reply)


@pytest.fixture
def patch_model(monkeypatch):
    def _install(reply, fail=False):
        m = ScriptedModel(reply, fail)
        def _build(**kwargs):
            m.kwargs = kwargs
            return m
        monkeypatch.setattr("app.agents.booking_graph.rewrite.build_chat_model", _build)
        return m
    return _install


def a_state(draft, previous=(), customer="giá bao nhiêu", **extra):
    return {"draft": draft, "previous_replies": list(previous), "customer_text": customer,
            "context_block": "", "messages": [], **extra}


class TestGuardNode:
    async def test_clean_draft_becomes_the_answer_unchanged(self):
        out = await make_guard_node(USER)(a_state("Dạ anh Tám, mai 9 giờ sáng ạ."))
        assert out["answer"] == "Dạ anh Tám, mai 9 giờ sáng ạ."
        assert out["answer_source"] == "llm"
        assert out.get("violations") == []

    async def test_violation_on_first_pass_requests_a_rewrite(self):
        out = await make_guard_node(USER)(a_state("Chị hỏi chủ tiệm giúp em nhé."))
        assert out["violations"] == ["address"]
        assert "answer" not in out
        assert route_after_guard({**a_state("x"), **out}) == "rewrite"

    async def test_second_pass_with_violations_falls_back_to_the_original_draft(self):
        state = a_state("Chị vẫn sai nè.", rewritten=True, original_draft="Chị sai lần đầu.")
        out = await make_guard_node(USER)(state)
        assert out["answer"] == "Chị sai lần đầu."
        assert route_after_guard({**state, **out}) == "end"

    async def test_second_pass_clean_uses_the_rewrite(self):
        state = a_state("Dạ anh Tám, chủ tiệm sẽ báo giá ạ.", rewritten=True, original_draft="Chị hỏi chủ tiệm nhé.")
        out = await make_guard_node(USER)(state)
        assert out["answer"] == "Dạ anh Tám, chủ tiệm sẽ báo giá ạ."

    async def test_second_pass_that_lost_a_time_falls_back(self):
        state = a_state("Dạ anh Tám, em giữ chỗ rồi ạ.", rewritten=True,
                        original_draft="Chị Tám, em giữ chỗ Thứ Ba 15/9, 9 giờ sáng rồi ạ.")
        out = await make_guard_node(USER)(state)
        assert out["answer"] == "Chị Tám, em giữ chỗ Thứ Ba 15/9, 9 giờ sáng rồi ạ."
        assert "content" in out["violations"]

    async def test_repeat_uses_previous_llm_replies(self):
        prev = ["Em chỉ xem và đặt lịch của anh Tám thôi. Anh muốn đặt hay xem lịch của mình ạ?"]
        out = await make_guard_node(USER)(a_state(prev[0], previous=prev))
        assert out["violations"] == ["repeat"]


class TestRewriteNode:
    async def test_rewrites_with_the_violation_hints_and_previous_reply(self, patch_model):
        model = patch_model("Dạ anh Tám, phần giá để chủ tiệm báo anh nhé.")
        state = a_state("Chị hỏi chủ tiệm giúp em nhé.", previous=["Câu trước."], violations=["address", "repeat"])
        out = await make_rewrite_node()(state)
        assert out == {"draft": "Dạ anh Tám, phần giá để chủ tiệm báo anh nhé.",
                       "original_draft": "Chị hỏi chủ tiệm giúp em nhé.", "rewritten": True}
        prompt = model.calls[0][0].content
        assert "address" in prompt and "repeat" in prompt and "Câu trước." in prompt
        assert model.kwargs["tags"] == [REWRITE_TAG] and model.kwargs["streaming"] is False

    async def test_model_failure_keeps_the_draft_and_marks_rewritten(self, patch_model):
        patch_model("", fail=True)
        out = await make_rewrite_node()(a_state("Chị ơi.", violations=["address"]))
        assert out == {"rewritten": True}

    async def test_rewrite_tag_is_never_respond(self):
        assert REWRITE_TAG != "respond"
```

`tests/test_graph_wiring.py`:

```python
"""Hình dạng graph khoá cứng: mọi câu LLM qua guard đúng một lần; rewrite tối đa một lần."""
from unittest.mock import MagicMock

from app.agents.booking_graph.graph import build_graph
from app.models.user import User


def edges():
    g = build_graph(MagicMock(), User(phone="0912345678", hashed_password="x", full_name="Cô Lan")).get_graph()
    return {(e.source, e.target) for e in g.edges}


def test_every_llm_node_feeds_the_guard():
    e = edges()
    for node in ("booking", "shop", "social"):
        assert (node, "guard") in e, node
        assert (node, "__end__") not in e, node


def test_guard_goes_to_rewrite_or_end_and_rewrite_returns_to_guard():
    e = edges()
    assert ("guard", "rewrite") in e and ("guard", "__end__") in e
    assert ("rewrite", "guard") in e
    assert ("rewrite", "__end__") not in e
```

Sửa `tests/test_subagents.py`: mọi `result["answer"]` của node LLM → `result["draft"]` (grep `["answer"]` trong file; node subagent không còn trả `answer`). Thêm:

```python
@pytest.mark.asyncio
async def test_llm_node_returns_a_draft_not_an_answer(patch_model):
    patch_model([AIMessage(content="Dạ.")])
    out = await make_subagent_node("prompt", [], tag="respond")(a_state("chào"))
    assert out == {"draft": "Dạ."}
```

Sửa `tests/test_graph_events.py` — trong hai test `FakeGraph` hiện có, output `{"answer": "Dạ."}` giữ nguyên (final_state đọc `answer`); thêm test:

```python
async def test_run_turn_records_answer_source_and_previous_llm_replies(test_db, monkeypatch):
    from app.agents.booking_graph import events
    from app.services.auth import AuthService
    from app.services.conversation import ConversationService

    user = await AuthService(test_db).create_user("0912345678", "matkhau123", "Cô Lan")
    convs = ConversationService(test_db)
    await convs.append(str(user.id), "assistant", "câu llm cũ")
    await convs.append(str(user.id), "assistant", "câu code cũ", source="code")
    seen_state = {}

    class FakeGraph:
        async def astream_events(self, state, config, version):
            seen_state.update(state)
            yield {"event": "on_chain_end", "data": {"output": {"answer": "Xong.", "answer_source": "code"}}}

    monkeypatch.setattr(events, "build_graph", lambda db, u: FakeGraph())
    monkeypatch.setattr(events, "schedule_compaction", lambda db, user_id: None)
    [e async for e in events.run_turn(test_db, user, "ừ")]

    assert seen_state["previous_replies"] == ["câu llm cũ"]
    assert seen_state["customer_text"] == "ừ"
    assert (await convs._all_messages(str(user.id)))[-1].source == "code"
```

- [ ] **Step 2: Chạy, thấy đỏ**

Run: `PYTHONPATH=. .venv/bin/python -m pytest -q tests/test_rewrite.py tests/test_graph_wiring.py tests/test_subagents.py tests/test_graph_events.py`
Expected: FAIL — import `make_guard_node`/`rewrite` thiếu; graph không có node `guard`.

- [ ] **Step 3: `state.py`**

```python
    draft: str                       # câu LLM vừa viết, chưa qua guard
    original_draft: str              # draft trước rewrite — dùng khi rewrite vẫn hỏng
    violations: List[str]
    rewritten: bool
    previous_replies: List[str]      # câu đáp LLM gần nhất (source == "llm")
    customer_text: str
    answer_source: str               # "llm" | "code" — ghi vào ChatMessage.source
    fallback: Optional[str]          # câu cứng của confirm (Task 4)
    phrase_fact: Optional[Dict[str, Any]]
```

- [ ] **Step 4: `prompts.py` — hint và prompt viết lại**

```python
# Mã vi phạm do guard.py phát hiện → một dòng giải thích cho rewrite. Tiếng
# Anh, không câu mẫu; "anh"/"chị" là chủ thể luật, không phải ví dụ.
VIOLATION_HINTS = {
    "repeat": "It repeats a sentence you already said in this conversation; say it differently.",
    "address": "It addresses the customer with the wrong pronoun; use exactly the form given below.",
    "register": "It uses the wrong register (a word for elders or a child speaker); stay in the em — anh/chị register.",
    "clock": "It writes a time with a colon or with number words; write digits followed by the part of the day, as the tools do.",
    "language": "It is not Vietnamese; every word must be Vietnamese.",
    "content": "It dropped a date, time or number that the original contained; keep every one of them.",
}

REWRITE_PROMPT = """You are fixing ONE reply written by a salon receptionist to a customer.
Rewrite it in Vietnamese so that it says the same thing with the same dates,
times and numbers, but without the problems listed. Change wording only —
never add facts, never remove a date, time or number. Reply with the new
sentence only, nothing else.

Problems:
{violations}

Address the customer as: {address}

Your previous reply to the customer (do not repeat it):
{previous}

Reply to fix:
{draft}"""
```

- [ ] **Step 5: `agents.py` — trả `draft`**

Trong `make_subagent_node`, đổi hai chỗ:

```python
            if not calls:
                return {"draft": reply.content or FALLBACK_ANSWER}
```

```python
        logger.warning("tool_loop_exhausted", extra={"tag": tag})
        return {"draft": FALLBACK_ANSWER}
```

Cập nhật docstring: "Node không set `answer` — chỉ `draft`; `guard` mới quyết câu cuối."

- [ ] **Step 6: `guard.py` — node và route**

Nối vào cuối file:

```python
from app.agents.booking_graph.context import address_phrase, derive_address   # đặt lên đầu file cùng các import khác
from app.agents.booking_graph.state import GraphState
from app.core.logging import get_logger
from app.models.user import User

logger = get_logger(__name__)


def make_guard_node(user: User):
    """Nơi DUY NHẤT set `answer`. Lần 1: vi phạm → xin rewrite. Lần 2: vi phạm
    (kể cả mất số liệu) → trả draft GỐC, không phải bản rewrite đã sai."""
    address = address_phrase(user.full_name)
    _, name = derive_address(user.full_name)

    async def node(state: GraphState) -> dict:
        draft = state.get("draft") or ""
        fact = state.get("phrase_fact")
        if fact:
            return _guard_phrase(state, draft, fact, address)          # Task 4

        codes = find_violations(
            draft, previous_replies=state.get("previous_replies") or [],
            address=address, name=name, customer_text=state.get("customer_text") or "",
        )
        if state.get("rewritten"):
            original = state.get("original_draft") or draft
            if not content_kept(original, draft):
                codes.append("content")
            if codes:
                logger.warning("guard_gave_up", extra={"codes": codes})
                return {"answer": original, "answer_source": "llm", "violations": codes}
            logger.info("guard_rewritten")
            return {"answer": draft, "answer_source": "llm", "violations": []}

        if codes:
            logger.info("guard_violation", extra={"codes": codes})
            return {"violations": codes}
        return {"answer": draft, "answer_source": "llm", "violations": []}

    return node


def _guard_phrase(state, draft, fact, address):
    """Thay ở Task 4. Task 2: chưa có phrase — không bao giờ tới đây."""
    return {"answer": draft, "answer_source": "llm", "violations": []}


def route_after_guard(state: GraphState) -> str:
    return "end" if "answer" in state and state.get("answer") else "rewrite"
```

Lưu ý `route_after_guard`: LangGraph gộp output node vào state trước khi gọi hàm route, nên khi guard không set `answer` (lần 1 có vi phạm) state không có `answer` → `"rewrite"`. Với state đến từ lượt trước có `answer` cũ: `run_turn` dựng state mới mỗi lượt, không mang `answer` — OK.

- [ ] **Step 7: `rewrite.py`**

```python
"""Viết lại MỘT lần câu bị guard cờ. LLM không quyết có lỗi hay không — code
đã quyết; nó chỉ sửa đúng lỗi được nêu."""
import asyncio

from langchain_core.messages import HumanMessage

from app.agents.booking_graph.context import address_phrase
from app.agents.booking_graph.guard import REWRITE_TIMEOUT_SECONDS
from app.agents.booking_graph.prompts import REWRITE_PROMPT, VIOLATION_HINTS
from app.agents.booking_graph.state import GraphState
from app.agents.llm import build_chat_model
from app.core.logging import get_logger

logger = get_logger(__name__)

REWRITE_TAG = "rewrite"   # KHÔNG phải "respond": bản viết lại không stream, complete mới thay


def make_rewrite_node():
    async def node(state: GraphState) -> dict:
        draft = state.get("draft") or ""
        codes = state.get("violations") or []
        previous = (state.get("previous_replies") or [])[-1:] or ["(none)"]
        # Cách gọi lấy từ khối bối cảnh: dòng "Gọi khách là: ..." — tránh nhận
        # thêm User vào closure chỉ để làm một chuỗi.
        address = _address_from_block(state.get("context_block") or "")
        prompt = REWRITE_PROMPT.format(
            violations="\n".join(f"- {c}: {VIOLATION_HINTS.get(c, c)}" for c in codes),
            address=address, previous=previous[0], draft=draft,
        )
        model = build_chat_model(tags=[REWRITE_TAG], temperature=0.3, streaming=False)
        try:
            reply = await asyncio.wait_for(model.ainvoke([HumanMessage(content=prompt)]),
                                           timeout=REWRITE_TIMEOUT_SECONDS)
        except Exception as exc:
            logger.warning("rewrite_failed", extra={"error": str(exc), "codes": codes})
            return {"rewritten": True}
        new = (reply.content or "").strip()
        if not new:
            return {"rewritten": True}
        return {"draft": new, "original_draft": draft, "rewritten": True}

    return node


def _address_from_block(block: str) -> str:
    for line in block.splitlines():
        if line.startswith("Gọi khách là:"):
            return line.split(":", 1)[1].strip().rstrip(".")
    return "anh chị"
```

Test `TestRewriteNode` dùng `context_block=""` → address "anh chị"; assertion không kiểm address nên ổn.

- [ ] **Step 8: `graph.py` — nối**

```python
from app.agents.booking_graph.guard import make_guard_node, route_after_guard
from app.agents.booking_graph.rewrite import make_rewrite_node
...
    graph.add_node("guard", make_guard_node(user))
    graph.add_node("rewrite", make_rewrite_node())
    ...
    # Mọi câu LLM đi qua guard ĐÚNG MỘT lần trước khi ra END; rewrite tối đa
    # một lần rồi quay lại guard để kiểm, không viết lại lần hai.
    for node in ("social", "shop", "booking"):
        graph.add_edge(node, "guard")
    graph.add_conditional_edges("guard", route_after_guard, {"rewrite": "rewrite", "end": END})
    graph.add_edge("rewrite", "guard")
```

Xoá vòng `for node in (...): graph.add_edge(node, END)`. Nhánh `confirm → END` giữ nguyên ở task này.

- [ ] **Step 9: `events.py`**

Trong `run_turn`, thêm vào `state`:

```python
            "previous_replies": [m.content for m in context["history"]
                                 if m.role == "assistant" and m.source == "llm"],
            "customer_text": question,
```

Và khi lưu:

```python
        answer = final_state.get("answer", "")
        if answer:
            await conversations.append(user_id, "user", question)
            await conversations.append(user_id, "assistant", answer,
                                       source=final_state.get("answer_source", "llm"))
```

`confirm` hiện trả `answer` mà không có `answer_source` → mặc định "llm"; Task 4 sửa. Tạm chấp nhận.

- [ ] **Step 10: Chạy toàn bộ, thấy xanh**

Run: `PYTHONPATH=. .venv/bin/python -m pytest -q`
Expected: PASS. Kiểm riêng `tests/test_socketio_chat.py` (fake `run_turn`, không ảnh hưởng).

- [ ] **Step 11: Commit**

```bash
git add app/agents/booking_graph tests/test_rewrite.py tests/test_graph_wiring.py tests/test_subagents.py tests/test_graph_events.py
git commit -m "graph: every LLM reply passes the guard; one rewrite round on violations

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 3: `parse_time` có neo

**Files:**
- Modify: `app/agents/booking_graph/timeparse.py`, `app/agents/booking_graph/tools.py`, `app/agents/booking_graph/context.py`
- Test: `tests/test_timeparse.py`, `tests/test_tools.py`, `tests/test_context_block.py`

**Interfaces:**
- Produces:
  - `ParsedTime.partial_hour: Optional[int]` (0–23 khi biết buổi, 1–12 khi chưa), `ParsedTime.partial_minute: int = 0`.
  - `apply_anchor(candidate: ParsedTime, anchor: Optional[datetime], hours: Optional[ShopHours]) -> ParsedTime` — thuần, không I/O.
  - `parse_vi_time(text, now, timeout=PARSE_TIMEOUT_SECONDS, anchor: Optional[datetime] = None, hours: Optional[ShopHours] = None)`.
  - Tool `parse_time(text: str, anchor: Optional[str] = None)`; `propose_appointment` trả thêm ` (iso: <start.isoformat()>)`; khối bối cảnh in `[id: <id>, iso: <to_local(start_at).isoformat()>]`.

- [ ] **Step 1: Viết test đỏ**

Thêm vào `tests/test_timeparse.py` (file có sẵn `TZ`, helpers — kiểm import đầu file; nếu thiếu thì thêm):

```python
from datetime import date, datetime

from app.agents.booking_graph.timeparse import ParsedTime, apply_anchor
from app.core.clock import TZ
from app.models.shop import ShopHours

HOURS = ShopHours(open_time="08:00", close_time="19:00")
ANCHOR_MORNING = datetime(2026, 9, 15, 9, 0, tzinfo=TZ)
ANCHOR_AFTERNOON = datetime(2026, 9, 15, 15, 0, tzinfo=TZ)


class TestApplyAnchor:
    """Bảng ca của spec mục 3. Chỉ chạy khi start_at chưa có và có neo."""

    def test_no_anchor_changes_nothing(self):
        c = ParsedTime(missing=["ngày nào"], partial_hour=10)
        assert apply_anchor(c, None, HOURS) == c

    def test_missing_day_takes_the_anchor_day(self):
        c = ParsedTime(missing=["ngày nào"], partial_hour=10, partial_minute=0)
        out = apply_anchor(c, ANCHOR_MORNING, HOURS)
        assert out.start_at == datetime(2026, 9, 15, 10, 0, tzinfo=TZ) and out.missing == []

    def test_missing_period_follows_the_anchor_period(self):
        """"chuyển qua 10 giờ" với lịch 9 giờ sáng → 10 giờ sáng cùng ngày."""
        c = ParsedTime(missing=["sáng hay chiều"], partial_date=date(2026, 9, 15), partial_hour=10)
        out = apply_anchor(c, ANCHOR_MORNING, HOURS)
        assert out.start_at == datetime(2026, 9, 15, 10, 0, tzinfo=TZ) and out.missing == []

    def test_missing_period_afternoon_anchor(self):
        c = ParsedTime(missing=["sáng hay chiều"], partial_date=date(2026, 9, 15), partial_hour=4)
        out = apply_anchor(c, ANCHOR_AFTERNOON, HOURS)
        assert out.start_at == datetime(2026, 9, 15, 16, 0, tzinfo=TZ)

    def test_anchor_period_outside_hours_uses_the_other_period(self):
        """Neo buổi sáng nhưng "7 giờ" sáng chưa mở → 7 giờ tối cũng đóng (19:00) → vẫn hỏi."""
        c = ParsedTime(missing=["sáng hay chiều"], partial_date=date(2026, 9, 15), partial_hour=7)
        out = apply_anchor(c, ANCHOR_MORNING, HOURS)
        assert out.start_at is None and out.missing == ["sáng hay chiều"]

    def test_anchor_period_outside_hours_but_other_period_open(self):
        """Neo buổi sáng, "6 giờ": 6 sáng đóng, 6 chiều (18:00) mở → 18:00."""
        c = ParsedTime(missing=["sáng hay chiều"], partial_date=date(2026, 9, 15), partial_hour=6)
        out = apply_anchor(c, ANCHOR_MORNING, HOURS)
        assert out.start_at == datetime(2026, 9, 15, 18, 0, tzinfo=TZ)

    def test_missing_both_day_and_period(self):
        c = ParsedTime(missing=["ngày nào", "sáng hay chiều"], partial_hour=10)
        out = apply_anchor(c, ANCHOR_MORNING, HOURS)
        assert out.start_at == datetime(2026, 9, 15, 10, 0, tzinfo=TZ)

    def test_missing_the_hour_itself_cannot_be_resolved(self):
        c = ParsedTime(missing=["giờ cụ thể"], partial_date=date(2026, 9, 15))
        assert apply_anchor(c, ANCHOR_MORNING, HOURS).start_at is None

    def test_no_hours_given_trusts_the_anchor_period(self):
        c = ParsedTime(missing=["sáng hay chiều"], partial_date=date(2026, 9, 15), partial_hour=10)
        assert apply_anchor(c, ANCHOR_MORNING, None).start_at == datetime(2026, 9, 15, 10, 0, tzinfo=TZ)
```

Thêm vào `tests/test_tools.py`:

```python
class TestParseTimeAnchor:
    async def test_bad_anchor_is_ignored_not_fatal(self, test_db):
        user = await a_user(test_db)
        out = await by_name(make_booking_tools(test_db, user), "parse_time").ainvoke(
            {"text": "chuyển qua 10 giờ", "anchor": "không phải iso"})
        assert '"missing"' in out          # vẫn là JSON ParsedTime, không crash

    async def test_description_explains_the_anchor(self, test_db):
        user = await a_user(test_db)
        desc = " ".join(by_name(make_booking_tools(test_db, user), "parse_time").description.split())
        assert "`anchor`" in desc and "already on the table" in desc

    async def test_propose_returns_the_iso_for_the_next_turn(self, test_db):
        user = await a_user(test_db)
        start = tomorrow_at(15)
        out = await by_name(make_booking_tools(test_db, user), "propose_appointment").ainvoke(
            {"start_at": start.isoformat(), "note": "làm tóc"})
        assert f"(iso: {start.isoformat()})" in out
```

Sửa `tests/test_context_block.py::TestUpcomingAppointmentsCarryTheirId`: thay assert `f"[id: {appt.id}]" in block` bằng:

```python
        assert f"[id: {appt.id}, iso: 2026-08-07T08:00:00+07:00]" in block
```

(và test `the_id_sits_on_the_same_line_as_the_time` giữ nguyên).

- [ ] **Step 2: Chạy, thấy đỏ**

Run: `PYTHONPATH=. .venv/bin/python -m pytest -q tests/test_timeparse.py tests/test_tools.py tests/test_context_block.py`
Expected: FAIL — `ImportError: apply_anchor`; `partial_hour` không phải trường; propose thiếu `(iso:`.

- [ ] **Step 3: `timeparse.py` — trường mới, `apply_anchor`, chữ ký `parse_vi_time`**

Thêm hai trường vào `ParsedTime` (sau `partial_date`):

```python
    partial_hour: Optional[int] = Field(
        default=None,
        description="Giờ đã nghe được. 0-23 nếu biết sáng/chiều; 1-12 nếu chưa biết buổi",
    )
    partial_minute: int = Field(default=0, description="Phút, mặc định 0")
```

Thêm vào `_PROMPT` (cùng phong cách các gạch đầu dòng hiện có, ngay sau dòng nói về `partial_date`):

```
- Nghe được giờ mà chưa đủ để điền start_at thì PHẢI điền partial_hour (và
  partial_minute): biết buổi thì ghi 0-23, chưa biết buổi thì ghi đúng số khách
  nói (1-12). "10 giờ" → partial_hour 10, missing ["sáng hay chiều"].
  "chiều mai 3 giờ" thiếu gì đó thì partial_hour 15.
```

Thêm hàm (đặt trước `parse_vi_time`), import `ShopHours`, `is_within` từ `app.models.shop` / `app.services.shop`:

```python
def _period_candidates(hour: int) -> List[int]:
    """1–12 chưa biết buổi → hai ứng viên 24h; 0–23 đã biết → chính nó."""
    if hour >= 13 or hour == 0:
        return [hour]
    return sorted({hour, (hour + 12) % 24})


def apply_anchor(candidate: ParsedTime, anchor: Optional[datetime],
                 hours: Optional[ShopHours]) -> ParsedTime:
    """Điền phần thiếu từ mốc neo — bằng code, không hỏi lại khách.

    Neo là mốc đang bàn (lịch cũ khi dời, giờ vừa đề nghị). Thiếu ngày → ngày
    của neo. Thiếu buổi → buổi của neo nếu giờ ra nằm trong giờ mở cửa, không
    thì buổi kia nếu hợp lệ; cả hai hợp lệ hay cả hai hỏng → vẫn hỏi như cũ.
    Không đụng regex và không đổi enum MissingPiece.
    """
    if anchor is None or candidate.start_at is not None or candidate.partial_hour is None:
        return candidate
    missing = set(candidate.missing)
    if "giờ cụ thể" in missing:
        return candidate

    local_anchor = anchor.astimezone(TZ)
    day = candidate.partial_date or local_anchor.date()
    minute = candidate.partial_minute or 0

    if "sáng hay chiều" in missing:
        options = _period_candidates(candidate.partial_hour)
        anchor_is_morning = local_anchor.hour < 12
        preferred = [h for h in options if (h < 12) == anchor_is_morning]
        others = [h for h in options if (h < 12) != anchor_is_morning]
        ordered = preferred + others
        if hours is not None:
            ordered = [h for h in ordered if is_within(hours, datetime(day.year, day.month, day.day, h, minute, tzinfo=TZ))]
            if len(ordered) == 2:          # cả hai đều mở — không đoán
                return candidate
        if not ordered:
            return candidate
        hour = ordered[0]
    else:
        hour = candidate.partial_hour

    start = datetime(day.year, day.month, day.day, hour, minute, tzinfo=TZ)
    return candidate.model_copy(update={"start_at": start, "missing": [], "partial_date": day})
```

Lưu ý ca `test_missing_period_follows_the_anchor_period`: options [10, 22], neo sáng → preferred [10]; `is_within` 10:00 mở, 22:00 đóng → ordered [10] → 10:00. Ca "7 giờ": [7, 19] → 7 đóng, 19:00 đóng (close 19:00 không tính) → rỗng → hỏi. Ca "6 giờ": [6, 18] → 18 mở → 18:00. Ca `no_hours_given`: ordered = preferred+others = [10, 22] → không lọc → lấy [0] = 10.

`parse_vi_time` đổi chữ ký và gọi `apply_anchor` **trước** `_guard` trên nhánh LLM (nhánh regex đã có start_at, không cần):

```python
async def parse_vi_time(
    text: str, now: datetime, timeout: float = PARSE_TIMEOUT_SECONDS,
    anchor: Optional[datetime] = None, hours: Optional[ShopHours] = None,
) -> ParsedTime:
    ...
    candidate = candidate.model_copy(update={"source": "llm"})
    candidate = apply_anchor(candidate, anchor, hours)
    return _guard(candidate, now)
```

Kiểm `is_within` không đụng DB (thuần) — đúng theo docstring của nó.

- [ ] **Step 4: `tools.py` — `parse_time(anchor)`, propose trả iso**

```python
    @tool
    async def parse_time(text: str, anchor: Optional[str] = None) -> str:
        """Turn what the customer said about time into a concrete date and time.
        Call this BEFORE find_free_slots and propose_appointment, every time the
        customer mentions a time. Never compute a date yourself.
        `text` must be the FULL phrase, joining what the customer said on
        earlier turns: if they named a day one turn and an hour the next, pass
        both together, not the hour alone. This tool reads only the string you
        give it — it cannot see earlier turns.
        `anchor`: pass it whenever the customer is changing or answering about a
        time already on the table — the appointment being moved (its `iso:` in
        the context block) or the slot just proposed (the `iso:` in
        propose_appointment's result). The tool then fills a missing day or
        part of the day from it instead of asking again.
        Result has `missing` -> ask the customer for exactly that ONE missing
        piece, one piece per turn, and nothing else. The value "sáng hay chiều"
        is quoted from the tool's own output, not an example: it means the hour
        is known but the part of the day is not."""
        anchor_dt = None
        if anchor:
            try:
                anchor_dt = _parse_local(anchor)
            except ValueError:
                # Neo bịa thì bỏ neo, không bỏ cả lượt.
                logger.warning("anchor_ignored", extra={"anchor": anchor[:60]})
        hours = await service.shop.get_hours()
        parsed: ParsedTime = await parse_vi_time(text, now_utc(), anchor=anchor_dt, hours=hours)
        return parsed.model_dump_json(
            include={"start_at", "partial_date", "missing"}, exclude_none=False
        )
```

Thêm `from app.core.logging import get_logger` và `logger = get_logger(__name__)` ở đầu `tools.py` nếu chưa có. Trong `propose_appointment`, hai chuỗi trả về thêm ` (iso: {start.isoformat()})` ngay sau `format_vi_datetime(start)`:

```python
                f"Đã giữ chỗ {format_vi_datetime(start)} (iso: {start.isoformat()}){note_text} để dời lịch "
```

```python
            f"Đã giữ chỗ {format_vi_datetime(start)} (iso: {start.isoformat()}){note_text}. "
```

- [ ] **Step 5: `context.py` — iso cạnh id**

```python
            lines.append(
                f"  - {format_vi_datetime(appt.start_at)}{note} "
                f"[id: {appt.id}, iso: {to_local(appt.start_at).isoformat()}]"
            )
```

- [ ] **Step 6: Chạy toàn bộ, thấy xanh, commit**

Run: `PYTHONPATH=. .venv/bin/python -m pytest -q`
Expected: PASS. `tests/test_prompts.py::TestToolDescriptionsAreFullyEnglish` vẫn xanh (docstring mới tiếng Anh; "sáng hay chiều" vẫn được trích).

```bash
git add app/agents/booking_graph/timeparse.py app/agents/booking_graph/tools.py app/agents/booking_graph/context.py tests/test_timeparse.py tests/test_tools.py tests/test_context_block.py
git commit -m "timeparse: resolve a missing day or part of day from an anchor time

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 4: `confirm` → `phrase` → `guard`

**Files:**
- Create: `app/agents/booking_graph/phrase.py`, `tests/test_phrase.py`
- Modify: `app/agents/booking_graph/confirm.py`, `guard.py` (`_guard_phrase`), `graph.py`, `prompts.py`
- Test: `tests/test_confirm.py`, `tests/test_graph_wiring.py`

**Interfaces:**
- Consumes: `make_guard_node` (Task 2), `GraphState.fallback/phrase_fact`.
- Produces:
  - `confirm` trả `{"confirm_fact": {...}, "fallback": <câu cứng>}` trên mọi nhánh đã ghi (kể cả lỗi); nhánh chưa đồng ý vẫn `{"route": "booking"}`. `confirm_fact = {"kind": "booked"|"moved"|"cancelled"|"failed", "when": str|None, "note": str|None, "address": str, "error": str|None}`.
  - `route_after_confirm(state) -> "booking" | "phrase"`.
  - `phrase.py`: `make_phrase_node() -> node` trả `{"draft": <câu LLM hoặc fallback>, "phrase_fact": fact}`.
  - `prompts.py`: `PHRASE_PROMPT` với `{kind_sentence}`, `{when}`, `{address}`.
  - `guard._guard_phrase`: draft phải chứa `when` (nếu có) và `address` (so thường hoá), `failed` phải chứa `error`; bất kỳ vi phạm (kể cả 5 phép chung) → `answer = fallback`, `answer_source = "code"`, không rewrite; sạch → `answer = draft`, `answer_source = "llm"` (hoặc `"code"` nếu `draft == fallback`).

- [ ] **Step 1: Viết test đỏ**

`tests/test_phrase.py`:

```python
import pytest
from langchain_core.messages import AIMessage, HumanMessage

from app.agents.booking_graph.guard import make_guard_node
from app.agents.booking_graph.phrase import make_phrase_node
from app.models.user import User

pytestmark = pytest.mark.asyncio
USER = User(phone="0912345678", hashed_password="x", full_name="Chú Tám")
FACT = {"kind": "booked", "when": "Thứ Ba 15/9, 9 giờ sáng", "note": "cắt tóc", "address": "anh Tám", "error": None}
FALLBACK = "Xong rồi ạ. Hẹn gặp anh Tám Thứ Ba 15/9, 9 giờ sáng nhé."


class ScriptedModel:
    def __init__(self, reply, fail=False):
        self.reply, self.fail, self.calls, self.kwargs = reply, fail, [], None
    async def ainvoke(self, messages, **kw):
        self.calls.append(messages)
        if self.fail:
            raise RuntimeError("down")
        return AIMessage(content=self.reply)


@pytest.fixture
def patch_model(monkeypatch):
    def _install(reply, fail=False):
        m = ScriptedModel(reply, fail)
        def _build(**kwargs):
            m.kwargs = kwargs
            return m
        monkeypatch.setattr("app.agents.booking_graph.phrase.build_chat_model", _build)
        return m
    return _install


def a_state(**extra):
    return {"messages": [HumanMessage(content="anh bận đột xuất, dời sang mai nha"), HumanMessage(content="ừ")],
            "context_block": "Gọi khách là: anh Tám.", "confirm_fact": FACT, "fallback": FALLBACK,
            "previous_replies": [], "customer_text": "ừ", **extra}


class TestPhraseNode:
    async def test_writes_a_draft_with_the_fact_and_streams_as_respond(self, patch_model):
        model = patch_model("Dạ anh Tám, em dời xong rồi, mai Thứ Ba 15/9, 9 giờ sáng gặp anh nhé.")
        out = await make_phrase_node()(a_state())
        assert out["draft"].startswith("Dạ anh Tám") and out["phrase_fact"] == FACT
        assert model.kwargs["tags"] == ["respond"]
        system = model.calls[0][0].content
        assert "Thứ Ba 15/9, 9 giờ sáng" in system and "anh Tám" in system
        # lịch sử đi vào để bắt mạch
        assert any("bận đột xuất" in m.content for m in model.calls[0])

    async def test_model_failure_yields_the_fallback_as_draft(self, patch_model):
        patch_model("", fail=True)
        out = await make_phrase_node()(a_state())
        assert out["draft"] == FALLBACK and out["phrase_fact"] == FACT


class TestGuardForPhrase:
    async def test_missing_when_falls_back_without_rewrite(self):
        out = await make_guard_node(USER)(a_state(draft="Dạ anh Tám, em đặt xong rồi nhé."))
        assert out["answer"] == FALLBACK and out["answer_source"] == "code"

    async def test_wrong_address_falls_back(self):
        out = await make_guard_node(USER)(a_state(draft="Chị Tám ơi, Thứ Ba 15/9, 9 giờ sáng em đặt rồi ạ."))
        assert out["answer"] == FALLBACK

    async def test_good_phrase_is_used(self):
        out = await make_guard_node(USER)(a_state(draft="Dạ anh Tám, Thứ Ba 15/9, 9 giờ sáng em giữ cho anh rồi, mai gặp nhé."))
        assert out["answer"].startswith("Dạ anh Tám") and out["answer_source"] == "llm"

    async def test_failed_kind_must_carry_the_error(self):
        fact = {**FACT, "kind": "failed", "when": None, "error": "Giờ đó đã có người đặt"}
        fb = "Dạ Giờ đó đã có người đặt ạ. Anh Tám chọn giờ khác giúp em nhé."
        out = await make_guard_node(USER)(a_state(draft="Dạ anh Tám, em chưa đặt được ạ.", confirm_fact=fact, fallback=fb))
        assert out["answer"] == fb
```

Sửa `tests/test_confirm.py`: các test hiện assert `result["answer"]` sau khi "ừ" → đổi sang assert trên `confirm_fact`/`fallback`. Cụ thể:
- `test_propose_then_yes_books_the_time_from_MONGO`: `assert result["confirm_fact"]["kind"] == "booked"` và `assert "xong" in result["fallback"].lower()`.
- `test_yes_creates_the_appointment`: `assert result["confirm_fact"]["kind"] == "booked"`.
- `test_taken_slot_produces_a_friendly_message_not_a_crash`: `assert result["confirm_fact"]["kind"] == "failed"` và `assert "có người" in result["fallback"].lower()`.
- `TestConfirmUsesTheRightHonorific`, `TestHonorificInTheOtherBranches`, `TestNotAffirmativeGoesToBooking::test_affirmative_still_writes_and_answers_without_llm`, `TestConfirmReschedules`, `TestConfirmCancels`: thay `out["answer"]` bằng `out["fallback"]` (câu cứng cũ giờ nằm ở `fallback`, nội dung y hệt) và thêm `assert out["confirm_fact"]["kind"] == "moved"` / `"cancelled"` ở hai class cuối.
- Thêm:

```python
async def test_confirm_returns_fact_and_fallback_not_answer(test_db):
    user = await _setup(test_db)
    pending = await ConversationService(test_db).get_pending(str(user.id))
    out = await make_confirm_node(test_db, user)(a_state(user, "ừ", pending))
    assert "answer" not in out
    assert out["confirm_fact"] == {"kind": "booked", "when": format_vi_datetime(tomorrow_at(15)),
                                   "note": "làm tóc", "address": "chị Lan", "error": None}
    assert out["fallback"].startswith("Xong rồi ạ")
```

(import `format_vi_datetime` từ `app.agents.booking_graph.context`).

Thêm vào `tests/test_graph_wiring.py`:

```python
def test_confirm_goes_to_phrase_then_guard_never_straight_to_end():
    e = edges()
    assert ("confirm", "phrase") in e and ("confirm", "booking") in e
    assert ("confirm", "__end__") not in e
    assert ("phrase", "guard") in e
```

- [ ] **Step 2: Chạy, thấy đỏ**

Run: `PYTHONPATH=. .venv/bin/python -m pytest -q tests/test_phrase.py tests/test_confirm.py tests/test_graph_wiring.py`
Expected: FAIL — module `phrase` thiếu; confirm vẫn trả `answer`.

- [ ] **Step 3: `prompts.py` — `PHRASE_PROMPT`**

```python
# Câu chốt lịch: code đã ghi xong, LLM chỉ viết lời. Số liệu {when} là chuỗi
# THẬT từ DB — guard bắt buộc câu phải chứa nguyên văn, sai thì dùng câu cứng.
PHRASE_PROMPT = f"""You are the receptionist at a Vietnamese nail and hair salon.

{_VIETNAMESE_ONLY}

{{kind_sentence}}

Tell the customer in one or two natural Vietnamese sentences that fit the
conversation so far. You MUST include this exact text for the date and time:
{{when}}
You MUST address the customer as: {{address}}
Do not add any other fact. Do not ask a new question unless the booking
failed, in which case ask them to pick another time.

VOICE: call yourself "em"; short sentences; no technical terms; no bullet
points. Never call yourself "con" and never say "cô", "chú" or "bác" — that is
a different register and does not go with "anh"/"chị"."""

PHRASE_KIND_SENTENCES = {
    "booked": "The salon has just BOOKED the appointment below for this customer.",
    "moved": "The salon has just MOVED this customer's appointment to the time below; the old one is cancelled.",
    "cancelled": "The salon has just CANCELLED this customer's appointment at the time below.",
    "failed": "The salon could NOT complete the booking. The reason, in Vietnamese, is: {error}. Say so and ask them to pick another time.",
}
```

- [ ] **Step 4: `confirm.py` — trả fact + fallback**

Thay các `return {"answer": ...}` sau nhánh `is_affirmative`:

```python
        def done(kind, when=None, note=None, error=None, fallback=""):
            return {"confirm_fact": {"kind": kind, "when": when, "note": note,
                                     "address": address, "error": error},
                    "fallback": fallback}

        if cancelling:
            try:
                await service.cancel(user, cancelling)
            except AppError as exc:
                return done("failed", error=exc.message, fallback=f"Dạ {exc.message} ạ.")
            try:
                when = format_vi_datetime(datetime.fromisoformat(pending["start_at"]))
            except (KeyError, ValueError):
                return done("cancelled", fallback=f"Em hủy lịch xong rồi ạ, {address} cần gì cứ nhắn em nhé.")
            return done("cancelled", when=when,
                        fallback=f"Em hủy lịch {when} cho {address} xong rồi ạ. Cần đặt lại thì cứ nhắn em nhé.")

        replaces = pending.get("replaces_appointment_id")
        try:
            start = datetime.fromisoformat(pending["start_at"])
            if replaces:
                appointment = await service.reschedule(user, replaces, start, pending.get("note"))
            else:
                appointment = await service.create(user, start, pending.get("note"))
        except AppError as exc:
            return done("failed", error=exc.message,
                        fallback=f"Dạ {exc.message} ạ. {_sentence_start(address)} chọn giờ khác giúp em nhé.")
        except (KeyError, ValueError):
            logger.warning("bad_pending_payload", extra={"payload": str(pending)[:120]})
            return done("failed", error="em nhầm mất rồi",
                        fallback=f"Dạ em nhầm mất rồi, {address} nhắc lại ngày giờ giúp em ạ.")

        when = format_vi_datetime(appointment.start_at)
        if replaces:
            return done("moved", when=when, note=appointment.note,
                        fallback=f"Em dời lịch xong rồi ạ. Hẹn gặp {address} {when} nhé.")
        return done("booked", when=when, note=appointment.note,
                    fallback=f"Xong rồi ạ. Hẹn gặp {address} {when} nhé.")
```

`route_after_confirm`:

```python
def route_after_confirm(state: GraphState) -> str:
    """Chưa chốt → booking; đã ghi (có confirm_fact) → phrase viết câu chốt."""
    return "booking" if state.get("route") == "booking" else "phrase"
```

- [ ] **Step 5: `phrase.py`**

```python
"""Câu chốt lịch do LLM viết theo mạch, số liệu do code cấp. Lỗi → fallback."""
import asyncio

from langchain_core.messages import HumanMessage, SystemMessage

from app.agents.booking_graph.guard import PHRASE_TIMEOUT_SECONDS
from app.agents.booking_graph.prompts import PHRASE_KIND_SENTENCES, PHRASE_PROMPT
from app.agents.booking_graph.state import GraphState
from app.agents.llm import build_chat_model
from app.core.logging import get_logger

logger = get_logger(__name__)


def make_phrase_node():
    async def node(state: GraphState) -> dict:
        fact = state.get("confirm_fact") or {}
        fallback = state.get("fallback") or ""
        kind_sentence = PHRASE_KIND_SENTENCES.get(fact.get("kind"), "").format(error=fact.get("error") or "")
        system = PHRASE_PROMPT.format(kind_sentence=kind_sentence,
                                      when=fact.get("when") or "(no time — the booking failed)",
                                      address=fact.get("address") or "anh chị")
        history, question = state["messages"][:-1], state["messages"][-1:]
        messages = [SystemMessage(content=system), *history,
                    HumanMessage(content=state.get("context_block", "")), *question]
        model = build_chat_model(tags=["respond"], temperature=0.3)
        try:
            reply = await asyncio.wait_for(model.ainvoke(messages), timeout=PHRASE_TIMEOUT_SECONDS)
            draft = (reply.content or "").strip() or fallback
        except Exception as exc:
            logger.warning("phrase_failed", extra={"error": str(exc)})
            draft = fallback
        return {"draft": draft, "phrase_fact": fact}

    return node
```

- [ ] **Step 6: `guard.py` — `_guard_phrase` thật**

```python
def _guard_phrase(state, draft, fact, address):
    """Khoảnh khắc chốt lịch: sai số liệu là dùng câu cứng ngay, không rewrite."""
    fallback = state.get("fallback") or draft
    when, kind, error = fact.get("when"), fact.get("kind"), fact.get("error")
    bad = []
    if when and when not in draft:
        bad.append("when")
    if address != "anh chị" and address.lower() not in draft.lower():
        bad.append("address")
    if kind == "failed" and error and error.lower() not in draft.lower():
        bad.append("error")
    bad += find_violations(draft, previous_replies=[], address=address,
                           name=(address.split()[1] if address != "anh chị" and len(address.split()) > 1 else ""),
                           customer_text=state.get("customer_text") or "")
    if bad:
        logger.info("phrase_fallback", extra={"codes": bad})
        return {"answer": fallback, "answer_source": "code", "violations": bad}
    return {"answer": draft, "answer_source": "code" if draft == fallback else "llm", "violations": []}
```

(`previous_replies=[]` vì câu chốt hiếm khi lặp và lặp cũng chấp nhận được; tránh cờ oan.)

- [ ] **Step 7: `graph.py`**

```python
from app.agents.booking_graph.phrase import make_phrase_node
...
    graph.add_node("phrase", make_phrase_node())
    graph.add_conditional_edges(
        "confirm", route_after_confirm, {"booking": "booking", "phrase": "phrase"}
    )
    graph.add_edge("phrase", "guard")
```

Xoá `"end": END` khỏi mapping của `confirm`.

- [ ] **Step 8: Chạy toàn bộ, thấy xanh, commit**

Run: `PYTHONPATH=. .venv/bin/python -m pytest -q`
Expected: PASS. `tests/test_prompts.py` — `PHRASE_PROMPT` không nằm trong `CUSTOMER_FACING` nên không bị kiểm "MUST be Vietnamese ≥ 2 lần"; nhưng nó **có** `_VIETNAMESE_ONLY` một lần và VOICE, phù hợp quy ước.

```bash
git add app/agents/booking_graph tests/test_phrase.py tests/test_confirm.py tests/test_graph_wiring.py
git commit -m "confirm: code writes the appointment, phrase node words the reply, guard enforces the facts

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 5: Supervisor và `booking` 7 tool

**Files:**
- Modify: `app/agents/booking_graph/prompts.py` (SUPERVISOR_PROMPT, BOOKING_PROMPT), `app/agents/booking_graph/graph.py` (tool của booking), `scripts/probe_supervisor.py`, `CONTEXT.md` (bẫy #7)
- Test: `tests/test_tools.py`, `tests/test_prompts.py`, `tests/test_subagents.py`

**Interfaces:**
- Produces: node `booking` được dựng với `make_booking_tools(db, user) + make_shop_tools(db, user)` (7 tool). `make_booking_tools` **không đổi** (vẫn 5) để test tên tool của nó đứng vững; ghép ở `graph.py`.

- [ ] **Step 1: Viết test đỏ**

`tests/test_prompts.py`:

```python
class TestCompoundQuestionsRouteToBooking:
    def test_supervisor_sends_mixed_shop_and_booking_to_booking(self):
        assert "mixes a shop question" in SUPERVISOR_PROMPT

    def test_booking_prompt_tells_the_model_to_use_shop_tools(self):
        assert "use the shop tools" in BOOKING_PROMPT.lower()
```

`tests/test_graph_wiring.py`:

```python
def test_booking_node_has_the_two_shop_tools_but_no_write_tool():
    from unittest.mock import MagicMock
    from app.agents.booking_graph.graph import booking_tools
    from app.models.user import User
    names = {t.name for t in booking_tools(MagicMock(), User(phone="0912345678", hashed_password="x", full_name="Cô Lan"))}
    assert names == {"parse_time", "find_free_slots", "propose_appointment", "list_my_appointments",
                     "cancel_appointment", "get_shop_hours", "get_shop_status"}
    assert "create_appointment" not in names
```

- [ ] **Step 2: Chạy, thấy đỏ**

Run: `PYTHONPATH=. .venv/bin/python -m pytest -q tests/test_prompts.py tests/test_graph_wiring.py -k "Compound or booking_node"`
Expected: FAIL — chuỗi thiếu; `booking_tools` không tồn tại.

- [ ] **Step 3: Sửa prompt**

`SUPERVISOR_PROMPT`, dòng `booking`:

```
- booking : book, change, or cancel an appointment; ask for free slots; look up
            their own appointments; just name the service they want — wanting a
            service IS wanting an appointment; or a message that mixes a shop
            question with anything about appointments
```

`BOOKING_PROMPT`, cuối rule 1 thêm:

```
   For shop hours or whether the owner is busy, use the shop tools — never
   guess; answer both halves of a mixed question in one reply.
```

Đo `len(BOOKING_PROMPT)`; nếu ≥ 4900, nâng mốc trong `test_the_prompt_actually_got_shorter` lên bội 100 kế tiếp và ghi số đo vào comment (mẫu comment có sẵn).

- [ ] **Step 4: `graph.py` — `booking_tools`**

```python
def booking_tools(db: AsyncIOMotorDatabase, user: User):
    """5 tool lịch + 2 tool đọc của shop. Câu kép ("mấy giờ đóng cửa, chiều nay
    còn giờ nào") vào booking và cần cả hai nhóm. Vẫn KHÔNG có tool ghi."""
    return make_booking_tools(db, user) + make_shop_tools(db, user)
```

và dùng `booking_tools(db, user)` khi dựng node `booking`.

- [ ] **Step 5: `scripts/probe_supervisor.py` — thêm 4 mẫu câu kép**

```python
    ("booking", "mấy giờ đóng cửa vậy, chiều nay anh qua cắt tóc được không"),
    ("booking", "tiệm còn làm không em, còn giờ nào trống chiều nay"),
    ("booking", "chủ tiệm đang bận hả, vậy mai 9 giờ đặt được không"),
    ("shop", "tiệm còn mở không em"),
```

Chạy `PYTHONPATH=. .venv/bin/python scripts/probe_supervisor.py` (LLM thật) và ghi kết quả vào report; kỳ vọng lệch 0/18. Lệch → sửa chữ trong dòng `booking`/`shop` của supervisor, chạy lại, ghi lại.

- [ ] **Step 6: `CONTEXT.md` bẫy #7**

Thay đoạn "Đúng **5 tool**: … Thêm `create_appointment` là phá cả hai lớp bảo vệ." bằng: "Node `booking` có **7 tool, 0 tool ghi**: 5 tool lịch + 2 tool đọc của shop (từ 2026-09-14, để trả lời câu kép). Thêm `create_appointment` là phá cả hai lớp bảo vệ — hàng rào là `test_there_is_NO_tool_that_writes_an_appointment`."

- [ ] **Step 7: Chạy toàn bộ, commit**

Run: `PYTHONPATH=. .venv/bin/python -m pytest -q`
Expected: PASS.

```bash
git add app/agents/booking_graph/prompts.py app/agents/booking_graph/graph.py scripts/probe_supervisor.py CONTEXT.md tests/test_prompts.py tests/test_graph_wiring.py
git commit -m "booking: answer mixed shop+appointment questions with the shop tools

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 6: Chạy thật, đo, tài liệu

**Files:**
- Modify: `CONTEXT.md` (checkpoint, chốt cứng, bẫy #21), `NOTE.md`, `RUNBOOK.md` (log của guard/rewrite/phrase), `scripts/chat_e2e_transcript.py` (kịch bản `tu_nhien`)
- Không đổi code trừ khi phép đo lộ lỗi.

- [ ] **Step 1: Toàn bộ test xanh, ghi con số**

Run: `PYTHONPATH=. .venv/bin/python -m pytest -q`

- [ ] **Step 2: Kịch bản mới `tu_nhien`** trong `SCENARIOS` của `scripts/chat_e2e_transcript.py` — bốn ca phải đúng trong một lượt:

```python
    # Bốn ca của spec 2026-09-14-natural-voice-guard: câu kép, dời lịch chỉ
    # nói giờ, hủy qua xác nhận, hỏi giá. Gieo sẵn lịch 9 giờ sáng mai trước khi chạy.
    "tu_nhien": [
        "alo em, tiệm còn làm không",
        "mấy giờ đóng cửa vậy, chiều nay anh qua cắt tóc được không",
        "khách nào đặt lúc 4 giờ chiều vậy em",
        "tiệm có nhuộm tóc bạc không, giá bao nhiêu",
        "à mà lịch mai của anh chuyển qua 10 giờ được không",
        "ừ",
        "vậy anh còn mấy lịch",
        "thôi hủy giùm anh",
        "ừ hủy đi",
        "cảm ơn em",
    ],
```

- [ ] **Step 3: Chạy thật**

```bash
docker compose exec -T mongo mongosh salon_booking --quiet --eval 'db.rate_limits.deleteMany({})'
PYTHONPATH=. nohup .venv/bin/python -m uvicorn main:app --port 8000 > /home/henryb1/.claude/jobs/7775d12f/tmp/uvicorn-voice.log 2>&1 &
sleep 5
# tài khoản mới qua API admin (0901234567 / chutiem123), full_name "Chú Tám", phone 0986000401
# gieo lịch 9 giờ sáng mai cho tài khoản đó bằng AppointmentService (xem cách ở checkpoint digest)
PYTHONPATH=. .venv/bin/python scripts/chat_e2e_transcript.py --scenario tu_nhien --phone 0986000401 --password khachhang123 --out after-voice-tu-nhien.txt
# tài khoản mới thứ hai cho kịch bản mốc
PYTHONPATH=. .venv/bin/python scripts/chat_e2e_transcript.py --scenario dai --phone 0986000402 --password khachhang123 --out after-voice-dai.txt
PYTHONPATH=. .venv/bin/python scripts/score_transcript.py baseline-dai.txt after-voice-dai.txt
grep -o "guard_violation\|guard_rewritten\|guard_gave_up\|phrase_fallback\|rewrite_failed\|anchor_ignored" /home/henryb1/.claude/jobs/7775d12f/tmp/uvicorn-voice.log | sort | uniq -c
```

Kiểm bằng mắt, ghi vào report: (1) lượt 2 trả lời **cả** giờ đóng cửa và giờ trống; (2) lượt 3 từ chối gọi đúng tên, không gọi tool; (3) lượt 4 không bị câu bảo mật; (4) lượt 5 **không hỏi "sáng hay chiều"** (neo = lịch 9 giờ sáng); (5) lượt 6 câu chốt do LLM viết, có "Thứ … 10 giờ sáng" và "anh Tám"; (6) lượt 8–9 hủy đúng một lần hỏi; (7) không câu nào lặp nguyên văn; (8) số lượt có `guard_violation` và tỷ lệ rewrite qua.

- [ ] **Step 4: Dọn**

```bash
docker compose exec -T mongo mongosh salon_booking --quiet --eval 'var ids=db.users.find({phone:/^0986/}).toArray().map(u=>String(u._id)); db.users.deleteMany({phone:/^0986/}); db.appointments.deleteMany({user_id:{$in:ids}}); db.conversations.deleteMany({user_id:{$in:ids}})'
fuser -k -TERM 8000/tcp
```

(Không dùng `pkill -f "uvicorn main:app"` trong cùng một lệnh shell với chuỗi đó — nó khớp chính shell.)

- [ ] **Step 5: Tài liệu**

`CONTEXT.md`:
- Bảng "Chốt cứng" thêm dòng: `Câu trả lời | **Mọi câu qua guard (code) trước khi phát; rewrite tối đa 1 lần; câu chốt LLM viết, số liệu code cấp** | Prompt không sửa được lặp/xưng hô — đo 2026-09-14`.
- Bẫy #21: "**Guard là code, không phải rubric.** 5 phép kiểm tất định + kiểm số liệu. Thêm phép kiểm mờ ('câu này có tự nhiên không') là biến guard thành LLM chấm — bẫy #16 đã đo là không phân giải được. Rewrite chỉ một lần; lần hai hỏng thì trả draft GỐC, không trả bản rewrite."
- Checkpoint: nhánh, số test, bảng rubric, tỷ lệ `guard_violation`/rewrite, kết quả 8 điểm kiểm ở Step 3, probe supervisor 18 mẫu.
- Cập nhật sơ đồ flow (mục "Vẽ lại flow" nếu có) hoặc ghi ngắn: `… → guard → (rewrite → guard) → END`; `confirm → phrase → guard`.

`RUNBOOK.md`: mục "Kiểm tầng gác": ý nghĩa `guard_violation{codes}`, `guard_rewritten`, `guard_gave_up`, `rewrite_failed`, `phrase_fallback`, `phrase_failed`, `anchor_ignored`; lệnh `scripts/probe_repeats.py`.

`NOTE.md`: số test, việc tiếp theo.

- [ ] **Step 6: Commit (không merge — báo chủ dự án)**

```bash
git add CONTEXT.md NOTE.md RUNBOOK.md scripts/chat_e2e_transcript.py after-voice-tu-nhien.txt after-voice-dai.txt
git commit -m "docs: record the guard/rewrite/phrase layer, its live measurement and trap 21

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

## Tự rà

**Phủ spec:** Mục 1 (graph, tag, state) → Task 2 + 4; Mục 2.1 (5 phép kiểm) → Task 1; 2.2 (repeat 2+3, source, miễn trừ, probe ngưỡng) → Task 1; 2.3 (rewrite, content, log) → Task 2; Mục 3 (neo, iso ở hai nguồn, anchor hỏng) → Task 3; Mục 4 (confirm_fact, phrase, guard riêng, fallback) → Task 4; Mục 5 (supervisor, 7 tool, probe, bẫy #7) → Task 5; Mục 6 (lỗi/biên) → rải ở Task 2/3/4 (rewrite_failed, gave_up, content, anchor_ignored, phrase_fallback, `source="code"`); Mục 7 → mọi task + Task 6.

**Placeholder:** không TBD; mọi bước code có mã; `_guard_phrase` ở Task 2 là stub CÓ Ý (Task 4 thay) và được ghi rõ.

**Nhất quán tên:** `find_violations`, `content_kept`, `make_guard_node`, `route_after_guard`, `make_rewrite_node`, `REWRITE_TAG`, `make_phrase_node`, `confirm_fact`, `fallback`, `phrase_fact`, `previous_replies`, `customer_text`, `answer_source`, `apply_anchor`, `partial_hour`, `partial_minute`, `booking_tools`, `VIOLATION_HINTS`, `REWRITE_PROMPT`, `PHRASE_PROMPT`, `PHRASE_KIND_SENTENCES`, `ChatMessage.source`, `append(source=)` — dùng thống nhất Task 1–6.
