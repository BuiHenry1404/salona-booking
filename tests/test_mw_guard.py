"""GuardMiddleware (`after_agent`): luật guard/rewrite/câu chốt cũ, nay chạy trong agent.

Các test node cũ của `test_rewrite.py`/`test_phrase.py` chuyển sang đây, gọi
thẳng `GuardMiddleware(user).aafter_agent(state, None)`; `rewrite_once` được
thay bằng hàm giả đếm số lần gọi.
"""
import pytest
from langchain_core.messages import AIMessage, HumanMessage

from app.agents.booking_graph import rewrite
from app.agents.booking_graph.guard import TOOL_NAMES, strip_tool_names
from app.agents.booking_graph.middleware.guard import GuardMiddleware
from app.agents.booking_graph.middleware.tools import FALLBACK_ANSWER
from app.models.user import User


USER = User(phone="0912345678", hashed_password="x", full_name="Chú Tám")
FACT = {"kind": "booked", "when": "Thứ Ba 15/9, 9 giờ sáng", "note": "cắt tóc",
        "address": "anh Tám", "error": None}
FALLBACK = "Xong rồi ạ. Hẹn gặp anh Tám Thứ Ba 15/9, 9 giờ sáng nhé."


@pytest.fixture
def rewrites(monkeypatch):
    """`rewrite_once` giả: trả lần lượt `replies`, ghi lại tham số mỗi lần gọi."""
    calls = []

    def _install(*replies):
        queue = list(replies)

        async def fake(draft, codes, previous_replies, context_block):
            calls.append({"draft": draft, "codes": list(codes),
                          "previous": list(previous_replies), "context_block": context_block})
            return queue.pop(0) if queue else None

        monkeypatch.setattr(rewrite, "rewrite_once", fake)
        return calls

    return _install


# Lịch có thật lúc 9 giờ sáng: câu "em giữ chỗ … 9 giờ sáng rồi" là đúng sự thật
# (lỗi live B3 #1 — lời nhận "đã giữ" phải khớp DB).
BOOKED_9 = "  - Thứ Ba 15/9, 9 giờ sáng — cắt tóc [id: a9, iso: 2026-09-15T09:00:00+07:00]"


def a_state(draft, previous=(), customer="giá bao nhiêu", **extra):
    return {"messages": [HumanMessage(content=customer), AIMessage(content=draft)],
            "previous_replies": list(previous), "customer_text": customer,
            "context_block": "", **extra}


async def guard(state):
    return await GuardMiddleware(USER).aafter_agent(state, None)


