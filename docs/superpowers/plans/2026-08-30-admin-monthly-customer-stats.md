# Admin Monthly Customer Stats — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Cung cấp `GET /api/v1/admin/stats/customers-by-month` trả 12 tháng gần nhất kèm số khách duy nhất có lịch `booked`, và nối Admin Dashboard vào API thật thay cho mock.

**Architecture:** Đi theo đúng tầng sẵn có của repo — helper thời gian thuần ở `app/core/clock.py`, aggregation Mongo ở `AppointmentRepository`, nghiệp vụ (điền tháng 0, sắp xếp, cắt đúng 12) ở service mới `app/services/stats.py`, và một router admin mới `app/api/v1/routers/admin.py` dùng lại dependency `require_admin` có sẵn. Frontend thêm hook `useMonthlyCustomerStats` gọi `api.get`, component `MonthlyCustomerStats` giữ nguyên (chỉ đổi nguồn dữ liệu).

**Tech Stack:** FastAPI + Motor (MongoDB 7) + Pydantic v2, pytest/pytest-asyncio (cần Mongo thật ở `localhost:27017`); React 18 + TypeScript + Vite + Vitest + Testing Library.

**Spec:** `docs/ADMIN_MONTHLY_CUSTOMER_STATS_API.md`

## Global Constraints

- Endpoint: `GET /api/v1/admin/stats/customers-by-month`. Không query param, không body trong v1.
- Auth: bắt buộc Bearer access token; chỉ `role = "admin"`. Customer → `403`, thiếu/hết hạn token → `401`.
- Trả **đúng 12** entries, thứ tự chronological (cũ → mới), tháng hiện tại là item **cuối**.
- `month` format `YYYY-MM`; `customer_count` là integer `>= 0`.
- Tháng không có lịch hợp lệ vẫn phải xuất hiện với `customer_count: 0`. Không bao giờ trả mảng rỗng.
- Chỉ tính `status = "booked"`. `status = "cancelled"` KHÔNG tính.
- Mỗi `user_id` chỉ tính 1 lần trong một tháng (COUNT DISTINCT `user_id`).
- Lịch tương lai trong tháng hiện tại VẪN tính (metric là "khách có lịch", không phải "khách đã đến").
- Ranh giới tháng theo timezone `Asia/Ho_Chi_Minh` (Mongo lưu `start_at` là UTC — phải quy đổi khi group).
- Response shape: `{ "months": [ { "month": "2025-09", "customer_count": 14 }, ... ] }`.

## Môi trường trước khi bắt đầu

Backend tests cần MongoDB thật (xem `tests/conftest.py`: `mongodb://localhost:27017`, db `chatbot_test_db`). Lúc lập plan, cổng 27017 đang **đóng**. Bật trước:

```bash
cd /home/henryb1/Desktop/HenryB1/data/salona-booking
docker compose up -d mongo
nc -z localhost 27017 && echo "mongo UP"
```

Python: `source .venv/bin/activate`. Frontend: chạy trong `frontend/` bằng `npm test`.

## File Structure

**Backend**

| File | Trách nhiệm |
|---|---|
| `app/core/clock.py` (modify) | Thêm helper thuần về tháng theo giờ VN: `month_key`, `recent_month_keys`, `month_window_bounds`. Không chạm DB. |
| `app/repositories/appointment.py` (modify) | Thêm `distinct_customers_by_month(start, end)` — aggregation pipeline, trả `dict[str, int]` thô, KHÔNG điền tháng thiếu. |
| `app/services/stats.py` (create) | `StatsService.customers_by_month(months=12)` — gọi repo, điền 0, sắp xếp, đảm bảo đúng 12 tháng. |
| `app/api/v1/schemas.py` (modify) | `MonthlyCustomerStat`, `MonthlyCustomerStatsResponse`. |
| `app/api/v1/routers/admin.py` (create) | Router `prefix="/admin"`, route `/stats/customers-by-month`, `Depends(require_admin)`. |
| `app/api/v1/routers/__init__.py` (modify) | `include_router(admin.router)`. |
| `tests/test_month_window.py` (create) | Test helper clock (thuần, không cần Mongo). |
| `tests/test_appointment_repository.py` (modify) | Test aggregation distinct/cancelled/timezone. |
| `tests/test_stats_service.py` (create) | Test điền 0, đúng 12 tháng, thứ tự. |
| `tests/test_admin_stats_api.py` (create) | Test 401/403/200 + shape response. |

**Frontend**

| File | Trách nhiệm |
|---|---|
| `frontend/src/lib/api.ts` (modify) | Nơi chính thức của type `MonthlyCustomerStat` / `MonthlyCustomerStatsResponse` (cạnh `AppointmentResponse`). |
| `frontend/src/hooks/useMonthlyCustomerStats.ts` (create) | Nạp một lần bằng REST, trả `{ data, loading, error }`. |
| `frontend/src/hooks/useMonthlyCustomerStats.test.ts` (create) | Test hook với `lib/api` bị mock. |
| `frontend/src/components/MonthlyCustomerStats.tsx` (modify) | Đổi import type sang `../lib/api`. Logic render giữ nguyên. |
| `frontend/src/components/MonthlyCustomerStats.test.tsx` (modify) | Đổi import type sang `../lib/api`. |
| `frontend/src/screens/AdminDashboard.tsx` (modify) | Dùng hook thay mock; xử lý loading/error. |
| `frontend/src/screens/AdminDashboard.test.tsx` (modify) | Mock hook mới; cập nhật test 13–16. |
| `frontend/src/mocks/monthlyCustomerStats.ts` (delete) | Hết vai trò sau khi nối API thật. |

