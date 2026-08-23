import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { api } from "./api";
import { session } from "../auth/session";

// ioMock phải qua vi.hoisted: vi.mock bị nâng lên trước mọi import.
const { ioMock } = vi.hoisted(() => ({ ioMock: vi.fn() }));

vi.mock("socket.io-client", () => ({ io: ioMock }));

type AuthFn = (cb: (payload: { token: string | null }) => void) => void;
type Handler = (data?: unknown) => void;

class FakeSocket {
  static created = 0;
  id: number;
  ioOptions: Record<string, unknown>;
  auth: AuthFn;
  connected = false;
  active = true; // socket.io: true khi còn hội tụ reconnect
  handlers = new Map<string, Handler[]>();
  constructor(opts: Record<string, unknown>) {
    FakeSocket.created += 1;
    this.id = FakeSocket.created;
    this.ioOptions = opts;
    this.auth = opts.auth as AuthFn;
  }
  on(event: string, handler: Handler) {
    this.handlers.set(event, [...(this.handlers.get(event) ?? []), handler]);
  }
  /** Có handler → chỉ gỡ đúng handler đó (semantics của socket.io-client);
   * không có → xóa cả event (wildcard, consumer khác phải tránh). */
  off(event: string, handler?: Handler) {
    if (handler) {
      const list = this.handlers.get(event) ?? [];
      const i = list.indexOf(handler);
      if (i >= 0) list.splice(i, 1);
      if (list.length === 0) this.handlers.delete(event);
    } else {
      this.handlers.delete(event);
    }
  }
  connect = vi.fn(() => {
    this.connected = true;
  });
  disconnect = vi.fn(() => {
    this.connected = false;
  });
  fire(event: string, data?: unknown) {
    for (const h of this.handlers.get(event) ?? []) h(data);
  }
}

// Wrapper đếm tham chiếu: afterEach release hết về 0 thì shared bị bỏ, test
// sau bắt đầu từ singleton sạch — không cần resetModules (reset sẽ tạo bản sao
// api/session riêng cho socket.ts và spy ở đây điều khiển nhầm bản khác).
import { acquireSocket as rawAcquire, releaseSocket as rawRelease } from "./socket";

let outstanding = 0;
const acquire = () => {
  outstanding += 1;
  return rawAcquire() as unknown as FakeSocket;
};
const release = () => {
  outstanding -= 1;
  rawRelease();
};

const meSpy = vi.spyOn(api, "get");

beforeEach(() => {
  FakeSocket.created = 0;
  ioMock.mockReset();
  ioMock.mockImplementation((_url: string, opts: Record<string, unknown>) =>
    new FakeSocket(opts),
  );
  meSpy.mockReset();
  session.clear();
});

afterEach(() => {
  while (outstanding > 0) release();
  outstanding = 0;
  session.clear();
});

describe("socket singleton", () => {
  it("A. acquire 2 lần → io() gọi 1 lần, cùng instance", () => {
    const a = acquire();
    const b = acquire();
    expect(ioMock).toHaveBeenCalledTimes(1);
    expect(a).toBe(b);
  });

  it("B. release 1 consumer → chưa disconnect", () => {
    acquire();
    const s = acquire();
    release();
    expect(s.disconnect).not.toHaveBeenCalled();
  });

  it("C. release consumer cuối → disconnect đúng 1 lần", () => {
    acquire();
    const s = acquire();
    release();
    release();
    expect(s.disconnect).toHaveBeenCalledTimes(1);
  });

  it("D. release hết rồi acquire lại → socket instance mới", () => {
    acquire();
    release();
    const second = acquire();
    expect(ioMock).toHaveBeenCalledTimes(2);
    expect(second).not.toBe(undefined);
    release();
  });

  it("io() nhận đúng options kết nối theo plan", () => {
    acquire();
    const opts = ioMock.mock.calls[0][1];
    expect(opts.transports).toEqual(["websocket", "polling"]);
    expect(opts.reconnection).toBe(true);
    expect(opts.reconnectionDelay).toBe(1000);
    expect(opts.reconnectionDelayMax).toBe(5000);
  });
});

describe("socket auth động", () => {
  it("E. auth là FUNCTION; tokcu → tokmoi mà không recreate socket", () => {
    session.save("tokcu", "user");
    const s = acquire();

    expect(typeof s.ioOptions.auth).toBe("function");

    let payload: { token: string | null } | undefined;
    s.auth((p) => (payload = p));
    expect(payload!.token).toBe("tokcu");

    // Token được xoay giữa chừng (api.ts refresh → session.save).
    session.save("tokmoi", "user");
    s.auth((p) => (payload = p));
    expect(payload!.token).toBe("tokmoi");

    expect(ioMock).toHaveBeenCalledTimes(1); // không recreate
    release();
  });
});

