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