---

### Task 1: Helper tháng theo giờ Việt Nam

Việc tính "12 tháng gần nhất theo giờ VN" là logic thuần, tách khỏi DB để test nhanh và để service không tự chế lại.

**Files:**
- Modify: `app/core/clock.py` (thêm vào cuối file)
- Test: `tests/test_month_window.py` (create)

**Interfaces:**
- Consumes: `TZ` (đã có trong `app/core/clock.py`).
- Produces:
  - `month_key(dt: datetime) -> str` — `"YYYY-MM"` theo giờ VN.
  - `recent_month_keys(months: int, now: datetime) -> list[str]` — đúng `months` phần tử, chronological, phần tử cuối là tháng của `now` theo giờ VN.
  - `month_window_bounds(months: int, now: datetime) -> tuple[datetime, datetime]` — `(start_utc, end_utc)`, nửa mở `[start, end)`, `start` là 00:00 giờ VN ngày 1 của tháng đầu cửa sổ, `end` là 00:00 giờ VN ngày 1 của tháng **kế sau** tháng hiện tại.

- [ ] **Step 1: Viết test thất bại**

Tạo `tests/test_month_window.py`:

```python
from datetime import datetime, timezone

from app.core.clock import TZ, month_key, month_window_bounds, recent_month_keys


def test_month_key_uses_vietnam_timezone():
    # 2026-08-31T18:00Z = 2026-09-01 01:00 giờ VN -> đã sang tháng 9.
    assert month_key(datetime(2026, 8, 31, 18, 0, tzinfo=timezone.utc)) == "2026-09"


def test_month_key_before_vn_midnight_stays_in_previous_month():
    # 2026-08-31T16:00Z = 2026-08-31 23:00 giờ VN -> vẫn tháng 8.
    assert month_key(datetime(2026, 8, 31, 16, 0, tzinfo=timezone.utc)) == "2026-08"


def test_recent_month_keys_returns_twelve_chronological_months():
    now = datetime(2026, 8, 15, 3, 0, tzinfo=timezone.utc)
    keys = recent_month_keys(12, now)
    assert len(keys) == 12
    assert keys == sorted(keys)
    assert keys[0] == "2025-09"
    assert keys[-1] == "2026-08"


def test_recent_month_keys_crosses_year_boundary():
    now = datetime(2026, 1, 10, 3, 0, tzinfo=timezone.utc)
    assert recent_month_keys(12, now)[0] == "2025-02"


def test_month_window_bounds_starts_at_vn_midnight_of_first_month():
    now = datetime(2026, 8, 15, 3, 0, tzinfo=timezone.utc)
    start, end = month_window_bounds(12, now)
    assert start.astimezone(TZ) == datetime(2025, 9, 1, 0, 0, tzinfo=TZ)
    assert end.astimezone(TZ) == datetime(2026, 9, 1, 0, 0, tzinfo=TZ)


def test_month_window_bounds_end_is_exclusive_and_covers_whole_current_month():
    now = datetime(2026, 8, 15, 3, 0, tzinfo=timezone.utc)
    start, end = month_window_bounds(12, now)
    last_moment = datetime(2026, 8, 31, 23, 59, tzinfo=TZ).astimezone(timezone.utc)
    assert start <= last_moment < end
```

- [ ] **Step 2: Chạy test cho chắc là fail**

Run: `pytest tests/test_month_window.py -v`
Expected: FAIL — `ImportError: cannot import name 'month_key' from 'app.core.clock'`

- [ ] **Step 3: Implement tối thiểu**

Thêm vào cuối `app/core/clock.py`:

```python
def _shift_month(year: int, month: int, delta: int) -> tuple[int, int]:
    """Dịch (year, month) đi `delta` tháng. Đếm bằng chỉ số tháng tuyệt đối để
    không phải xử lý riêng chuyện qua năm."""
    index = year * 12 + (month - 1) + delta
    return index // 12, index % 12 + 1


def month_key(dt: datetime) -> str:
    """Nhãn 'YYYY-MM' của một thời điểm, tính theo giờ Việt Nam.

    Mongo lưu start_at là UTC. 23:00 ngày 31/8 giờ VN là 16:00 UTC cùng ngày,
    còn 01:00 ngày 1/9 giờ VN đã là 18:00 UTC ngày 31/8 — quy đổi trước khi
    cắt tháng, nếu không lịch đầu/cuối tháng sẽ bị đếm nhầm sang tháng khác.
    """
    local = to_local(dt)
    return f"{local.year:04d}-{local.month:02d}"


def recent_month_keys(months: int, now: datetime) -> list[str]:
    """`months` nhãn tháng gần nhất theo giờ VN, cũ -> mới, gồm cả tháng hiện tại."""
    local = to_local(now)
    keys = []
    for delta in range(-(months - 1), 1):
        year, month = _shift_month(local.year, local.month, delta)
        keys.append(f"{year:04d}-{month:02d}")
    return keys


def month_window_bounds(months: int, now: datetime) -> tuple[datetime, datetime]:
    """Mốc UTC [start, end) phủ đúng `months` tháng gần nhất theo giờ VN.

    end là 00:00 giờ VN ngày 1 của tháng KẾ SAU tháng hiện tại, nên toàn bộ
    tháng hiện tại — kể cả lịch đặt trước cho những ngày còn lại — nằm trong
    cửa sổ.
    """
    local = to_local(now)
    first_year, first_month = _shift_month(local.year, local.month, -(months - 1))
    end_year, end_month = _shift_month(local.year, local.month, 1)
    start_local = datetime(first_year, first_month, 1, tzinfo=TZ)
    end_local = datetime(end_year, end_month, 1, tzinfo=TZ)
    return start_local.astimezone(timezone.utc), end_local.astimezone(timezone.utc)
```

