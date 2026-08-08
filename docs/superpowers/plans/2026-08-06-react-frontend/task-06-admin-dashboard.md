# Task 6 · Bảng điều khiển chủ tiệm

> Thuộc plan [Frontend React](README.md). **Đọc [Ràng buộc toàn cục](README.md#ràng-buộc-toàn-cục) trước khi bắt đầu** — chúng áp cho mọi task, kể cả khi không nhắc lại ở đây.

**Files:**
- Create: `frontend/src/hooks/useAdminFeed.ts`, `frontend/src/components/BusySwitch.tsx`, `frontend/src/components/BusySwitch.css`, `frontend/src/screens/AdminDashboard.tsx`, `frontend/src/screens/AdminDashboard.test.tsx`
- Modify: `frontend/src/App.tsx`

**Interfaces:**
- Consumes: `api`, `ShopStatus`, `AppointmentResponse` (task 2), `useShopStatus` (task 3), `AppointmentCard` (task 5)
- Produces:
  - `useAdminFeed(day) -> { appointments, newIds, error, reload }` — nghe `appointment_created`
  - `<BusySwitch status onApplied />`
  - `<AdminDashboard />` tại route `/chu-tiem`

- [ ] **Step 1: Viết test (sẽ fail)**

Tạo `frontend/src/screens/AdminDashboard.test.tsx`:

```tsx
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { AdminDashboard } from "./AdminDashboard";

const shop = {
  status: { is_busy: false, busy_until: null, minutes_left: null } as {
    is_busy: boolean;
    busy_until: string | null;
    minutes_left: number | null;
  },
  loading: false,
  apply: vi.fn(),
};
const feed = {
  appointments: [] as unknown[],
  newIds: new Set<string>(),
  error: null as string | null,
  reload: vi.fn(),
};

vi.mock("../hooks/useShopStatus", () => ({ useShopStatus: () => shop }));
vi.mock("../hooks/useAdminFeed", () => ({ useAdminFeed: () => feed }));
vi.mock("../auth/AuthContext", () => ({
  useAuth: () => ({ role: "admin", fullName: "Chủ tiệm", logout: vi.fn() }),
}));

const appointment = {
  id: "a1",
  start_at: "2026-08-07T08:00:00Z",
  duration_minutes: 60,
  note: "làm tóc",
  status: "booked",
  user_name: "Cô Lan",
  phone: "0912345678",
};

function mockFetch() {
  const fn = vi.fn().mockResolvedValue({
    ok: true,
    status: 200,
    json: async () => ({ is_busy: true, busy_until: null, minutes_left: 30 }),
  });
  vi.stubGlobal("fetch", fn);
  return fn;
}

beforeEach(() => {
  shop.status = { is_busy: false, busy_until: null, minutes_left: null };
  shop.apply.mockClear();
  feed.appointments = [];
  feed.newIds = new Set();
  feed.error = null;
});
afterEach(() => vi.unstubAllGlobals());

const renderScreen = () =>
  render(
    <MemoryRouter>
      <AdminDashboard />
    </MemoryRouter>,
  );

describe("AdminDashboard", () => {
  it("đang rảnh thì nút chính là 'Tôi đang bận'", () => {
    renderScreen();
    expect(screen.getByRole("button", { name: /tôi đang bận/i })).toBeInTheDocument();
  });

  it("bấm 'Tôi đang bận' hiện đúng bốn lựa chọn thời lượng", async () => {
    renderScreen();
    await userEvent.click(screen.getByRole("button", { name: /tôi đang bận/i }));

    for (const label of ["15 phút", "30 phút", "1 tiếng", "2 tiếng"]) {
      expect(screen.getByRole("button", { name: label })).toBeInTheDocument();
    }
  });

  it("chọn 1 tiếng gửi đúng 60 phút, không phải 1", async () => {
    const fetchMock = mockFetch();
    renderScreen();

    await userEvent.click(screen.getByRole("button", { name: /tôi đang bận/i }));
    await userEvent.click(screen.getByRole("button", { name: "1 tiếng" }));

    await waitFor(() => expect(fetchMock).toHaveBeenCalled());
    const call = fetchMock.mock.calls.at(-1);
    expect(call[0]).toContain("/api/v1/shop/busy");
    expect(JSON.parse(call[1].body)).toEqual({ minutes: 60 });
  });

  it("KHÔNG chờ broadcast: áp ngay trạng thái server trả về", async () => {
    // Nếu chỉ nghe `shop_status_changed`, mà bên phát nằm ở Plan 3, thì bấm
    // xong màn hình đứng im — chủ tiệm tưởng máy hỏng và bấm lại nhiều lần.
    mockFetch();
    renderScreen();

    await userEvent.click(screen.getByRole("button", { name: /tôi đang bận/i }));
    await userEvent.click(screen.getByRole("button", { name: "30 phút" }));

    await waitFor(() =>
      expect(shop.apply).toHaveBeenCalledWith({
        is_busy: true,
        busy_until: null,
        minutes_left: 30,
      }),
    );
  });

  it("đang bận thì hiện đếm ngược BẰNG CHỮ và nút 'Tôi rảnh rồi'", () => {
    shop.status = { is_busy: true, busy_until: "2026-08-07T09:00:00Z", minutes_left: 25 };
    renderScreen();

    expect(screen.getByText(/đang bận/i)).toBeInTheDocument();
    expect(screen.getByText(/25 phút/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /tôi rảnh rồi/i })).toBeInTheDocument();
  });

  it("bấm 'Tôi rảnh rồi' gọi đúng endpoint", async () => {
    shop.status = { is_busy: true, busy_until: null, minutes_left: 25 };
    const fetchMock = mockFetch();
    renderScreen();

    await userEvent.click(screen.getByRole("button", { name: /tôi rảnh rồi/i }));
    await waitFor(() => expect(fetchMock.mock.calls.at(-1)[0]).toContain("/api/v1/shop/free"));
  });

  it("lịch hôm nay hiện tên và số điện thoại khách", () => {
    feed.appointments = [appointment];
    renderScreen();
    expect(screen.getByText(/Cô Lan/)).toBeInTheDocument();
    expect(screen.getByText(/0912345678/)).toBeInTheDocument();
  });

  it("lịch vừa được Socket.IO đẩy lên có nhãn MỚI", () => {
    feed.appointments = [appointment];
    feed.newIds = new Set(["a1"]);
    renderScreen();
    expect(screen.getByText("MỚI")).toBeInTheDocument();
  });

  it("hôm nay trống thì nói rõ, không để màn hình trắng", () => {
    renderScreen();
    expect(screen.getByText(/hôm nay chưa có lịch nào/i)).toBeInTheDocument();
  });

  it("có link sang giờ mở cửa và danh sách khách", () => {
    renderScreen();
    expect(screen.getByRole("link", { name: /khách hàng/i })).toHaveAttribute(
      "href",
      "/chu-tiem/khach",
    );
    expect(screen.getByRole("link", { name: /giờ mở cửa/i })).toHaveAttribute(
      "href",
      "/chu-tiem/gio-mo-cua",
    );
  });
});
```

- [ ] **Step 2: Chạy test để xác nhận fail**

Run: `cd frontend && npm test`
Expected: FAIL — không tìm thấy `./AdminDashboard`

- [ ] **Step 3: Viết `frontend/src/hooks/useAdminFeed.ts`**

```ts
import { useCallback, useEffect, useState } from "react";
import { api } from "../lib/api";
import type { AppointmentResponse } from "../lib/api";
import { acquireSocket, releaseSocket } from "../lib/socket";
import { useAuth } from "../auth/AuthContext";

/** Ngày dạng `YYYY-MM-DD` theo giờ tiệm, khớp route `/appointments/day/{day}`. */
export function todayInVn(): string {
  return new Intl.DateTimeFormat("en-CA", { timeZone: "Asia/Ho_Chi_Minh" }).format(new Date());
}

export function useAdminFeed(day: string = todayInVn()) {
  const { token } = useAuth();
  const [appointments, setAppointments] = useState<AppointmentResponse[]>([]);
  const [newIds, setNewIds] = useState<Set<string>>(new Set());
  const [error, setError] = useState<string | null>(null);

  const reload = useCallback(async () => {
    try {
      setAppointments(await api.get<AppointmentResponse[]>(`/api/v1/appointments/day/${day}`));
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Không xem được lịch ạ.");
    }
  }, [day]);

  useEffect(() => {
    void reload();
  }, [reload]);

  useEffect(() => {
    if (!token) return;
    const socket = acquireSocket(token);

    const onCreated = (data: AppointmentResponse) => {
      // Chỉ nhận lịch của đúng ngày đang xem — khách đặt cho tuần sau không
      // được nhảy vào danh sách hôm nay.
      const dayOfNew = new Intl.DateTimeFormat("en-CA", {
        timeZone: "Asia/Ho_Chi_Minh",
      }).format(new Date(data.start_at));
      if (dayOfNew !== day) return;

      setAppointments((prev) =>
        prev.some((a) => a.id === data.id)
          ? prev
          : [...prev, data].sort((a, b) => a.start_at.localeCompare(b.start_at)),
      );
      setNewIds((prev) => new Set(prev).add(data.id));
    };

    const onCancelled = (data: { id: string }) => {
      setAppointments((prev) => prev.filter((a) => a.id !== data.id));
    };

    socket.on("appointment_created", onCreated);
    socket.on("appointment_cancelled", onCancelled);

    // Gỡ handler rồi mới trả socket — socket dùng chung với `useShopStatus`.
    return () => {
      socket.off("appointment_created", onCreated);
      socket.off("appointment_cancelled", onCancelled);
      releaseSocket();
    };
  }, [token, day]);

  return { appointments, newIds, error, reload };
}
```

- [ ] **Step 4: Viết `frontend/src/components/BusySwitch.tsx`**

```tsx
import { useEffect, useState } from "react";
import { Button } from "./Button";
import { api } from "../lib/api";
import type { ShopStatus } from "../lib/api";
import "./BusySwitch.css";

const DURATIONS: Array<{ label: string; minutes: number }> = [
  { label: "15 phút", minutes: 15 },
  { label: "30 phút", minutes: 30 },
  { label: "1 tiếng", minutes: 60 },
  { label: "2 tiếng", minutes: 120 },
];

/** Nút khổng lồ chiếm nửa trên màn hình. Đây là thứ chủ tiệm bấm nhiều nhất
 *  trong ngày, thường bằng một tay khi tay kia đang cầm kéo. */
export function BusySwitch({
  status,
  onApplied,
}: {
  status: ShopStatus | null;
  onApplied: (next: ShopStatus) => void;
}) {
  const [picking, setPicking] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Đóng bảng chọn khi trạng thái đã đổi, kể cả khi lệnh đến từ bot Telegram.
  useEffect(() => {
    if (status?.is_busy) setPicking(false);
  }, [status?.is_busy]);

  async function call(path: string, body?: unknown) {
    setBusy(true);
    setError(null);
    try {
      // Dùng LUÔN kết quả server trả về. `POST /shop/busy` và `/shop/free` đều
      // trả `ShopStatusResponse`, nên đây không phải đoán trước — vẫn là sự
      // thật từ server, chỉ là không ngồi chờ broadcast vọng lại.
      //
      // Việc phát `shop_status_changed` nằm ở Plan 3. Nếu chỉ chờ broadcast,
      // Plan 3 chưa xong là bấm nút xong màn hình đứng im, chủ tiệm tưởng hỏng
      // rồi bấm lại mấy lần. Broadcast vẫn cần — nhưng để đồng bộ MÁY KHÁC.
      onApplied(await api.post<ShopStatus>(path, body));
      setPicking(false);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Đổi trạng thái không được ạ.");
    } finally {
      setBusy(false);
    }
  }

  if (status?.is_busy) {
    return (
      <section className="switch switch--busy">
        <p className="switch__state">Đang bận</p>
        {status.minutes_left != null && (
          <p className="switch__count">Còn khoảng {status.minutes_left} phút</p>
        )}
        {error && <p role="alert" className="alert alert--danger">{error}</p>}
        <Button variant="accent" loading={busy} onClick={() => call("/api/v1/shop/free")}>
          Tôi rảnh rồi
        </Button>
      </section>
    );
  }

  return (
    <section className="switch switch--free">
      <p className="switch__state">Đang rảnh</p>
      {error && <p role="alert" className="alert alert--danger">{error}</p>}

      {picking ? (
        <>
          <p className="switch__ask">Bận khoảng bao lâu ạ?</p>
          <div className="switch__grid">
            {DURATIONS.map((d) => (
              <Button
                key={d.minutes}
                variant="primary"
                fullWidth={false}
                loading={busy}
                onClick={() => call("/api/v1/shop/busy", { minutes: d.minutes })}
              >
                {d.label}
              </Button>
            ))}
          </div>
          <Button variant="ghost" onClick={() => setPicking(false)}>
            Thôi, để sau
          </Button>
        </>
      ) : (
        <Button variant="danger" onClick={() => setPicking(true)}>
          Tôi đang bận
        </Button>
      )}
    </section>
  );
}
```

Tạo `frontend/src/components/BusySwitch.css`:

```css
.switch {
  padding: var(--s4) var(--s3);
  border-radius: var(--r);
  border: 2px solid var(--color-border);
  background: var(--color-surface);
  margin-bottom: var(--s4);
  text-align: center;
}
.switch--busy { background: var(--color-warn-weak); border-color: #b45309; }
.switch--free { background: var(--color-accent-weak); border-color: var(--color-accent); }
.switch__state { font-size: 32px; font-weight: 700; margin-bottom: var(--s1); }
.switch__count { font-size: 21px; color: var(--color-fg-muted); margin-bottom: var(--s3); }
.switch__ask   { font-size: 20px; margin-bottom: var(--s2); }
.switch__grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: var(--s2);
  margin-bottom: var(--s2);
}
```

- [ ] **Step 5: Viết `frontend/src/screens/AdminDashboard.tsx`**

```tsx
import { Link } from "react-router-dom";
import { AppointmentCard } from "../components/AppointmentCard";
import { BusySwitch } from "../components/BusySwitch";
import { useAdminFeed } from "../hooks/useAdminFeed";
import { useShopStatus } from "../hooks/useShopStatus";
import { useAuth } from "../auth/AuthContext";

export function AdminDashboard() {
  const { status, loading, apply } = useShopStatus();
  const { appointments, newIds, error } = useAdminFeed();
  const { logout } = useAuth();

  return (
    <div className="screen">
      <h1 style={{ fontSize: 28, marginBottom: "var(--s3)" }}>Tiệm của tôi</h1>

      {loading ? <p>Đang xem…</p> : <BusySwitch status={status} onApplied={apply} />}

      <h2 style={{ fontSize: 24, marginBottom: "var(--s3)" }}>Lịch hôm nay</h2>

      {error && (
        <p role="alert" className="alert alert--danger">
          {error}
        </p>
      )}

      {appointments.length === 0 && !error && (
        <p style={{ fontSize: 20, color: "var(--color-fg-muted)" }}>Hôm nay chưa có lịch nào ạ.</p>
      )}

      {appointments.map((appointment) => (
        <AppointmentCard
          key={appointment.id}
          appointment={appointment}
          showCustomer
          isNew={newIds.has(appointment.id)}
        />
      ))}

      {/* Hai việc ít làm nên nằm dưới cùng dạng link, không chiếm chỗ của nút
          bận/rảnh — thứ chủ tiệm bấm mỗi ngày. */}
      <div style={{ marginTop: "var(--s5)", display: "grid", gap: "var(--s2)" }}>
        <Link to="/chu-tiem/khach" style={{ fontSize: 20, color: "var(--color-primary)" }}>
          Khách hàng
        </Link>
        <Link to="/chu-tiem/gio-mo-cua" style={{ fontSize: 20, color: "var(--color-primary)" }}>
          Giờ mở cửa
        </Link>
        <button
          onClick={logout}
          style={{
            background: "none",
            border: "none",
            font: "inherit",
            fontSize: 20,
            color: "var(--color-fg-muted)",
            textAlign: "left",
            minHeight: "var(--tap)",
            cursor: "pointer",
          }}
        >
          Đăng xuất
        </button>
      </div>
    </div>
  );
}
```

- [ ] **Step 6: Nối route**

Trong `frontend/src/App.tsx`:

```tsx
import { AdminDashboard } from "./screens/AdminDashboard";
```

```tsx
          <Route
            path="/chu-tiem"
            element={
              <RequireAuth role="admin">
                <AdminDashboard />
              </RequireAuth>
            }
          />
```

- [ ] **Step 7: Chạy test để xác nhận pass**

Run: `cd frontend && npm test`
Expected: PASS (49 passed) — quan trọng nhất là `chọn 1 tiếng gửi đúng 60 phút` và `KHÔNG chờ broadcast`

- [ ] **Step 8: Kiểm tay realtime hai máy**

Mở màn hình chủ tiệm ở một cửa sổ, màn hình chat của khách ở cửa sổ ẩn danh. Bấm "Tôi đang bận" → "30 phút": thẻ trạng thái bên khách phải đổi **ngay**, không tải lại. Sau đó nhắn bot Telegram "Tôi rảnh rồi": **cả hai** màn hình phải đổi về Đang rảnh. Đặt một lịch từ máy khách → thẻ có nhãn MỚI phải hiện trên màn hình chủ tiệm.

- [ ] **Step 9: Commit**

```bash
git add frontend
git commit -m "feat(fe): owner dashboard with realtime busy switch and today's schedule"
```
