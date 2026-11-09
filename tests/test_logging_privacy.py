"""Traceback trong log không kèm biến cục bộ — locals của một lượt chat chứa
chữ khách (review Task 15)."""
import io

import pytest
import structlog

from app.core import logging as app_logging
from app.core.config import settings

SECRET = "chi-lan-noi-bi-mat-123"


@pytest.mark.parametrize("env", ["dev", "prod"])
def test_exception_logs_have_a_traceback_but_no_locals(monkeypatch, env):
    previous = structlog.get_config()
    out = io.StringIO()
    monkeypatch.setattr(settings, "env", env)
    monkeypatch.setattr(app_logging.sys, "stdout", out)
    try:
        app_logging.configure_logging()
        logger = structlog.get_logger("privacy-test")
        customer_text = SECRET  # noqa: F841 — biến cục bộ mà traceback không được in
        try:
            raise ValueError("boom")
        except ValueError:
            logger.error("turn_failed", exc_info=True)
    finally:
        structlog.configure(**previous)

    text = out.getvalue()
    assert "ValueError: boom" in text and "Traceback" in text
    assert SECRET not in text