- [ ] **Step 4: Chạy test cho chắc là pass**

Run: `pytest tests/test_month_window.py -v`
Expected: PASS — 6 passed

- [ ] **Step 5: Commit**

```bash
git add app/core/clock.py tests/test_month_window.py
git commit -m "feat(core): helper cửa sổ 12 tháng gần nhất theo giờ Việt Nam"
```

---

### Task 2: Aggregation đếm khách duy nhất theo tháng

**Files:**
- Modify: `app/repositories/appointment.py` (thêm method vào cuối class `AppointmentRepository`)
- Test: `tests/test_appointment_repository.py` (thêm vào cuối file)

**Interfaces:**
- Consumes: `settings.timezone` (`app/core/config.py`), `BaseRepository.collection`.
- Produces: `AppointmentRepository.distinct_customers_by_month(start: datetime, end: datetime) -> dict[str, int]` — key `"YYYY-MM"`, value là số `user_id` phân biệt. **Chỉ chứa các tháng thực sự có lịch booked** — việc điền tháng 0 thuộc về service (Task 3).

- [ ] **Step 1: Viết test thất bại**

Thêm vào cuối `tests/test_appointment_repository.py`:

```python
async def test_distinct_customers_counts_each_user_once_per_month(test_db):
    repo = AppointmentRepository(test_db)
    await _book(repo, utc(8, day=7), user_id="u1")
    await _book(repo, utc(9, day=8), user_id="u1")
    await _book(repo, utc(10, day=9), user_id="u1")

    counts = await repo.distinct_customers_by_month(
        datetime(2026, 8, 1, tzinfo=timezone.utc), datetime(2026, 9, 1, tzinfo=timezone.utc)
    )
    assert counts["2026-08"] == 1


async def test_distinct_customers_counts_two_different_users(test_db):
    repo = AppointmentRepository(test_db)
    await _book(repo, utc(8, day=7), user_id="u1")
    await _book(repo, utc(9, day=7), user_id="u2")

    counts = await repo.distinct_customers_by_month(
        datetime(2026, 8, 1, tzinfo=timezone.utc), datetime(2026, 9, 1, tzinfo=timezone.utc)
    )
    assert counts["2026-08"] == 2


async def test_distinct_customers_ignores_cancelled(test_db):
    repo = AppointmentRepository(test_db)
    appt = await _book(repo, utc(8, day=7), user_id="u1")
    await _book(repo, utc(9, day=7), user_id="u2")
    await repo.cancel(str(appt.id))

    counts = await repo.distinct_customers_by_month(
        datetime(2026, 8, 1, tzinfo=timezone.utc), datetime(2026, 9, 1, tzinfo=timezone.utc)
    )
    assert counts["2026-08"] == 1


async def test_distinct_customers_returns_no_key_for_month_without_bookings(test_db):
    repo = AppointmentRepository(test_db)
    counts = await repo.distinct_customers_by_month(
        datetime(2026, 8, 1, tzinfo=timezone.utc), datetime(2026, 9, 1, tzinfo=timezone.utc)
    )
    assert counts == {}


async def test_distinct_customers_groups_by_vietnam_month_boundary(test_db):
    # 2026-08-31T18:00Z = 2026-09-01 01:00 giờ VN -> phải thuộc tháng 9.
    repo = AppointmentRepository(test_db)
    await _book(repo, datetime(2026, 8, 31, 18, 0, tzinfo=timezone.utc), user_id="u1")

    counts = await repo.distinct_customers_by_month(
        datetime(2026, 8, 1, tzinfo=timezone.utc), datetime(2026, 10, 1, tzinfo=timezone.utc)
    )
    assert counts == {"2026-09": 1}
```

- [ ] **Step 2: Chạy test cho chắc là fail**

Run: `pytest tests/test_appointment_repository.py -k distinct_customers -v`
Expected: FAIL — `AttributeError: 'AppointmentRepository' object has no attribute 'distinct_customers_by_month'`

- [ ] **Step 3: Implement tối thiểu**

Thêm import ở đầu `app/repositories/appointment.py`:

```python
from app.core.config import settings
```

Thêm method vào cuối class `AppointmentRepository`:

