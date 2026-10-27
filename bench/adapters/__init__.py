"""Registry adapter chat của benchmark."""

from __future__ import annotations

from bench.adapters.a import AdapterA
from bench.adapters.base import ChatAdapter, Event, TurnRecord

ADAPTERS: dict[str, type[ChatAdapter]] = {
    "a": AdapterA,
}

__all__ = ["ChatAdapter", "Event", "TurnRecord", "ADAPTERS", "AdapterA"]
