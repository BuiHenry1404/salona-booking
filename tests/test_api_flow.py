from datetime import datetime, timedelta

import pytest

from app.core.clock import TZ
from app.services.auth import AuthService

pytestmark = pytest.mark.asyncio


async def _login(client, phone, password):
    resp = await client.post("/api/v1/auth/login", json={"phone": phone, "password": password})
    assert resp.status_code == 200, resp.text
    return {"Authorization": f"Bearer {resp.json()['access_token']}"}


@pytest.fixture
async def admin_headers(async_client, test_db):
    await AuthService(test_db).create_user("0901234567", "chutiem123", "Chủ tiệm", role="admin")
    return await _login(async_client, "0901234567", "chutiem123")


@pytest.fixture
async def user_headers(async_client, test_db):
    await AuthService(test_db).create_user("0912345678", "matkhau123", "Cô Lan")
    return await _login(async_client, "0912345678", "matkhau123")


def tomorrow_at(hour):
    local = datetime.now(TZ) + timedelta(days=1)
    return local.replace(hour=hour, minute=0, second=0, microsecond=0).isoformat()


async def test_login_with_wrong_password_returns_401(async_client, user_headers):
    resp = await async_client.post(
        "/api/v1/auth/login", json={"phone": "0912345678", "password": "sai"}
    )
    assert resp.status_code == 401


async def test_repeated_wrong_passwords_end_in_429_not_401(async_client, user_headers):
    """Chốt phần đấu dây: service ném RateLimitedError, handler dịch thành 429."""
    for _ in range(10):
        resp = await async_client.post(
            "/api/v1/auth/login", json={"phone": "0912345678", "password": "sai"}
        )
        assert resp.status_code == 401

    resp = await async_client.post(
        "/api/v1/auth/login", json={"phone": "0912345678", "password": "sai"}
    )
    assert resp.status_code == 429
    assert "Thử lại quá nhiều lần" in resp.json()["detail"]


async def test_lockout_also_refuses_the_correct_password(async_client, user_headers):
    for _ in range(10):
        await async_client.post(
            "/api/v1/auth/login", json={"phone": "0912345678", "password": "sai"}
        )

    resp = await async_client.post(
        "/api/v1/auth/login", json={"phone": "0912345678", "password": "matkhau123"}
    )
    assert resp.status_code == 429


async def test_normal_user_cannot_create_accounts(async_client, user_headers):
    resp = await async_client.post(
        "/api/v1/auth/users",
        json={"phone": "0999888777", "password": "abcd", "full_name": "X"},
        headers=user_headers,
    )
    assert resp.status_code == 403


async def test_admin_can_create_accounts(async_client, admin_headers):
    resp = await async_client.post(
        "/api/v1/auth/users",
        json={"phone": "0999888777", "password": "abcd", "full_name": "Khách mới"},
        headers=admin_headers,
    )
    assert resp.status_code == 201
    assert resp.json()["phone"] == "0999888777"


async def test_book_then_list_then_cancel(async_client, user_headers):
    created = await async_client.post(
        "/api/v1/appointments",
        json={"start_at": tomorrow_at(15), "note": "làm tóc"},
        headers=user_headers,
    )
    assert created.status_code == 201, created.text
    appointment_id = created.json()["id"]

    listed = await async_client.get("/api/v1/appointments/mine", headers=user_headers)
    assert [a["id"] for a in listed.json()] == [appointment_id]

    deleted = await async_client.delete(
        f"/api/v1/appointments/{appointment_id}", headers=user_headers
    )
    assert deleted.status_code == 204

    listed_again = await async_client.get("/api/v1/appointments/mine", headers=user_headers)
    assert listed_again.json() == []


async def test_double_booking_returns_409(async_client, user_headers, admin_headers):
    payload = {"start_at": tomorrow_at(16), "note": None}
    first = await async_client.post("/api/v1/appointments", json=payload, headers=user_headers)
    assert first.status_code == 201

    clash = await async_client.post("/api/v1/appointments", json=payload, headers=admin_headers)
    assert clash.status_code == 409


async def test_booking_at_3am_returns_400(async_client, user_headers):
    resp = await async_client.post(
        "/api/v1/appointments", json={"start_at": tomorrow_at(3), "note": None}, headers=user_headers
    )
    assert resp.status_code == 400


async def test_shop_status_flow(async_client, admin_headers, user_headers):
    free = await async_client.get("/api/v1/shop/status", headers=user_headers)
    assert free.json()["is_busy"] is False

    await async_client.post("/api/v1/shop/busy", json={"minutes": 30}, headers=admin_headers)
    busy = await async_client.get("/api/v1/shop/status", headers=user_headers)
    assert busy.json()["is_busy"] is True
    assert busy.json()["minutes_left"] <= 30

    await async_client.post("/api/v1/shop/free", headers=admin_headers)
    assert (await async_client.get("/api/v1/shop/status", headers=user_headers)).json()["is_busy"] is False