```python
    async def distinct_customers_by_month(
        self, start: datetime, end: datetime
    ) -> dict[str, int]:
        """Số khách PHÂN BIỆT có lịch booked mỗi tháng, trong khoảng [start, end).

        Group hai nhịp: nhịp một gộp (tháng, user_id) để một khách đặt mười
        lịch trong tháng chỉ còn một dòng, nhịp hai đếm số dòng ấy. Không dùng
        $addToSet vì mảng user_id của tháng đông khách có thể vượt giới hạn
        16MB mỗi document của Mongo.

        Nhãn tháng cắt theo `timezone` cấu hình (Asia/Ho_Chi_Minh) ngay trong
        Mongo — start_at lưu UTC, cắt theo UTC sẽ đẩy lịch tối cuối tháng sang
        tháng sau.

        Chỉ trả về những tháng THỰC SỰ có lịch. Điền tháng rỗng là việc của
        lớp service.
        """
        pipeline = [
            {"$match": {"status": "booked", "start_at": {"$gte": start, "$lt": end}}},
            {
                "$group": {
                    "_id": {
                        "month": {
                            "$dateToString": {
                                "format": "%Y-%m",
                                "date": "$start_at",
                                "timezone": settings.timezone,
                            }
                        },
                        "user_id": "$user_id",
                    }
                }
            },
            {"$group": {"_id": "$_id.month", "customer_count": {"$sum": 1}}},
        ]
        rows = await self.collection.aggregate(pipeline).to_list(length=None)
        return {row["_id"]: row["customer_count"] for row in rows}
```

- [ ] **Step 4: Chạy test cho chắc là pass**

Run: `pytest tests/test_appointment_repository.py -v`
Expected: PASS — toàn bộ file, gồm 5 test mới.

- [ ] **Step 5: Commit**

```bash
git add app/repositories/appointment.py tests/test_appointment_repository.py
git commit -m "feat(repo): aggregation đếm khách duy nhất theo tháng giờ VN"
```

---

### Task 3: StatsService — điền tháng rỗng, đúng 12 tháng, đúng thứ tự

**Files:**
- Create: `app/services/stats.py`
- Test: `tests/test_stats_service.py`

**Interfaces:**
- Consumes: `AppointmentRepository.distinct_customers_by_month(start, end) -> dict[str, int]` (Task 2); `month_window_bounds`, `recent_month_keys`, `now_utc` (Task 1 + có sẵn).
- Produces: `StatsService(db).customers_by_month(months: int = 12) -> list[dict]` — mỗi phần tử `{"month": "YYYY-MM", "customer_count": int}`, chronological, độ dài đúng bằng `months`.

- [ ] **Step 1: Viết test thất bại**

Tạo `tests/test_stats_service.py`:

```python
from datetime import datetime, timedelta, timezone

import pytest

from app.core.clock import TZ
from app.repositories.appointment import AppointmentRepository
from app.services.stats import StatsService

pytestmark = pytest.mark.asyncio


async def _book(db, start, user_id="u1"):
    return await AppointmentRepository(db).insert_booked(
        user_id=user_id, user_name="Cô Lan", phone="0912345678",
        start_at=start, duration_minutes=60, note=None, created_via="chat",
    )


def _this_month_at(day: int, hour: int) -> datetime:
    """Một thời điểm trong THÁNG HIỆN TẠI theo giờ VN, trả về dạng UTC."""
    local = datetime.now(TZ).replace(
        day=day, hour=hour, minute=0, second=0, microsecond=0
    )
    return local.astimezone(timezone.utc)


async def test_returns_exactly_twelve_months_when_db_is_empty(test_db):
    result = await StatsService(test_db).customers_by_month()
    assert len(result) == 12
    assert all(item["customer_count"] == 0 for item in result)


async def test_months_are_chronological_and_current_month_is_last(test_db):
    result = await StatsService(test_db).customers_by_month()
    months = [item["month"] for item in result]
    assert months == sorted(months)
    assert months[-1] == datetime.now(TZ).strftime("%Y-%m")


async def test_same_customer_booking_three_times_counts_once(test_db):
    await _book(test_db, _this_month_at(6, 8), user_id="u1")
    await _book(test_db, _this_month_at(7, 8), user_id="u1")
    await _book(test_db, _this_month_at(8, 8), user_id="u1")

    result = await StatsService(test_db).customers_by_month()
    assert result[-1]["customer_count"] == 1


async def test_two_customers_count_two(test_db):
    await _book(test_db, _this_month_at(6, 8), user_id="u1")
    await _book(test_db, _this_month_at(6, 9), user_id="u2")

    result = await StatsService(test_db).customers_by_month()
    assert result[-1]["customer_count"] == 2


async def test_customer_with_both_booked_and_cancelled_counts_once(test_db):
    appt = await _book(test_db, _this_month_at(6, 8), user_id="u1")
    await _book(test_db, _this_month_at(7, 8), user_id="u1")
    await AppointmentRepository(test_db).cancel(str(appt.id))

    result = await StatsService(test_db).customers_by_month()
    assert result[-1]["customer_count"] == 1


async def test_only_cancelled_in_month_counts_zero(test_db):
    appt = await _book(test_db, _this_month_at(6, 8), user_id="u1")
    await AppointmentRepository(test_db).cancel(str(appt.id))

    result = await StatsService(test_db).customers_by_month()
    assert result[-1]["customer_count"] == 0


async def test_future_booking_in_current_month_is_counted(test_db):
    # Metric là "khách CÓ lịch", không phải "khách đã đến" — lịch ngày mai
    # trong cùng tháng vẫn tính. Bỏ qua nếu ngày mai đã sang tháng khác.
    tomorrow_local = datetime.now(TZ) + timedelta(days=1)
    if tomorrow_local.month != datetime.now(TZ).month:
        pytest.skip("ngày mai đã sang tháng khác")
    start = tomorrow_local.replace(hour=8, minute=0, second=0, microsecond=0)
    await _book(test_db, start.astimezone(timezone.utc), user_id="u1")

    result = await StatsService(test_db).customers_by_month()
    assert result[-1]["customer_count"] == 1


async def test_bookings_older_than_window_are_excluded(test_db):
    old = datetime.now(TZ).replace(day=1, hour=8, minute=0, second=0, microsecond=0)
    old = (old - timedelta(days=400)).astimezone(timezone.utc)
    await _book(test_db, old, user_id="u1")

    result = await StatsService(test_db).customers_by_month()
    assert sum(item["customer_count"] for item in result) == 0
```

