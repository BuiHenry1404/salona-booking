"""Shim tạm (Task 6.1, Task 6.3 xoá): module đã dời sang `app.agents.booking.timeparse`.

Không dùng `import *`: tên cũ trỏ thẳng vào CHÍNH module mới (sys.modules) để
`monkeypatch.setattr(<module cũ>, ...)` của test cũ vẫn trúng hàm thật — kể cả
tên `_riêng` mà `import *` bỏ sót."""
import sys

from app.agents.booking import timeparse as _moved

sys.modules[__name__] = _moved
