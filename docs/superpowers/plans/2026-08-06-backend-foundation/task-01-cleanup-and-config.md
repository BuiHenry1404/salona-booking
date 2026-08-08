# Task 1 · Dọn dẹp template và khung cấu hình

> Thuộc plan [Nền tảng backend](README.md). **Đọc [Ràng buộc toàn cục](README.md#ràng-buộc-toàn-cục) trước khi bắt đầu** — chúng áp cho mọi task, kể cả khi không nhắc lại ở đây.

**Files:**
- Delete: `app/agents/soulcare_team.py`, `tests/test_youtube_search.py`, `test_pydantic_fix.py`, `create_test_user.py`
- Modify: `app/core/config.py`, `requirements.txt`, `docker-compose.yml`, `.env.example`
- Create: `app/core/errors.py`, `tests/test_config.py`

**Interfaces:**
- Consumes: —
- Produces: `settings.booking_slot_minutes: int`, `settings.timezone: str`, `settings.shop_phone: str`, `settings.postgres_uri: str | None`, `settings.telegram_bot_token: SecretStr | None`, `settings.telegram_admin_chat_ids: str`, `settings.langfuse_public_key: SecretStr | None`, `settings.langfuse_secret_key: SecretStr | None`, `settings.langfuse_host: str | None`, `settings.embedding_dims: int`, `settings.azure_openai_embedding_model: str`, `settings.azure_openai_embedding_deployment: str | None`; lớp lỗi `AppError`, `SlotTakenError`, `OutsideShopHoursError`, `PastTimeError`, `RateLimitedError`, `NotFoundError`, `ForbiddenError`

- [ ] **Step 1: Xóa phần AutoGen và script rác**

```bash
git rm app/agents/soulcare_team.py tests/test_youtube_search.py test_pydantic_fix.py create_test_user.py
```

- [ ] **Step 2: Gỡ và thêm phụ thuộc**

Trong `requirements.txt`, xóa các dòng chứa `autogen-agentchat`, `autogen-core`, `autogen-ext`, `google-api-python-client`. Thêm:

```
pytest-asyncio>=0.23
freezegun>=1.5
```

- [ ] **Step 3: Viết test cấu hình (sẽ fail)**

Tạo `tests/test_config.py`:

```python
from app.core.config import settings


def test_booking_defaults():
    assert settings.booking_slot_minutes == 60
    assert settings.timezone == "Asia/Ho_Chi_Minh"


def test_optional_integrations_default_to_none():
    assert settings.telegram_bot_token is None
    assert settings.langfuse_public_key is None
    assert settings.postgres_uri is None


def test_embedding_dims_default():
    assert settings.embedding_dims == 1536
```

- [ ] **Step 4: Chạy test để xác nhận fail**

Run: `pytest tests/test_config.py -v`
Expected: FAIL với `AttributeError: 'Settings' object has no attribute 'booking_slot_minutes'`

- [ ] **Step 5: Thêm setting mới**

Trong `app/core/config.py`, xóa khối `youtube_api_key`, và thêm vào class `Settings`:

```python
    # Booking
    booking_slot_minutes: int = 60
    timezone: str = "Asia/Ho_Chi_Minh"

    # SĐT tiệm — hiện lên khi máy chủ hỏng để khách còn gọi được người thật.
    # Bắt buộc có giá trị thật trước khi chạy production.
    shop_phone: str = ""

    # Postgres (memory ngữ nghĩa — dùng ở Plan 2)
    postgres_uri: Optional[str] = None
    embedding_dims: int = 1536
    azure_openai_embedding_model: str = "text-embedding-3-small"
    # Trên Azure, tên deployment do người tạo đặt và thường KHÁC tên model.
    # Để trống thì lấy tên model làm tên deployment.
    azure_openai_embedding_deployment: Optional[str] = None

    # Langfuse — để trống thì tắt trace
    langfuse_public_key: Optional[SecretStr] = None
    langfuse_secret_key: Optional[SecretStr] = None
    langfuse_host: Optional[str] = None

    # Telegram — để trống thì không chạy bot
    telegram_bot_token: Optional[SecretStr] = None
    telegram_admin_chat_ids: str = ""
```

- [ ] **Step 6: Chạy test để xác nhận pass**

Run: `pytest tests/test_config.py -v`
Expected: PASS (3 passed)

- [ ] **Step 7: Tạo lớp lỗi nghiệp vụ**

Tạo `app/core/errors.py`:

```python
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
```

- [ ] **Step 8: Thêm Postgres vào docker-compose**

Trong `docker-compose.yml`, thêm service (ngang hàng với `mongo`):

```yaml
  postgres:
    image: pgvector/pgvector:pg16
    ports:
      - "5432:5432"
    environment:
      - POSTGRES_USER=salon
      - POSTGRES_PASSWORD=salon
      - POSTGRES_DB=salon_memory
    volumes:
      - pg_data:/var/lib/postgresql/data
```

Và thêm `pg_data:` vào khối `volumes:` ở cuối file.

- [ ] **Step 9: Cập nhật `.env.example`**

Thêm vào cuối, giữ hai dòng embedding cạnh nhau đúng như spec cảnh báo:

```
# Booking
BOOKING_SLOT_MINUTES=60
TIMEZONE=Asia/Ho_Chi_Minh

# SĐT tiệm — hiện cho khách khi máy chủ hỏng. Điền số thật trước khi lên production.
SHOP_PHONE=0900000000

# Postgres — chỉ dùng cho memory ngữ nghĩa (Plan 2)
POSTGRES_URI=postgresql://salon:salon@localhost:5432/salon_memory

# CẢNH BÁO: hai giá trị dưới phải khớp nhau.
# Lệch số chiều thì pgvector IM LẶNG nuốt lỗi ghi — không có đường migrate,
# phải xóa volume và tạo lại dữ liệu.
AZURE_OPENAI_EMBEDDING_MODEL=text-embedding-3-small
EMBEDDING_DIMS=1536
# Tên deployment trên Azure, để trống nếu trùng tên model.
AZURE_OPENAI_EMBEDDING_DEPLOYMENT=

# Langfuse — để trống thì tắt trace
LANGFUSE_PUBLIC_KEY=
LANGFUSE_SECRET_KEY=
LANGFUSE_HOST=https://cloud.langfuse.com

# Telegram — để trống thì không chạy bot
TELEGRAM_BOT_TOKEN=
TELEGRAM_ADMIN_CHAT_IDS=
```

- [ ] **Step 10: Chạy toàn bộ test và khởi động app**

Run: `pytest tests/test_config.py -v && python -c "import main"`
Expected: PASS, và import `main` không lỗi (không còn tham chiếu AutoGen)

- [ ] **Step 11: Commit**

```bash
git add -A
git commit -m "chore: remove AutoGen, add booking/postgres/telegram settings"
```

---