- [ ] **Step 2: Chạy test cho chắc là fail**

Run: `pytest tests/test_stats_service.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.services.stats'`

- [ ] **Step 3: Implement tối thiểu**

Tạo `app/services/stats.py`:

```python
from typing import List

from motor.motor_asyncio import AsyncIOMotorDatabase

from app.core.clock import month_window_bounds, now_utc, recent_month_keys
from app.repositories.appointment import AppointmentRepository

MONTHS_IN_WINDOW = 12


class StatsService:
    """Số liệu tổng hợp cho bảng điều khiển chủ tiệm."""

    def __init__(self, db: AsyncIOMotorDatabase):
        self.appointments = AppointmentRepository(db)

    async def customers_by_month(self, months: int = MONTHS_IN_WINDOW) -> List[dict]:
        """Số khách duy nhất có lịch mỗi tháng, đúng `months` tháng gần nhất.

        Khung tháng do lịch VIỆT NAM quyết định chứ không do dữ liệu: tiệm mới
        mở hoặc tháng ế vẫn phải hiện thành cột 0. Trả mảng ngắn đi thì biểu đồ
        bên React lệch trục, chủ tiệm nhìn tưởng tháng đó biến mất.
        """
        now = now_utc()
        start, end = month_window_bounds(months, now)
        counts = await self.appointments.distinct_customers_by_month(start, end)
        return [
            {"month": key, "customer_count": counts.get(key, 0)}
            for key in recent_month_keys(months, now)
        ]
```

- [ ] **Step 4: Chạy test cho chắc là pass**

Run: `pytest tests/test_stats_service.py -v`
Expected: PASS — 8 passed (test lịch tương lai có thể `skipped` vào ngày cuối tháng).

- [ ] **Step 5: Commit**

```bash
git add app/services/stats.py tests/test_stats_service.py
git commit -m "feat(service): thống kê khách theo tháng, điền đủ 12 tháng"
```

---

### Task 4: Schema + router admin + wiring

**Files:**
- Modify: `app/api/v1/schemas.py` (thêm sau `AppointmentResponse`)
- Create: `app/api/v1/routers/admin.py`
- Modify: `app/api/v1/routers/__init__.py`
- Test: `tests/test_admin_stats_api.py` (create)

**Interfaces:**
- Consumes: `StatsService(db).customers_by_month()` (Task 3); `require_admin`, `get_db` (`app/api/deps.py`).
- Produces:
  - `MonthlyCustomerStat(month: str, customer_count: int)`
  - `MonthlyCustomerStatsResponse(months: list[MonthlyCustomerStat])`
  - Route `GET /api/v1/admin/stats/customers-by-month`.

- [ ] **Step 1: Viết test thất bại**

Tạo `tests/test_admin_stats_api.py`:

```python
from datetime import datetime, timezone

import pytest

from app.core.clock import TZ
from app.repositories.appointment import AppointmentRepository
from app.services.auth import AuthService

pytestmark = pytest.mark.asyncio

URL = "/api/v1/admin/stats/customers-by-month"


async def _login(client, phone, password):
    resp = await client.post("/api/v1/auth/login", json={"phone": phone, "password": password})
    assert resp.status_code == 200, resp.text
    return {"Authorization": f"Bearer {resp.json()['access_token']}"}


@pytest.fixture
async def admin_headers(async_client, test_db):
    await AuthService(test_db).create_user("0901234567", "chutiem123", "Chủ tiệm", role="admin")
    return await _login(async_client, "0901234567", "chutiem123")


@pytest.fixture
async def user_headers(async_client, test_db):
    await AuthService(test_db).create_user("0912345678", "matkhau123", "Cô Lan")
    return await _login(async_client, "0912345678", "matkhau123")


async def test_without_token_returns_401(async_client):
    resp = await async_client.get(URL)
    assert resp.status_code == 401


async def test_customer_cannot_read_admin_stats(async_client, user_headers):
    resp = await async_client.get(URL, headers=user_headers)
    assert resp.status_code == 403


async def test_admin_gets_twelve_months_even_with_empty_database(async_client, admin_headers):
    resp = await async_client.get(URL, headers=admin_headers)
    assert resp.status_code == 200, resp.text
    months = resp.json()["months"]
    assert len(months) == 12
    assert all(m["customer_count"] == 0 for m in months)


async def test_response_months_are_chronological_with_current_month_last(
    async_client, admin_headers
):
    months = (await async_client.get(URL, headers=admin_headers)).json()["months"]
    keys = [m["month"] for m in months]
    assert keys == sorted(keys)
    assert keys[-1] == datetime.now(TZ).strftime("%Y-%m")
    assert all(len(k) == 7 and k[4] == "-" for k in keys)


async def test_counts_distinct_customers_of_current_month(
    async_client, admin_headers, test_db
):
    repo = AppointmentRepository(test_db)
    local = datetime.now(TZ).replace(day=6, hour=8, minute=0, second=0, microsecond=0)
    for user_id, hour in (("u1", 8), ("u1", 9), ("u2", 10)):
        await repo.insert_booked(
            user_id=user_id, user_name="Khách", phone="0912345678",
            start_at=local.replace(hour=hour).astimezone(timezone.utc),
            duration_minutes=60, note=None, created_via="chat",
        )

    months = (await async_client.get(URL, headers=admin_headers)).json()["months"]
    assert months[-1]["customer_count"] == 2
```

