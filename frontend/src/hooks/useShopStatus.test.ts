import { act, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { useShopStatus } from "./useShopStatus";

/** Socket giả theo semantics socket.io-client: off(event, handler) chỉ gỡ
 * đúng handler đó — wildcard off(event) gỡ cả tay khác đang dùng chung. */
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

const FREE = { is_busy: false, busy_until: null, minutes_left: null };
const BUSY30 = { is_busy: true, busy_until: "2026-08-07T08:30:00Z", minutes_left: 30 };

function respond(body: unknown) {
  return { ok: true, status: 200, json: async () => body };
}

/** Response giả tối giản — đủ các trường api client đọc. */
interface FakeResponse {
  ok: boolean;
  status: number;
  json: () => Promise<unknown>;
}

/** Response treo được: resolve khi test muốn, để kẹp request giữa dòng. */
function deferredResponse(): {
  promise: Promise<FakeResponse>;
  resolve: (value: FakeResponse) => void;
} {
  let resolve!: (value: FakeResponse) => void;
  const promise = new Promise<FakeResponse>((res) => {
    resolve = res;
  });
  return { promise, resolve };
}

/** GET /api/v1/shop/status theo thứ tự: từng đối số là một lần trả lời,
 * hết thì trả rảnh (fallback cho mọi lượt refetch không quan trọng). */
function mockFetch(...bodies: unknown[]) {
  const fn = vi.fn();
  for (const body of bodies) fn.mockResolvedValueOnce(respond(body));
  fn.mockResolvedValue(respond(FREE));
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
  vi.useFakeTimers({ shouldAdvanceTime: true });
});

afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

