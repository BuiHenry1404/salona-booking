import { act, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { todayInVn, useAdminFeed } from "./useAdminFeed";

/** Test implementation THẬT của useAdminFeed — chỉ mock ranh giới: socket
 * (lib/socket), auth (AuthContext) và fetch toàn cục. Pattern theo
 * useShopStatus.test.ts. */

/** Socket giả theo semantics socket.io-client: off(event, handler) chỉ gỡ
 * đúng handler đó; offs ghi lại mọi lượt off để assert cleanup. */
class FakeSocket {
  handlers = new Map<string, Set<(data?: unknown) => void>>();
  offs: Array<{ event: string; handler?: (data?: unknown) => void }> = [];

  on(event: string, handler: (data?: unknown) => void) {
    const set = this.handlers.get(event) ?? new Set();
    set.add(handler);
    this.handlers.set(event, set);
  }

  off(event: string, handler?: (data?: unknown) => void) {
    this.offs.push({ event, handler });
    if (!handler) {
      this.handlers.delete(event);
      return;
    }
    this.handlers.get(event)?.delete(handler);
  }

  fire(event: string, data?: unknown) {
    for (const h of this.handlers.get(event) ?? []) h(data);
  }
}

let socket: FakeSocket;
let released = 0;

vi.mock("../lib/socket", () => ({
  acquireSocket: () => socket,
  releaseSocket: () => {
    released += 1;
  },
}));

vi.mock("../auth/AuthContext", () => ({ useAuth: () => ({ token: "tok" }) }));

/** Lịch hẹn mẫu — đúng payload 5 field của appointment_created. start_at phải
 * rơi vào NGÀY HÔM NAY theo giờ VN (day mặc định của hook là hôm nay thật,
 * chạy vào ngày nào test cũng phải xanh). */
const APPT_A1 = {
  id: "a1",
  start_at: `${todayInVn()}T08:00:00Z`, // 8:00Z = 15:00 VN, cùng ngày
  user_name: "Cô Lan",
  phone: "0912345678",
  note: "làm tóc",
};

/** Ngày chắc chắn KHÁC hôm nay VN (30 ngày sau) cho test bỏ-sai-ngày. */
function wrongDay(): string {
  return new Intl.DateTimeFormat("en-CA", { timeZone: "Asia/Ho_Chi_Minh" }).format(
    new Date(Date.now() + 30 * 86_400_000),
  );
}

function respond(body: unknown) {
  return { ok: true, status: 200, json: async () => body };
}

/** GET /api/v1/appointments/day/{day} trả bare array; các lượt sau fallback []. */
function mockFetch(...bodies: unknown[]) {
  const fn = vi.fn();
  for (const body of bodies) fn.mockResolvedValueOnce(respond(body));
  fn.mockResolvedValue(respond([]));
  vi.stubGlobal("fetch", fn);
  return fn;
}

/** Xả hết microtask đang treo (chuỗi promise của api.get) trong act. */
async function flush() {
  await act(async () => {});
}

beforeEach(() => {
  socket = new FakeSocket();
  released = 0;
});

afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

describe("useAdminFeed", () => {
  it("1. mount: GET /api/v1/appointments/day/{day} với day YYYY-MM-DD hôm nay VN", async () => {
    const fn = mockFetch([APPT_A1]);
    const { result } = renderHook(() => useAdminFeed());

    await flush();

    const url = String(fn.mock.calls[0]?.[0]);
    const day = url.replace(/^.*\/day\//, "");
    expect(url).toContain("/api/v1/appointments/day/");
    expect(day).toMatch(/^\d{4}-\d{2}-\d{2}$/);
    expect(day).toBe(todayInVn());
    expect(result.current.appointments).toEqual([APPT_A1]);
    expect(result.current.error).toBeNull();
  });

  it("2. todayInVn: 2026-08-20T17:30:00Z (0:30 hôm sau ở VN) → 2026-08-21", () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    vi.setSystemTime(new Date("2026-08-20T17:30:00Z"));

    expect(todayInVn()).toBe("2026-08-21");
  });
});

describe("useAdminFeed — realtime", () => {
  /** Mount với initial GET đã xong, trả tiện thể đối số day mặc định. */
  async function mountFeed(initial: unknown[] = []) {
    mockFetch(initial);
    const view = renderHook(() => useAdminFeed());
    await flush();
    return view;
  }

  it("3. appointment_created ĐÚNG ngày VN: thêm vào appointments", async () => {
    const { result } = await mountFeed();

    act(() => socket.fire("appointment_created", APPT_A1));

    expect(result.current.appointments.map((a) => a.id)).toEqual(["a1"]);
  });

  it("4. appointment_created SAI ngày VN: bỏ qua, không thêm", async () => {
    const { result } = await mountFeed();

    act(() =>
      socket.fire("appointment_created", {
        ...APPT_A1,
        id: "a2",
        start_at: `${wrongDay()}T08:00:00Z`, // ngày khác ngày đang xem
      }),
    );

    expect(result.current.appointments).toEqual([]);
  });

  it("5. duplicate id: initial có a1, created lại a1 → vẫn chỉ 1 item", async () => {
    const { result } = await mountFeed([APPT_A1]);

    act(() => socket.fire("appointment_created", APPT_A1));

    expect(result.current.appointments.filter((a) => a.id === "a1")).toHaveLength(1);
  });

  it("6. created hợp lệ: id được đánh dấu vào newIds", async () => {
    const { result } = await mountFeed();

    act(() => socket.fire("appointment_created", APPT_A1));

    expect(result.current.newIds.has("a1")).toBe(true);
  });

  it("7. appointment_cancelled: bỏ khỏi appointments VÀ newIds", async () => {
    const { result } = await mountFeed();

    act(() => socket.fire("appointment_created", APPT_A1));
    expect(result.current.newIds.has("a1")).toBe(true);

    act(() => socket.fire("appointment_cancelled", { id: "a1", start_at: APPT_A1.start_at }));

    expect(result.current.appointments.map((a) => a.id)).not.toContain("a1");
    expect(result.current.newIds.has("a1")).toBe(false);
  });

  it("8. cleanup: off ĐÚNG 2 handler đã on + releaseSocket đúng 1 lần", async () => {
    const view = await mountFeed();

    // Chụp lại handler đã đăng ký TRƯỚC khi unmount (off sẽ gỡ nó khỏi map).
    const createdHandlers = [...(socket.handlers.get("appointment_created") ?? [])];
    const cancelledHandlers = [...(socket.handlers.get("appointment_cancelled") ?? [])];
    expect(createdHandlers).toHaveLength(1);
    expect(cancelledHandlers).toHaveLength(1);

    view.unmount();

    // Mỗi event: đúng MỘT lượt off, và handler truyền vào CHÍNH LÀ handler
    // đã on — không phải wildcard off(event) sẽ gỡ cả listener của hook khác.
    expect(socket.offs).toContainEqual({
      event: "appointment_created",
      handler: createdHandlers[0],
    });
    expect(socket.offs).toContainEqual({
      event: "appointment_cancelled",
      handler: cancelledHandlers[0],
    });
    expect(socket.offs.filter((o) => o.handler === undefined)).toHaveLength(0);
    // Handler thật sự bị gỡ — fire sau unmount không còn tác dụng.
    expect(socket.handlers.get("appointment_created")?.size ?? 0).toBe(0);
    expect(socket.handlers.get("appointment_cancelled")?.size ?? 0).toBe(0);
    expect(released).toBe(1);
  });
});

describe("useAdminFeed — lỗi", () => {
  it("9. GET lỗi: error là câu thân thiện, không raw lỗi kỹ thuật, appointments []", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new TypeError("Failed to fetch")));

    const { result } = renderHook(() => useAdminFeed());
    await flush();

    expect(result.current.error).toBeTruthy();
    expect(result.current.error).not.toMatch(/TypeError|Failed to fetch|internal error/i);
    expect(result.current.appointments).toEqual([]);
  });
});