- [ ] **Step 2: Chạy test cho chắc là fail**

Run: `pytest tests/test_admin_stats_api.py -v`
Expected: FAIL — route chưa tồn tại, các test trả `404` thay vì `401/403/200`.

- [ ] **Step 3: Implement tối thiểu**

Thêm vào `app/api/v1/schemas.py`, ngay sau class `AppointmentResponse`:

```python
class MonthlyCustomerStat(BaseModel):
    """Một cột của biểu đồ khách theo tháng. `month` là 'YYYY-MM' giờ VN."""

    month: str
    customer_count: int = Field(..., ge=0)


class MonthlyCustomerStatsResponse(BaseModel):
    months: List[MonthlyCustomerStat]
```

Tạo `app/api/v1/routers/admin.py`:

```python
from fastapi import APIRouter, Depends
from motor.motor_asyncio import AsyncIOMotorDatabase

from app.api.deps import get_db, require_admin
from app.api.v1.schemas import MonthlyCustomerStatsResponse
from app.models.user import User
from app.services.stats import StatsService

router = APIRouter(prefix="/admin", tags=["admin"])


@router.get("/stats/customers-by-month", response_model=MonthlyCustomerStatsResponse)
async def customers_by_month(
    _: User = Depends(require_admin), db: AsyncIOMotorDatabase = Depends(get_db)
):
    """Số khách duy nhất CÓ LỊCH mỗi tháng, 12 tháng gần nhất.

    Chưa có trạng thái "đã phục vụ" trong DB, nên đây là "khách có lịch
    booked" chứ không phải "khách đã đến tiệm" — đừng đọc thành doanh thu.
    """
    return MonthlyCustomerStatsResponse(months=await StatsService(db).customers_by_month())
```

Sửa `app/api/v1/routers/__init__.py`:

```python
from fastapi import APIRouter

from app.api.v1.routers import admin, appointments, auth, conversations, health, shop

api_router = APIRouter()
api_router.include_router(health.router)
api_router.include_router(auth.router)
api_router.include_router(appointments.router)
api_router.include_router(shop.router)
api_router.include_router(conversations.router)
api_router.include_router(admin.router)
```

- [ ] **Step 4: Chạy test cho chắc là pass**

Run: `pytest tests/test_admin_stats_api.py -v`
Expected: PASS — 5 passed

Rồi chạy cả bộ backend để chắc không vỡ chỗ khác:

Run: `pytest -q`
Expected: PASS — không có failure mới so với trước Task 1.

- [ ] **Step 5: Commit**

```bash
git add app/api/v1/schemas.py app/api/v1/routers/admin.py app/api/v1/routers/__init__.py tests/test_admin_stats_api.py
git commit -m "feat(api): GET /api/v1/admin/stats/customers-by-month"
```

---

### Task 5: Hook frontend `useMonthlyCustomerStats`

**Files:**
- Modify: `frontend/src/lib/api.ts` (thêm type sau `AppointmentCreateRequest`)
- Create: `frontend/src/hooks/useMonthlyCustomerStats.ts`
- Test: `frontend/src/hooks/useMonthlyCustomerStats.test.ts` (create)

**Interfaces:**
- Consumes: `api.get<T>(path)` từ `frontend/src/lib/api.ts`; endpoint của Task 4.
- Produces:
  - `export type MonthlyCustomerStat = { month: string; customer_count: number }` (trong `lib/api.ts`)
  - `export type MonthlyCustomerStatsResponse = { months: MonthlyCustomerStat[] }` (trong `lib/api.ts`)
  - `useMonthlyCustomerStats(): { data: MonthlyCustomerStatsResponse | null; loading: boolean; error: string | null }`

- [ ] **Step 1: Viết test thất bại**

Tạo `frontend/src/hooks/useMonthlyCustomerStats.test.ts`:

```ts
import { renderHook, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { useMonthlyCustomerStats } from "./useMonthlyCustomerStats";

const get = vi.fn();
vi.mock("../lib/api", () => ({ api: { get: (path: string) => get(path) } }));

const RESPONSE = {
  months: [
    { month: "2026-07", customer_count: 26 },
    { month: "2026-08", customer_count: 32 },
  ],
};

beforeEach(() => {
  get.mockReset();
});

describe("useMonthlyCustomerStats", () => {
  it("gọi đúng endpoint admin", async () => {
    get.mockResolvedValue(RESPONSE);
    renderHook(() => useMonthlyCustomerStats());

    await waitFor(() =>
      expect(get).toHaveBeenCalledWith("/api/v1/admin/stats/customers-by-month"),
    );
  });

  it("trả dữ liệu và tắt loading khi thành công", async () => {
    get.mockResolvedValue(RESPONSE);
    const { result } = renderHook(() => useMonthlyCustomerStats());

    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(result.current.data).toEqual(RESPONSE);
    expect(result.current.error).toBeNull();
  });

  it("giữ data null và nêu lỗi khi request hỏng", async () => {
    get.mockRejectedValue(new Error("Phần này chỉ chủ tiệm mới xem được ạ."));
    const { result } = renderHook(() => useMonthlyCustomerStats());

    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(result.current.data).toBeNull();
    expect(result.current.error).toBe("Phần này chỉ chủ tiệm mới xem được ạ.");
  });

  it("chỉ gọi API một lần cho mỗi lần mount", async () => {
    get.mockResolvedValue(RESPONSE);
    const { result } = renderHook(() => useMonthlyCustomerStats());

    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(get).toHaveBeenCalledTimes(1);
  });
});
```

