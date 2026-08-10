import socketio
from typing import Dict, Optional
from motor.motor_asyncio import AsyncIOMotorDatabase
from app.core.logging import get_logger
from app.core.security import verify_token
from app.services.auth import AuthService
from app.services.task import TaskService
from app.models.user import CurrentUser

logger = get_logger(__name__)


class SocketIOService:
    """Service for handling Socket.IO real-time chat functionality."""
    
    def __init__(self, db: AsyncIOMotorDatabase, llm_manager=None):
        self.db = db
        self.llm_manager = llm_manager
        self.auth_service = AuthService(db)
        self.task_service = TaskService(db)
        self.sio = socketio.AsyncServer(
            async_mode='asgi',
            cors_allowed_origins="*",  # Configure based on your needs
            logger=False,  # Disable socket.io logging to avoid conflicts
            engineio_logger=False
        )
        self.user_sessions: Dict[str, str] = {}  # sid -> user_id mapping
        self.user_rooms: Dict[str, str] = {}  # user_id -> room mapping
        
        # Register event handlers
        self._register_handlers()
    
    def _register_handlers(self):
        """Register Socket.IO event handlers."""
        
        @self.sio.event
        async def connect(sid, environ, auth):
            """Handle client connection with authentication."""
            try:
                # Extract token from auth data
                if not auth or 'token' not in auth:
                    logger.warning(f"Connection attempt without token: {sid}")
                    await self.sio.disconnect(sid)
                    return False
                
                token = auth['token']
                
                # Verify token and get user
                try:
                    payload = verify_token(token)
                    user_id = payload.get("sub")
                    if not user_id:
                        raise ValueError("Invalid token payload")
                    
                    user = await self.auth_service.get_user_by_id(user_id)
                    if not user or not user.is_active:
                        raise ValueError("User not found or inactive")
                    
                    # Store user session
                    self.user_sessions[sid] = user_id
                    room_name = f"user_{user_id}"
                    self.user_rooms[user_id] = room_name
                    
                    # Join user-specific room
                    await self.sio.enter_room(sid, room_name)
                    
                    logger.info(f"User {user.username} connected with session {sid}")
                    
                    # Send connection success
                    await self.sio.emit('connected', {
                        'message': 'Successfully connected',
                        'user': {
                            'id': str(user.id),
                            'username': user.username,
                            'email': user.email
                        }
                    }, room=sid)
                    
                    return True
                    
                except Exception as e:
                    logger.error(f"Authentication failed for {sid}: {e}")
                    await self.sio.disconnect(sid)
                    return False
                    
            except Exception as e:
                logger.error(f"Connection error for {sid}: {e}")
                await self.sio.disconnect(sid)
                return False
        
        @self.sio.event
        async def disconnect(sid):
            """Handle client disconnection."""
            user_id = self.user_sessions.pop(sid, None)
            if user_id:
                room_name = self.user_rooms.pop(user_id, None)
                if room_name:
                    await self.sio.leave_room(sid, room_name)
                logger.info(f"User {user_id} disconnected from session {sid}")
        
        @self.sio.event
        async def chat(sid, data):
            """Handle chat messages from clients."""
            try:
                user_id = self.user_sessions.get(sid)
                if not user_id:
                    await self.sio.emit('error', {
                        'message': 'Not authenticated'
                    }, room=sid)
                    return
                
                # Validate message data
                if not isinstance(data, dict) or 'message' not in data:
                    await self.sio.emit('error', {
                        'message': 'Invalid message format'
                    }, room=sid)
                    return
                
                message = data.get('message', '').strip()
                if not message:
                    await self.sio.emit('error', {
                        'message': 'Message cannot be empty'
                    }, room=sid)
                    return
                conversation_id = data.get('conversation_id')
                metadata = data.get('metadata', {})
                
                # Add socket session info to metadata
                metadata.update({
                    'socket_session': sid,
                    'realtime': True
                })
                
                logger.info(f"Processing chat message from user {user_id}: {message[:50]}...")

                # TODO(Plan 2 task 10): triển khai xử lý tin nhắn thật.
                # Dịch vụ chat AI chưa sẵn sàng — thông báo cho client biết.
                await self.sio.emit('conversation', {
                    'message': 'Chưa sẵn sàng'
                }, room=sid)
                
            except Exception as e:
                logger.error(f"Chat error for session {sid}: {e}")
                await self.sio.emit('error', {
                    'message': 'Failed to process message',
                    'error': str(e)
                }, room=sid)
        
        @self.sio.event
        async def join_conversation(sid, data):
            """Allow users to join specific conversation rooms."""
            try:
                user_id = self.user_sessions.get(sid)
                if not user_id:
                    await self.sio.emit('error', {
                        'message': 'Not authenticated'
                    }, room=sid)
                    return
                
                conversation_id = data.get('conversation_id')
                if not conversation_id:
                    await self.sio.emit('error', {
                        'message': 'Conversation ID required'
                    }, room=sid)
                    return
                
                # TODO(Plan 2 task 10): xác thực quyền truy cập conversation khi có ConversationService mới.
                # Join conversation room
                room_name = f"conversation_{conversation_id}"
                await self.sio.enter_room(sid, room_name)
                
                await self.sio.emit('joined_conversation', {
                    'conversation_id': conversation_id,
                    'message': 'Joined conversation successfully'
                }, room=sid)
                
                logger.info(f"User {user_id} joined conversation {conversation_id}")
                
            except Exception as e:
                logger.error(f"Join conversation error for session {sid}: {e}")
                await self.sio.emit('error', {
                    'message': 'Failed to join conversation',
                    'error': str(e)
                }, room=sid)
        
        @self.sio.event
        async def leave_conversation(sid, data):
            """Allow users to leave conversation rooms."""
            try:
                conversation_id = data.get('conversation_id')
                if not conversation_id:
                    return
                
                room_name = f"conversation_{conversation_id}"
                await self.sio.leave_room(sid, room_name)
                
                await self.sio.emit('left_conversation', {
                    'conversation_id': conversation_id,
                    'message': 'Left conversation successfully'
                }, room=sid)
                
            except Exception as e:
                logger.error(f"Leave conversation error for session {sid}: {e}")
        
        @self.sio.event
        async def soulcare_chat(sid, data):
            """Handle soulcare team chat messages."""
            # TODO(Plan 2 task 10): triển khai agent soulcare thật.
            # Agent AI chưa sẵn sàng — thông báo cho client biết.
            await self.sio.emit('conversation', {
                'message': 'Chưa sẵn sàng'
            }, room=sid)
    
    async def broadcast_to_conversation(self, conversation_id: str, event: str, data: dict):
        """Broadcast message to all users in a conversation."""
        room_name = f"conversation_{conversation_id}"
        await self.sio.emit(event, data, room=room_name)
    
    async def send_to_user(self, user_id: str, event: str, data: dict):
        """Send message to a specific user."""
        room_name = f"user_{user_id}"
        await self.sio.emit(event, data, room=room_name)
    
    def get_asgi_app(self):
        """Get the Socket.IO ASGI application."""
        return socketio.ASGIApp(self.sio)
    
    async def get_connected_users(self) -> Dict[str, str]:
        """Get currently connected users."""
        return self.user_sessions.copy()
    
    async def disconnect_user(self, user_id: str):
        """Disconnect a specific user."""
        sessions_to_disconnect = [
            sid for sid, uid in self.user_sessions.items() 
            if uid == user_id
        ]
        
        for sid in sessions_to_disconnect:
            await self.sio.disconnect(sid) 