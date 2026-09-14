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
