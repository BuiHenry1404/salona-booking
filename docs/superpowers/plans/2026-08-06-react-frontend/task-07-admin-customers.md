# Task 7 · Khách hàng, đặt lịch hộ và giờ mở cửa

> Thuộc plan [Frontend React](README.md). **Đọc [Ràng buộc toàn cục](README.md#ràng-buộc-toàn-cục) trước khi bắt đầu** — chúng áp cho mọi task, kể cả khi không nhắc lại ở đây.

**Files:**
- Create: `frontend/src/screens/CustomersScreen.tsx`, `frontend/src/screens/CustomersScreen.test.tsx`, `frontend/src/screens/BookForCustomer.tsx`, `frontend/src/screens/ShopHoursScreen.tsx`, `frontend/src/screens/ShopHoursScreen.test.tsx`
- Modify: `frontend/src/App.tsx`, `frontend/src/lib/api.ts`
- Modify (backend): `app/api/v1/schemas.py`, `app/api/v1/routers/appointments.py`, `tests/test_routers.py`

**Interfaces:**
- Consumes: `api`, `UserResponse`, `ShopHours` (task 2), `Button`, `Field` (task 1–2), `formatViTime` (task 5)
- Produces:
  - `<CustomersScreen />` tại `/chu-tiem/khach`
  - `<BookForCustomer />` tại `/chu-tiem/khach/:userId/dat-lich`
  - `<ShopHoursScreen />` tại `/chu-tiem/gio-mo-cua`
  - Backend: `POST /api/v1/appointments` nhận thêm `for_user_id` (chỉ admin)

## Lỗ hổng của Plan 1 phải vá ở đây

Spec yêu cầu chủ tiệm **đặt lịch hộ khi khách gọi điện**, nhưng `POST /api/v1/appointments` của Plan 1 luôn đặt cho chính người đang đăng nhập. Không có đường nào đặt hộ. Step 1–3 dưới đây bổ sung `for_user_id` vào đúng endpoint đó, chỉ admin dùng được — không thêm endpoint mới, không đụng vào tầng service.

- [ ] **Step 1: Viết test backend (sẽ fail)**

Thêm vào `tests/test_routers.py`:

```python
async def test_admin_books_on_behalf_of_a_customer(async_client, admin_headers, test_db):
    """Khách gọi điện, chủ tiệm bấm hộ. Lịch phải thuộc về KHÁCH, không phải admin."""
    from app.services.auth import AuthService

    customer = await AuthService(test_db).create_user("0987654321", "matkhau123", "Cô Hoa")

    resp = await async_client.post(
        "/api/v1/appointments",
        json={"start_at": tomorrow_at(9), "note": "làm nail", "for_user_id": str(customer.id)},
        headers=admin_headers,
    )
    assert resp.status_code == 201
    assert resp.json()["user_name"] == "Cô Hoa"
    assert resp.json()["phone"] == "0987654321"


async def test_a_customer_cannot_book_in_someone_elses_name(
    async_client, user_headers, test_db
):
    """Chặn ở BACKEND: sửa localStorage thành admin cũng không đặt hộ được."""
    from app.services.auth import AuthService

    victim = await AuthService(test_db).create_user("0987654321", "matkhau123", "Cô Hoa")

    resp = await async_client.post(
        "/api/v1/appointments",
        json={"start_at": tomorrow_at(9), "note": None, "for_user_id": str(victim.id)},
        headers=user_headers,
    )
    assert resp.status_code == 403


async def test_booking_for_an_unknown_customer_is_404(async_client, admin_headers):
    resp = await async_client.post(
        "/api/v1/appointments",
        json={"start_at": tomorrow_at(9), "note": None,
              "for_user_id": "000000000000000000000000"},
        headers=admin_headers,
    )
    assert resp.status_code == 404
```

- [ ] **Step 2: Chạy test để xác nhận fail**

Run: `pytest tests/test_routers.py -k on_behalf -v`
Expected: FAIL — `for_user_id` bị bỏ qua, lịch thuộc về admin

- [ ] **Step 3: Vá backend**

Trong `app/api/v1/schemas.py`, thêm một trường vào `AppointmentCreateRequest`:

```python
class AppointmentCreateRequest(BaseModel):
    start_at: datetime
    note: Optional[str] = None
    # Chỉ admin được điền. Khách gửi lên sẽ bị từ chối 403 ở router.
    for_user_id: Optional[str] = None
```

Trong `app/api/v1/routers/appointments.py`, viết lại `create_appointment`:

```python
@router.post("", response_model=AppointmentResponse, status_code=status.HTTP_201_CREATED)
async def create_appointment(
    payload: AppointmentCreateRequest,
    user: User = Depends(get_current_user),
    db: AsyncIOMotorDatabase = Depends(get_db),
):
    booking_for = user
    created_via = "admin" if user.role == "admin" else "chat"

    if payload.for_user_id and payload.for_user_id != str(user.id):
        # Kiểm quyền Ở ĐÂY, không dựa vào việc React ẩn nút.
        if user.role != "admin":
            raise HTTPException(status.HTTP_403_FORBIDDEN, "Không có quyền đặt hộ người khác")
        booking_for = await AuthService(db).get_user_by_id(payload.for_user_id)
        if not booking_for:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Không tìm thấy khách này")
        created_via = "admin"

    appt = await AppointmentService(db).create(
        booking_for, payload.start_at, payload.note, created_via
    )
    return _to_response(appt)
```

Thêm import ở đầu file:

```python
from fastapi import HTTPException

from app.services.auth import AuthService
```

- [ ] **Step 4: Chạy test backend để xác nhận pass**

Run: `pytest tests/test_routers.py -v`
Expected: PASS toàn bộ

- [ ] **Step 5: Cập nhật kiểu ở frontend**

Trong `frontend/src/lib/api.ts`, thêm:

```ts
export interface AppointmentCreateRequest {
  start_at: string;
  note: string | null;
  /** Chỉ admin dùng — đặt lịch hộ khi khách gọi điện. */
  for_user_id?: string;
}
```

- [ ] **Step 6: Viết test màn hình khách hàng (sẽ fail)**

Tạo `frontend/src/screens/CustomersScreen.test.tsx`:

```tsx
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import { CustomersScreen } from "./CustomersScreen";

vi.mock("../auth/AuthContext", () => ({
  useAuth: () => ({ role: "admin", fullName: "Chủ tiệm", logout: vi.fn() }),
}));

const users = [
  { id: "u1", phone: "0912345678", full_name: "Cô Lan", role: "user", is_active: true },
  { id: "u2", phone: "0987654321", full_name: "Cô Hoa", role: "user", is_active: true },
  { id: "a1", phone: "0900000000", full_name: "Chủ tiệm", role: "admin", is_active: true },
];

function mockApi(onPost?: (body: unknown) => unknown) {
  vi.stubGlobal(
    "fetch",
    vi.fn().mockImplementation(async (_url: string, init?: { method?: string; body?: string }) => {
      if (init?.method === "POST") {
        const created = onPost?.(JSON.parse(init.body ?? "{}")) ?? {
          id: "u3",
          phone: "0900111222",
          full_name: "Cô Mai",
          role: "user",
          is_active: true,
        };
        return { ok: true, status: 201, json: async () => created };
      }
      return { ok: true, status: 200, json: async () => users };
    }),
  );
}

afterEach(() => vi.unstubAllGlobals());

const renderScreen = () =>
  render(
    <MemoryRouter>
      <CustomersScreen />
    </MemoryRouter>,
  );

describe("CustomersScreen", () => {
  it("liệt kê khách kèm số điện thoại", async () => {
    mockApi();
    renderScreen();
    expect(await screen.findByText("Cô Lan")).toBeInTheDocument();
    expect(screen.getByText("0987654321")).toBeInTheDocument();
  });

  it("tìm theo SĐT lọc ngay tại chỗ", async () => {
    mockApi();
    renderScreen();
    await screen.findByText("Cô Lan");

    await userEvent.type(screen.getByLabelText(/tìm theo số điện thoại/i), "0987");
    expect(screen.queryByText("Cô Lan")).not.toBeInTheDocument();
    expect(screen.getByText("Cô Hoa")).toBeInTheDocument();
  });

  it("tìm được cả theo tên, vì chủ tiệm nhớ tên hơn nhớ số", async () => {
    mockApi();
    renderScreen();
    await screen.findByText("Cô Lan");

    await userEvent.type(screen.getByLabelText(/tìm theo số điện thoại/i), "hoa");
    expect(screen.getByText("Cô Hoa")).toBeInTheDocument();
  });

  it("tạo tài khoản gửi đúng SĐT, tên và mật khẩu ban đầu", async () => {
    const seen: unknown[] = [];
    mockApi((body) => {
      seen.push(body);
      return { id: "u3", phone: "0900111222", full_name: "Cô Mai", role: "user", is_active: true };
    });
    renderScreen();

    await userEvent.click(await screen.findByRole("button", { name: /tạo tài khoản/i }));
    // Khớp CHÍNH XÁC: ô tìm kiếm có nhãn "Tìm theo số điện thoại" cũng khớp
    // regex /số điện thoại/i và sẽ làm getByLabelText ném lỗi trùng.
    await userEvent.type(screen.getByLabelText("Số điện thoại"), "0900111222");
    await userEvent.type(screen.getByLabelText(/tên khách/i), "Cô Mai");
    await userEvent.type(screen.getByLabelText(/mật khẩu ban đầu/i), "1234");
    await userEvent.click(screen.getByRole("button", { name: /lưu khách mới/i }));

    await waitFor(() => expect(seen).toHaveLength(1));
    expect(seen[0]).toEqual({
      phone: "0900111222",
      full_name: "Cô Mai",
      password: "1234",
      role: "user",
    });
  });

  it("nhắc đọc mật khẩu ban đầu cho khách — không có email để gửi", async () => {
    mockApi();
    renderScreen();
    await userEvent.click(await screen.findByRole("button", { name: /tạo tài khoản/i }));
    expect(screen.getByText(/đọc.*cho khách/i)).toBeInTheDocument();
  });

  it("mỗi khách có nút đặt lịch hộ", async () => {
    mockApi();
    renderScreen();
    const links = await screen.findAllByRole("link", { name: /đặt lịch hộ/i });
    expect(links[0]).toHaveAttribute("href", "/chu-tiem/khach/u1/dat-lich");
  });
});
```

- [ ] **Step 7: Chạy test để xác nhận fail**

Run: `cd frontend && npm test`
Expected: FAIL — không tìm thấy `./CustomersScreen`

- [ ] **Step 8: Viết `frontend/src/screens/CustomersScreen.tsx`**

```tsx
import { useCallback, useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { Button } from "../components/Button";
import { Field } from "../components/Field";
import { api } from "../lib/api";
import type { UserResponse } from "../lib/api";

export function CustomersScreen() {
  const [users, setUsers] = useState<UserResponse[]>([]);
  const [query, setQuery] = useState("");
  const [creating, setCreating] = useState(false);
  const [form, setForm] = useState({ phone: "", full_name: "", password: "" });
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [note, setNote] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      setUsers(await api.get<UserResponse[]>("/api/v1/auth/users"));
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Không xem được danh sách ạ.");
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  // Lọc tại chỗ chứ không gọi API mỗi lần gõ: một tiệm nhỏ có vài trăm khách,
  // tải hết một lần rẻ hơn nhiều so với gọi lại theo từng phím.
  const shown = useMemo(() => {
    const needle = query.trim().toLowerCase();
    return users
      .filter((u) => u.role !== "admin")
      .filter(
        (u) =>
          !needle ||
          u.phone.includes(needle) ||
          (u.full_name ?? "").toLowerCase().includes(needle),
      );
  }, [users, query]);

  async function createUser() {
    if (!form.phone.trim() || !form.password) {
      setError("Cô chú nhập số điện thoại và mật khẩu ban đầu giúp con ạ.");
      return;
    }
    setBusy(true);
    setError(null);
    try {
      await api.post<UserResponse>("/api/v1/auth/users", {
        phone: form.phone.trim(),
        full_name: form.full_name.trim() || null,
        password: form.password,
        role: "user",
      });
      setNote(`Đã tạo tài khoản. Mật khẩu ban đầu là ${form.password}, đọc cho khách nhớ.`);
      setForm({ phone: "", full_name: "", password: "" });
      setCreating(false);
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Tạo tài khoản không được ạ.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="screen">
      <Link to="/chu-tiem" style={{ fontSize: 19 }}>
        ← Về bảng điều khiển
      </Link>
      <h1 style={{ fontSize: 28, margin: "var(--s3) 0 var(--s4)" }}>Khách hàng</h1>

      {error && <p role="alert" className="alert alert--danger">{error}</p>}
      {note && <p className="alert alert--info">{note}</p>}

      {creating ? (
        <section style={{ marginBottom: "var(--s4)" }}>
          <Field
            label="Số điện thoại"
            inputMode="numeric"
            value={form.phone}
            onChange={(e) => setForm({ ...form, phone: e.target.value })}
          />
          <Field
            label="Tên khách"
            value={form.full_name}
            onChange={(e) => setForm({ ...form, full_name: e.target.value })}
          />
          <Field
            label="Mật khẩu ban đầu"
            hint="Đọc mật khẩu này cho khách ngay — hệ thống không gửi tin nhắn được."
            value={form.password}
            onChange={(e) => setForm({ ...form, password: e.target.value })}
          />
          <Button loading={busy} onClick={createUser}>
            Lưu khách mới
          </Button>
          <div style={{ height: "var(--s2)" }} />
          <Button variant="ghost" onClick={() => setCreating(false)}>
            Thôi, để sau
          </Button>
        </section>
      ) : (
        <Button onClick={() => setCreating(true)}>Tạo tài khoản</Button>
      )}

      <div style={{ marginTop: "var(--s4)" }}>
        <Field
          label="Tìm theo số điện thoại"
          hint="Gõ vài số cuối cũng được, hoặc gõ tên khách."
          inputMode="numeric"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
        />
      </div>

      {shown.length === 0 && <p style={{ fontSize: 20 }}>Không tìm thấy khách nào ạ.</p>}

      {shown.map((user) => (
        <article key={user.id} className="appt">
          <p className="appt__when" style={{ fontSize: 21 }}>
            {user.full_name ?? "Khách"}
          </p>
          <p className="appt__who">{user.phone}</p>
          <Link
            to={`/chu-tiem/khach/${user.id}/dat-lich`}
            className="btn btn--ghost btn--full"
            style={{ textDecoration: "none", marginTop: "var(--s2)" }}
          >
            Đặt lịch hộ
          </Link>
        </article>
      ))}
    </div>
  );
}
```

- [ ] **Step 9: Viết `frontend/src/screens/BookForCustomer.tsx`**

```tsx
import { useEffect, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { Button } from "../components/Button";
import { Field } from "../components/Field";
import { api } from "../lib/api";
import type { AppointmentResponse, UserResponse } from "../lib/api";
import { formatViTime, formatViDateTime } from "../lib/viDate";
import { todayInVn } from "../hooks/useAdminFeed";

export function BookForCustomer() {
  const { userId = "" } = useParams();
  const navigate = useNavigate();
  const [customer, setCustomer] = useState<UserResponse | null>(null);
  const [day, setDay] = useState(todayInVn());
  const [slots, setSlots] = useState<string[]>([]);
  const [chosen, setChosen] = useState<string | null>(null);
  const [note, setNote] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    api
      .get<UserResponse[]>("/api/v1/auth/users")
      .then((all) => setCustomer(all.find((u) => u.id === userId) ?? null))
      .catch(() => undefined);
  }, [userId]);

  useEffect(() => {
    setChosen(null);
    api
      .get<{ slots: string[] }>(`/api/v1/appointments/free-slots/${day}`)
      .then((res) => setSlots(res.slots))
      .catch((err) => setError(err instanceof Error ? err.message : "Không xem được giờ trống ạ."));
  }, [day]);

  async function book() {
    if (!chosen) return;
    setBusy(true);
    setError(null);
    try {
      await api.post<AppointmentResponse>("/api/v1/appointments", {
        start_at: chosen,
        note: note.trim() || null,
        for_user_id: userId,
      });
      navigate("/chu-tiem");
    } catch (err) {
      // Trùng giờ là chuyện thường khi vừa đặt qua điện thoại vừa có khách
      // đặt qua app. Nạp lại giờ trống thay vì chỉ báo lỗi rồi để đó.
      setError(err instanceof Error ? err.message : "Đặt không được ạ.");
      const res = await api.get<{ slots: string[] }>(`/api/v1/appointments/free-slots/${day}`);
      setSlots(res.slots);
      setChosen(null);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="screen">
      <Link to="/chu-tiem/khach" style={{ fontSize: 19 }}>
        ← Về danh sách khách
      </Link>
      <h1 style={{ fontSize: 26, margin: "var(--s3) 0 var(--s4)" }}>
        Đặt lịch hộ {customer?.full_name ?? "khách"}
      </h1>

      {error && <p role="alert" className="alert alert--danger">{error}</p>}

      <Field label="Ngày" type="date" value={day} onChange={(e) => setDay(e.target.value)} />

      <p style={{ fontSize: 20, marginBottom: "var(--s2)" }}>Giờ còn trống:</p>
      {slots.length === 0 && <p style={{ fontSize: 19 }}>Ngày này hết chỗ rồi ạ.</p>}

      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr 1fr", gap: "var(--s2)" }}>
        {slots.map((slot) => (
          <Button
            key={slot}
            variant={chosen === slot ? "primary" : "ghost"}
            fullWidth={false}
            onClick={() => setChosen(slot)}
          >
            {formatViTime(slot)}
          </Button>
        ))}
      </div>

      <div style={{ marginTop: "var(--s4)" }}>
        <Field label="Làm gì" value={note} onChange={(e) => setNote(e.target.value)} />
      </div>

      {chosen && <p style={{ fontSize: 20 }}>Sẽ đặt: {formatViDateTime(chosen)}</p>}

      <Button loading={busy} disabled={!chosen} onClick={book}>
        Đặt lịch
      </Button>
    </div>
  );
}
```

- [ ] **Step 10: Viết test giờ mở cửa (sẽ fail)**

Tạo `frontend/src/screens/ShopHoursScreen.test.tsx`:

```tsx
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ShopHoursScreen } from "./ShopHoursScreen";

const hours = { open_time: "08:00", close_time: "19:00", closed_days: [6] };

function mockApi(onPut?: (body: unknown) => void) {
  vi.stubGlobal(
    "fetch",
    vi.fn().mockImplementation(async (_url: string, init?: { method?: string; body?: string }) => {
      if (init?.method === "PUT") {
        onPut?.(JSON.parse(init.body ?? "{}"));
        return { ok: true, status: 200, json: async () => JSON.parse(init.body ?? "{}") };
      }
      return { ok: true, status: 200, json: async () => hours };
    }),
  );
}

afterEach(() => vi.unstubAllGlobals());

const renderScreen = () =>
  render(
    <MemoryRouter>
      <ShopHoursScreen />
    </MemoryRouter>,
  );

describe("ShopHoursScreen", () => {
  it("nạp giờ hiện tại vào ô", async () => {
    mockApi();
    renderScreen();
    expect(await screen.findByLabelText(/mở cửa/i)).toHaveValue("08:00");
    expect(screen.getByLabelText(/đóng cửa/i)).toHaveValue("19:00");
  });

  it("ngày nghỉ đang bật hiện đúng theo dữ liệu", async () => {
    mockApi();
    renderScreen();
    expect(await screen.findByRole("checkbox", { name: /chủ nhật/i })).toBeChecked();
  });

  it("thứ Hai là 0 và Chủ Nhật là 6 — sai chỗ này là nghỉ nhầm ngày", async () => {
    const seen: unknown[] = [];
    mockApi((body) => seen.push(body));
    renderScreen();

    await userEvent.click(await screen.findByRole("checkbox", { name: /thứ hai/i }));
    await userEvent.click(screen.getByRole("button", { name: /lưu/i }));

    await waitFor(() => expect(seen).toHaveLength(1));
    expect((seen[0] as { closed_days: number[] }).closed_days.sort()).toEqual([0, 6]);
  });

  it("đóng cửa sớm hơn mở cửa thì chặn ngay, không gọi API", async () => {
    const seen: unknown[] = [];
    mockApi((body) => seen.push(body));
    renderScreen();

    const close = await screen.findByLabelText(/đóng cửa/i);
    await userEvent.clear(close);
    await userEvent.type(close, "07:00");
    await userEvent.click(screen.getByRole("button", { name: /lưu/i }));

    expect(await screen.findByRole("alert")).toBeInTheDocument();
    expect(seen).toHaveLength(0);
  });
});
```

- [ ] **Step 11: Chạy test để xác nhận fail**

Run: `cd frontend && npm test`
Expected: FAIL — không tìm thấy `./ShopHoursScreen`

- [ ] **Step 12: Viết `frontend/src/screens/ShopHoursScreen.tsx`**

```tsx
import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { Button } from "../components/Button";
import { Field } from "../components/Field";
import { api } from "../lib/api";
import type { ShopHours } from "../lib/api";

// Thứ tự khớp Python `date.weekday()`: Thứ Hai là 0, Chủ Nhật là 6.
// Đảo nhầm chỗ này thì tiệm nghỉ sai ngày và khách đến đóng cửa.
const DAYS = ["Thứ Hai", "Thứ Ba", "Thứ Tư", "Thứ Năm", "Thứ Sáu", "Thứ Bảy", "Chủ Nhật"];

export function ShopHoursScreen() {
  const [hours, setHours] = useState<ShopHours | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    api
      .get<ShopHours>("/api/v1/shop/hours")
      .then(setHours)
      .catch((err) => setError(err instanceof Error ? err.message : "Không xem được giờ ạ."));
  }, []);

  function toggleDay(index: number) {
    if (!hours) return;
    const closed = new Set(hours.closed_days);
    closed.has(index) ? closed.delete(index) : closed.add(index);
    setHours({ ...hours, closed_days: [...closed].sort() });
  }

  async function save() {
    if (!hours) return;
    if (hours.close_time <= hours.open_time) {
      // So sánh chuỗi "HH:MM" là đủ vì cả hai luôn có hai chữ số.
      setError("Giờ đóng cửa phải sau giờ mở cửa ạ.");
      return;
    }
    setBusy(true);
    setError(null);
    try {
      setHours(await api.put<ShopHours>("/api/v1/shop/hours", hours));
      setSaved(true);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Lưu không được ạ.");
    } finally {
      setBusy(false);
    }
  }

  if (!hours) {
    return (
      <div className="screen">
        {error ? <p role="alert" className="alert alert--danger">{error}</p> : <p>Đang xem…</p>}
      </div>
    );
  }

  return (
    <div className="screen">
      <Link to="/chu-tiem" style={{ fontSize: 19 }}>
        ← Về bảng điều khiển
      </Link>
      <h1 style={{ fontSize: 28, margin: "var(--s3) 0 var(--s4)" }}>Giờ mở cửa</h1>

      {error && <p role="alert" className="alert alert--danger">{error}</p>}
      {saved && <p className="alert alert--info">Đã lưu ạ.</p>}

      <Field
        label="Mở cửa"
        type="time"
        value={hours.open_time}
        onChange={(e) => setHours({ ...hours, open_time: e.target.value })}
      />
      <Field
        label="Đóng cửa"
        type="time"
        value={hours.close_time}
        onChange={(e) => setHours({ ...hours, close_time: e.target.value })}
      />

      <p style={{ fontSize: 20, fontWeight: 600, marginBottom: "var(--s2)" }}>Ngày nghỉ</p>
      {DAYS.map((label, index) => (
        <label
          key={label}
          style={{
            display: "flex",
            alignItems: "center",
            gap: "var(--s2)",
            minHeight: "var(--tap)",
            fontSize: 20,
          }}
        >
          <input
            type="checkbox"
            style={{ width: 26, height: 26 }}
            checked={hours.closed_days.includes(index)}
            onChange={() => toggleDay(index)}
          />
          {label}
        </label>
      ))}

      <div style={{ marginTop: "var(--s4)" }}>
        <Button loading={busy} onClick={save}>
          Lưu
        </Button>
      </div>
    </div>
  );
}
```

- [ ] **Step 13: Nối route**

Trong `frontend/src/App.tsx`:

```tsx
import { BookForCustomer } from "./screens/BookForCustomer";
import { CustomersScreen } from "./screens/CustomersScreen";
import { ShopHoursScreen } from "./screens/ShopHoursScreen";
```

```tsx
          <Route
            path="/chu-tiem/khach"
            element={
              <RequireAuth role="admin">
                <CustomersScreen />
              </RequireAuth>
            }
          />
          <Route
            path="/chu-tiem/khach/:userId/dat-lich"
            element={
              <RequireAuth role="admin">
                <BookForCustomer />
              </RequireAuth>
            }
          />
          <Route
            path="/chu-tiem/gio-mo-cua"
            element={
              <RequireAuth role="admin">
                <ShopHoursScreen />
              </RequireAuth>
            }
          />
```

- [ ] **Step 14: Chạy toàn bộ test**

Run: `cd frontend && npm test` và `pytest -v`
Expected: PASS cả hai — frontend 58 passed

- [ ] **Step 15: Kiểm tay**

Đăng nhập bằng tài khoản admin, tạo một khách mới, rồi đặt lịch hộ khách đó. Đăng nhập bằng chính tài khoản khách vừa tạo → vào "Lịch của tôi" phải **thấy** lịch đó. Sau đó thử gọi thẳng API bằng token của khách với `for_user_id` của người khác — phải nhận 403.

- [ ] **Step 16: Commit**

```bash
git add -A
git commit -m "feat: admin customer list, booking on behalf and shop hours editor"
```
