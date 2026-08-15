from datetime import date

from fastapi import APIRouter, Depends
from motor.motor_asyncio import AsyncIOMotorDatabase

from app.api.deps import get_current_user, get_db
from app.api.v1.schemas import (ChatMessageResponse, DayListResponse,
                                DayMessagesResponse, DaySummaryResponse)
from app.core.clock import now_utc, to_local
from app.models.user import User
from app.services.conversation import ConversationService

router = APIRouter(prefix="/conversations", tags=["conversations"])


@router.get("/days", response_model=DayListResponse)
async def list_days(
    user: User = Depends(get_current_user),
    db: AsyncIOMotorDatabase = Depends(get_db),
):
    """Những ngày khách từng trò chuyện, mới nhất trước."""
    days = await ConversationService(db).list_days(str(user.id))
    return DayListResponse(
        days=[
            DaySummaryResponse(day=d.day, message_count=d.message_count, preview=d.preview)
            for d in days
        ]
    )


@router.get("/days/{day}", response_model=DayMessagesResponse)
async def messages_on_day(
    day: date,
    user: User = Depends(get_current_user),
    db: AsyncIOMotorDatabase = Depends(get_db),
):
    """Toàn bộ tin của một ngày. Ngày cũ chỉ để đọc lại — `is_today` cho frontend
    biết có nên mở ô nhập hay không; chat mới luôn nối vào ngày hôm nay."""
    messages = await ConversationService(db).messages_on(str(user.id), day)
    return DayMessagesResponse(
        day=day,
        is_today=day == to_local(now_utc()).date(),
        messages=[
            ChatMessageResponse(role=m.role, content=m.content, created_at=m.created_at)
            for m in messages
        ],
    )
