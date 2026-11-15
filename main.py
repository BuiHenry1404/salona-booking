from contextlib import asynccontextmanager
import asyncio
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, ORJSONResponse
from fastapi.staticfiles import StaticFiles
from pymongo.errors import PyMongoError
import structlog

from app.core.config import settings
from app.core.errors import AppError
from app.core.logging import configure_logging
from app.api.v1.routers import api_router
from app.services.socketio_service import SocketIOService
from app.infrastructure.database import create_mongodb_connection

# Configure structured logging
configure_logging()
logger = structlog.get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan manager."""
    logger.info("Starting up application", app_name=settings.app_name, env=settings.env)
    
    # Initialize database connection
    try:
        mongo_db = await create_mongodb_connection(
            uri=str(settings.mongo_uri), 
            db_name=settings.mongo_db_name
        )
        
        # Store database instances in app state for dependency injection
        app.state.mongo_db = mongo_db
        app.state.mongo = mongo_db.get_client()
        app.state.db = mongo_db.get_database()
        app.state.settings = settings

        from app.infrastructure.database import ensure_indexes
        await ensure_indexes(app.state.db)

        # Migration một lần, idempotent (Task 13): tiền tố admin gõ sẵn trong
        # full_name ("Cô Lan") là thông tin ĐÃ BIẾT → seed self_address cho
        # khách chưa có giá trị, để address_phrase không phải đợi khách tự
        # xưng lại từ đầu. Không fail-hard: thiếu seed chỉ làm bot gọi "anh
        # chị" một lúc, không đáng sập cả app vì nó.
        try:
            from app.repositories.user import UserRepository
            users = UserRepository(app.state.db)
            seeded = await users.seed_self_address_from_names()
            logger.info("self_address_seed_done", seeded=seeded)
            # Một lần (live try 15/11): "chị" / "anh" cũ của khách tên "Cô …",
            # "Chú …"… thành đúng tiền tố. Chỉ log số khách, không chữ khách.
            migrated = await users.migrate_honorific_self_address()
            logger.info("self_address_honorific_migration_done", migrated=migrated)
        except Exception as e:
            logger.warning("self_address_seed_failed", error=str(e))

        # Checkpointer (lịch sử model theo thread) — cùng DB, cùng fail-hard:
        # thiếu nó thì mọi lượt chat đều hỏng, chạy tiếp chỉ giấu lỗi.
        from app.agents.checkpoint import make_checkpointer, set_checkpointer
        app.state.checkpointer = make_checkpointer()
        set_checkpointer(app.state.checkpointer)

        # Store bộ nhớ dài hạn (Task 14, spec §3.5) — cùng DB, cùng `MongoClient`
        # của checkpointer (`get_sync_client()`); hàm dựng của `MongoDBStore` tự
        # tạo index unique `(namespace_str, key)`, không có bước `setup()` riêng.
        # Cùng fail-hard với checkpointer: thiếu nó thì bộ nhớ dài hạn im lặng
        # mất, chạy tiếp chỉ giấu lỗi.
        from app.agents.salon.memory.store import make_store, set_store
        app.state.memory_store = make_store()
        set_store(app.state.memory_store)

        logger.info("Database initialized successfully", db_name=settings.mongo_db_name)

    except Exception as e:
        logger.error("Failed to initialize database", error=str(e))
        raise

    # Redis tuỳ chọn (Task 10, dedupe tin nhắn) — KHÔNG fail-hard như Mongo.
    # Không cấu hình REDIS_URL thì bỏ qua hẳn; cấu hình mà ping lỗi thì chỉ
    # warning, app chạy tiếp bằng Mongo fallback (MessageDedupe tự xử lý).
    from app.infrastructure.redis import ping_redis
    if settings.redis_url:
        if await ping_redis():
            logger.info("Redis connected", url=settings.redis_url)
        else:
            logger.warning("Redis unavailable at startup, falling back to Mongo dedupe")

    # Initialize Socket.IO service (cũng không fail-hard, cùng lý do)
    app.state.socketio_service = None
    try:
        app.state.socketio_service = SocketIOService(app.state.db)
        logger.info("Socket.IO service initialized")

        # Mount Socket.IO application.
        # Mounted tại "/socket.io" để không che các router HTTP (/api/v1, /docs, /).
        # Starlette strip prefix mount khiến scope['path'] thành "/" khi gọi ASGIApp.
        # Wrapping ASGIApp để khôi phục lại scope['path'] về "/socket.io..." giúp
        # python-socketio nhận diện đúng URI socketio và tránh lỗi 500 khi handshake.
        raw_sio_app = app.state.socketio_service.get_asgi_app()

        async def socketio_asgi_wrapper(scope, receive, send):
            if scope.get("type") in ("http", "websocket"):
                root_path = scope.get("root_path", "")
                path = scope.get("path", "")
                if root_path and not path.startswith(root_path):
                    scope = dict(scope, path=root_path + path)
            await raw_sio_app(scope, receive, send)

        app.mount("/socket.io", socketio_asgi_wrapper)
        logger.info("Socket.IO mounted at /socket.io with path prefix preservation")

    except Exception as e:
        logger.warning("Socket.IO unavailable, continuing without realtime", error=str(e))

    from app.services.notifications import notifications
    from app.services.socket_notifier import SocketNotifier

    notifications.clear()
    if app.state.socketio_service is not None:
        notifications.register(SocketNotifier(app.state.socketio_service, app.state.db))

    # Kênh Telegram của chỗ tỏa tin — chỉ đăng ký khi có token. Thiếu token thì
    # app chạy bình thường, không bot, không kênh Telegram rác. Phải đăng ký
    # TRƯỚC start_bot để bot đổi bận/rảnh có kênh để tỏa tin.
    from app.telegram.client import TelegramClient, is_configured as telegram_is_configured
    from app.telegram.notify import TelegramNotifier
    if telegram_is_configured():
        notifications.register(TelegramNotifier(TelegramClient(), app.state.db))
        logger.info("Telegram notifier registered")

    # Bot Telegram — long polling chạy như asyncio task trong lifespan, chỉ bật
    # khi có TELEGRAM_BOT_TOKEN. Thiếu token thì app chạy bình thường, không bot.
    # Phải đứng SAU khi đăng ký kênh tỏa tin để handler đổi bận/rảnh có thể phát
    # shop_status_changed qua cả Socket.IO lẫn Telegram.
    from app.telegram import start_bot
    app.state.telegram_task = start_bot(app.state.db)

    yield
    
    # Shutdown
    logger.info("Shutting down application")
    
    # Dừng bot Telegram trước khi đóng các kênh tỏa tin
    if getattr(app.state, 'telegram_task', None):
        app.state.telegram_task.cancel()
        try:
            await asyncio.wait_for(app.state.telegram_task, timeout=5)
        except (asyncio.CancelledError, Exception):
            pass
        logger.info("Telegram bot stopped")

    # Cleanup Socket.IO connections
    if getattr(app.state, 'socketio_service', None):
        await app.state.socketio_service.sio.shutdown()
        logger.info("Socket.IO connections closed")

    if getattr(app.state, 'memory_store', None) is not None:
        from app.agents.salon.memory.store import set_store
        set_store(None)
        app.state.memory_store = None

    if getattr(app.state, 'checkpointer', None) is not None:
        from app.agents.checkpoint import set_checkpointer, set_sync_client
        set_checkpointer(None)
        app.state.checkpointer.close()
        app.state.checkpointer = None
        # `checkpointer.close()` vừa đóng MongoClient dùng chung (checkpointer
        # VÀ store bộ nhớ dài hạn) — pymongo không cho dùng lại một client đã
        # đóng, nên lần khởi động kế tiếp của lifespan (test chạy nhiều app
        # trong cùng tiến trình) phải tạo client mới, không phải client cũ.
        set_sync_client(None)

    # Shutdown database connection
    if hasattr(app.state, 'mongo_db'):
        await app.state.mongo_db.disconnect()
        logger.info("Database connections closed")


# Create FastAPI application
app = FastAPI(
    title="Chatbot API",
    description="A FastAPI template for managing chatbot tasks and conversations with MongoDB and real-time Socket.IO chat",
    version="1.0.0",
    lifespan=lifespan,
    default_response_class=ORJSONResponse,
    docs_url="/docs" if settings.env != "prod" else None,
    redoc_url="/redoc" if settings.env != "prod" else None,
)

# Add CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.allowed_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.exception_handler(AppError)
async def app_error_handler(request: Request, exc: AppError):
    """Người dùng không bao giờ thấy lỗi kỹ thuật — mọi lỗi thành một câu tiếng Việt."""
    return JSONResponse(status_code=exc.status_code, content={"detail": exc.message})


@app.exception_handler(PyMongoError)
async def mongo_error_handler(request: Request, exc: PyMongoError):
    """Mongo chết → 503 kèm SĐT tiệm. Traceback chỉ vào log, không ra màn hình.

    `PyMongoError` là lớp cha của `ServerSelectionTimeoutError`, `AutoReconnect`
    và `NetworkTimeout` — bắt lớp cha để không phải liệt kê thiếu.

    Cạm bẫy: `DuplicateKeyError` cũng là lớp con của `PyMongoError`. Repository
    đã bắt nó và đổi thành `SlotTakenError`, nên trùng giờ vẫn ra 409. Chỗ ghi
    Mongo mới nào quên bắt sẽ khiến khách thấy "máy của tiệm đang trục trặc" trong
    khi thật ra chỉ là trùng giờ.
    """
    logger.exception("mongo_unavailable", path=request.url.path)
    # Xưng "em", gọi khách "anh chị" (không đoán tuổi/giới). Thiếu SĐT trong .env
    # thì không ghép chữ "tiệm" làm số điện thoại.
    if settings.shop_phone:
        detail = (
            "Dạ máy của tiệm đang trục trặc ạ. "
            f"Anh chị gọi giúp em số {settings.shop_phone} nhé."
        )
    else:
        detail = "Dạ máy của tiệm đang trục trặc ạ. Anh chị gọi điện cho tiệm giúp em nhé."
    return JSONResponse(
        status_code=503,
        content={
            "detail": detail,
            "shop_phone": settings.shop_phone,
        },
    )


# Request logging middleware
@app.middleware("http")
async def log_requests(request: Request, call_next):
    """Log all requests."""
    logger.info(
        "Request received",
        method=request.method,
        url=str(request.url),
        client_ip=request.client.host if request.client else None
    )
    
    response = await call_next(request)
    
    logger.info(
        "Request completed",
        method=request.method,
        url=str(request.url),
        status_code=response.status_code
    )
    
    return response


# Include routers
app.include_router(api_router, prefix="/api/v1")

# Mount static files for the test client
app.mount("/static", StaticFiles(directory="static"), name="static")


@app.get("/", tags=["root"])
async def root():
    """Root endpoint."""
    return {
        "message": "Welcome to Chatbot API with Real-time Chat",
        "version": "1.0.0",
        "docs": "/docs",
        "health": "/api/v1/health",
        "socketio": "/socket.io",
        "test_client": "/static/socketio_test.html"
    }


# Add Socket.IO info endpoint
@app.get("/socket-info", tags=["socketio"])
async def socket_info():
    """Get Socket.IO connection information."""
    return {
        "socket_url": "/socket.io",
        "test_client": "/static/socketio_test.html",
        "events": {
            "client_to_server": {
                "chat": "Send a chat message",
                "join_conversation": "Join a specific conversation room",
                "leave_conversation": "Leave a conversation room"
            },
            "server_to_client": {
                "connected": "Connection established successfully",
                "conversation": "Chat response received",
                "error": "Error occurred",
                "joined_conversation": "Successfully joined conversation",
                "left_conversation": "Successfully left conversation"
            }
        },
        "authentication": "Include JWT token in auth object during connection",
        "example_connection": {
            "auth": {"token": "your_jwt_token_here"},
            "events": {
                "chat": {"message": "Hello!", "conversation_id": "optional"},
                "join_conversation": {"conversation_id": "conversation_id_here"}
            }
        }
    }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=8000,
        reload=settings.debug,
        log_level=settings.log_level.lower()
    ) 