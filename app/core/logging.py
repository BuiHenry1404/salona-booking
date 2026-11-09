import logging
import sys
from typing import Any
import structlog
from app.core.config import settings


def _renderers() -> list[Any]:
    """Traceback KHÔNG kèm biến cục bộ: locals của một lượt chat chứa chữ khách
    (state, prompt, SalonContext) — log không bao giờ được mang chữ khách.
    Mặc định của structlog (Rich `show_locals=True` ở dev, `dict_tracebacks` ở
    prod) đều in locals, nên dev dùng `plain_traceback`, prod dùng
    `format_exc_info` (chuỗi traceback thường trong trường `exception`)."""
    if settings.env == "prod":
        return [structlog.processors.format_exc_info, structlog.processors.JSONRenderer()]
    return [structlog.dev.ConsoleRenderer(exception_formatter=structlog.dev.plain_traceback)]


def configure_logging() -> None:
    """Configure structured logging."""
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.StackInfoRenderer(),
            structlog.dev.set_exc_info,
            *_renderers(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(
            getattr(logging, settings.log_level.upper())
        ),
        logger_factory=structlog.WriteLoggerFactory(file=sys.stdout),
        cache_logger_on_first_use=True,
    )


def get_logger(name: str) -> Any:
    """Get a structured logger instance."""
    return structlog.get_logger(name) 