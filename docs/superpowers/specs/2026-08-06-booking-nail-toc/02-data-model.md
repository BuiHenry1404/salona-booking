# Data model

## MongoDB

**`users`** — thay `email`/`username` của template bằng SĐT.

```
phone            string, unique index
hashed_password  string
full_name        string
role             "user" | "admin"
is_active        bool
created_at / updated_at
```

**`appointments`**

```
user_id            string
user_name, phone   chụp lại lúc đặt, để admin đọc không cần join
start_at           datetime (UTC)
duration_minutes   int, mặc định từ config
slot_keys          array<string>, xem [Chặn trùng giờ](#chặn-trùng-giờ)
note               string, ghi chú tự do
status             "booked" | "cancelled"
created_via        "chat" | "admin"
created_at / updated_at
```

Index: `slot_keys` (unique **partial**, xem [Chặn trùng giờ](#chặn-trùng-giờ)), `start_at`, `(user_id, start_at)`.

**`shop_hours`** — một document duy nhất, giờ mở cửa dùng cho `find_free_slots`.

```
open_time    "08:00"   giờ địa phương
close_time   "19:00"
closed_days  array<int>, 0 = Chủ nhật
```

Admin sửa được trong màn hình bảng điều khiển. Không có giờ mở cửa thì AI sẽ mời khách 3 giờ sáng.

**`shop_status`** — một document duy nhất.

```
is_busy      bool
busy_until   datetime, nullable
updated_at   datetime
```

AI diễn giải: `is_busy = false` → chủ tiệm đang rảnh. `is_busy = true` → đang bận, **xong lúc `busy_until`** — nói mốc giờ chứ không nói "còn N phút", vì câu trả lời nằm lại trong lịch sử chat và sẽ sai ngay sau đó. Nếu `now > busy_until` thì coi như rảnh mà không cần admin bấm lại — đây là cách hiện thực yêu cầu "hết giờ tự về Rảnh".

**`conversations`** / **`messages`** — tái dùng model có sẵn, chuyển `task` thành lịch sử chat.

## Chặn trùng giờ

MongoDB một node không có transaction, nên kiểm tra rồi mới ghi có thể lọt hai lịch trùng khi hai người đặt cùng lúc.

Giải pháp: lượng tử hóa thời gian thành lưới 15 phút, lưu `slot_keys` là mảng các mốc mà lịch chiếm.

```
start 15:00, duration 60 →
slot_keys = ["2026-08-07T15:00", "2026-08-07T15:15",
             "2026-08-07T15:30", "2026-08-07T15:45"]
```

MongoDB áp ràng buộc unique cho từng phần tử mảng xuyên document, nên hai lịch chồng giờ bị chính database từ chối — nguyên tử, không cần transaction, không cần khóa. Service bắt lỗi trùng khóa và trả về gợi ý giờ khác.

Index phải là **partial**, chỉ bao các lịch còn hiệu lực:

```js
db.appointments.createIndex(
  { slot_keys: 1 },
  { unique: true, partialFilterExpression: { status: "booked" } }
)
```

**Vì sao bắt buộc partial chứ không phải sparse.** Cách hiển nhiên là khi hủy thì xóa `slot_keys` thành mảng rỗng — nhưng MongoDB đánh index mảng rỗng thành `undefined`, nên lịch bị hủy **thứ hai** sẽ ném `E11000 dup key: { : undefined }`. Sparse index không cứu được vì mảng rỗng vẫn được coi là trường có mặt. Với partial index thì hủy lịch chỉ cần đổi `status` sang `cancelled`: document tự rơi khỏi index, chỗ đó đặt lại được ngay, và `slot_keys` vẫn được giữ nguyên để tra cứu lịch sử.

Hệ quả: giờ đặt được làm tròn về bội số 15 phút. Điều này phù hợp với người lớn tuổi và được coi là ưu điểm chứ không phải hạn chế.

`find_free_slots` chỉ trả về các mốc nằm trong `shop_hours`, không rơi vào `closed_days`, và không ở quá khứ.

## Múi giờ

Lưu UTC trong DB. Hiển thị và diễn giải ngôn ngữ tự nhiên ("3h chiều mai") theo `Asia/Ho_Chi_Minh`. Việc chuyển đổi tập trung ở một chỗ duy nhất trong service, không rải rác.

