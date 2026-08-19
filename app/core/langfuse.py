from typing import Any, Dict, List, Optional

from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger(__name__)

_client_ready = False


def is_enabled() -> bool:
    return bool(settings.langfuse_public_key and settings.langfuse_secret_key)


def _load_handler_class():
    """v3: `langfuse.langchain`. v2 dùng `langfuse.callback` và ĐÃ BỎ."""
    try:
        from langfuse.langchain import CallbackHandler

        return CallbackHandler
    except Exception as exc:
        logger.warning("langfuse_import_failed", extra={"error": str(exc)})
        return None


def _init_client() -> bool:
    """Khởi tạo client MỘT lần. Credential nằm ở đây, không ở handler.

    Đây là điểm khác lớn nhất giữa v2 và v3: v2 truyền public_key/secret_key
    thẳng vào CallbackHandler, v3 thì handler không nhận tham số nào — truyền
    vào sẽ ném TypeError, và vì mọi ngoại lệ đều bị nuốt nên hậu quả là trace
    tắt hẳn mà log chỉ có một dòng warning.
    """
    global _client_ready
    if _client_ready:
        return True
    if not is_enabled():
        return False
    try:
        from langfuse import Langfuse

        Langfuse(
            public_key=settings.langfuse_public_key.get_secret_value(),
            secret_key=settings.langfuse_secret_key.get_secret_value(),
            host=settings.langfuse_host or "https://cloud.langfuse.com",
        )
        _client_ready = True
        return True
    except Exception as exc:
        logger.warning("langfuse_init_failed", extra={"error": str(exc)})
        return False


def get_callbacks() -> List[Any]:
    """Callback cho LangChain/LangGraph. Trả về [] nếu chưa cấu hình.

    Langfuse lỗi không bao giờ được làm hỏng một lượt chat — nuốt mọi ngoại lệ.
    """
    if not _init_client():
        return []

    handler_class = _load_handler_class()
    if handler_class is None:
        return []

    try:
        return [handler_class()]
    except Exception as exc:
        logger.warning("langfuse_handler_failed", extra={"error": str(exc)})
        return []


def get_trace_metadata(
    user_id: Optional[str] = None, conversation_id: Optional[str] = None
) -> Dict[str, str]:
    """Metadata gắn vào `config` của lượt chạy.

    Trong v3, danh tính đi qua metadata chứ không qua handler. Tên khóa phải
    đúng nguyên văn — Langfuse chỉ đọc tiền tố `langfuse_`, sai tên thì trace
    vẫn lên nhưng mất đường lần ngược từ một lịch sai về đúng cuộc hội thoại.
    """
    metadata: Dict[str, str] = {}
    if user_id:
        metadata["langfuse_user_id"] = user_id
    if conversation_id:
        metadata["langfuse_session_id"] = conversation_id
    return metadata
