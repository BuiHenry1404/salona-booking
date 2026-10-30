from typing import Optional

from app.core.clock import to_local

_WEEKDAYS = ["Thứ Hai", "Thứ Ba", "Thứ Tư", "Thứ Năm", "Thứ Sáu", "Thứ Bảy", "Chủ Nhật"]


def _clock_phrase(hour: int, minute: int) -> str:
    """'3 giờ chiều', '9 giờ rưỡi sáng', '1 giờ 45 chiều'.

    Dùng chung cho cả datetime lẫn chuỗi 'HH:MM' — hai chỗ mà lệch nhau một
    chữ là khách nghe ra hai giọng khác nhau trong cùng một cuộc.
    """
    # Buổi trong ngày theo cách người Việt thật sự nói. Hai biên dễ sai:
    #
    # - ĐÚNG 12 giờ là "trưa", không phải "chiều". "Trưa" tồn tại chính xác để
    #   phân biệt 12h ban ngày với 12h đêm; "12 giờ chiều" không ai nói. Tiệm
    #   mở 08:00–19:00 không nghỉ trưa nên các ô 12:00–12:45 đặt được hằng
    #   ngày — sai ở đây là sai với khách thường xuyên. Chính parser của dự án
    #   đã hiểu đầu vào "12 giờ trưa", nên trước 2026-09-20 hệ ĐỌC VÀO "trưa"
    #   mà NÓI RA "chiều".
    # - 0 giờ là "12 giờ đêm", không phải "0 giờ sáng".
    if hour == 0:
        period, display = "đêm", 12
    elif hour < 12:
        period, display = "sáng", hour
    elif hour == 12:
        period, display = "trưa", 12
    elif hour < 18:
        period, display = "chiều", hour - 12
    else:
        period, display = "tối", hour - 12

    if minute == 0:
        clock = f"{display} giờ"
    elif minute == 30:
        clock = f"{display} giờ rưỡi"      # không ai đọc "9 giờ 30"
    else:
        clock = f"{display} giờ {minute:02d}"
    return f"{clock} {period}"


def format_vi_hhmm(hhmm: str) -> str:
    """'08:00' -> '8 giờ sáng'. Giờ mở cửa lưu dạng chuỗi, không phải datetime."""
    hour, minute = (int(x) for x in hhmm.split(":"))
    return _clock_phrase(hour, minute)


def format_vi_datetime(dt) -> str:
    """'Thứ Sáu 7/8, 3 giờ chiều' — cách người Việt lớn tuổi thực sự nói giờ.

    Không dùng dấu hai chấm: "3:00 chiều" là cách máy viết giờ. Chuỗi này đi
    thẳng vào lời thoại (câu chốt lịch của node confirm, danh sách lịch trong
    khối bối cảnh) nên nó phải đọc lên nghe được.
    """
    local = to_local(dt)
    return (f"{_WEEKDAYS[local.weekday()]} {local.day}/{local.month}, "
            f"{_clock_phrase(local.hour, local.minute)}")


# Tiền tố xưng hô nào cũng phải CẮT khỏi tên (prompt cấm nói cô/chú/bác),
# nhưng chỉ một số cho biết giới tính để suy ra "anh" hay "chị".
_HONORIFICS = {"cô", "chị", "bà", "chú", "anh", "ông", "bác", "em"}
_GENDERED = {
    "cô": "chị", "chị": "chị", "bà": "chị",
    "chú": "anh", "anh": "anh", "ông": "anh",
}


def derive_address(full_name: Optional[str]) -> tuple[str, str]:
    """Suy cách gọi khách từ tên. Trả (cách_gọi, tên_đã_bỏ_tiền_tố).

    13/13 khách trong DB có full_name mang sẵn tiền tố ("Cô Lan", "Bác Bảy").
    Đó là tín hiệu giới tính DUY NHẤT đang có — User model không có trường
    giới tính — nên map nó thay vì vứt đi. "Bác" không cho biết giới nên lùi
    về "anh chị", trùng với fallback vốn có ở confirm.py.
    """
    parts = (full_name or "").split()
    if len(parts) >= 2 and parts[0].lower() in _HONORIFICS:
        return _GENDERED.get(parts[0].lower(), "anh chị"), " ".join(parts[1:])
    return "anh chị", ""
