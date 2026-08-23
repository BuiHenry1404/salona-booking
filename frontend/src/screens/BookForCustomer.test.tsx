import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { BookForCustomer } from "./BookForCustomer";
import { todayInVn } from "../hooks/useAdminFeed";

/**
 * Màn đặt lịch hộ khách — route `/chu-tiem/khach/:userId/dat-lich`.
 *
 * API thật (backend đã có, Task 7 đã GREEN cho_user_id):
 *   GET  /api/v1/appointments/free-slots/{day} → { slots: string[] ISO }
 *   POST /api/v1/appointments
 *     body { start_at, note, for_user_id } → 201 AppointmentResponse
 *     trùng giờ → 409 "Giờ đó đã có người đặt"
 *
 * Mount QUA Routes thật để useParams() nhận userId như production — không
 * mock react-router. Fetch stub toàn cục theo pattern các screen test khác.
 */

/** Ngày hôm nay VN + N ngày — slot mẫu luôn thuộc đúng ngày đang xem. */
function dayOffset(days: number): string {
  return new Intl.DateTimeFormat("en-CA", { timeZone: "Asia/Ho_Chi_Minh" }).format(
    new Date(Date.now() + days * 86_400_000),
  );
}
const TODAY = dayOffset(0);
const TOMORROW = dayOffset(1);

/** Hai mốc giờ trong ngày: 08:00Z = 3:00 chiều, 09:00Z = 4:00 chiều (giờ VN). */
function slotAt(day: string, hourZ: number): string {
  return `${day}T${String(hourZ).padStart(2, "0")}:00:00Z`;
}

const CUSTOMER = {
  id: "u123",
  phone: "0987654321",
  full_name: "Cô Hoa",
  role: "user" as const,
  is_active: true,
};

/** Response 201 thật theo AppointmentResponse backend. */
const CREATED = {
  id: "a1",
  start_at: slotAt(TODAY, 8),
  duration_minutes: 60,
  note: "làm nail",
  status: "booked",
  user_name: "Cô Hoa",
  phone: "0987654321",
};

interface FetchPlan {
  getSlots?: () => string[]; // mỗi lượt GET free-slots gọi một lần
  postStatus?: number; // mặc định 201
  postBody?: () => unknown;
}

function mockApi(plan: FetchPlan = {}) {
  const fn = vi.fn().mockImplementation(async (url: string, init?: { method?: string; body?: string }) => {
    const path = new URL(String(url), "http://x").pathname;
    if (init?.method === "POST" && path === "/api/v1/appointments") {
      const status = plan.postStatus ?? 201;
      return {
        ok: status < 400,
        status,
        json: async () => (status === 201 ? CREATED : { detail: "Giờ đó đã có người đặt" }),
      };
    }
    if (path.startsWith("/api/v1/appointments/free-slots/")) {
      const slots = plan.getSlots ? plan.getSlots() : [];
      return { ok: true, status: 200, json: async () => ({ slots }) };
    }
    if (path === "/api/v1/auth/users") {
      return { ok: true, status: 200, json: async () => [CUSTOMER] };
    }
    return { ok: false, status: 404, json: async () => ({}) };
  });
  vi.stubGlobal("fetch", fn);
  return fn;
}

beforeEach(() => {
  vi.useFakeTimers({ shouldAdvanceTime: true });
  vi.setSystemTime(new Date(`${TODAY}T10:00:00Z`));
});

afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

const renderAt = (userId = "u123") =>
  render(
    <MemoryRouter initialEntries={[`/chu-tiem/khach/${userId}/dat-lich`]}>
      <Routes>
        <Route path="/chu-tiem/khach/:userId/dat-lich" element={<BookForCustomer />} />
      </Routes>
    </MemoryRouter>,
  );