- [ ] **Step 2: Chạy test cho chắc là fail**

Run: `cd frontend && npm test -- useMonthlyCustomerStats`
Expected: FAIL — không resolve được `./useMonthlyCustomerStats`.

- [ ] **Step 3: Implement tối thiểu**

Thêm vào `frontend/src/lib/api.ts`, ngay sau `AppointmentCreateRequest`:

```ts
/** Contract của GET /api/v1/admin/stats/customers-by-month
 * (backend: MonthlyCustomerStatsResponse trong app/api/v1/schemas.py).
 * `month` là "YYYY-MM" theo giờ Việt Nam; luôn đủ 12 phần tử, cũ -> mới. */
export interface MonthlyCustomerStat {
  month: string;
  customer_count: number;
}

export interface MonthlyCustomerStatsResponse {
  months: MonthlyCustomerStat[];
}
```

Tạo `frontend/src/hooks/useMonthlyCustomerStats.ts`:

```ts
import { useEffect, useState } from "react";
import { api } from "../lib/api";
import type { MonthlyCustomerStatsResponse } from "../lib/api";

/**
 * Khách theo tháng cho bảng chủ tiệm. Nạp MỘT lần lúc mở màn — số liệu tổng
 * hợp cả tháng không nhúc nhích theo từng lịch mới, nên không cần socket.
 *
 * Lỗi thì để `data` là null chứ không rơi về mảng rỗng: biểu đồ 12 cột số 0
 * trông y hệt "tháng nào cũng ế", chủ tiệm không phân biệt được với "không
 * tải được".
 */
export function useMonthlyCustomerStats() {
  const [data, setData] = useState<MonthlyCustomerStatsResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;

    (async () => {
      try {
        const res = await api.get<MonthlyCustomerStatsResponse>(
          "/api/v1/admin/stats/customers-by-month",
        );
        if (!alive) return;
        setData(res);
        setError(null);
      } catch (err) {
        if (!alive) return;
        setError(
          err instanceof Error ? err.message : "Chưa xem được thống kê ạ.",
        );
      } finally {
        if (alive) setLoading(false);
      }
    })();

    return () => {
      alive = false;
    };
  }, []);

  return { data, loading, error };
}
```

- [ ] **Step 4: Chạy test cho chắc là pass**

Run: `cd frontend && npm test -- useMonthlyCustomerStats`
Expected: PASS — 4 passed

- [ ] **Step 5: Commit**

```bash
git add frontend/src/lib/api.ts frontend/src/hooks/useMonthlyCustomerStats.ts frontend/src/hooks/useMonthlyCustomerStats.test.ts
git commit -m "feat(frontend): hook nạp thống kê khách theo tháng từ API"
```

---

### Task 6: Nối AdminDashboard vào API thật, bỏ mock

**Files:**
- Modify: `frontend/src/screens/AdminDashboard.tsx`
- Modify: `frontend/src/screens/AdminDashboard.test.tsx`
- Modify: `frontend/src/components/MonthlyCustomerStats.tsx:2` (đổi đường import type)
- Modify: `frontend/src/components/MonthlyCustomerStats.test.tsx:4` (đổi đường import type)
- Delete: `frontend/src/mocks/monthlyCustomerStats.ts`
- Modify: `docs/ADMIN_MONTHLY_CUSTOMER_STATS_API.md` (ghi nhận đã implement)

**Interfaces:**
- Consumes: `useMonthlyCustomerStats()` (Task 5); type `MonthlyCustomerStatsResponse` từ `../lib/api`.
- Produces: không có API mới — đây là task cuối của chuỗi.

- [ ] **Step 1: Viết test thất bại**

Trong `frontend/src/screens/AdminDashboard.test.tsx`, thêm mock hook ngay dưới ba dòng `vi.mock` sẵn có:

```ts
/** Hình dạng thật của useMonthlyCustomerStats() — data là null khi đang nạp
 * hoặc khi request hỏng. */
const stats = {
  data: {
    months: [
      { month: "2025-09", customer_count: 14 },
      { month: "2025-10", customer_count: 21 },
      { month: "2025-11", customer_count: 18 },
      { month: "2025-12", customer_count: 27 },
      { month: "2026-01", customer_count: 24 },
      { month: "2026-02", customer_count: 19 },
      { month: "2026-03", customer_count: 31 },
      { month: "2026-04", customer_count: 28 },
      { month: "2026-05", customer_count: 35 },
      { month: "2026-06", customer_count: 30 },
      { month: "2026-07", customer_count: 26 },
      { month: "2026-08", customer_count: 32 },
    ],
  } as { months: Array<{ month: string; customer_count: number }> } | null,
  loading: false,
  error: null as string | null,
};

vi.mock("../hooks/useMonthlyCustomerStats", () => ({
  useMonthlyCustomerStats: () => stats,
}));
```

