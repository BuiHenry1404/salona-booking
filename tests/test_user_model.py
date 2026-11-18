import pytest

from app.core.vi_format import derive_address
from app.models.user import STAFF_NOTE_MAX, User
from app.repositories.user import UserRepository


def a_user(full_name):
    return User(phone="0912345678", hashed_password="x", full_name=full_name)


class TestFullNameIsSanitised:
    """`full_name` đi vào khối bối cảnh, mà khối đó mang vai HumanMessage và
    tự nói "hãy tin phần trên". Tên có xuống dòng là một lối để người ngoài
    ghi luật vào prompt.

    Làm sạch ở MODEL chứ không ở schema tạo user: như vậy dữ liệu đã nằm
    sẵn trong DB cũng được làm sạch lúc đọc lên.
    """

    def test_full_name_goes_through_single_line(self):
        """`User.full_name` đi qua `single_line` y hệt test_text.py — ở đây
        chỉ cần kiểm nó thật sự được gọi (kiểm hàm gốc ở `test_text.py`)."""
        assert a_user("Lan\r\n\tHoa").full_name == "Lan Hoa"

    def test_long_names_are_truncated(self):
        u = a_user("Lan" * 100)
        assert len(u.full_name) == 60

    def test_an_ordinary_name_is_untouched(self):
        assert a_user("Cô Lan").full_name == "Cô Lan"


class TestStaffNoteIsSanitised:
    """Ghi chú tiệm cũng đi vào khối bối cảnh — cùng lý do `single_line` áp
    dụng cho `full_name` áp dụng y hệt ở đây, dù do admin gõ."""

    def test_newlines_collapse(self):
        u = User(phone="0912345678", hashed_password="x", staff_note="hay trễ\nđổi ý")
        assert u.staff_note == "hay trễ đổi ý"

    def test_truncated_at_the_limit(self):
        u = User(phone="0912345678", hashed_password="x", staff_note="a" * (STAFF_NOTE_MAX + 50))
        assert len(u.staff_note) == STAFF_NOTE_MAX

    def test_none_stays_none(self):
        assert User(phone="0912345678", hashed_password="x").staff_note is None


class TestSelfAddressDefaultsToNone:
    def test_new_user_has_no_self_address(self):
        assert a_user("Nguyễn Thị Lan").self_address is None


class TestSetSelfAddressOverwrites:
    """Task 3: `remember_address` gọi lại khi khách đổi cách xưng — repo phải
    cho GHI ĐÈ (khác luật "chỉ ghi một lần" cũ)."""

    async def test_set_self_address_overwrites(self, test_db):
        repo = UserRepository(test_db)
        user = await repo.create_user(phone="0911111111", hashed_password="x",
                                      full_name="Lan", role="user")

        assert await repo.set_self_address(str(user.id), "chị")
        assert (await repo.get_by_id(str(user.id))).self_address == "chị"

        assert await repo.set_self_address(str(user.id), "cô")
        assert (await repo.get_by_id(str(user.id))).self_address == "cô"


class TestSeedSelfAddressFromNames:
    """Migration một lần (Task 13, main.py lifespan): tiền tố admin gõ sẵn
    ("Cô Lan", "Bác Bảy") là thông tin ĐÃ BIẾT — seed `self_address` cho
    khách chưa có giá trị, không chạm khách đã có (admin sửa tay)."""

    async def test_prefixed_name_seeds_self_address_once(self, test_db):
        repo = UserRepository(test_db)
        co_lan = await repo.create_user(phone="0911111111", hashed_password="x",
                                        full_name="Cô Lan", role="user")
        chu_hung = await repo.create_user(phone="0922222222", hashed_password="x",
                                          full_name="Chú Hùng", role="user")
        bac_bay = await repo.create_user(phone="0933333333", hashed_password="x",
                                         full_name="Bác Bảy", role="user")
        full_name_only = await repo.create_user(phone="0944444444", hashed_password="x",
                                                 full_name="Nguyễn Thị Lan", role="user")
        already_corrected = await repo.create_user(phone="0955555555", hashed_password="x",
                                                    full_name="Cô Mai", role="user")
        chi_ha = await repo.create_user(phone="0966666666", hashed_password="x",
                                        full_name="Chị Hà", role="user")
        await repo.set_self_address(str(already_corrected.id), "anh")  # admin đã sửa tay

        seeded = await repo.seed_self_address_from_names()

        # Live try 15/11 (L3b): tiền tố admin gõ là đúng cách gọi đó — cô / chú /
        # bác giữ nguyên, không còn đổi thành chị / anh; không tiền tố thì trống.
        assert seeded == 4
        assert (await repo.get_by_id(str(co_lan.id))).self_address == "cô"
        assert (await repo.get_by_id(str(chu_hung.id))).self_address == "chú"
        assert (await repo.get_by_id(str(bac_bay.id))).self_address == "bác"
        assert (await repo.get_by_id(str(chi_ha.id))).self_address == "chị"
        assert (await repo.get_by_id(str(full_name_only.id))).self_address is None
        # Giá trị admin đã sửa tay không bị seed ghi đè.
        assert (await repo.get_by_id(str(already_corrected.id))).self_address == "anh"

    async def test_running_twice_is_a_no_op_the_second_time(self, test_db):
        repo = UserRepository(test_db)
        await repo.create_user(phone="0911111111", hashed_password="x",
                               full_name="Cô Lan", role="user")
        await repo.seed_self_address_from_names()
        assert await repo.seed_self_address_from_names() == 0