class TestGuard:
    async def test_clean_draft_becomes_the_answer_unchanged(self, rewrites):
        calls = rewrites()
        out = await guard(a_state("Dạ anh Tám, mai 9 giờ sáng ạ."))
        assert out == {"answer": "Dạ anh Tám, mai 9 giờ sáng ạ.", "answer_source": "llm",
                       "violations": []}
        assert calls == []

    async def test_violation_asks_for_exactly_one_rewrite_with_the_codes(self, rewrites):
        calls = rewrites("Dạ anh Tám, chủ tiệm sẽ báo giá ạ.")
        out = await guard(a_state("Chị hỏi chủ tiệm giúp em nhé.", previous=["Câu trước."],
                                  context_block="Gọi khách là: anh Tám."))
        assert len(calls) == 1
        assert calls[0] == {"draft": "Chị hỏi chủ tiệm giúp em nhé.", "codes": ["pronoun"],
                            "previous": ["Câu trước."], "context_block": "Gọi khách là: anh Tám."}
        assert out["answer"] == "Dạ anh Tám, chủ tiệm sẽ báo giá ạ."

    async def test_second_pass_with_violations_falls_back_to_the_original_draft(self, rewrites):
        calls = rewrites("Chị vẫn sai nè.", "không bao giờ dùng tới")
        out = await guard(a_state("Chị sai lần đầu."))
        assert out["answer"] == "Chị sai lần đầu."
        assert out["violations"] == ["pronoun"]
        assert len(calls) == 1, "rewrite tối đa MỘT lần"

    async def test_second_pass_clean_uses_the_rewrite(self, rewrites):
        rewrites("Dạ anh Tám, chủ tiệm sẽ báo giá ạ.")
        out = await guard(a_state("Chị hỏi chủ tiệm nhé."))
        assert out == {"answer": "Dạ anh Tám, chủ tiệm sẽ báo giá ạ.", "answer_source": "llm",
                       "violations": []}

    async def test_second_pass_that_lost_a_time_falls_back(self, rewrites):
        rewrites("Dạ anh Tám, em giữ chỗ rồi ạ.")
        out = await guard(a_state("Chị Tám, em giữ chỗ Thứ Ba 15/9, 9 giờ sáng rồi ạ.",
                                  context_block=BOOKED_9))
        assert out["answer"] == "Chị Tám, em giữ chỗ Thứ Ba 15/9, 9 giờ sáng rồi ạ."
        assert "content" in out["violations"]

    async def test_an_unfixable_repeat_ships_the_rewrite_not_the_original(self, rewrites):
        """Bản gốc là chính câu lặp — không được phát lại (LLM-32, 2026-09-19)."""
        lap = "Dạ anh Tám, em chỉ xem và đặt lịch của chính anh chị thôi ạ."
        rewrites("Dạ anh Tám, em chỉ xem và đặt lịch của chính anh chị thôi nhé ạ.")
        out = await guard(a_state(lap, previous=[lap]))
        assert out["violations"] == ["repeat"]
        assert out["answer"] != lap

    async def test_an_unfixable_content_loss_still_ships_the_original(self, rewrites):
        """Ngược lại: mất mốc giờ thì bản gốc vẫn an toàn hơn. Đừng nới ca này."""
        original = "Dạ anh Tám, em giữ chỗ Thứ Ba 15/9, 9 giờ sáng rồi ạ."
        prev = [original]
        rewrites("Dạ anh Tám, em giữ chỗ rồi ạ.")
        out = await guard(a_state(original, previous=prev, context_block=BOOKED_9))
        assert "content" in out["violations"]
        assert out["answer"] == original

    async def test_repeat_uses_previous_llm_replies(self, rewrites):
        calls = rewrites()
        prev = ["Em chỉ xem và đặt lịch của anh Tám thôi. Anh muốn đặt hay xem lịch của mình ạ?"]
        out = await guard(a_state(prev[0], previous=prev))
        assert calls[0]["codes"] == ["repeat"]
        assert out["violations"] == ["repeat"]

    async def test_failed_rewrite_ships_the_original(self, rewrites):
        """rewrite_once trả None (model lỗi/quá giờ/rỗng): không viết lại lần hai."""
        calls = rewrites(None)
        out = await guard(a_state("Chị hỏi chủ tiệm giúp em nhé."))
        assert out["answer"] == "Chị hỏi chủ tiệm giúp em nhé."
        assert out["violations"] == ["pronoun"] and len(calls) == 1

    async def test_empty_agent_reply_is_guarded_as_the_fallback(self, rewrites):
        rewrites()
        out = await guard(a_state(""))
        assert out["answer"] == FALLBACK_ANSWER

    async def test_the_last_ai_message_is_the_draft(self, rewrites):
        rewrites()
        state = a_state("Dạ anh Tám, mai 9 giờ sáng ạ.")
        state["messages"] = [AIMessage("câu cũ"), HumanMessage("q"), *state["messages"][1:]]
        assert (await guard(state))["answer"] == "Dạ anh Tám, mai 9 giờ sáng ạ."