Trong `beforeEach`, thêm reset để test này không rò sang test khác — đặt ngay sau `feed.error = null;`:

```ts
  stats.loading = false;
  stats.error = null;
```

Rồi thay ba test 13–16 cũ và thêm hai test mới, ở cuối `describe`:

```ts
  it("17. đang nạp thống kê: KHÔNG vẽ biểu đồ rỗng", () => {
    stats.loading = true;
    renderScreen();
    expect(
      screen.queryByRole("heading", { name: /khách theo tháng/i }),
    ).not.toBeInTheDocument();
  });

  it("18. lỗi tải thống kê: báo bằng chữ, không vẽ 12 cột số 0", () => {
    // 12 cột 0 trông y hệt "tháng nào cũng ế" — nói sai còn tệ hơn không nói.
    stats.error = "Phần này chỉ chủ tiệm mới xem được ạ.";
    renderScreen();
    expect(screen.getByText(/chỉ chủ tiệm mới xem được/i)).toBeInTheDocument();
    expect(
      screen.queryByRole("heading", { name: /khách theo tháng/i }),
    ).not.toBeInTheDocument();
  });
```

Giữ nguyên nội dung test 13–16 (chúng vẫn đúng vì dữ liệu mock hook trùng dữ liệu cũ), chỉ đảm bảo chúng chạy với `stats.loading = false`.

- [ ] **Step 2: Chạy test cho chắc là fail**

Run: `cd frontend && npm test -- AdminDashboard`
Expected: FAIL — test 17 và 18 fail vì `AdminDashboard` vẫn render `mockMonthlyCustomerStats` bất kể loading/error.

- [ ] **Step 3: Implement tối thiểu**

Trong `frontend/src/screens/AdminDashboard.tsx`, bỏ dòng import mock và thêm import hook:

```ts
import { useMonthlyCustomerStats } from "../hooks/useMonthlyCustomerStats";
```

Trong thân component, thêm sau `const { logout } = useAuth();`:

```ts
  const monthlyStats = useMonthlyCustomerStats();
```

Thay dòng `<MonthlyCustomerStats data={mockMonthlyCustomerStats} />` bằng:

```tsx
      {monthlyStats.error && (
        <p role="alert" className="alert alert--danger">
          {monthlyStats.error}
        </p>
      )}

      {monthlyStats.data && <MonthlyCustomerStats data={monthlyStats.data} />}
```

Trong `frontend/src/components/MonthlyCustomerStats.tsx`, đổi dòng 2:

```ts
import type { MonthlyCustomerStatsResponse } from "../lib/api";
```

Trong `frontend/src/components/MonthlyCustomerStats.test.tsx`, đổi dòng 4:

```ts
import type { MonthlyCustomerStatsResponse } from "../lib/api";
```

Xoá file mock:

```bash
git rm frontend/src/mocks/monthlyCustomerStats.ts
```

Cập nhật `docs/ADMIN_MONTHLY_CUSTOMER_STATS_API.md` — chèn ngay dưới dòng tiêu đề `# API — Thống kê khách theo tháng`:

```markdown
> **Trạng thái:** đã implement. Route thật `GET /api/v1/admin/stats/customers-by-month`
> (`app/api/v1/routers/admin.py`), nghiệp vụ ở `app/services/stats.py`, aggregation ở
> `AppointmentRepository.distinct_customers_by_month`. Frontend nạp qua
> `useMonthlyCustomerStats` — mock `frontend/src/mocks/monthlyCustomerStats.ts` đã bỏ.
```

- [ ] **Step 4: Chạy test cho chắc là pass**

Run: `cd frontend && npm test`
Expected: PASS — toàn bộ suite, không còn file nào import `../mocks/monthlyCustomerStats`.

Run: `cd frontend && npm run build`
Expected: PASS — `tsc -b` không báo type error (bắt được import mồ côi tới file mock đã xoá).

Run: `cd frontend && npm run lint`
Expected: PASS — không lỗi mới.

- [ ] **Step 5: Commit**

```bash
git add frontend/src docs/ADMIN_MONTHLY_CUSTOMER_STATS_API.md
git commit -m "feat(frontend): dashboard đọc thống kê khách từ API thật, bỏ mock"
```

---

## Kiểm tra cuối trước khi merge

- [ ] Backend: `pytest -q` xanh (Mongo đang chạy).
- [ ] Frontend: `npm test`, `npm run build`, `npm run lint` đều xanh.
- [ ] Smoke thủ công: chạy app, đăng nhập tài khoản admin, mở `/chu-tiem`, thấy 12 cột với tháng hiện tại ở cuối; đăng nhập tài khoản khách rồi gọi thẳng endpoint → `403`.

## Git flow

Theo quy ước của repo (việc → `henry/develop` → `main`, không commit thẳng lên `main`):

```bash
git checkout henry/develop
git pull --ff-only
git checkout -b feat/admin-monthly-customer-stats-api
# ... thực hiện Task 1..6, commit sau mỗi task ...
git checkout henry/develop
git merge --no-ff feat/admin-monthly-customer-stats-api
git push origin henry/develop
```

Lưu ý: remote đã có sẵn nhánh tên `feat/admin-monthly-customer-stats` (nhánh frontend/mock trước đó). Đặt tên nhánh mới là `feat/admin-monthly-customer-stats-api` để không đụng.
