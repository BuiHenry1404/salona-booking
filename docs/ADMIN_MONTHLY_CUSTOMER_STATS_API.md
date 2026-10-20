# API — Thống kê khách theo tháng

> **Trạng thái:** đã implement. Route thật `GET /api/v1/admin/stats/customers-by-month`
> (`app/api/v1/routers/admin.py`), nghiệp vụ ở `app/services/stats.py`, aggregation ở
> `AppointmentRepository.distinct_customers_by_month`. Frontend nạp qua
> `useMonthlyCustomerStats` — mock `frontend/src/mocks/monthlyCustomerStats.ts` đã bỏ.

## Mục đích

Frontend Admin Dashboard cần số khách duy nhất có lịch theo từng tháng.

## Business Rules

- Trả đúng 12 tháng gần nhất.
- Bao gồm tháng hiện tại.
- Mỗi `user_id` chỉ tính 1 lần trong một tháng.
- Cùng khách đặt nhiều lịch trong tháng vẫn `count = 1`.
- Appointment `status = "cancelled"` KHÔNG tính.
- Hiện tại status hợp lệ để tính là `"booked"`.
- Month boundaries phải theo timezone `Asia/Ho_Chi_Minh`.

**Quan trọng:** Database hiện chưa có trạng thái "đã phục vụ" / "completed". Vì vậy metric này là **"khách có lịch"** — tức khách có ít nhất một appointment `booked` trong tháng — chứ không phải "khách đã đến tiệm".

## Endpoint

```http
GET /api/v1/admin/stats/customers-by-month
```

> Route này nằm dưới `/api/v1/admin` để nhất quán với quyền admin. Nếu cấu trúc router hiện tại chưa có router admin, có thể tạo mới hoặc đặt tại `GET /api/v1/appointments/stats/customers-by-month` với decorator `require_admin`.

## Authorization

- Yêu cầu Bearer access token hợp lệ.
- Chỉ `role = "admin"` được phép truy cập.
- Customer thường → `403 Forbidden`.
- Access token hết hạn / sai / bị thu hồi → `401 Unauthorized`.
- Thiếu hẳn header Authorization → `403 Forbidden` (xem "Error behavior"
  bên dưới).

## Request

Không có body, không có query parameters trong v1.

Backend tự động trả 12 tháng gần nhất (bao gồm tháng hiện tại) theo giờ Việt Nam.

## Response

```json
{
  "months": [
    { "month": "2025-09", "customer_count": 14 },
    { "month": "2025-10", "customer_count": 18 },
    { "month": "2025-11", "customer_count": 21 },
    { "month": "2025-12", "customer_count": 27 },
    { "month": "2026-01", "customer_count": 24 },
    { "month": "2026-02", "customer_count": 19 },
    { "month": "2026-03", "customer_count": 31 },
    { "month": "2026-04", "customer_count": 28 },
    { "month": "2026-05", "customer_count": 35 },
    { "month": "2026-06", "customer_count": 30 },
    { "month": "2026-07", "customer_count": 26 },
    { "month": "2026-08", "customer_count": 32 }
  ]
}
```

Yêu cầu:

- Đúng 12 entries.
- Thứ tự từ cũ đến mới (chronological).
- Tháng hiện tại là item cuối.
- `month` định dạng `YYYY-MM`.
- `customer_count` là integer `>= 0`.
- Tháng không có lịch hợp lệ vẫn phải trả với `customer_count: 0`.

## Counting Definition

Với mỗi calendar month theo giờ Việt Nam:

```
COUNT DISTINCT appointment.user_id
WHERE
  status = "booked"
  AND appointment.start_at falls within that month in Asia/Ho_Chi_Minh
```

## Xử lý lịch hủy

Appointment có `status = "cancelled"` không được tính.

## Lịch tương lai trong tháng hiện tại

Có tính. Ví dụ: hôm nay là 15/08/2026, khách đặt lịch 20/08/2026 thì vẫn
tính cho tháng 08. Metric là "khách có lịch", không phải "khách đã đến".

## Ví dụ

Tháng 8/2026 có:

- Khách A: 2 lịch booked
- Khách B: 1 lịch booked
- Khách C: 1 lịch cancelled

Kết quả: `customer_count = 2`.

## Empty data

Vẫn trả 12 tháng với `customer_count: 0`:

```json
{
  "months": [
    ...,
    { "month": "2026-08", "customer_count": 0 }
  ]
}
```

Không trả mảng rỗng chỉ vì chưa có appointment.

## Error behavior

- `401 Unauthorized`: access token hết hạn / sai / bị thu hồi.
- `403 Forbidden`: thiếu hẳn header Authorization (convention chung của
  `HTTPBearer` toàn app — `app/api/deps.py:10`, không đổi riêng cho endpoint
  này), hoặc user không phải admin.
- `5xx`: lỗi server/database bất ngờ.

## Mongo aggregation guidance

Các bước concept:

1. Tính range 12 tháng theo giờ Việt Nam (từ đầu tháng N-11 đến cuối tháng
   hiện tại).
2. Match `appointments` với `status = "booked"` và `start_at` trong range.
3. Group theo calendar year/month theo giờ Việt Nam.
4. Đếm DISTINCT `user_id` mỗi group.
5. Fill các tháng thiếu với `customer_count: 0`.
6. Sắp xếp chronological.
7. Trả về.

**Lưu ý timezone:** Mongo lưu `start_at` là UTC. Khi group theo tháng,
cần chuyển về `Asia/Ho_Chi_Minh` để không bị lệch ranh giới tháng. Có thể
dùng `$dateToString` với `timezone: "Asia/Ho_Chi_Minh"` hoặc tính UTC bounds
ở Python trước khi query.

## Frontend TypeScript Contract

```typescript
export type MonthlyCustomerStat = {
  month: string;
  customer_count: number;
};

export type MonthlyCustomerStatsResponse = {
  months: MonthlyCustomerStat[];
};
```

## Acceptance Cases

1. Cùng khách đặt 3 lịch booked trong tháng → `customer_count = 1`.
2. Hai khách khác nhau mỗi người 1 lịch booked → `customer_count = 2`.
3. Chỉ có lịch cancelled trong tháng → `customer_count = 0`.
4. Một khách có cả booked và cancelled trong tháng → `customer_count = 1`.
5. Tháng không có appointment nào → vẫn trả tháng đó với `0`.
6. Trả đúng 12 tháng.
7. Tháng hiện tại nằm trong danh sách.
8. Lịch booked tương lai trong tháng hiện tại vẫn tính.
9. Ranh giới tháng tính theo `Asia/Ho_Chi_Minh`.
10. Customer không thể truy cập endpoint admin.
