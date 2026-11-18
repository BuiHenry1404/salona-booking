import pytest

from app.agents.salon.shared.text_scan import (
    digits_in,
    scan_day_offsets,
    scan_money,
    scan_spoken_clocks,
    scan_times,
    scan_weekdays,
    split_sentences,
    words,
)


def test_words_splits_punctuation_cleanly():
    assert words("Dạ, anh Tám! 15:30 nhé?") == ["dạ", "anh", "tám", "15", "30", "nhé"]


def test_digits_in_collects_ascii_digit_runs():
    assert digits_in("gọi 090.123.4567 nhé") == ["090", "123", "4567"]
    assert digits_in("không có số nào") == []


def test_split_sentences_preserves_date_commas():
    text = "Em đang giữ Thứ Tư 21/10, 9 giờ sáng. Còn 2 giờ chiều em chưa giữ ạ!"
    sents = split_sentences(text)
    assert sents == [
        "Em đang giữ Thứ Tư 21/10, 9 giờ sáng.",
        "Còn 2 giờ chiều em chưa giữ ạ!",
    ]


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


@pytest.mark.parametrize(
    "text, expected",
    [
        ("1 triệu 2 đến 1 triệu 5", [1_200_000, 1_500_000]),
        ("1 triệu 200 nghìn", [1_200_000]),
        ("1,2 triệu", [1_200_000]),
        ("2 triệu", [2_000_000]),
        ("120 đến 150 nghìn", [120_000, 150_000]),
        ("120k đến 150k", [120_000, 150_000]),
    ],
)
def test_scan_money_reads_million_forms(text, expected):
    assert scan_money(text) == expected


@pytest.mark.parametrize(
    "text, expected",
    [
        # Review F4: the "X triệu Y" shorthand digit never crosses punctuation
        # and is never a clock ("giờ" / "h" after Y).
        ("giá 1 triệu, 3 giờ chiều", [1_000_000]),
        ("giá 1 triệu 3 giờ chiều", [1_000_000]),
        ("giá 1 triệu 3h chiều", [1_000_000]),
        ("1 triệu. 2 người", [1_000_000]),
        ("1 triệu rưỡi", [1_500_000]),
        ("1.5 triệu", [1_500_000]),
        ("1,5 triệu", [1_500_000]),
        ("1 triệu 5", [1_500_000]),
        ("1.200.000đ", [1_200_000]),
    ],
)
def test_scan_money_million_shorthand_edges(text, expected):
    assert scan_money(text) == expected
