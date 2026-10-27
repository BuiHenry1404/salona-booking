"""Đọc trực tiếp collection `appointments` từ Mongo (hộp đen — không qua app)
và so sánh hai lần chụp (trước/sau một lượt chạy kịch bản) để xem lịch nào
mới được tạo, bị huỷ, hay thay đổi (ví dụ dời giờ).

Hộp đen: module này KHÔNG `import app`. Dùng pymongo đồng bộ (sync) vì chỉ
chạy ở biên kịch bản (trước/sau), không cần async.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from pymongo import MongoClient

# Giờ Việt Nam cố định — khớp với bench/scenario.py (không phụ thuộc tzdata hệ thống).
VN_TZ = timezone(timedelta(hours=7))


@dataclass(frozen=True)
class ApptRow:
    id: str
    user_id: str
    start_at: datetime
    status: str
    note: str | None = None


@dataclass(frozen=True)
class Changes:
    created: list[ApptRow]
    cancelled: list[ApptRow]
    # (trước, sau) — mọi thay đổi khác không phải "mới tạo" hay "huỷ" (vd: dời giờ).
    other: list[tuple[ApptRow, ApptRow]]


def snapshot(mongo_uri: str, db_name: str) -> list[ApptRow]:
    """Đọc toàn bộ collection `appointments`, trả về danh sách `ApptRow` với
    `start_at` đã chuyển sang giờ VN (+07:00)."""
    client: MongoClient = MongoClient(mongo_uri, tz_aware=True)
    try:
        db = client[db_name]
        rows: list[ApptRow] = []
        for doc in db["appointments"].find({}):
            start_at = doc["start_at"]
            if start_at.tzinfo is None:
                start_at = start_at.replace(tzinfo=timezone.utc)
            rows.append(
                ApptRow(
                    id=str(doc["_id"]),
                    user_id=doc["user_id"],
                    start_at=start_at.astimezone(VN_TZ),
                    status=doc["status"],
                    note=doc.get("note"),
                )
            )
        return rows
    finally:
        client.close()


def diff(before: list[ApptRow], after: list[ApptRow]) -> Changes:
    """So sánh hai lần chụp theo `id`.

    - `created`: id chỉ có ở `after`.
    - `cancelled`: id có ở cả hai, trạng thái đổi từ "booked" sang "cancelled"
      (đúng với ràng buộc cứng: huỷ = lật `status`, không bao giờ xoá document).
    - `other`: id có ở cả hai, có thay đổi nhưng không phải huỷ (ví dụ dời giờ).
    """
    before_by_id = {row.id: row for row in before}
    after_by_id = {row.id: row for row in after}

    created = [row for rid, row in after_by_id.items() if rid not in before_by_id]
    cancelled: list[ApptRow] = []
    other: list[tuple[ApptRow, ApptRow]] = []

    for rid, before_row in before_by_id.items():
        after_row = after_by_id.get(rid)
        if after_row is None or after_row == before_row:
            continue
        if before_row.status == "booked" and after_row.status == "cancelled":
            cancelled.append(after_row)
        else:
            other.append((before_row, after_row))

    return Changes(created=created, cancelled=cancelled, other=other)