async def test_normal_user_cannot_change_shop_status(async_client, user_headers):
    resp = await async_client.post("/api/v1/shop/busy", json={"minutes": 30}, headers=user_headers)
    assert resp.status_code == 403


async def test_mongo_down_returns_503_with_shop_phone(async_client, user_headers, monkeypatch):
    """Mongo chết là fail-hard: 503 kèm SĐT tiệm để khách gọi người thật.

    Tuyệt đối không để traceback của Motor lọt ra ngoài — người lớn tuổi
    thấy chữ tiếng Anh là nghĩ mình làm hỏng máy.
    """
    from pymongo.errors import ServerSelectionTimeoutError

    from app.core.config import settings
    from app.services.shop import ShopService

    async def _die(self):
        raise ServerSelectionTimeoutError("no primary available")

    monkeypatch.setattr(ShopService, "get_status", _die)
    monkeypatch.setattr(settings, "shop_phone", "0287654321")

    resp = await async_client.get("/api/v1/shop/status", headers=user_headers)
    assert resp.status_code == 503
    body = resp.json()
    assert "0287654321" in body["detail"]
    assert body["shop_phone"] == "0287654321"
    assert "ServerSelection" not in body["detail"]


async def test_creating_a_duplicate_phone_returns_409_not_503(async_client, admin_headers):
    """DuplicateKeyError là con của PyMongoError; lọt ra thì khách bị báo
    'máy của tiệm đang hỏng' cho một lỗi nhập liệu bình thường."""
    payload = {"phone": "0999888777", "password": "abcd", "full_name": "Khách mới"}
    first = await async_client.post("/api/v1/auth/users", json=payload, headers=admin_headers)
    assert first.status_code == 201

    again = await async_client.post("/api/v1/auth/users", json=payload, headers=admin_headers)
    assert again.status_code == 409
    assert "tài khoản" in again.json()["detail"]


async def test_listed_appointments_keep_their_utc_offset(async_client, user_headers):
    """POST và GET phải trả cùng một dạng. Thiếu offset thì new Date() ở trình
    duyệt hiểu là giờ địa phương và hiện lệch 7 tiếng."""
    created = await async_client.post(
        "/api/v1/appointments",
        json={"start_at": tomorrow_at(14), "note": None},
        headers=user_headers,
    )
    assert created.status_code == 201, created.text

    [listed] = (await async_client.get("/api/v1/appointments/mine", headers=user_headers)).json()
    assert listed["start_at"] == created.json()["start_at"]
    assert listed["start_at"].endswith("+00:00") or listed["start_at"].endswith("Z")



async def test_chat_history_days_are_listed_for_the_logged_in_customer(
    async_client, user_headers, test_db
):
    from app.repositories.user import UserRepository
    from app.services.conversation import ConversationService

    user = await UserRepository(test_db).get_by_phone("0912345678")
    await ConversationService(test_db).append(str(user.id), "user", "mai 3 giờ chiều nhé con")

    resp = await async_client.get("/api/v1/conversations/days", headers=user_headers)
    assert resp.status_code == 200, resp.text
    [today] = resp.json()["days"]
    assert today["message_count"] == 1
    assert today["preview"].startswith("mai 3 giờ chiều")

    messages = await async_client.get(
        f"/api/v1/conversations/days/{today['day']}", headers=user_headers
    )
    assert messages.status_code == 200, messages.text
    body = messages.json()
    assert body["is_today"] is True
    assert [m["content"] for m in body["messages"]] == ["mai 3 giờ chiều nhé con"]


async def test_chat_history_requires_login(async_client):
    assert (await async_client.get("/api/v1/conversations/days")).status_code == 403


async def test_an_old_day_is_marked_read_only(async_client, user_headers, test_db):
    from app.core.clock import now_utc
    from app.repositories.user import UserRepository
    from app.services.conversation import ConversationService

    user = await UserRepository(test_db).get_by_phone("0912345678")
    await ConversationService(test_db).append(str(user.id), "user", "chuyện hôm kia")
    await test_db["conversations"].update_one(
        {"user_id": str(user.id)},
        {"$set": {"messages.0.created_at": now_utc() - timedelta(days=2)}},
    )

    [old_day] = (
        await async_client.get("/api/v1/conversations/days", headers=user_headers)
    ).json()["days"]
    body = (
        await async_client.get(
            f"/api/v1/conversations/days/{old_day['day']}", headers=user_headers
        )
    ).json()
    assert body["is_today"] is False
    assert [m["content"] for m in body["messages"]] == ["chuyện hôm kia"]


