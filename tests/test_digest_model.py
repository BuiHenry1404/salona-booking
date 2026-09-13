from datetime import date, datetime, timezone

from app.models.conversation import Conversation, Digest
from app.services.digest import (BULLET_MAX_CHARS, MAX_BULLETS,
                                 DigestBullets)


def test_conversation_without_digest_field_loads():
    """Document cũ không có trường digest — không migrate."""
    conv = Conversation(user_id="u1")
    assert conv.digest is None


def test_digest_round_trips_through_model_dump():
    d = Digest(day=date(2026, 9, 14),
               covers_until=datetime(2026, 9, 14, 3, 0, tzinfo=timezone.utc),
               bullets=["Khách muốn làm tóc."], updated_at=datetime.now(timezone.utc))
    assert Digest(**d.model_dump()).bullets == ["Khách muốn làm tóc."]
    assert d.failures == 0


class TestDigestBulletsCoerces:
    """Ép, không từ chối: lớp dưới fail-soft, validator nghiêm là cách đắt
    nhất để vứt dữ liệu tốt (bài học ParsedTime)."""

    def test_too_many_lines_are_cut_to_max(self):
        out = DigestBullets(bullets=[f"dòng {i}" for i in range(20)])
        assert len(out.bullets) == MAX_BULLETS

    def test_long_lines_are_flattened_and_cut(self):
        out = DigestBullets(bullets=["a\nb " + "x" * 500])
        assert "\n" not in out.bullets[0]
        assert len(out.bullets[0]) <= BULLET_MAX_CHARS

    def test_empty_and_whitespace_lines_are_dropped(self):
        assert DigestBullets(bullets=["", "  ", "còn một"]).bullets == ["còn một"]

    def test_leading_dash_is_stripped(self):
        """Model hay tự thêm '- ' dù đã bảo trả danh sách."""
        assert DigestBullets(bullets=["- Khách muốn làm nail."]).bullets == ["Khách muốn làm nail."]
