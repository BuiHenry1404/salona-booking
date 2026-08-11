from typing import Optional
import structlog
from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorDatabase
from app.core.config import settings

logger = structlog.get_logger(__name__)


class MongoDatabase:
    """Simple MongoDB connection manager."""
    
    def __init__(self, uri: str, db_name: str):
        self.uri = uri
        self.db_name = db_name
        self.client: Optional[AsyncIOMotorClient] = None
        self.database: Optional[AsyncIOMotorDatabase] = None
    
    async def connect(self) -> None:
        """Establish MongoDB connection."""
        try:
            # tz_aware=True: BSON chỉ lưu UTC không kèm offset, mặc định Motor
            # trả datetime naive. Lịch đọc lại từ DB mà mất tzinfo thì trình
            # duyệt hiểu là giờ địa phương và hiện lệch 7 tiếng.
            self.client = AsyncIOMotorClient(self.uri, tz_aware=True)
            self.database = self.client[self.db_name]
            logger.info("MongoDB client created", db_name=self.db_name)
        except Exception as e:
            logger.error("Failed to create MongoDB client", error=str(e))
            raise
    
    async def disconnect(self) -> None:
        """Close MongoDB connection."""
        if self.client is not None:
            self.client.close()
            logger.info("MongoDB connection closed")
    
    async def ping(self) -> bool:
        """Test MongoDB connection."""
        try:
            if self.client is None:
                return False
            await self.client.admin.command('ping')
            logger.info("MongoDB ping successful", db_name=self.db_name)
            return True
        except Exception as e:
            logger.error("MongoDB ping failed", error=str(e))
            return False
    
    def get_database(self) -> AsyncIOMotorDatabase:
        """Get MongoDB database instance."""
        if self.database is None:
            raise RuntimeError("Database not connected. Call connect() first.")
        return self.database
    
    def get_client(self) -> AsyncIOMotorClient:
        """Get MongoDB client instance."""
        if self.client is None:
            raise RuntimeError("Database not connected. Call connect() first.")
        return self.client
    

async def ensure_indexes(db: AsyncIOMotorDatabase) -> None:
    """Tạo mọi index. Gọi lúc khởi động app và trong fixture test.

    Index trên slot_keys BẮT BUỘC là partial lọc status="booked".
    Không được dùng sparse: MongoDB đánh index mảng rỗng thành undefined, nên
    lịch bị hủy thứ hai sẽ ném E11000 dup key: { : undefined }.
    """
    await db["users"].create_index("phone", unique=True)

    await db["appointments"].create_index(
        "slot_keys",
        unique=True,
        partialFilterExpression={"status": "booked"},
        name="uniq_active_slot_keys",
    )
    await db["appointments"].create_index("start_at")
    await db["appointments"].create_index([("user_id", 1), ("start_at", 1)])

    await db["rate_limits"].create_index("expires_at", expireAfterSeconds=0)
    await db["rate_limits"].create_index([("key", 1), ("created_at", 1)])


async def create_mongodb_connection(uri: str = None, db_name: str = None) -> MongoDatabase:
    """Create and initialize a MongoDB connection."""
    uri = uri or str(settings.mongo_uri)
    db_name = db_name or settings.mongo_db_name
    
    mongo_db = MongoDatabase(uri, db_name)
    await mongo_db.connect()
    
    # Test connection
    if not await mongo_db.ping():
        raise RuntimeError("Failed to establish MongoDB connection")

    return mongo_db

