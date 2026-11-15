from typing import List, Optional

from bson import ObjectId
from motor.motor_asyncio import AsyncIOMotorDatabase
from pymongo.errors import DuplicateKeyError

from app.core.errors import PhoneTakenError
from app.core.phone import InvalidPhoneError, normalize_phone
from app.models.user import Role, User
from app.repositories.base import BaseRepository

# Dấu các migration một lần đã chạy (`{"_id": tên}`).
_MIGRATIONS = "migrations"
_HONORIFIC_MIGRATION = "self_address_honorific_prefix"
# Tiền tố tên mà luật seed cũ đổi sang "anh" / "chị" (hoặc bỏ trống).
_HONORIFIC_PREFIXES = frozenset({"cô", "chú", "bác", "ông", "bà"})


class UserRepository(BaseRepository[User]):
    def __init__(self, db: AsyncIOMotorDatabase):
        super().__init__(db, User, "users")

    async def get_by_phone(self, phone: str) -> Optional[User]:
        try:
            normalised = normalize_phone(phone)
        except InvalidPhoneError:
            return None
        doc = await self.collection.find_one({"phone": normalised})
        return User(**doc) if doc else None

    async def create_user(
        self, phone: str, hashed_password: str, full_name: Optional[str], role: Role = "user"
    ) -> User:
        """DuplicateKeyError phải được dịch ngay tại đây.

        Nó là lớp con của PyMongoError, mà handler PyMongoError trả 503 "máy của
        tiệm đang hỏng". Để lọt ra thì admin tạo trùng SĐT sẽ bị báo hỏng máy
        thay vì "số này đã có tài khoản". Kiểm tra trước khi ghi ở service
        không đủ: hai admin bấm cùng lúc vẫn qua được cửa đó.
        """
        try:
            return await self.create({
                "phone": normalize_phone(phone),
                "hashed_password": hashed_password,
                "full_name": full_name,
                "role": role,
                "is_active": True,
            })
        except DuplicateKeyError:
            raise PhoneTakenError()

    async def set_password(self, user_id: str, hashed_password: str) -> bool:
        """Tăng `token_version` cùng lúc để thu hồi mọi token đã phát trước đó."""
        result = await self.collection.update_one(
            {"_id": ObjectId(user_id)},
            {"$set": {"hashed_password": hashed_password}, "$inc": {"token_version": 1}},
        )
        return result.matched_count == 1

    async def list_all(self) -> List[User]:
        docs = await self.collection.find().sort("full_name", 1).to_list(length=500)
        return [User(**d) for d in docs]

    async def set_self_address(self, user_id: str, self_address: str) -> bool:
        """Ghi đè `self_address` — khách tự nói cách muốn được gọi lần nào
        cũng thắng giá trị cũ (tool `remember_address`, spec "Xưng hô": khách
        đổi cách xưng thì tool gọi lại). Không còn lọc "chỉ ghi khi `None`";
        `seed_self_address_from_names` tự giữ idempotence bằng cách bỏ qua
        khách đã có giá trị, không còn nhờ filter này.
        """
        if not ObjectId.is_valid(user_id):
            return False
        result = await self.collection.update_one(
            {"_id": ObjectId(user_id)},
            {"$set": {"self_address": self_address}},
        )
        return result.matched_count == 1

    async def set_staff_note(self, user_id: str, staff_note: Optional[str]) -> bool:
        """Admin ghi đè tự do — không có bẫy "chỉ ghi khi chưa có" ở đây."""
        from app.core.text import single_line
        from app.models.user import STAFF_NOTE_MAX

        if not ObjectId.is_valid(user_id):
            return False
        result = await self.collection.update_one(
            {"_id": ObjectId(user_id)},
            {"$set": {"staff_note": single_line(staff_note, STAFF_NOTE_MAX)}},
        )
        return result.matched_count == 1

    async def seed_self_address_from_names(self) -> int:
        """Migration lúc khởi động (Task 13): tiền tố admin gõ sẵn trong
        `full_name` ("Cô Lan", "Bác Bảy") là thông tin ĐÃ BIẾT, không phải bot
        đoán — seed `self_address` từ đó (đúng cách gọi của tiền tố, live try
        15/11 L3b) cho khách chưa có giá trị. Không tiền tố thì để trống.
        Idempotent: vòng lặp ở đây bỏ qua khách đã có `self_address` (dù do
        migration lần trước hay admin sửa tay), nên gọi lại nhiều lần (mỗi lần
        khởi động app) không có tác dụng phụ — `set_self_address` giờ ghi đè
        tự do, idempotence không còn tới từ nó.
        """
        from app.core.vi_format import derive_address

        seeded = 0
        for user in await self.list_all():
            if user.self_address is not None:
                continue
            address, _ = derive_address(user.full_name)
            if address != "anh chị":
                if await self.set_self_address(str(user.id), address):
                    seeded += 1
        return seeded

    async def migrate_honorific_self_address(self) -> int:
        """Migration MỘT lần lúc khởi động (live try 15/11, L3c): luật seed cũ
        đổi tiền tố cô / bà thành "chị", chú / ông thành "anh" (và bác thành
        trống). Khách có tiền tố tên thuộc {cô, chú, bác, ông, bà} mà
        `self_address` còn là "anh" / "chị" được đổi sang đúng tiền tố.

        Chỉ chạy một lần (dấu `_id` trong collection `migrations`): sau đó khách
        tự nói lại "gọi chị" (`remember_address`) thì lần khởi động sau không
        đổi ngược. Trả số khách đã đổi (0 khi đã chạy rồi)."""
        migrations = self.collection.database[_MIGRATIONS]
        if await migrations.find_one({"_id": _HONORIFIC_MIGRATION}) is not None:
            return 0
        changed = 0
        cursor = self.collection.find({"self_address": {"$in": ["anh", "chị"]}},
                                      {"full_name": 1, "self_address": 1})
        async for doc in cursor:
            parts = (doc.get("full_name") or "").split()
            prefix = parts[0].lower() if len(parts) >= 2 else ""
            if prefix in _HONORIFIC_PREFIXES:
                result = await self.collection.update_one(
                    {"_id": doc["_id"], "self_address": doc["self_address"]},
                    {"$set": {"self_address": prefix}},
                )
                changed += result.modified_count
        await migrations.update_one({"_id": _HONORIFIC_MIGRATION},
                                    {"$setOnInsert": {"changed": changed}}, upsert=True)
        return changed
