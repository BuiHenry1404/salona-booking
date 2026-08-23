from datetime import date, datetime
from typing import List, Literal, Optional

from pydantic import BaseModel, ConfigDict, EmailStr, Field, model_validator


class LoginRequest(BaseModel):
    phone: str
    password: str


class TokenResponse(BaseModel):
    """Refresh token KHÔNG nằm ở đây — nó đi bằng cookie HttpOnly.

    Trả trong body là buộc frontend cất ở nơi JavaScript đọc được; IETF
    "OAuth 2.0 for Browser-Based Applications" khuyến nghị mạnh cookie HttpOnly
    cho ứng dụng xử lý dữ liệu cá nhân, mà app này giữ tên, SĐT và lịch của khách.
    """

    access_token: str
    token_type: str = "bearer"
    role: Literal["user", "admin"]


class CreateUserRequest(BaseModel):
    phone: str
    password: str = Field(..., min_length=4)
    full_name: Optional[str] = None
    role: Literal["user", "admin"] = "user"


class ResetPasswordRequest(BaseModel):
    phone: str
    new_password: str = Field(..., min_length=4)


class UserResponse(BaseModel):
    id: str
    phone: str
    full_name: Optional[str] = None
    role: Literal["user", "admin"]
    is_active: bool


class AppointmentCreateRequest(BaseModel):
    start_at: datetime
    note: Optional[str] = None
    # Chỉ admin được điền — khách gửi lên sẽ bị từ chối 403 ở router.
    for_user_id: Optional[str] = None


class AppointmentResponse(BaseModel):
    id: str
    start_at: datetime
    duration_minutes: int
    note: Optional[str] = None
    status: Literal["booked", "cancelled"]
    user_name: Optional[str] = None
    phone: Optional[str] = None


class ShopStatusResponse(BaseModel):
    is_busy: bool
    busy_until: Optional[datetime] = None
    minutes_left: Optional[int] = None


class SetBusyRequest(BaseModel):
    minutes: int = Field(..., ge=5, le=480)


_HHMM = r"^([01]\d|2[0-3]):[0-5]\d$"


class ShopHoursRequest(BaseModel):
    """Giờ mở cửa phải chặn ngay tại đây, không để xuống service.

    `_to_minutes` làm `int(hhmm.split(":"))` không bẫy, và `is_within` gọi nó ở
    MỌI lượt đặt lịch. Lưu được "25:99" một lần là từ đó khách nào đặt cũng ăn
    500 — chủ tiệm gõ nhầm một cú, cả tiệm ngừng nhận lịch mà không ai biết vì sao.
    """

    open_time: str = Field("08:00", pattern=_HHMM)
    close_time: str = Field("19:00", pattern=_HHMM)
    closed_days: List[int] = Field(default_factory=list)

    @model_validator(mode="after")
    def _close_must_be_after_open(self):
        """Đóng trước mở thì `is_within` không bao giờ đúng: tiệm đóng cửa vĩnh
        viễn trong khi giao diện vẫn hiện giờ làm bình thường."""
        if self.close_time <= self.open_time:
            raise ValueError("Giờ đóng cửa phải sau giờ mở cửa")
        return self


class FreeSlotsResponse(BaseModel):
    slots: List[datetime]


class ChatMessageResponse(BaseModel):
    role: Literal["user", "assistant"]
    content: str
    created_at: datetime


class DaySummaryResponse(BaseModel):
    day: date
    message_count: int
    preview: str


class DayListResponse(BaseModel):
    days: List[DaySummaryResponse]


class DayMessagesResponse(BaseModel):
    day: date
    # Ngày cũ chỉ để đọc lại; frontend dùng cờ này để khoá ô nhập.
    is_today: bool
    messages: List[ChatMessageResponse]


class HealthResponse(BaseModel):
    status: str
    timestamp: datetime
    version: str = "1.0.0"
