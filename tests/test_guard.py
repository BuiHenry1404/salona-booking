"""Phép kiểm tất định trên câu nháp (`app.agents.booking.draft_checks`):
`strip_tool_names`, `unclaim`, `has_false_claim`, `repeats` — không gọi LLM.

Task 6.3 (M6-cutover): module này tên `guard.py` ở gói cũ, nhưng Task 6.1 đã
dời các hàm này sang `app.agents.booking.draft_checks` (không phải
`app.agents.booking.guard`, module khác hẳn — kiểm `ReplyBrief`/`must_say`,
xem `tests/test_reply_guard.py`). `check_pronoun`/`check_clock`/`find_violations`
của gói cũ không còn tương đương ở gói mới — không dời.
"""
from app.agents.booking.draft_checks import TOOL_NAMES, has_false_claim, repeats, strip_tool_names, unclaim


class TestStripToolNames:
    def test_clean_text_is_returned_untouched(self):
        text = "Dạ anh Tám,\n  mai 9 giờ sáng (cắt tóc) ạ ."
        assert strip_tool_names(text) is text

    def test_snake_case_identifier_in_brackets_is_removed(self):
        assert strip_tool_names("Dạ còn trống [slot_keys] 3 giờ chiều ạ.") == "Dạ còn trống 3 giờ chiều ạ."

    def test_every_bare_name_is_removed(self):
        for name in TOOL_NAMES:
            out = strip_tool_names(f"Dạ em dùng {name} để xem rồi ạ.")
            assert name not in out
            assert out == "Dạ em dùng để xem rồi ạ."

    def test_a_list_of_names_leaves_no_stray_commas(self):
        out = strip_tool_names("Em gọi `parse_time`, find_free_slots và get_shop_status rồi ạ.")
        assert out == "Em gọi rồi ạ."


class TestUnclaim:
    def test_booked_claim_becomes_a_hold(self):
        assert unclaim("Dạ em đã đặt lịch rồi ạ.") == "Dạ em giữ chỗ rồi ạ."

    def test_move_claim_becomes_a_future_promise(self):
        assert unclaim("Em đã dời lịch rồi ạ.") == "Em sẽ dời lịch ạ."

    def test_sentence_with_no_claim_is_left_alone(self):
        draft = "Dạ anh Tám, mai 9 giờ sáng ạ."
        assert unclaim(draft) == draft

    def test_ghi_nhan_is_not_a_booking_claim(self):
        """"em đã ghi nhận" là "em đã nghe", không phải "em đã ghi lịch"."""
        draft = "Dạ em đã ghi nhận yêu cầu hủy lịch rồi ạ."
        assert unclaim(draft) == draft


class TestHasFalseClaim:
    def test_claim_matching_a_known_clock_is_not_false(self):
        facts = {"clocks": [(9, 0, "sáng")], "any": True, "unknown": False}
        assert has_false_claim("Dạ em đã giữ 9 giờ sáng cho anh rồi ạ.", facts) is False

    def test_claim_not_matching_any_known_clock_is_false(self):
        facts = {"clocks": [(9, 0, "sáng")], "any": True, "unknown": False}
        assert has_false_claim("Dạ em đã giữ 10 giờ sáng cho anh rồi ạ.", facts) is True

    def test_no_facts_is_never_a_false_claim(self):
        assert has_false_claim("Dạ em đã giữ 10 giờ sáng cho anh rồi ạ.", None) is False

    def test_unknown_facts_never_flag_a_false_claim(self):
        facts = {"clocks": [], "any": False, "unknown": True}
        assert has_false_claim("Dạ em đã giữ 10 giờ sáng cho anh rồi ạ.", facts) is False

    def test_negated_claim_is_not_flagged(self):
        facts = {"clocks": [], "any": False, "unknown": False}
        assert has_false_claim("Dạ em chưa giữ lịch nào cho anh ạ.", facts) is False


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

    def test_same_template_with_a_different_time_is_not_a_repeat(self):
        prev = ["Em giữ chỗ Thứ Ba 15/9, 9 giờ sáng cho cắt tóc. Anh xác nhận giúp em nhé?"]
        draft = "Em giữ chỗ Thứ Ba 15/9, 10 giờ sáng cho cắt tóc. Anh xác nhận giúp em nhé?"
        assert repeats(draft, prev) is False

    def test_same_template_with_the_same_time_is_a_repeat(self):
        prev = ["Em giữ chỗ Thứ Ba 15/9, 9 giờ sáng cho cắt tóc. Anh xác nhận giúp em nhé?"]
        draft = "Em giữ chỗ Thứ Ba 15/9, 9 giờ sáng cho cắt tóc. Anh xác nhận giúp em nhé?"
        assert repeats(draft, prev) is True
