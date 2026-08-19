import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import { MyAppointmentsScreen } from "./MyAppointmentsScreen";

/**
 * Màn "Lịch của tôi" — route `/lich-cua-toi`.
 *
 * API thật (backend đã có sẵn):
 *   GET    /api/v1/appointments/mine → bare array AppointmentResponse[]
 *   DELETE /api/v1/appointments/{id} → 204, body rỗng
 * Contract từng field khớp `AppointmentResponse` trong lib/api.ts (từ
 * app/api/v1/schemas.py của backend).
 *
 * Stub `fetch` toàn cục theo key "METHOD /path" — không mock module `api`
 * để còn phủ cả đường refresh 401 / parse lỗi bên trong.
 */

/** 2026-08-21T08:00:00Z = Thứ Sáu 21/8, 3:00 chiều giờ Việt Nam — ngày tương
 * lai so với 2026-08-19, khớp ngữ cảnh `upcoming_for` của backend. */
const appointment = {
  id: "a1",
  start_at: "2026-08-21T08:00:00Z",
  duration_minutes: 60,
  note: "làm tóc",
  status: "booked",
  user_name: "Cô Lan",
  phone: "0912345678",
} as const;

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

const renderScreen = () =>
  render(
    <MemoryRouter initialEntries={["/lich-cua-toi"]}>
      <MyAppointmentsScreen />
    </MemoryRouter>,
  );

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("MyAppointmentsScreen", () => {
  it("1. có lịch: giờ dạng chữ người Việt dễ đọc, KHÔNG hiện ISO, có ghi chú", async () => {
    mockApi({ "GET /api/v1/appointments/mine": () => [appointment] });
    renderScreen();

    expect(await screen.findByText(/Thứ Sáu, 21\/8 — 3:00 chiều/)).toBeInTheDocument();
    expect(screen.getByText(/làm tóc/)).toBeInTheDocument();
    expect(screen.queryByText(/2026-08-21T/)).not.toBeInTheDocument();
  });

  it("2. chưa có lịch nào thì nói rõ việc cần làm tiếp: nhắn cho tiệm", async () => {
    mockApi({ "GET /api/v1/appointments/mine": () => [] });
    renderScreen();

    expect(await screen.findByText(/chưa có lịch nào/i)).toBeInTheDocument();
    const link = screen.getByRole("link", { name: /nhắn cho tiệm/i });
    expect(link).toHaveAttribute("href", "/");
  });

  it("3. HỦY PHẢI QUA XÁC NHẬN — bấm một lần KHÔNG gọi DELETE", async () => {
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
    expect(screen.getByText(/cô chú chắc chưa ạ\?/i)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /^hủy lịch này$/i })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /giữ lịch/i })).toBeInTheDocument();
  });

  it("4. xác nhận hủy: gọi DELETE đúng id rồi NẠP LẠI từ server, không tự cắt mảng local", async () => {
    const calls: string[] = [];
    let listed: unknown[] = [appointment];
    const fetchMock = vi.fn().mockImplementation(async (url: string, init?: { method?: string }) => {
      const key = `${init?.method ?? "GET"} ${new URL(url, "http://x").pathname}`;
      calls.push(key);
      if (key === "GET /api/v1/appointments/mine") return { ok: true, status: 200, json: async () => listed };
      if (key === "DELETE /api/v1/appointments/a1") {
        listed = []; // server thật giờ trả rỗng — UI phải thấy đúng sự thật này
        return { ok: true, status: 204, json: async () => null };
      }
      return { ok: false, status: 404, json: async () => ({}) };
    });
    vi.stubGlobal("fetch", fetchMock);

    renderScreen();
    await userEvent.click(await screen.findByRole("button", { name: /hủy lịch/i }));
    await userEvent.click(screen.getByRole("button", { name: /^hủy lịch này$/i }));

    await waitFor(() => expect(screen.queryByText(/làm tóc/)).not.toBeInTheDocument());
    expect(calls).toContain("DELETE /api/v1/appointments/a1");
    // Sau DELETE phải có GET lại — nạp từ server chứ không phải tự bỏ khỏi mảng.
    expect(calls[calls.length - 1]).toBe("GET /api/v1/appointments/mine");
  });

  it("5. đổi ý bấm 'Giữ lịch': quay về bình thường, KHÔNG gọi DELETE", async () => {
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
    await userEvent.click(screen.getByRole("button", { name: /giữ lịch/i }));

    expect(del).not.toHaveBeenCalled();
    expect(screen.getByText(/làm tóc/)).toBeInTheDocument();
    expect(screen.queryByText(/cô chú chắc chưa ạ\?/i)).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: /hủy lịch/i })).toBeInTheDocument();
  });

  it("6. API hỏng: lời xin lỗi qua role=alert, KHÔNG hiện raw lỗi kỹ thuật", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new TypeError("Failed to fetch")));
    renderScreen();

    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent(/mạng/i);
    expect(alert.textContent).not.toMatch(/failed to fetch|TypeError|error/i);
  });
});