describe("BookForCustomer", () => {
  it("1. mount: ngày mặc định là hôm nay VN, GET free-slots đúng ngày đó", async () => {
    const fetchMock = mockApi({ getSlots: () => [slotAt(TODAY, 8)] });
    renderAt();

    const dayInput = await screen.findByLabelText(/^ngày$/i);
    expect(dayInput).toHaveValue(todayInVn());

    const slotUrls = fetchMock.mock.calls
      .map((c) => String(c[0]))
      .filter((u) => u.includes("/free-slots/"));
    expect(slotUrls[0]).toContain(`/api/v1/appointments/free-slots/${todayInVn()}`);
  });

  it("2. đổi ngày: GET free-slots cho ngày MỚI, không dùng ngày cũ", async () => {
    const fetchMock = mockApi({ getSlots: () => [] });
    renderAt();
    const dayInput = await screen.findByLabelText(/^ngày$/i);

    await userEvent.clear(dayInput);
    await userEvent.type(dayInput, TOMORROW);

    const slotUrls = fetchMock.mock.calls
      .map((c) => String(c[0]))
      .filter((u) => u.includes("/free-slots/"));
    expect(slotUrls).toContain(`/api/v1/appointments/free-slots/${TOMORROW}`);
    // Ngày cũ đã bị thay — URL cuối cùng phải là ngày mới.
    expect(slotUrls[slotUrls.length - 1]).toContain(TOMORROW);
  });

  it("3. slot hiển thị bằng giờ Việt Nam dễ đọc, KHÔNG bắt đọc ISO", async () => {
    mockApi({
      getSlots: () => [slotAt(todayInVn(), 8), slotAt(todayInVn(), 9)], // 3:00 + 4:00 chiều
    });
    renderAt();

    expect(await screen.findByRole("button", { name: "3:00 chiều" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "4:00 chiều" })).toBeInTheDocument();
    expect(screen.queryByText(/T\d{2}:\d{2}:\d{2}Z/)).not.toBeInTheDocument();
  });

  it("4. đặt lịch hộ: POST đúng body { start_at, note, for_user_id }", async () => {
    const seen: Array<{ method?: string; body: unknown }> = [];
    vi.stubGlobal(
      "fetch",
      vi.fn().mockImplementation(async (url: string, init?: { method?: string; body?: string }) => {
        const path = new URL(String(url), "http://x").pathname;
        if (init?.method === "POST" && path === "/api/v1/appointments") {
          seen.push({ method: init.method, body: JSON.parse(init.body ?? "{}") });
          return { ok: true, status: 201, json: async () => CREATED };
        }
        if (path.startsWith("/api/v1/appointments/free-slots/")) {
          return { ok: true, status: 200, json: async () => ({ slots: [slotAt(todayInVn(), 8)] }) };
        }
        if (path === "/api/v1/auth/users") {
          return { ok: true, status: 200, json: async () => [CUSTOMER] };
        }
        return { ok: false, status: 404, json: async () => ({}) };
      }),
    );

    renderAt();
    await userEvent.click(await screen.findByRole("button", { name: "3:00 chiều" }));
    await userEvent.type(screen.getByLabelText(/làm gì/i), "làm nail");
    await userEvent.click(screen.getByRole("button", { name: /^đặt lịch$/i }));

    await waitFor(() => expect(seen).toHaveLength(1));
    expect(seen[0].method).toBe("POST");
    expect(seen[0].body).toEqual({
      start_at: slotAt(todayInVn(), 8),
      note: "làm nail",
      for_user_id: "u123",
    });
  });

  it("5. POST 201: hiện 'Đã đặt lịch' bằng chữ — không chỉ đổi màu", async () => {
    mockApi({ getSlots: () => [slotAt(todayInVn(), 8)] });
    renderAt();

    await userEvent.click(await screen.findByRole("button", { name: "3:00 chiều" }));
    await userEvent.click(screen.getByRole("button", { name: /^đặt lịch$/i }));

    expect(await screen.findByText(/đã đặt lịch/i)).toBeInTheDocument();
  });

  it("6. conflict 409: lỗi thân thiện role=alert VÀ nạp lại free-slots của ngày đang xem", async () => {
    let getCalls = 0;
    const fetchMock = vi.fn().mockImplementation(async (url: string, init?: { method?: string; body?: string }) => {
      const path = new URL(String(url), "http://x").pathname;
      if (init?.method === "POST" && path === "/api/v1/appointments") {
        return {
          ok: false,
          status: 409,
          json: async () => ({ detail: "Giờ đó đã có người đặt" }),
        };
      }
      if (path.startsWith("/api/v1/appointments/free-slots/")) {
        getCalls += 1;
        // Sau xung đột, server trả danh sách MỚI: slot 3:00 chiều đã mất.
        const slots = getCalls === 1 ? [slotAt(todayInVn(), 8)] : [slotAt(todayInVn(), 10)];
        return { ok: true, status: 200, json: async () => ({ slots }) };
      }
      if (path === "/api/v1/auth/users") {
        return { ok: true, status: 200, json: async () => [CUSTOMER] };
      }
      return { ok: false, status: 404, json: async () => ({}) };
    });
    vi.stubGlobal("fetch", fetchMock);

    renderAt();
    await userEvent.click(await screen.findByRole("button", { name: "3:00 chiều" }));
    await userEvent.click(screen.getByRole("button", { name: /^đặt lịch$/i }));

    // Lỗi hiển thị thân thiện qua role=alert, không phải raw "409".
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent(/đặt|giờ/i);

    // Sau 409 phải GET lại free-slots — slot bị giành mất phải biến khỏi
    // lựa chọn theo danh sách server trả.
    await waitFor(() => expect(getCalls).toBeGreaterThanOrEqual(2));
    await waitFor(() =>
      expect(screen.queryByRole("button", { name: "3:00 chiều" })).not.toBeInTheDocument(),
    );
    expect(await screen.findByRole("button", { name: "5:00 chiều" })).toBeInTheDocument();
    const urls = fetchMock.mock.calls
      .map((c) => String(c[0]))
      .filter((u) => u.includes("/free-slots/"));
    expect(urls[urls.length - 1]).toContain(`/free-slots/${todayInVn()}`);
  });
});