class TestToolNames:
    def test_tool_names_match_the_real_tools(self):
        from unittest.mock import MagicMock

        from app.agents.booking_graph.graph import booking_tools
        names = {t.name for t in booking_tools(MagicMock(), USER)}
        assert set(TOOL_NAMES) == names

    def test_clean_text_is_returned_untouched(self):
        text = "Dạ anh Tám,\n  mai 9 giờ sáng (cắt tóc) ạ ."
        assert strip_tool_names(text) is text

    async def test_tool_name_in_parentheses_is_removed(self, rewrites):
        rewrites()
        out = await guard(a_state("Dạ em xem rồi (parse_time, find_free_slots) ạ."))
        assert out["answer"] == "Dạ em xem rồi ạ."

    def test_snake_case_identifier_in_brackets_is_removed(self):
        assert strip_tool_names("Dạ còn trống [slot_keys] 3 giờ chiều ạ.") == "Dạ còn trống 3 giờ chiều ạ."

    @pytest.mark.parametrize("name", TOOL_NAMES)
    def test_every_bare_name_is_removed(self, name):
        out = strip_tool_names(f"Dạ em dùng {name} để xem rồi ạ.")
        assert name not in out
        assert out == "Dạ em dùng để xem rồi ạ."

    def test_a_list_of_names_leaves_no_stray_commas(self):
        out = strip_tool_names("Em gọi `parse_time`, find_free_slots và get_shop_status rồi ạ.")
        assert out == "Em gọi rồi ạ."

    async def test_bare_tool_name_never_ships(self, rewrites):
        """Kể cả nhánh `guard_gave_up`: bản gốc lẫn bản viết lại đều đã bị xoá tên."""
        rewrites("Chị ơi em gọi propose_appointment rồi.")
        out = await guard(a_state("Chị ơi em gọi find_free_slots rồi."))
        assert out["violations"]                       # vẫn sai đại từ → gave up
        for name in TOOL_NAMES:
            assert name not in out["answer"]

    async def test_repeat_only_gave_up_branch_strips_the_rewrite_too(self, rewrites):
        lap = "Dạ anh Tám, em chỉ xem và đặt lịch của chính anh chị thôi ạ."
        rewrites(lap + " Em đã gọi list_my_appointments.")
        out = await guard(a_state(lap, previous=[lap]))
        assert "list_my_appointments" not in out["answer"]

    async def test_tool_names_are_stripped_before_the_checks(self, rewrites):
        calls = rewrites()
        out = await guard(a_state("Dạ anh Tám, em xem get_shop_hours rồi, mai 9 giờ sáng ạ."))
        assert calls == []
        assert out["answer"] == "Dạ anh Tám, em xem rồi, mai 9 giờ sáng ạ."


def phrase_state(draft, **extra):
    return {"messages": [HumanMessage(content="ừ"), AIMessage(content=draft)],
            "context_block": "Gọi khách là: anh Tám.", "confirm_fact": FACT, "fallback": FALLBACK,
            "previous_replies": [], "customer_text": "ừ", **extra}


class TestGuardForPhrase:
    async def test_missing_when_falls_back_without_rewrite(self, rewrites):
        calls = rewrites()
        out = await guard(phrase_state("Dạ anh Tám, em đặt xong rồi nhé."))
        assert out["answer"] == FALLBACK and out["answer_source"] == "code"
        assert calls == []

    async def test_wrong_address_falls_back(self, rewrites):
        rewrites()
        out = await guard(phrase_state("Chị Tám ơi, Thứ Ba 15/9, 9 giờ sáng em đặt rồi ạ."))
        assert out["answer"] == FALLBACK

    async def test_good_phrase_is_used(self, rewrites):
        rewrites()
        out = await guard(phrase_state(
            "Dạ anh Tám, Thứ Ba 15/9, 9 giờ sáng em giữ cho anh rồi, mai gặp nhé."))
        assert out["answer"].startswith("Dạ anh Tám") and out["answer_source"] == "llm"

    async def test_failed_kind_must_carry_the_error(self, rewrites):
        rewrites()
        fact = {**FACT, "kind": "failed", "when": None, "error": "Giờ đó đã có người đặt"}
        fb = "Dạ Giờ đó đã có người đặt ạ. Anh Tám chọn giờ khác giúp em nhé."
        out = await guard(phrase_state("Dạ anh Tám, em chưa đặt được ạ.",
                                       confirm_fact=fact, fallback=fb))
        assert out["answer"] == fb

    async def test_phrase_fact_path_uses_fallback_on_wrong_numbers(self, rewrites):
        """`phrase_fact` (PhraseMiddleware ghi) đi đúng đường câu chốt như
        `confirm_fact`: giờ sai → câu cứng, không rewrite."""
        calls = rewrites()
        out = await guard(phrase_state("Dạ anh Tám, Thứ Ba 15/9, 10 giờ sáng em giữ rồi ạ.",
                                       confirm_fact=None, phrase_fact=FACT))
        assert out["answer"] == FALLBACK and out["answer_source"] == "code"
        assert "when" in out["violations"]
        assert calls == []

    async def test_tool_name_in_a_phrase_is_stripped(self, rewrites):
        rewrites()
        out = await guard(phrase_state(
            "Dạ anh Tám, Thứ Ba 15/9, 9 giờ sáng em giữ rồi (propose_appointment) ạ."))
        assert out["answer"] == "Dạ anh Tám, Thứ Ba 15/9, 9 giờ sáng em giữ rồi ạ."