async def test_admin_can_book_for_customer(async_client, admin_headers, test_db):
    """Khách gọi điện, chủ tiệm bấm hộ. Lịch phải THUỘC KHÁCH: chứng minh bằng
    chính đăng nhập của khách thấy lịch trong /mine, không chỉ dựa vào
    user_name/phone trong response (hai field đó chỉ là bản chụp)."""
    customer = await AuthService(test_db).create_user("0987654321", "matkhau123", "Cô Hoa")

    resp = await async_client.post(
        "/api/v1/appointments",
        json={"start_at": tomorrow_at(9), "note": "làm nail", "for_user_id": str(customer.id)},
        headers=admin_headers,
    )
    assert resp.status_code == 201, resp.text
    assert resp.json()["user_name"] == "Cô Hoa"
    assert resp.json()["phone"] == "0987654321"

    # Ownership THẬT: đăng nhập bằng chính tài khoản khách, lịch phải nằm
    # trong danh sách "Lịch của tôi" của khách.
    customer_headers = await _login(async_client, "0987654321", "matkhau123")
    listed = await async_client.get("/api/v1/appointments/mine", headers=customer_headers)
    assert listed.status_code == 200, listed.text
    assert [a["id"] for a in listed.json()] == [resp.json()["id"]]


async def test_customer_cannot_book_for_another_user(async_client, user_headers, test_db):
    """Chặn ở BACKEND: sửa localStorage thành admin cũng không đặt hộ được."""
    victim = await AuthService(test_db).create_user("0987654321", "matkhau123", "Cô Hoa")

    resp = await async_client.post(
        "/api/v1/appointments",
        json={"start_at": tomorrow_at(9), "note": None, "for_user_id": str(victim.id)},
        headers=user_headers,
    )
    assert resp.status_code == 403


async def test_admin_booking_for_unknown_user_returns_404(async_client, admin_headers):
    resp = await async_client.post(
        "/api/v1/appointments",
        json={
            "start_at": tomorrow_at(9),
            "note": None,
            "for_user_id": "000000000000000000000000",
        },
        headers=admin_headers,
    )
    assert resp.status_code == 404


class TestShopHoursValidation:
    """Giờ mở cửa rác phải bị chặn ngay ở cổng vào.

    `_to_minutes` trong app/core/... làm `int(hhmm.split(":"))` không bẫy, mà
    `is_within` gọi nó ở MỌI lượt đặt lịch. Lưu được "25:99" một lần là từ đó
    khách nào đặt lịch cũng ăn 500, và chủ tiệm không hề biết mình vừa làm gì.
    """

    async def test_garbage_open_time_is_rejected(self, async_client, admin_headers):
        resp = await async_client.put(
            "/api/v1/shop/hours",
            json={"open_time": "abc", "close_time": "19:00", "closed_days": []},
            headers=admin_headers,
        )
        assert resp.status_code == 422

    async def test_hour_out_of_range_is_rejected(self, async_client, admin_headers):
        resp = await async_client.put(
            "/api/v1/shop/hours",
            json={"open_time": "25:99", "close_time": "19:00", "closed_days": []},
            headers=admin_headers,
        )
        assert resp.status_code == 422

    async def test_closing_before_opening_is_rejected(self, async_client, admin_headers):
        """Mở 19:00 đóng 08:00 thì `is_within` không bao giờ đúng — tiệm đóng
        cửa vĩnh viễn mà giao diện vẫn báo giờ làm bình thường."""
        resp = await async_client.put(
            "/api/v1/shop/hours",
            json={"open_time": "19:00", "close_time": "08:00", "closed_days": []},
            headers=admin_headers,
        )
        assert resp.status_code == 422

    async def test_closing_equal_to_opening_is_rejected(self, async_client, admin_headers):
        resp = await async_client.put(
            "/api/v1/shop/hours",
            json={"open_time": "09:00", "close_time": "09:00", "closed_days": []},
            headers=admin_headers,
        )
        assert resp.status_code == 422

    async def test_valid_hours_are_still_accepted(self, async_client, admin_headers):
        resp = await async_client.put(
            "/api/v1/shop/hours",
            json={"open_time": "09:00", "close_time": "18:00", "closed_days": [0]},
            headers=admin_headers,
        )
        assert resp.status_code == 200
        assert resp.json()["open_time"] == "09:00"

    async def test_booking_still_works_after_valid_hours_are_set(
        self, async_client, admin_headers, user_headers
    ):
        """Hàng rào mới không được chặn nhầm giờ hợp lệ: đặt lịch vẫn chạy."""
        await async_client.put(
            "/api/v1/shop/hours",
            json={"open_time": "08:00", "close_time": "19:00", "closed_days": []},
            headers=admin_headers,
        )
        day = (datetime.now(TZ) + timedelta(days=1)).date().isoformat()
        slots = await async_client.get(
            f"/api/v1/appointments/free-slots/{day}", headers=user_headers
        )
        assert slots.status_code == 200
        assert slots.json()["slots"]
