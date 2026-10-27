from app.agents.booking_graph import tokens


def test_tokens_module_contains_no_regex_imports_or_calls():
    import inspect
    source = inspect.getsource(tokens)
    assert "import re" not in source and "re.compile" not in source


def test_words_and_cased_words_split_punctuation_cleanly():
    assert tokens.words("Dạ, anh Tám! 15:30 nhé?") == ["dạ", "anh", "tám", "15", "30", "nhé"]
    assert tokens.cased_words("Dạ, Chú Tám ơi!") == ["Dạ", "Chú", "Tám", "ơi"]
    # Dấu câu sinh vách ngăn `|` trong padded để cụm nhiều từ không nhảy qua dấu phẩy/chấm
    p = tokens.padded("mất bao, lâu hả chị")
    assert tokens.has_phrase(p, ("bao lâu",)) is False
    assert tokens.has_phrase(p, ("bao",)) is True


def test_unaccented_words_masks_accented_tokens():
    assert tokens.unaccented_words("dạ Hồng khong đi") == ["_", "_", "khong", "_"]


def test_has_phrase_matches_whole_words_and_multiword_phrases():
    p = tokens.padded("ok em, việc gì cũng phải tính trước cho chắc")
    assert tokens.has_phrase(p, ("ok",)) is True
    assert tokens.has_phrase(p, ("k",)) is False
    assert tokens.has_phrase(p, ("tính trước",)) is True
    assert tokens.has_phrase(p, ("tính sau",)) is False


def test_split_sentences_and_clauses_preserve_date_commas():
    text = "Em đang giữ Thứ Tư 21/10, 9 giờ sáng. Còn 2 giờ chiều em chưa giữ ạ!"
    sents = tokens.split_sentences(text)
    assert sents == [
        "Em đang giữ Thứ Tư 21/10, 9 giờ sáng.",
        "Còn 2 giờ chiều em chưa giữ ạ!",
    ]
    clauses = tokens.split_clauses("Thứ Tư 21/10, 9 giờ sáng, còn 2 giờ chiều thì sao",
                                   break_words=("còn",))
    assert clauses == ["Thứ Tư 21/10, 9 giờ sáng", "2 giờ chiều thì sao"]


def test_scan_spoken_clocks_and_scan_times():
    assert tokens.scan_spoken_clocks("Thứ Tư 21/10, 9 giờ sáng và 3 giờ rưỡi chiều") == [
        (9, 0, "sáng"),
        (3, 30, "chiều"),
    ]
    assert tokens.scan_times("dời qua 15h30 hoặc 3 giờ rưỡi chiều hay 16:00") == [
        (15, None, 30),
        (3, "chiều", 30),
        (16, None, 0),
    ]
    # "3 giờ chiều 2 người" không được lấy "2" làm phút
    assert tokens.scan_times("ừ 3 giờ chiều 2 người") == [(3, "chiều", None)]


def test_scan_day_offsets_weekdays_and_anchor_helpers():
    assert tokens.scan_day_offsets("chiều mai hoặc ngày mốt") == [1, 2]
    assert tokens.scan_day_offsets("một giờ chiều") == []
    assert tokens.scan_weekdays("thứ năm hoặc chủ nhật") == [3, 6]
    assert tokens.scan_weekdays("thu 6 hay cn") == [4, 6]
    assert tokens.names_a_clock("dời qua 4 giờ chiều") is True
    assert tokens.names_a_day("dời qua 4 giờ chiều") is False
    assert tokens.names_a_day("4 giờ chiều ngày 10/8") is True
    assert tokens.names_a_clock("giờ em qua được không") is False