class TestDeriveAddress:
    """Live try 15/11 (L3b): tiền tố admin gõ cho ĐÚNG cách gọi đó (`SelfAddress`
    giờ có cô / chú / bác / ông / bà); không tiền tố, hay tiền tố "em", vẫn là
    "anh chị" (quyết định 8: không đoán)."""

    @pytest.mark.parametrize("full_name, expected", [
        ("Cô Lan", ("cô", "Lan")),
        ("chú Hùng", ("chú", "Hùng")),
        ("Bác Bảy", ("bác", "Bảy")),
        ("Ông Năm", ("ông", "Năm")),
        ("Bà Tư", ("bà", "Tư")),
        ("Chị Hà", ("chị", "Hà")),
        ("Anh Minh", ("anh", "Minh")),
        ("Em Vy", ("anh chị", "Vy")),
        ("Nguyễn Thị Lan", ("anh chị", "")),
        ("Cô", ("anh chị", "")),
        (None, ("anh chị", "")),
    ])
    def test_prefix_gives_that_form(self, full_name, expected):
        assert derive_address(full_name) == expected


class TestHonorificSelfAddressMigration:
    """Live try 15/11 (L3c): khách đã được seed "chị" / "anh" từ tiền tố cô / chú /
    bác / ông / bà (luật cũ) được đổi sang đúng tiền tố — MỘT lần."""

    async def test_old_seeded_forms_take_the_prefix_once(self, test_db):
        repo = UserRepository(test_db)

        async def user(phone, name, form):
            created = await repo.create_user(phone=phone, hashed_password="x",
                                             full_name=name, role="user")
            if form is not None:
                await repo.set_self_address(str(created.id), form)
            return str(created.id)

        changed = {
            await user("0911111111", "Cô Lan", "chị"): "cô",
            await user("0922222222", "Chú Hùng", "anh"): "chú",
            await user("0933333333", "bác Bảy", "anh"): "bác",
            await user("0944444444", "Ông Năm", "anh"): "ông",
            await user("0955555555", "Bà Tư", "chị"): "bà",
        }
        kept = {
            await user("0966666666", "Chị Hà", "chị"): "chị",
            await user("0977777777", "Cô Hoa", "cô"): "cô",
            await user("0988888888", "Nguyễn Văn Ba", "anh"): "anh",
            await user("0999999999", "Cô Mai", None): None,
        }

        assert await repo.migrate_honorific_self_address() == 5

        for user_id, form in {**changed, **kept}.items():
            assert (await repo.get_by_id(user_id)).self_address == form, user_id

    async def test_runs_only_once(self, test_db):
        repo = UserRepository(test_db)
        created = await repo.create_user(phone="0911111111", hashed_password="x",
                                         full_name="Cô Lan", role="user")
        await repo.set_self_address(str(created.id), "chị")
        assert await repo.migrate_honorific_self_address() == 1
        # Khách tự nói lại "gọi chị" sau migration (remember_address): lần khởi
        # động sau không được đổi ngược.
        await repo.set_self_address(str(created.id), "chị")

        assert await repo.migrate_honorific_self_address() == 0
        assert (await repo.get_by_id(str(created.id))).self_address == "chị"
