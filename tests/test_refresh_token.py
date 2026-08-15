from datetime import timedelta

import pytest

from app.core.clock import now_utc
from app.core.errors import InvalidRefreshTokenError
from app.services.auth import AuthService

pytestmark = pytest.mark.asyncio


async def _seed(db, phone="0912345678", password="matkhau123"):
    svc = AuthService(db)
    user = await svc.create_user(phone=phone, password=password, full_name="Cô Lan")
    return svc, user


async def _age_used_at(db, seconds: int) -> None:
    """Đẩy mốc `used_at` lùi lại để ra khỏi cửa sổ ân hạn — không cần chờ thật."""
    await db["refresh_tokens"].update_many(
        {"used_at": {"$ne": None}},
        [{"$set": {"used_at": {"$subtract": ["$used_at", seconds * 1000]}}}],
    )


async def test_refresh_token_is_stored_hashed_never_in_the_clear(test_db):
    """DB rò rỉ thì không được tương đương trao phiên đăng nhập."""
    svc, user = await _seed(test_db)
    raw = await svc.issue_refresh_token(str(user.id))

    docs = await test_db["refresh_tokens"].find().to_list(length=10)
    assert docs
    assert all(raw not in str(doc.values()) for doc in docs)


async def test_consuming_a_token_returns_the_user_and_a_new_token(test_db):
    svc, user = await _seed(test_db)
    raw = await svc.issue_refresh_token(str(user.id))

    got_user, new_raw = await svc.consume_refresh_token(raw)

    assert got_user.phone == "0912345678"
    assert new_raw != raw


async def test_the_rotated_token_keeps_working(test_db):
    svc, user = await _seed(test_db)
    raw = await svc.issue_refresh_token(str(user.id))
    _, second = await svc.consume_refresh_token(raw)

    got_user, third = await svc.consume_refresh_token(second)
    assert got_user.phone == "0912345678"
    assert third not in (raw, second)


async def test_a_reused_token_inside_the_grace_window_is_allowed(test_db):
    """Điện thoại mạng chập chờn bắn hai request song song lúc access token hết
    hạn. Cái thứ hai trình lại đúng token đó — không được coi là bị đánh cắp."""
    svc, user = await _seed(test_db)
    raw = await svc.issue_refresh_token(str(user.id))

    _, first = await svc.consume_refresh_token(raw)
    _, second = await svc.consume_refresh_token(raw)

    assert first != second
    for token in (first, second):
        got_user, _ = await svc.consume_refresh_token(token)
        assert got_user.phone == "0912345678"


async def test_a_reused_token_after_the_grace_window_revokes_the_whole_family(test_db):
    svc, user = await _seed(test_db)
    raw = await svc.issue_refresh_token(str(user.id))
    _, live = await svc.consume_refresh_token(raw)
    await _age_used_at(test_db, 60)

    with pytest.raises(InvalidRefreshTokenError):
        await svc.consume_refresh_token(raw)

    with pytest.raises(InvalidRefreshTokenError):
        await svc.consume_refresh_token(live)


async def test_revoking_one_family_leaves_other_sessions_alone(test_db):
    """Khách đăng nhập trên điện thoại và máy tính bảng — mất một máy không
    được đá luôn máy kia."""
    svc, user = await _seed(test_db)
    phone_session = await svc.issue_refresh_token(str(user.id))
    tablet_session = await svc.issue_refresh_token(str(user.id))

    _, live = await svc.consume_refresh_token(phone_session)
    await _age_used_at(test_db, 60)
    with pytest.raises(InvalidRefreshTokenError):
        await svc.consume_refresh_token(phone_session)

    got_user, _ = await svc.consume_refresh_token(tablet_session)
    assert got_user.phone == "0912345678"


async def test_an_expired_token_is_refused(test_db):
    svc, user = await _seed(test_db)
    raw = await svc.issue_refresh_token(str(user.id))
    await test_db["refresh_tokens"].update_many(
        {}, {"$set": {"expires_at": now_utc() - timedelta(seconds=1)}}
    )

    with pytest.raises(InvalidRefreshTokenError):
        await svc.consume_refresh_token(raw)


async def test_an_unknown_token_is_refused(test_db):
    svc, _ = await _seed(test_db)
    with pytest.raises(InvalidRefreshTokenError):
        await svc.consume_refresh_token("khong-phai-token-that")


async def test_a_deactivated_user_cannot_refresh(test_db):
    svc, user = await _seed(test_db)
    raw = await svc.issue_refresh_token(str(user.id))
    await test_db["users"].update_one({"_id": user.id}, {"$set": {"is_active": False}})

    with pytest.raises(InvalidRefreshTokenError):
        await svc.consume_refresh_token(raw)


async def test_changing_the_password_kills_every_refresh_token(test_db):
    """Không có bước này thì vá thu hồi access token là vô nghĩa: kẻ chiếm tài
    khoản vẫn tự cấp access token mới bằng refresh token cũ."""
    svc, user = await _seed(test_db)
    raw = await svc.issue_refresh_token(str(user.id))

    await svc.reset_password("0912345678", "matkhaumoi456", ip="1.2.3.4")

    with pytest.raises(InvalidRefreshTokenError):
        await svc.consume_refresh_token(raw)


async def test_logout_kills_the_session_it_was_given(test_db):
    svc, user = await _seed(test_db)
    raw = await svc.issue_refresh_token(str(user.id))

    await svc.revoke_refresh_family(raw)

    with pytest.raises(InvalidRefreshTokenError):
        await svc.consume_refresh_token(raw)


async def test_logout_with_an_unknown_token_is_not_an_error(test_db):
    """Nút đăng xuất không bao giờ được báo lỗi cho khách."""
    svc, _ = await _seed(test_db)
    await svc.revoke_refresh_family("khong-phai-token-that")


async def test_one_users_token_never_returns_another_user(test_db):
    svc, lan = await _seed(test_db)
    hoa = await svc.create_user(phone="0987654321", password="matkhau123", full_name="Cô Hoa")
    raw = await svc.issue_refresh_token(str(hoa.id))

    got_user, _ = await svc.consume_refresh_token(raw)
    assert got_user.phone == "0987654321"
