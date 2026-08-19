from motor.motor_asyncio import AsyncIOMotorDatabase

from app.models.conversation import Conversation
from app.repositories.base import BaseRepository


class ConversationRepository(BaseRepository[Conversation]):
    def __init__(self, db: AsyncIOMotorDatabase):
        super().__init__(db, Conversation, "conversations")
