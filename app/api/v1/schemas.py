from datetime import datetime
from typing import List, Literal, Optional

from pydantic import BaseModel, ConfigDict, EmailStr, Field


class LoginRequest(BaseModel):
    phone: str
    password: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    role: Literal["user", "admin"]


class CreateUserRequest(BaseModel):
    phone: str
    password: str = Field(..., min_length=4)
    full_name: Optional[str] = None
    role: Literal["user", "admin"] = "user"


class ResetPasswordRequest(BaseModel):
    phone: str
    new_password: str = Field(..., min_length=4)


class UserResponse(BaseModel):
    id: str
    phone: str
    full_name: Optional[str] = None
    role: Literal["user", "admin"]
    is_active: bool


class AppointmentCreateRequest(BaseModel):
    start_at: datetime
    note: Optional[str] = None


class AppointmentResponse(BaseModel):
    id: str
    start_at: datetime
    duration_minutes: int
    note: Optional[str] = None
    status: Literal["booked", "cancelled"]
    user_name: Optional[str] = None
    phone: Optional[str] = None


class ShopStatusResponse(BaseModel):
    is_busy: bool
    busy_until: Optional[datetime] = None
    minutes_left: Optional[int] = None


class SetBusyRequest(BaseModel):
    minutes: int = Field(..., ge=5, le=480)


class ShopHoursRequest(BaseModel):
    open_time: str = "08:00"
    close_time: str = "19:00"
    closed_days: List[int] = Field(default_factory=list)


class FreeSlotsResponse(BaseModel):
    slots: List[datetime]


# ---------------------------------------------------------------------------
# Legacy schemas — still used by pre-task-11 routers; retained so `import main`
# continues to work. Task 11 will remove these when it rewrites the routers.
# ---------------------------------------------------------------------------

class UserCreate(BaseModel):
    email: EmailStr
    username: str = Field(..., min_length=3, max_length=50)
    full_name: Optional[str] = None
    password: str = Field(..., min_length=6)


class Token(BaseModel):
    access_token: str
    token_type: str = "bearer"


class TokenData(BaseModel):
    username: Optional[str] = None


class ConversationCreate(BaseModel):
    title: str = Field(..., min_length=1, max_length=200)
    description: Optional[str] = Field(None, max_length=1000)
    metadata: dict = Field(default_factory=dict)


class ConversationUpdate(BaseModel):
    title: Optional[str] = Field(None, min_length=1, max_length=200)
    description: Optional[str] = Field(None, max_length=1000)
    is_active: Optional[bool] = None
    metadata: Optional[dict] = None


class ConversationResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    user_id: str
    title: str
    description: Optional[str] = None
    task_ids: List[str] = Field(default_factory=list)
    is_active: bool
    metadata: dict
    created_at: datetime
    updated_at: datetime


class ConversationList(BaseModel):
    conversations: List[ConversationResponse]
    total: int
    skip: int
    limit: int


class ChatMessageResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    role: Literal["user", "assistant", "system"]
    content: str
    metadata: dict
    timestamp: float


class TaskCreate(BaseModel):
    conversation_id: Optional[str] = None
    user_message: str = Field(..., min_length=1)
    category: Optional[str] = Field(None, max_length=50)
    tags: List[str] = Field(default_factory=list)
    priority: Literal["low", "medium", "high", "urgent"] = Field(default="medium")
    estimated_duration: Optional[int] = None
    metadata: dict = Field(default_factory=dict)


class TaskUpdate(BaseModel):
    status: Optional[Literal["pending", "in_progress", "completed", "failed"]] = None
    priority: Optional[Literal["low", "medium", "high", "urgent"]] = None
    category: Optional[str] = Field(None, max_length=50)
    tags: Optional[List[str]] = None
    completion_percentage: Optional[int] = Field(None, ge=0, le=100)
    estimated_duration: Optional[int] = None
    actual_duration: Optional[int] = None
    metadata: Optional[dict] = None


class TaskResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    conversation_id: str
    user_id: str
    user_message: str
    messages: List[ChatMessageResponse] = Field(default_factory=list)
    status: Literal["pending", "in_progress", "completed", "failed"]
    priority: Literal["low", "medium", "high", "urgent"]
    category: Optional[str] = None
    tags: List[str] = Field(default_factory=list)
    completion_percentage: int
    estimated_duration: Optional[int] = None
    actual_duration: Optional[int] = None
    metadata: dict
    created_at: datetime
    updated_at: datetime


class TaskList(BaseModel):
    tasks: List[TaskResponse]
    total: int
    skip: int
    limit: int


class AddMessageToTask(BaseModel):
    role: Literal["assistant", "system"]
    content: str = Field(..., min_length=1)
    metadata: dict = Field(default_factory=dict)


class ChatRequest(BaseModel):
    message: str = Field(..., min_length=1)
    conversation_id: Optional[str] = None
    metadata: dict = Field(default_factory=dict)


class ChatResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    task_id: str
    conversation_id: str
    user_message: ChatMessageResponse
    assistant_responses: List[ChatMessageResponse] = Field(default_factory=list)


class HealthResponse(BaseModel):
    status: str
    timestamp: datetime
    version: str = "1.0.0"
