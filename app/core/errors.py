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


class InvalidRefreshTokenError(AppError):
    """Refresh token sai, hết hạn, đã bị thu hồi, hoặc bị phát lại.

    Cố ý dùng CHUNG một thông báo cho mọi nguyên nhân: phân biệt "token không
    tồn tại" với "token đã bị thu hồi" là nói cho kẻ tấn công biết nó đoán trúng
    tới đâu.
    """

    status_code = 401
    message = "Phiên đăng nhập đã hết hạn, anh chị đăng nhập lại giúp em nhé"


class ForbiddenError(AppError):
    status_code = 403
    message = "Không có quyền"
