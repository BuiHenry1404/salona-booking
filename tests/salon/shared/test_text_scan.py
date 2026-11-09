import pytest

from app.agents.salon.shared.text_scan import (
    digits_in,
    scan_day_offsets,
    scan_money,
    scan_spoken_clocks,
    scan_times,
    scan_weekdays,
    split_clauses,
    split_sentences,
    words,
)


def test_words_splits_punctuation_cleanly():
    assert words("Dạ, anh Tám! 15:30 nhé?") == ["dạ", "anh", "tám", "15", "30", "nhé"]


def test_digits_in_collects_ascii_digit_runs():
    assert digits_in("gọi 090.123.4567 nhé") == ["090", "123", "4567"]
    assert digits_in("không có số nào") == []


def test_split_sentences_and_clauses_preserve_date_commas():
    text = "Em đang giữ Thứ Tư 21/10, 9 giờ sáng. Còn 2 giờ chiều em chưa giữ ạ!"
    sents = split_sentences(text)
    assert sents == [
        "Em đang giữ Thứ Tư 21/10, 9 giờ sáng.",
        "Còn 2 giờ chiều em chưa giữ ạ!",
    ]
    clauses = split_clauses(
        "Thứ Tư 21/10, 9 giờ sáng, còn 2 giờ chiều thì sao", break_words=("còn",)
    )
    assert clauses == ["Thứ Tư 21/10, 9 giờ sáng", "2 giờ chiều thì sao"]


def test_scan_spoken_clocks_and_scan_times():
    assert scan_spoken_clocks("Thứ Tư 21/10, 9 giờ sáng và 3 giờ rưỡi chiều") == [
        (9, 0, "sáng"),
        (3, 30, "chiều"),
    ]
    assert scan_times("dời qua 15h30 hoặc 3 giờ rưỡi chiều hay 16:00") == [
        (15, None, 30),
        (3, "chiều", 30),
        (16, None, 0),
    ]
    # "3 giờ chiều 2 người" không được lấy "2" làm phút
    assert scan_times("ừ 3 giờ chiều 2 người") == [(3, "chiều", None)]


def test_scan_day_offsets_and_weekdays():
    assert scan_day_offsets("chiều mai hoặc ngày mốt") == [1, 2]
    assert scan_day_offsets("một giờ chiều") == []
    assert scan_weekdays("thứ năm hoặc chủ nhật") == [3, 6]
    assert scan_weekdays("thu 6 hay cn") == [4, 6]


@pytest.mark.parametrize(
    "text, amounts",
    [
        ("sơn gel 120k", [120000]),
        ("giá 120.000đ ạ", [120000]),
        ("120 nghìn", [120000]),
        ("khoảng 150 nghìn", [150000]),
        ("từ 120 tới 180 nghìn", [120000, 180000]),
        ("120-180k", [120000, 180000]),
        ("1 triệu 2", [1200000]),
        ("1tr2", [1200000]),
        ("3 giờ chiều", []),
        ("phòng 8 người", []),
        ("700.000 đồng", [700000]),
    ],
)
def test_scan_money_table(text, amounts):
    assert scan_money(text) == amounts