class TestNoClaimBeforeWriting:
    """Lỗi live B2 #4: chỉ mới giữ chỗ (đề xuất lượt này, chưa ghi) mà câu nói
    "em đã đặt lịch … rồi ạ, chị xác nhận giúp em nhé?". Guard sửa tất định."""

    @pytest.mark.parametrize("draft,expected", [
        ("Dạ, em đã đặt lịch cho anh Tám vào Thứ Ba 20/10, 3 giờ chiều rồi ạ, anh xác nhận giúp em nhé?",
         "Dạ, em giữ chỗ cho anh Tám vào Thứ Ba 20/10, 3 giờ chiều rồi ạ, anh xác nhận giúp em nhé?"),
        ("Em đã đặt cho anh Tám Thứ Tư 21/10, 9 giờ sáng, anh chốt giúp em nhé?",
         "Em giữ chỗ cho anh Tám Thứ Tư 21/10, 9 giờ sáng, anh chốt giúp em nhé?"),
        ("Dạ anh Tám, em đã dời lịch sang Thứ Ba 20/10, 5 giờ chiều rồi ạ. Anh xác nhận giúp em nhé?",
         "Dạ anh Tám, em sẽ dời lịch sang Thứ Ba 20/10, 5 giờ chiều ạ. Anh xác nhận giúp em nhé?"),
        ("Dạ, em vừa hủy lịch Thứ Ba 20/10, 4 giờ chiều rồi ạ, anh Tám đồng ý giúp em nhé?",
         "Dạ, em sẽ hủy lịch Thứ Ba 20/10, 4 giờ chiều ạ, anh Tám đồng ý giúp em nhé?"),
        ("Dạ, đã đặt lịch Thứ Ba 20/10, 3 giờ chiều cho anh Tám rồi ạ, anh chốt nhé?",
         "Dạ, em giữ chỗ Thứ Ba 20/10, 3 giờ chiều cho anh Tám rồi ạ, anh chốt nhé?"),
    ])
    async def test_claim_is_rewritten_when_only_proposing(self, rewrites, draft, expected):
        calls = rewrites()
        out = await guard(a_state(draft, customer="ok em", proposal_id="p1"))
        assert out["answer"] == expected
        assert calls == []                       # tất định, không gọi LLM

    async def test_true_claim_on_the_approve_turn_is_kept(self, rewrites):
        rewrites()
        draft = "Dạ em đã đặt cho anh Tám Thứ Ba 15/9, 9 giờ sáng rồi ạ."
        out = await guard(a_state(draft, confirm_fact=FACT, fallback=FALLBACK, proposal_id=None))
        assert out["answer"] == draft

    @pytest.mark.parametrize("draft", [
        "Dạ lịch đó em đã hủy rồi ạ.",                      # chuyện của lượt trước — có thể đúng
        "Dạ em đã đặt cho anh Tám Thứ Ba 15/9, 9 giờ sáng rồi ạ.",
    ])
    async def test_no_proposal_this_turn_leaves_the_sentence_alone(self, rewrites, draft):
        """Không có đề xuất mới thì `unclaim` không sửa. Lời "đã đặt" vẫn phải
        khớp DB (TestClaimsMatchFacts) — ở đây lịch 9 giờ có thật."""
        rewrites()
        assert (await guard(a_state(draft, context_block=BOOKED_9)))["answer"] == draft

    async def test_customer_already_booked_is_not_touched(self, rewrites):
        """`already_yours`: "anh đã đặt" là sự thật về khách, không phải em nhận vơ."""
        rewrites()
        draft = "Dạ anh Tám đã đặt lịch Thứ Ba 20/10, 3 giờ chiều rồi ạ, anh muốn đặt thêm không?"
        assert (await guard(a_state(draft, proposal_id="p1")))["answer"] == draft


