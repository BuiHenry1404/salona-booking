# Task 5 · Màn hình "Lịch của tôi"

> Thuộc plan [Frontend React](README.md). **Đọc [Ràng buộc toàn cục](README.md#ràng-buộc-toàn-cục) trước khi bắt đầu** — chúng áp cho mọi task, kể cả khi không nhắc lại ở đây.

**Files:**
- Create: `frontend/src/components/AppointmentCard.tsx`, `frontend/src/components/AppointmentCard.css`, `frontend/src/screens/MyAppointmentsScreen.tsx`, `frontend/src/screens/MyAppointmentsScreen.test.tsx`
- Modify: `frontend/src/App.tsx`

**Interfaces:**
- Consumes: `api`, `AppointmentResponse` (task 2), `formatViDateTime` (task 3), `Button` (task 1), `BottomNav` (task 4)
- Produces:
  - `<AppointmentCard appointment onCancel? />`
  - `<MyAppointmentsScreen />` tại route `/lich-cua-toi`

- [ ] **Step 1: Viết test màn hình (sẽ fail)**

Tạo `frontend/src/screens/MyAppointmentsScreen.test.tsx`:

```tsx
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import { MyAppointmentsScreen } from "./MyAppointmentsScreen";

vi.mock("../auth/AuthContext", () => ({
  useAuth: () => ({ role: "user", fullName: "Cô Lan", logout: vi.fn() }),
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

function mockApi(handlers: Record<string, () => unknown>) {
  vi.stubGlobal(
    "fetch",
    vi.fn().mockImplementation(async (url: string, init?: { method?: string }) => {
      const key = `${init?.method ?? "GET"} ${new URL(url, "http://x").pathname}`;
      const handler = handlers[key];
      if (!handler) return { ok: false, status: 404, json: async () => ({}) };
      const body = handler();
      return { ok: true, status: body === null ? 204 : 200, json: async () => body };
    }),
  );
}

afterEach(() => vi.unstubAllGlobals());

const renderScreen = () =>
  render(
    <MemoryRouter>
      <MyAppointmentsScreen />
    </MemoryRouter>,
  );

describe("MyAppointmentsScreen", () => {
  it("hiện lịch bằng chữ người Việt nói, không phải ISO", async () => {
    mockApi({ "GET /api/v1/appointments/mine": () => [appointment] });
    renderScreen();

    expect(await screen.findByText(/Thứ Sáu, 7\/8 — 3:00 chiều/)).toBeInTheDocument();
    expect(screen.getByText(/làm tóc/)).toBeInTheDocument();
    expect(screen.queryByText(/2026-08-07T/)).not.toBeInTheDocument();
  });

  it("chưa có lịch nào thì nói rõ phải làm gì tiếp", async () => {
    mockApi({ "GET /api/v1/appointments/mine": () => [] });
    renderScreen();

    expect(await screen.findByText(/chưa có lịch nào/i)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /nhắn cho tiệm/i })).toBeInTheDocument();
  });

  it("HỦY PHẢI QUA MỘT BƯỚC XÁC NHẬN — bấm một lần không xóa gì", async () => {
    const del = vi.fn();
    mockApi({
      "GET /api/v1/appointments/mine": () => [appointment],
      "DELETE /api/v1/appointments/a1": () => {
        del();
        return null;
      },
    });
    renderScreen();

    await userEvent.click(await screen.findByRole("button", { name: /hủy lịch/i }));
    expect(del).not.toHaveBeenCalled();
    expect(screen.getByText(/cô chú chắc chưa/i)).toBeInTheDocument();
  });

  it("xác nhận rồi thì mới gọi API và bỏ thẻ khỏi danh sách", async () => {
    let listed = [appointment];
    mockApi({
      "GET /api/v1/appointments/mine": () => listed,
      "DELETE /api/v1/appointments/a1": () => {
        listed = [];
        return null;
      },
    });
    renderScreen();

    await userEvent.click(await screen.findByRole("button", { name: /hủy lịch/i }));
    await userEvent.click(screen.getByRole("button", { name: /^hủy lịch này$/i }));

    await waitFor(() => expect(screen.queryByText(/làm tóc/)).not.toBeInTheDocument());
  });

  it("đổi ý thì quay lại được, lịch còn nguyên", async () => {
    mockApi({ "GET /api/v1/appointments/mine": () => [appointment] });
    renderScreen();

    await userEvent.click(await screen.findByRole("button", { name: /hủy lịch/i }));
    await userEvent.click(screen.getByRole("button", { name: /giữ lịch/i }));

    expect(screen.getByText(/làm tóc/)).toBeInTheDocument();
    expect(screen.queryByText(/cô chú chắc chưa/i)).not.toBeInTheDocument();
  });

  it("API hỏng thì hiện lời xin lỗi, không phải màn hình trắng", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new TypeError("Failed to fetch")));
    renderScreen();
    expect(await screen.findByRole("alert")).toHaveTextContent(/mạng/i);
  });
});
```

- [ ] **Step 2: Chạy test để xác nhận fail**

Run: `cd frontend && npm test`
Expected: FAIL — không tìm thấy `./MyAppointmentsScreen`

- [ ] **Step 3: Viết `frontend/src/components/AppointmentCard.tsx`**

```tsx
import { useState } from "react";
import { Button } from "./Button";
import { formatViDateTime } from "../lib/viDate";
import type { AppointmentResponse } from "../lib/api";
import "./AppointmentCard.css";

interface Props {
  appointment: AppointmentResponse;
  onCancel?: (id: string) => Promise<void> | void;
  /** Màn hình chủ tiệm cần thấy tên và số điện thoại khách. */
  showCustomer?: boolean;
  isNew?: boolean;
}

export function AppointmentCard({ appointment, onCancel, showCustomer, isNew }: Props) {
  const [confirming, setConfirming] = useState(false);
  const [busy, setBusy] = useState(false);

  async function confirm() {
    setBusy(true);
    try {
      await onCancel?.(appointment.id);
    } finally {
      setBusy(false);
      setConfirming(false);
    }
  }

  return (
    <article className="appt">
      {isNew && <span className="appt__new">MỚI</span>}
      <p className="appt__when">{formatViDateTime(appointment.start_at)}</p>
      {appointment.note && <p className="appt__note">{appointment.note}</p>}
      {showCustomer && (
        <p className="appt__who">
          {appointment.user_name ?? "khách"} · {appointment.phone ?? ""}
        </p>
      )}

      {onCancel &&
        // Xác nhận TẠI CHỖ, không mở modal: modal che mất thứ đang xem và người
        // lớn tuổi không rõ mình đang xác nhận hủy lịch nào.
        (confirming ? (
          <div className="appt__confirm">
            <p className="appt__ask">Cô chú chắc chưa ạ? Hủy rồi là mất chỗ này.</p>
            <div className="appt__row">
              <Button variant="danger" fullWidth={false} loading={busy} onClick={confirm}>
                Hủy lịch này
              </Button>
              <Button variant="ghost" fullWidth={false} onClick={() => setConfirming(false)}>
                Giữ lịch
              </Button>
            </div>
          </div>
        ) : (
          <Button variant="ghost" onClick={() => setConfirming(true)}>
            Hủy lịch
          </Button>
        ))}
    </article>
  );
}
```

Tạo `frontend/src/components/AppointmentCard.css`:

```css
.appt {
  position: relative;
  background: var(--color-surface);
  border: 2px solid var(--color-border);
  border-radius: var(--r);
  padding: var(--s3);
  margin-bottom: var(--s3);
}
.appt__new {
  position: absolute;
  top: var(--s2); right: var(--s2);
  background: var(--color-accent);
  color: #fff;
  font-size: 15px;
  font-weight: 700;
  letter-spacing: 0.06em;
  padding: 4px 10px;
  border-radius: 999px;
}
.appt__when { font-size: 23px; font-weight: 700; }
.appt__note { font-size: 20px; margin-top: 4px; }
.appt__who  { font-size: 18px; color: var(--color-fg-muted); margin-top: 4px; }
.appt__confirm { margin-top: var(--s3); }
.appt__ask { font-size: 19px; margin-bottom: var(--s2); }
.appt__row { display: flex; gap: var(--s2); }
.appt__row > * { flex: 1; }
.appt > .btn { margin-top: var(--s3); }
```

- [ ] **Step 4: Viết `frontend/src/screens/MyAppointmentsScreen.tsx`**

```tsx
import { useCallback, useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { AppointmentCard } from "../components/AppointmentCard";
import { BottomNav } from "../components/BottomNav";
import { api } from "../lib/api";
import type { AppointmentResponse } from "../lib/api";

export function MyAppointmentsScreen() {
  const [appointments, setAppointments] = useState<AppointmentResponse[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  const load = useCallback(async () => {
    try {
      setAppointments(await api.get<AppointmentResponse[]>("/api/v1/appointments/mine"));
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Không xem được lịch ạ.");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  async function cancel(id: string) {
    try {
      await api.del(`/api/v1/appointments/${id}`);
      // Nạp lại từ server thay vì tự bỏ khỏi mảng: nếu backend từ chối hủy
      // (lịch đã qua chẳng hạn) thì màn hình phải phản ánh đúng sự thật.
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Hủy không được ạ.");
    }
  }

  return (
    <div className="screen" style={{ paddingBottom: 96 }}>
      <h1 style={{ fontSize: 28, marginBottom: "var(--s4)" }}>Lịch của tôi</h1>

      {error && (
        <p role="alert" className="alert alert--danger">
          {error}
        </p>
      )}

      {loading && <p>Đang xem lịch…</p>}

      {!loading && appointments.length === 0 && !error && (
        <div>
          <p style={{ fontSize: 20, marginBottom: "var(--s3)" }}>
            Cô chú chưa có lịch nào ạ.
          </p>
          <Link to="/" className="btn btn--primary btn--full" style={{ textDecoration: "none" }}>
            Nhắn cho tiệm
          </Link>
        </div>
      )}

      {appointments.map((appointment) => (
        <AppointmentCard key={appointment.id} appointment={appointment} onCancel={cancel} />
      ))}

      <BottomNav />
    </div>
  );
}
```

- [ ] **Step 5: Nối route**

Trong `frontend/src/App.tsx`:

```tsx
import { MyAppointmentsScreen } from "./screens/MyAppointmentsScreen";
```

```tsx
          <Route
            path="/lich-cua-toi"
            element={
              <RequireAuth>
                <MyAppointmentsScreen />
              </RequireAuth>
            }
          />
```

- [ ] **Step 6: Chạy test để xác nhận pass**

Run: `cd frontend && npm test`
Expected: PASS (65 passed) — quan trọng nhất là `HỦY PHẢI QUA MỘT BƯỚC XÁC NHẬN`

- [ ] **Step 7: Commit**

```bash
git add frontend
git commit -m "feat(fe): my appointments list with inline cancel confirmation"
```