describe("useShopStatus", () => {
  it("A. initial REST: loading true → GET /api/v1/shop/status → status đúng, loading false", async () => {
    const fn = mockFetch(BUSY30);
    const { result } = renderHook(() => useShopStatus());

    // Chưa có phản hồi nào thì vẫn đang tải.
    expect(result.current.loading).toBe(true);

    await flush();
    expect(result.current.status).toEqual(BUSY30);
    expect(result.current.loading).toBe(false);
    expect(String(fn.mock.calls[0]?.[0])).toBe("/api/v1/shop/status");
  });

  it("B. initial REST lỗi: không throw, loading false, status null", async () => {
    const fn = vi.fn().mockRejectedValue(new TypeError("Failed to fetch"));
    vi.stubGlobal("fetch", fn);

    const { result } = renderHook(() => useShopStatus());
    await flush();

    expect(result.current.status).toBe(null);
    expect(result.current.loading).toBe(false);
  });

  it("C. shop_status_changed thay TOÀN BỘ status, không merge từng field", async () => {
    mockFetch(FREE);
    const { result } = renderHook(() => useShopStatus());
    await flush();
    expect(result.current.status).toEqual(FREE);

    act(() => {
      socket.fire("shop_status_changed", BUSY30);
    });
    expect(result.current.status).toEqual(BUSY30);

    act(() => {
      socket.fire("shop_status_changed", FREE);
    });
    expect(result.current.status).toEqual(FREE);
  });

  it("D. rảnh: KHÔNG hẹn giờ nào — advance bao nhiêu cũng chỉ một GET", async () => {
    const fn = mockFetch(FREE);
    const { result } = renderHook(() => useShopStatus());
    await flush();

    await act(async () => {
      vi.advanceTimersByTime(24 * 60 * 60_000);
    });
    expect(result.current.status).toEqual(FREE);
    expect(fn).toHaveBeenCalledTimes(1);
  });

  it("E. chưa tới giờ thì giữ nguyên đang bận", async () => {
    mockFetch(BUSY30);
    const { result } = renderHook(() => useShopStatus());
    await flush();
    expect(result.current.status?.is_busy).toBe(true);

    await act(async () => {
      vi.advanceTimersByTime(29 * 60_000);
    });
    expect(result.current.status?.is_busy).toBe(true);
    expect(result.current.status?.busy_until).toBe("2026-08-07T08:30:00Z");
  });

  it("F. TỚI GIỜ THÌ TỰ ĐỔI SANG RẢNH — backend không có timer nào báo", async () => {
    mockFetch(BUSY30);
    const { result } = renderHook(() => useShopStatus());
    await flush();
    expect(result.current.status?.is_busy).toBe(true);

    await act(async () => {
      vi.advanceTimersByTime(31 * 60_000);
    });
    expect(result.current.status?.is_busy).toBe(false);
    expect(result.current.status?.busy_until).toBe(null);
    expect(result.current.status?.minutes_left).toBe(null);
  });

  it("G. đồng hồ máy sai giờ không ảnh hưởng — hẹn theo khoảng, không theo mốc", async () => {
    vi.setSystemTime(new Date("2019-01-01T00:00:00Z")); // máy lệch 7 năm
    mockFetch(BUSY30);
    const { result } = renderHook(() => useShopStatus());
    await flush();
    expect(result.current.status?.is_busy).toBe(true);

    await act(async () => {
      vi.advanceTimersByTime(29 * 60_000);
    });
    expect(result.current.status?.is_busy).toBe(true); // chưa tới giờ

    await act(async () => {
      vi.advanceTimersByTime(2 * 60_000);
    });
    expect(result.current.status?.is_busy).toBe(false); // đúng 30 phút sau
  });

  it("H. bận thêm lần nữa thì HỦY hẹn giờ cũ, không lật thẻ giữa chừng", async () => {
    mockFetch(BUSY30);
    const { result } = renderHook(() => useShopStatus());
    await flush();
    expect(result.current.status?.is_busy).toBe(true);

    await act(async () => {
      vi.advanceTimersByTime(25 * 60_000);
    });

    // Chủ tiệm bấm bận thêm 60 phút nữa.
    act(() => {
      socket.fire("shop_status_changed", {
        is_busy: true,
        busy_until: "2026-08-07T09:30:00Z",
        minutes_left: 60,
      });
    });

    await act(async () => {
      vi.advanceTimersByTime(10 * 60_000); // qua mốc 30 phút của hẹn cũ
    });

    expect(result.current.status?.is_busy).toBe(true);
    expect(result.current.status?.busy_until).toBe("2026-08-07T09:30:00Z");
  });

  it("I. hết giờ refetch ra VẪN BẬN: quay lại busy và hẹn giờ mới được arm lại", async () => {
    mockFetch(BUSY30, { is_busy: true, busy_until: "2026-08-07T09:00:00Z", minutes_left: 15 });
    const { result } = renderHook(() => useShopStatus());
    await flush();
    expect(result.current.status?.is_busy).toBe(true);

    await act(async () => {
      vi.advanceTimersByTime(31 * 60_000);
    });
    // Lật free rồi refetch trả về vẫn bận 15 phút nữa.
    expect(result.current.status?.is_busy).toBe(true);
    expect(result.current.status?.busy_until).toBe("2026-08-07T09:00:00Z");

    // Hẹn giờ MỚI: thêm 16 phút nữa thì hết.
    await act(async () => {
      vi.advanceTimersByTime(16 * 60_000);
    });
    expect(result.current.status?.is_busy).toBe(false);
  });

  it("J. hết giờ refetch lỗi: giữ optimistic rảnh, không retry loop", async () => {
    const fn = vi.fn();
    fn.mockResolvedValueOnce(respond(BUSY30)).mockRejectedValue(new TypeError("Failed to fetch"));
    vi.stubGlobal("fetch", fn);
    const { result } = renderHook(() => useShopStatus());
    await flush();
    expect(result.current.status?.is_busy).toBe(true);

    await act(async () => {
      vi.advanceTimersByTime(31 * 60_000);
    });
    expect(result.current.status).toEqual(FREE);

    // Đúng MỘT refetch, và thêm thời gian cũng không tự thử lại.
    await act(async () => {
      vi.advanceTimersByTime(61 * 60_000);
    });
    expect(fn).toHaveBeenCalledTimes(2);
    expect(result.current.status).toEqual(FREE);
  });

  it("K. apply(next): áp trạng thái như socket receive VÀ arm được timer", async () => {
    mockFetch(FREE);
    const { result } = renderHook(() => useShopStatus());
    await flush();

    act(() => {
      result.current.apply({ is_busy: true, busy_until: "2026-08-07T10:00:00Z", minutes_left: 20 });
    });
    expect(result.current.status?.is_busy).toBe(true);

    await act(async () => {
      vi.advanceTimersByTime(21 * 60_000);
    });
    expect(result.current.status?.is_busy).toBe(false);
  });

  it("L. unmount: off ĐÚNG handler của mình, release đúng 1 lần, timer chết theo", async () => {
    const fn = mockFetch(BUSY30);
    const { result, unmount } = renderHook(() => useShopStatus());
    await flush();
    expect(result.current.status?.is_busy).toBe(true);

    // Tay ngoài cùng nghe sự kiện này — mô phỏng useAgentStream của màn hình chat.
    const foreign = vi.fn();
    socket.on("shop_status_changed", foreign);

    unmount();

    // Tay ngoài phải còn nguyên: off phải mang theo handler, không wildcard.
    const lastOff = socket.offs.at(-1);
    expect(lastOff?.event).toBe("shop_status_changed");
    expect(typeof lastOff?.handler).toBe("function");
    expect(socket.handlers.get("shop_status_changed")?.has(foreign)).toBe(true);
    expect(socket.handlers.get("shop_status_changed")?.size).toBe(1);
    expect(released).toBe(1);

    // Timer đã chết theo unmount: không lật thẻ, không refetch.
    await act(async () => {
      vi.advanceTimersByTime(60 * 60_000);
    });
    expect(result.current.status?.is_busy).toBe(true);
    expect(fn).toHaveBeenCalledTimes(1);
  });

  it("M. StrictMode mount→unmount→mount: một handler, event xử lý một lần, release đúng nhịp", async () => {
    // Vòng mount cũ cũng GET một lần — trả lời đủ cho cả hai vòng.
    mockFetch(BUSY30, BUSY30);
    const first = renderHook(() => useShopStatus());
    first.unmount();
    const second = renderHook(() => useShopStatus());

    await flush();
    expect(second.result.current.status).toEqual(BUSY30);
    // Chỉ MỘT handler còn sống — vòng mount cũ không để lại bản kép.
    expect(socket.handlers.get("shop_status_changed")?.size ?? 0).toBe(1);

    act(() => {
      socket.fire("shop_status_changed", FREE);
    });
    expect(second.result.current.status).toEqual(FREE);

    expect(released).toBe(1); // đúng một release cho mỗi chu kỳ mount
  });

  it("N. RACE 1: initial REST cũ không ghi đè realtime tới trong lúc GET còn chờ", async () => {
    const initial = deferredResponse();
    vi.stubGlobal("fetch", vi.fn(() => initial.promise));

    const { result } = renderHook(() => useShopStatus());
    await flush();
    expect(result.current.loading).toBe(true); // GET đang treo

    act(() => {
      socket.fire("shop_status_changed", BUSY30);
    });
    expect(result.current.status).toEqual(BUSY30);

    await act(async () => {
      initial.resolve(respond(FREE));
    });
    // Response initial đến SAU realtime thì bị bỏ, không ghi đè.
    expect(result.current.status).toEqual(BUSY30);
    expect(result.current.loading).toBe(false);
  });

  it("O. RACE 2: refetch xác nhận cũ không ghi đè realtime mới hơn; timer mới vẫn chạy", async () => {
    const BUSY60 = { is_busy: true, busy_until: "2026-08-07T09:30:00Z", minutes_left: 60 };
    const refetch = deferredResponse();
    const fn = vi.fn();
    fn.mockResolvedValueOnce(respond(BUSY30)); // initial
    fn.mockImplementationOnce(() => refetch.promise); // refetch sau expiry — treo
    fn.mockResolvedValue(respond(FREE)); // mọi lượt về sau
    vi.stubGlobal("fetch", fn);

    const { result } = renderHook(() => useShopStatus());
    await flush();
    expect(result.current.status?.is_busy).toBe(true);

    await act(async () => {
      vi.advanceTimersByTime(31 * 60_000);
    });
    expect(result.current.status).toEqual(FREE); // đã lật optimistic, refetch treo

    // Realtime mới hơn tới trong lúc refetch còn treo.
    act(() => {
      socket.fire("shop_status_changed", BUSY60);
    });
    expect(result.current.status).toEqual(BUSY60);

    await act(async () => {
      refetch.resolve(respond(FREE)); // response cũ, stale
    });
    expect(result.current.status).toEqual(BUSY60);

    // Timer của BUSY60 vẫn sống: đủ 61 phút thì lật rảnh.
    await act(async () => {
      vi.advanceTimersByTime(61 * 60_000);
    });
    expect(result.current.status?.is_busy).toBe(false);
  });

  it("P. optimistic FREE có mặt TRƯỚC khi refetch resolve; response server áp sau nếu không có gì mới hơn", async () => {
    const BUSY15 = { is_busy: true, busy_until: "2026-08-07T09:00:00Z", minutes_left: 15 };
    const refetch = deferredResponse();
    const fn = vi.fn();
    fn.mockResolvedValueOnce(respond(BUSY30));
    fn.mockImplementationOnce(() => refetch.promise);
    fn.mockResolvedValue(respond(FREE));
    vi.stubGlobal("fetch", fn);

    const { result } = renderHook(() => useShopStatus());
    await flush();
    expect(result.current.status?.is_busy).toBe(true);

    await act(async () => {
      vi.advanceTimersByTime(31 * 60_000);
    });
    // Refetch vẫn đang treo mà thẻ đã rảnh — khách không phải chờ mạng.
    expect(result.current.status).toEqual(FREE);
    expect(fn).toHaveBeenCalledTimes(2);

    await act(async () => {
      refetch.resolve(respond(BUSY15));
    });
    expect(result.current.status).toEqual(BUSY15);
  });
});