# --- Lỗi live B3 #1: nhận vơ "đã giữ/đã đặt" một giờ KHÔNG có trong DB ---------
import json  # noqa: E402

from langchain_core.messages import ToolMessage  # noqa: E402

HUNG = User(phone="0987654321", hashed_password="x", full_name="Chú Hùng")
HUNG_FACT = {"kind": "booked", "when": "Thứ Tư 21/10, 9 giờ sáng", "note": "cắt tóc cho chú",
             "address": "anh Hùng", "error": None,
             "queued_when": ["Thứ Tư 21/10, 2 giờ chiều"]}
HUNG_FALLBACK = "Xong rồi ạ. Hẹn gặp anh Hùng Thứ Tư 21/10, 9 giờ sáng nhé."
# Câu chốt thật ở lượt 2 (history-B3), trước câu mời lịch kế do code nối.
B3_TURN2 = ("Dạ em đã ghi cho anh Hùng lúc Thứ Tư 21/10, 9 giờ sáng. "
            "Còn 2 giờ chiều em cũng đã giữ cho cháu rồi ạ.")
APPT_LINE = ("Lịch sắp tới của khách:\n  - Thứ Tư 21/10, 9 giờ sáng — cắt tóc cho chú "
             "[id: a1, iso: 2026-10-21T09:00:00+07:00]")


def held(iso, spoken, **extra):
    return ToolMessage(content=json.dumps({"held": {"iso": iso, "spoken": spoken}, **extra},
                                          ensure_ascii=False),
                       name="propose_appointment", tool_call_id="t1")


async def hung_guard(state):
    return await GuardMiddleware(HUNG).aafter_agent(state, None)


