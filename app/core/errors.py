class AppError(Exception):
    """Lỗi nghiệp vụ. Router dịch sang HTTP, service không bao giờ ném HTTPException."""

    status_code = 400
    message = "Có lỗi xảy ra"

    def __init__(self, message: str | None = None, **extra):
        self.message = message or self.message
        self.extra = extra
        super().__init__(self.message)


class SlotTakenError(AppError):
    status_code = 409
    message = "Giờ đó đã có người đặt"


class PhoneTakenError(AppError):
    status_code = 409
    message = "Số điện thoại này đã có tài khoản"


class OutsideShopHoursError(AppError):
    message = "Giờ đó tiệm không mở cửa"


class PastTimeError(AppError):
    message = "Không đặt được lịch trong quá khứ"


class RateLimitedError(AppError):
    status_code = 429
    message = "Thử lại quá nhiều lần, vui lòng đợi một lát"


class NotFoundError(AppError):
    status_code = 404
    message = "Không tìm thấy"


class ForbiddenError(AppError):
    status_code = 403
    message = "Không có quyền"
