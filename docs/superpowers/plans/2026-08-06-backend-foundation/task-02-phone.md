# Task 2 · Chuẩn hóa số điện thoại

> Thuộc plan [Nền tảng backend](README.md). **Đọc [Ràng buộc toàn cục](README.md#ràng-buộc-toàn-cục) trước khi bắt đầu** — chúng áp cho mọi task, kể cả khi không nhắc lại ở đây.

**Files:**
- Create: `app/core/phone.py`, `tests/test_phone.py`

**Interfaces:**
- Consumes: `AppError` từ Task 1
- Produces: `normalize_phone(raw: str) -> str` — trả về dạng chuẩn `0XXXXXXXXX` (10 chữ số, bắt đầu bằng 0). Ném `InvalidPhoneError` nếu không hợp lệ.

- [ ] **Step 1: Viết test (sẽ fail)**

Tạo `tests/test_phone.py`:

```python
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
```

- [ ] **Step 2: Chạy test để xác nhận fail**

Run: `pytest tests/test_phone.py -v`
Expected: FAIL với `ModuleNotFoundError: No module named 'app.core.phone'`

- [ ] **Step 3: Viết cài đặt tối thiểu**

Tạo `app/core/phone.py`:

```python
import re

from app.core.errors import AppError


class InvalidPhoneError(AppError):
    message = "Số điện thoại không hợp lệ"


_VALID = re.compile(r"^0\d{9}$")


def normalize_phone(raw: str) -> str:
    """Quy mọi cách viết SĐT Việt Nam về một chuỗi duy nhất: 0XXXXXXXXX.

    Người lớn tuổi nhập mỗi lần một kiểu; không chuẩn hóa thì không đăng nhập được.
    """
    digits = re.sub(r"\D", "", raw or "")

    if digits.startswith("840"):
        digits = digits[2:]
    elif digits.startswith("84") and len(digits) == 11:
        digits = "0" + digits[2:]

    if not _VALID.match(digits):
        raise InvalidPhoneError()
    return digits
```

- [ ] **Step 4: Chạy test để xác nhận pass**

Run: `pytest tests/test_phone.py -v`
Expected: PASS (13 passed)

- [ ] **Step 5: Commit**

```bash
git add app/core/phone.py tests/test_phone.py
git commit -m "feat: normalise Vietnamese phone numbers to one canonical form"
```

---
