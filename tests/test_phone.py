import pytest
from app.core.phone import normalize_phone, InvalidPhoneError


@pytest.mark.parametrize("raw", [
    "0912345678",
    "0912 345 678",
    "+84912345678",
    "84912345678",
    "+84 912 345 678",
    "0912-345-678",
    "840912345678",
])
def test_all_variants_normalise_to_same_string(raw):
    assert normalize_phone(raw) == "0912345678"


@pytest.mark.parametrize("raw", ["", "   ", "abc", "091234", "09123456789012", None])
def test_invalid_input_raises(raw):
    with pytest.raises(InvalidPhoneError):
        normalize_phone(raw)


@pytest.mark.parametrize("raw", [
    "0312345678",   # Viettel
    "0512345678",   # Vietnamobile
    "0712345678",   # Mobifone
    "0812345678",   # Vinaphone
    "0912345678",   # Vinaphone
])
def test_every_vietnamese_mobile_prefix_is_accepted(raw):
    assert normalize_phone(raw) == raw


@pytest.mark.parametrize("raw", [
    "0000000000",   # gõ nhầm phím 0
    "0123456789",   # dãy tăng dần, không phải đầu số thật
    "0287654321",   # số bàn Hà Nội — dự án chỉ nhận di động
    "0412345678",   # đầu 04 không tồn tại
    "0612345678",   # đầu 06 không tồn tại
])
def test_non_mobile_numbers_are_rejected(raw):
    """Nhận bừa thì sai sót chỉ lộ ra lúc khách không đăng nhập được — quá muộn."""
    with pytest.raises(InvalidPhoneError):
        normalize_phone(raw)
