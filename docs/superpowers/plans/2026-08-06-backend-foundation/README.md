# Nền tảng backend — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Mỗi task là một file riêng; steps dùng checkbox (`- [ ]`) để theo dõi.

**Goal:** Dựng toàn bộ tầng nghiệp vụ và API cho app đặt lịch — đăng nhập bằng SĐT, đặt/hủy lịch có chặn trùng nguyên tử, trạng thái bận/rảnh, giờ mở cửa — chạy và test được hoàn toàn không cần AI.

**Architecture:** Bốn tầng theo spec: `routers` → `services` → `repositories` → MongoDB. Toàn bộ nghiệp vụ nằm ở `services/` vì Plan 2 (agent), Plan 3 (Telegram) và Plan 4 (React) đều chỉ bọc mỏng bên ngoài cùng những service này. Chặn trùng giờ không làm ở tầng ứng dụng mà đẩy xuống MongoDB bằng unique partial index trên mảng `slot_keys`.

**Tech Stack:** FastAPI, Motor (MongoDB async), Pydantic v2, pytest + pytest-asyncio, python-jose (JWT), passlib.

Spec: [`docs/superpowers/specs/2026-08-06-booking-nail-toc/`](../../specs/2026-08-06-booking-nail-toc/README.md) · Lộ trình 4 plan: [roadmap](../2026-08-06-booking-nail-toc-roadmap.md)

## Ràng buộc toàn cục

Áp cho **mọi** task, kể cả khi file task không nhắc lại:

- **Múi giờ:** lưu UTC trong DB. Diễn giải và hiển thị theo `Asia/Ho_Chi_Minh`. Chuyển đổi chỉ ở một chỗ (`app/core/clock.py`).
- **Lưới slot:** 15 phút. Mọi `start_at` phải rơi đúng bội số 15 phút.
- **Thời lượng mặc định:** `BOOKING_SLOT_MINUTES = 60`, đọc từ env.
- **Số worker:** đúng 1. Không `--workers`, không `gunicorn -w N`.
- **Rate limit đổi mật khẩu:** 5 lần mỗi giờ, tính riêng theo SĐT và theo IP.
- **Không có đăng ký tự do.** Chỉ tài khoản `role="admin"` mới tạo được user.
- **Mọi lỗi nghiệp vụ** là lớp con của `AppError` trong `app/core/errors.py`. Tầng service không bao giờ ném `HTTPException`.
- **Python 3.11+** (dùng `zoneinfo`, `datetime.UTC`).

## Thứ tự task

Làm tuần tự. Cột "Cần trước" là task mà file này dùng lại interface của nó.

| # | Task | Sản phẩm | Cần trước |
|---|---|---|---|
| 1 | [Dọn dẹp và cấu hình](task-01-cleanup-and-config.md) | Gỡ AutoGen, thêm setting mới, `AppError`, Postgres trong compose | — |
| 2 | [Chuẩn hóa SĐT](task-02-phone.md) | `normalize_phone()` — hàm thuần, không DB | 1 |
| 3 | [Đồng hồ và slot](task-03-clock-and-slots.md) | `clock.py`, `slots.py` — hàm thuần, không DB | 1 |
| 4 | [Model và repo User](task-04-user-model.md) | User theo SĐT, `ensure_indexes()` | 2 |
| 5 | [Rate limit](task-05-rate-limit.md) | `RateLimitService` trên Mongo + TTL | 1 |
| 6 | [AuthService](task-06-auth-service.md) | Đăng nhập, admin tạo tài khoản, quên mật khẩu | 4, 5 |
| 7 | [Repo Appointment](task-07-appointment-repository.md) | Chặn trùng nguyên tử bằng unique partial index | 3, 4 |
| 8 | [Giờ mở cửa và bận/rảnh](task-08-shop.md) | `ShopService`, bận hết giờ tự về rảnh | 3 |
| 9 | [AppointmentService](task-09-appointment-service.md) | Đặt (idempotency), hủy, tìm giờ trống | 7, 8 |
| 10 | [Schema và phân quyền](task-10-schemas-and-deps.md) | Schema API, `get_current_user`, `require_admin` | 6 |
| 11 | [Router REST](task-11-routers.md) | Toàn bộ endpoint + handler dịch `AppError` → HTTP | 9, 10 |
| 12 | [README vận hành](task-12-readme.md) | Hướng dẫn chạy, tạo admin đầu tiên, cạm bẫy | 11 |

Task 2, 3 và 5 độc lập nhau — chạy song song được nếu dùng nhiều subagent.

## Ba test bảo vệ chỗ thiết kế từng sai

Đừng bỏ qua ba test này, chúng là lý do plan được viết như hiện tại:

1. **Hai request đặt cùng lúc chỉ một thắng** (task 7) — chứng minh unique index chặn thật, không phải kiểm-tra-rồi-ghi.
2. **Hủy hai lịch liên tiếp không ném `E11000`** (task 7) — bản thiết kế đầu tiên đặt `slot_keys = []` khi hủy, mà MongoDB đánh index mảng rỗng thành `undefined` nên lịch hủy thứ hai sẽ nổ. Partial index lọc `status="booked"` mới đúng.
3. **Hủy xong đặt lại đúng khung giờ đó phải thành công** (task 7) — nếu không, slot bị khóa vĩnh viễn.

## Kiểm tra sau khi xong

```bash
pytest -v
```

Và kiểm tay qua Swagger tại `/docs`: đăng nhập bằng SĐT ở mọi định dạng (`0912…`, `+84912…`, `84912…`), admin tạo tài khoản, đặt lịch, đặt trùng nhận 409, hủy rồi đặt lại đúng khung giờ đó phải thành công, bấm bận 30 phút rồi xem `minutes_left` giảm dần.

Xong Plan 1 là có backend hoàn chỉnh không cần AI. Tiếp theo: Plan 2 (agent + memory + streaming).