class TestClaimsMatchFacts:
    async def test_b3_turn2_confirmation_claiming_the_queued_time_falls_back(self, rewrites):
        calls = rewrites()
        state = {"messages": [HumanMessage(content="dc em"), AIMessage(content=B3_TURN2)],
                 "context_block": "Gọi khách là: anh Hùng.", "confirm_fact": HUNG_FACT,
                 "fallback": HUNG_FALLBACK, "previous_replies": [], "customer_text":
                 "dc em, 9h la cua chu. con 2h chieu cho thang chau thi sao"}
        out = await hung_guard(state)
        assert out["answer"] == HUNG_FALLBACK and "claim" in out["violations"]
        assert calls == []

    async def test_true_claim_about_the_booked_time_is_kept_in_the_phrase(self, rewrites):
        rewrites()
        draft = "Dạ em đã ghi cho anh Hùng lúc Thứ Tư 21/10, 9 giờ sáng rồi ạ."
        state = {"messages": [HumanMessage(content="dc em"), AIMessage(content=draft)],
                 "context_block": "", "confirm_fact": HUNG_FACT, "fallback": HUNG_FALLBACK,
                 "previous_replies": [], "customer_text": "dc em"}
        assert (await hung_guard(state))["answer"] == draft

    @pytest.mark.parametrize("claim", [
        "Còn 2 giờ chiều em cũng đã giữ cho cháu rồi ạ.",
        "Em đã đặt luôn 2 giờ chiều cho cháu rồi ạ.",
        "Lịch 2 giờ chiều em đang giữ cho cháu ạ.",
        "Dạ em đã đặt lịch cho anh Hùng rồi ạ.",           # không giờ, không có gì trong DB
    ])
    async def test_made_up_claim_on_a_turn_without_tools_is_rewritten(self, rewrites, claim):
        """Lượt không gọi tool nào (không proposal_id): câu nhận vơ vẫn bị bắt."""
        calls = rewrites("Dạ, lịch 2 giờ chiều em chưa giữ chỗ ạ.")
        state = {"messages": [HumanMessage(content="2h thi sao em"), AIMessage(content=claim)],
                 "context_block": "Khách chưa có lịch nào sắp tới.", "previous_replies": [],
                 "customer_text": "2h thi sao em"}
        out = await hung_guard(state)
        assert calls and "claim" in calls[0]["codes"]
        assert out["answer"] == "Dạ, lịch 2 giờ chiều em chưa giữ chỗ ạ."

    async def test_claim_that_survives_the_rewrite_is_dropped(self, rewrites):
        rewrites("Dạ 2 giờ chiều em đã giữ cho cháu rồi ạ.")
        draft = "Dạ 2 giờ chiều em đã giữ cho cháu rồi ạ. Anh Hùng cần gì thêm không ạ?"
        state = {"messages": [HumanMessage(content="2h?"), AIMessage(content=draft)],
                 "context_block": "", "previous_replies": [], "customer_text": "2h?"}
        out = await hung_guard(state)
        assert "đã giữ" not in out["answer"]
        assert out["answer"] == "Anh Hùng cần gì thêm không ạ?"

    async def test_claim_about_an_upcoming_appointment_is_true(self, rewrites):
        calls = rewrites()
        draft = "Dạ 9 giờ sáng Thứ Tư em đã đặt cho anh Hùng rồi ạ."
        state = {"messages": [HumanMessage(content="lich chu con khong"), AIMessage(content=draft)],
                 "context_block": APPT_LINE, "previous_replies": [], "customer_text": "x"}
        assert (await hung_guard(state))["answer"] == draft and calls == []

    async def test_claim_about_this_turns_hold_is_true_and_others_are_not(self, rewrites):
        calls = rewrites()
        ok = "Dạ em đang giữ 2 giờ chiều Thứ Tư 21/10 cho cháu, anh Hùng xác nhận giúp em nhé?"
        state = {"messages": [HumanMessage(content="u giu luon em"),
                              held("2026-10-21T14:00:00+07:00", "Thứ Tư 21/10, 2 giờ chiều"),
                              AIMessage(content=ok)],
                 "context_block": APPT_LINE, "previous_replies": [], "customer_text": "u",
                 "proposal_id": "p2"}
        assert (await hung_guard(state))["answer"] == ok and calls == []

        bad = "Dạ em đang giữ 2 giờ chiều cho cháu, còn 4 giờ chiều em cũng đã giữ rồi ạ."
        calls = rewrites("Dạ em đang giữ 2 giờ chiều cho cháu ạ.")
        out = await hung_guard({**state, "messages": [*state["messages"][:2], AIMessage(content=bad)]})
        assert calls and "claim" in calls[0]["codes"]

    @pytest.mark.parametrize("draft", [
        "Dạ em ghi nhận rồi ạ. Anh Hùng muốn giữ giờ khác không ạ?",
        "Dạ lịch đó em đã hủy rồi ạ.",
        "Dạ em chưa giữ 2 giờ chiều đâu ạ, anh Hùng muốn em giữ luôn không ạ?",
        "Còn lịch 2 giờ chiều, anh Hùng muốn em giữ chỗ tiếp luôn không ạ?",
    ])
    async def test_sentences_that_claim_nothing_are_left_alone(self, rewrites, draft):
        calls = rewrites()
        state = {"messages": [HumanMessage(content="x"), AIMessage(content=draft)],
                 "context_block": "", "previous_replies": [], "customer_text": "x"}
        assert (await hung_guard(state))["answer"] == draft and calls == []

    async def test_ghi_nhan_is_not_a_booking_claim_on_a_proposal_turn(self, rewrites):
        """"em đã ghi nhận" là "em đã nghe", không phải "em đã ghi lịch" —
        `unclaim` không được biến nó thành "em giữ chỗ nhận"."""
        calls = rewrites()
        draft = ("Dạ em đã ghi nhận yêu cầu hủy lịch Thứ Tư 21/10, 9 giờ sáng, "
                 "anh Hùng xác nhận giúp em nhé?")
        state = {"messages": [HumanMessage(content="huy di em"), AIMessage(content=draft)],
                 "context_block": APPT_LINE, "previous_replies": [], "customer_text": "x",
                 "proposal_id": "p3"}
        assert (await hung_guard(state))["answer"] == draft and calls == []