describe("connect_error auth recovery", () => {
  it("F. socket.active === true → không can thiệp (Socket.IO tự lo)", () => {
    const s = acquire();
    s.active = true;
    s.fire("connect_error", new Error("xhr poll error"));
    expect(meSpy).not.toHaveBeenCalled();
    expect(s.connect).not.toHaveBeenCalled();
    release();
  });

  it("G. socket.active === false → /auth/me rồi socket.connect() đúng 1 lần", async () => {
    session.save("tokcu", "user");
    meSpy.mockResolvedValue({ id: "u1" });
    const s = acquire();

    s.active = false;
    s.fire("connect_error", new Error("Connection rejected"));
    await vi.waitFor(() => expect(s.connect).toHaveBeenCalledTimes(1));

    expect(meSpy).toHaveBeenCalledTimes(1);
    expect(String(meSpy.mock.calls[0][0])).toBe("/api/v1/auth/me");
    release();
  });

  it("H. recovery fail → không connect, không loop", async () => {
    session.save("tokcu", "user");
    meSpy.mockRejectedValue(new Error("hết phiên"));
    const s = acquire();

    s.active = false;
    s.fire("connect_error", new Error("Connection rejected"));
    await vi.waitFor(() => expect(meSpy).toHaveBeenCalledTimes(1));

    // Cho thêm một khoảng để chắc không có vòng lặp thử lại.
    await new Promise((r) => setTimeout(r, 30));
    expect(meSpy).toHaveBeenCalledTimes(1);
    expect(s.connect).not.toHaveBeenCalled();
    release();
  });

  it("I. nhiều connect_error dồn dập → chỉ MỘT recovery in-flight", async () => {
    session.save("tokcu", "user");
    let resolveMe: (v: unknown) => void = () => {};
    meSpy.mockReturnValue(
      new Promise((res) => {
        resolveMe = res;
      }),
    );
    const s = acquire();

    s.active = false;
    s.fire("connect_error", new Error("rejected"));
    s.fire("connect_error", new Error("rejected"));
    s.fire("connect_error", new Error("rejected"));

    expect(meSpy).toHaveBeenCalledTimes(1); // 2 lỗi sau chờ, không bắn thêm

    resolveMe({ id: "u1" });
    await vi.waitFor(() => expect(s.connect).toHaveBeenCalledTimes(1));
    expect(meSpy).toHaveBeenCalledTimes(1);
    release();
  });

  it("listener recovery được gỡ khi socket bị release về 0 — socket mới không kế thừa", async () => {
    session.save("tokcu", "user");
    meSpy.mockResolvedValue({ id: "u1" });
    const old = acquire();
    release(); // disconnect + dọn listener recovery

    // Socket mới bắn connect_error của riêng nó → không đụng recovery của cũ.
    const fresh = acquire();
    expect(fresh).not.toBe(old);
    fresh.active = false;
    fresh.fire("connect_error", new Error("rejected"));
    await vi.waitFor(() => expect(meSpy).toHaveBeenCalledTimes(1));
    release();
  });

  it("REGRESSION: OLD recovery pending → release OLD → NEW phải recovery RIÊNG, không bị chặn", async () => {
    session.save("tokcu", "user");

    // OLD bắt đầu recovery, /auth/me lần 1 treo pending.
    let resolveOld: (v: unknown) => void = () => {};
    meSpy.mockImplementationOnce(
      () =>
        new Promise((res) => {
          resolveOld = res;
        }),
    );
    const old = acquire();
    old.active = false;
    old.fire("connect_error", new Error("rejected"));
    await vi.waitFor(() => expect(meSpy).toHaveBeenCalledTimes(1));

    // OLD bị release hoàn toàn giữa lúc recovery còn pending.
    release();

    // NEW xuất hiện và cũng bị từ chối connection.
    meSpy.mockResolvedValueOnce({ id: "u1" });
    const fresh = acquire();
    fresh.active = false;
    fresh.fire("connect_error", new Error("rejected"));

    // NEW phải bắt đầu recovery RIÊNG dù recovery của OLD còn treo.
    await vi.waitFor(() => expect(meSpy).toHaveBeenCalledTimes(2));
    await vi.waitFor(() => expect(fresh.connect).toHaveBeenCalledTimes(1));

    // Recovery của OLD khép lại muộn: KHÔNG được connect OLD, không thêm lần
    // connect nào của NEW, không phá state của recovery vừa xong.
    resolveOld({ id: "u1" });
    await new Promise((r) => setTimeout(r, 30));
    expect(old.connect).not.toHaveBeenCalled();
    expect(fresh.connect).toHaveBeenCalledTimes(1);
    release();
  });
});
