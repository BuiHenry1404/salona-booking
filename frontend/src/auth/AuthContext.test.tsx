/// <reference types="node" />
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { act, render, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { AuthProvider, useAuth } from "./AuthContext";
import { api } from "../lib/api";
import { session } from "./session";

function mockFetchSequence(steps: Array<{ status: number; body?: unknown }>) {
  let i = 0;
  const fn = vi.fn().mockImplementation(async () => {
    const step = steps[Math.min(i, steps.length - 1)];
    i += 1;
    return {
      ok: step.status < 400,
      status: step.status,
      json: async () => step.body,
    };
  });
  vi.stubGlobal("fetch", fn);
  return fn;
}

const ME = {
  id: "u1",
  phone: "0912345678",
  full_name: "Cô Lan",
  role: "user",
  is_active: true,
};

/** Lấy giá trị context từ bên trong provider. */
let ctx: ReturnType<typeof useAuth> | null = null;
function Probe() {
  ctx = useAuth();
  return null;
}

function renderProvider(children?: ReactNode) {
  return render(<AuthProvider>{children ?? <Probe />}</AuthProvider>);
}

beforeEach(() => {
  session.clear();
  ctx = null;
});
afterEach(() => vi.unstubAllGlobals());

describe("AuthContext bootstrap", () => {
  it("có token trong memory → /auth/me thành công: authenticated + fullName + role", async () => {
    session.save("tok", "user");
    const fetchMock = mockFetchSequence([{ status: 200, body: ME }]);

    renderProvider();

    await waitFor(() => {
      expect(ctx?.loading).toBe(false);
      expect(ctx?.token).toBe("tok");
      expect(ctx?.role).toBe("user");
      expect(ctx?.fullName).toBe("Cô Lan");
    });
    expect(String(fetchMock.mock.calls[0][0])).toContain("/api/v1/auth/me");
  });

  it("không có token trong memory → thử refresh bằng cookie; cookie chết → anonymous, không gọi /me", async () => {
    const fetchMock = mockFetchSequence([{ status: 401, body: {} }]);

    renderProvider();

    await waitFor(() => expect(ctx?.loading).toBe(false));
    expect(ctx?.token).toBeNull();
    expect(ctx?.role).toBeNull();
    expect(String(fetchMock.mock.calls[0][0])).toContain("/api/v1/auth/refresh");
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it("reload mất memory token nhưng cookie còn → refresh được rồi /me: vẫn đăng nhập", async () => {
    mockFetchSequence([
      { status: 200, body: { access_token: "tokmoi", token_type: "bearer", role: "user" } },
      { status: 200, body: ME },
    ]);

    renderProvider();

    await waitFor(() => {
      expect(ctx?.loading).toBe(false);
      expect(ctx?.token).toBe("tokmoi");
      expect(ctx?.role).toBe("user");
      expect(ctx?.fullName).toBe("Cô Lan");
    });
    expect(session.load().token).toBe("tokmoi");
  });

  it("hết phiên thật (refresh cũng chết) → anonymous, loading kết thúc", async () => {
    session.save("tokcu", "user");
    // /me 401 → api client refresh → refresh 401 → clear session → /me vẫn ném 401.
    mockFetchSequence([
      { status: 401, body: {} },
      { status: 401, body: {} },
      { status: 401, body: {} },
    ]);

    renderProvider();

    await waitFor(() => {
      expect(ctx?.loading).toBe(false);
      expect(ctx?.token).toBeNull();
      expect(ctx?.role).toBeNull();
    });
    expect(session.load().token).toBeNull();
  });
});

describe("AuthContext login/logout", () => {
  it("login thành công → session có token/role, context cập nhật, trả đúng role", async () => {
    mockFetchSequence([
      // Bootstrap (memory rỗng): refresh bằng cookie rồi /me.
      { status: 200, body: { access_token: "tokcu", token_type: "bearer", role: "user" } },
      { status: 200, body: ME },
      // Login thật + /me sau login.
      { status: 200, body: { access_token: "tokmoi", token_type: "bearer", role: "admin" } },
      { status: 200, body: { ...ME, role: "admin", full_name: "Chủ tiệm" } },
    ]);

    renderProvider();
    await waitFor(() => expect(ctx?.loading).toBe(false));

    let returned: string | undefined;
    await act(async () => {
      returned = await ctx!.login("0901234567", "matkhau123");
    });

    expect(returned).toBe("admin");
    expect(session.load().token).toBe("tokmoi");
    expect(session.load().role).toBe("admin");
    expect(ctx?.token).toBe("tokmoi");
    expect(ctx?.role).toBe("admin");
    expect(ctx?.fullName).toBe("Chủ tiệm");
  });

  it("login sai → ném lỗi của backend, context giữ anonymous", async () => {
    mockFetchSequence([
      { status: 401, body: { detail: "Số điện thoại hoặc mật khẩu không đúng" } },
    ]);

    renderProvider();
    await waitFor(() => expect(ctx?.loading).toBe(false));

    await act(async () => {
      await expect(ctx!.login("0901234567", "sai")).rejects.toThrow(
        "Số điện thoại hoặc mật khẩu không đúng",
      );
    });

    expect(ctx?.token).toBeNull();
    expect(session.load().token).toBeNull();
  });

  it("logout gọi /auth/logout rồi clear session/state", async () => {
    session.save("tok", "user");
    mockFetchSequence([
      { status: 200, body: ME }, // bootstrap /me
      { status: 204 }, // logout
    ]);

    renderProvider();
    await waitFor(() => expect(ctx?.token).toBe("tok"));

    await act(async () => {
      await ctx!.logout();
    });

    expect(ctx?.token).toBeNull();
    expect(ctx?.role).toBeNull();
    expect(ctx?.fullName).toBeNull();
    expect(session.load().token).toBeNull();
  });

  it("logout API chết vẫn clear session/state (finally)", async () => {
    session.save("tok", "user");
    mockFetchSequence([
      { status: 200, body: ME },
      { status: 500, body: { detail: "server chết" } },
    ]);

    renderProvider();
    await waitFor(() => expect(ctx?.token).toBe("tok"));

    await act(async () => {
      await ctx!.logout();
    });

    expect(ctx?.token).toBeNull();
    expect(session.load().token).toBeNull();
  });
});

describe("AuthContext storage guard", () => {
  it("KHÔNG dùng localStorage/sessionStorage", () => {
    const read = (rel: string) => readFileSync(resolve(process.cwd(), rel), "utf8");
    const source = read("src/auth/AuthContext.tsx");
    expect(source).not.toContain("localStorage");
    expect(source).not.toContain("sessionStorage");
  });
});

describe("AuthContext đồng bộ token với session (stale token)", () => {
  it("LUỒNG THẬT: api request 401 → refresh token mới → context tự thấy, không remount", async () => {
    session.save("tokcu", "user");
    // Bootstrap /me thành công với tokcu.
    mockFetchSequence([
      { status: 200, body: ME },
      // Sau đó một API request bất kỳ: gốc 401 → refresh → retry.
      { status: 401, body: {} },
      { status: 200, body: { access_token: "tokmoi", token_type: "bearer", role: "user" } },
      { status: 200, body: [] },
    ]);

    renderProvider();
    await waitFor(() => expect(ctx?.token).toBe("tokcu"));

    // Request ngoài context — như useShopStatus của task 3 sẽ gọi.
    await act(async () => {
      await api.get("/api/v1/appointments/mine");
    });

    expect(session.load().token).toBe("tokmoi");
    // KHÔNG remount provider — context phải tự cập nhật theo session.
    expect(ctx?.token).toBe("tokmoi");
    expect(ctx?.role).toBe("user");
  });

  it("session.clear() bên ngoài context → context tự về anonymous", async () => {
    session.save("tok", "user");
    mockFetchSequence([{ status: 200, body: ME }]);

    renderProvider();
    await waitFor(() => expect(ctx?.token).toBe("tok"));

    await act(async () => {
      session.clear();
    });

    expect(ctx?.token).toBeNull();
    expect(ctx?.role).toBeNull();
  });

  it("session.save() bên ngoài context → context thấy token/role mới", async () => {
    session.save("tok", "user");
    mockFetchSequence([{ status: 200, body: ME }]);

    renderProvider();
    await waitFor(() => expect(ctx?.token).toBe("tok"));

    await act(async () => {
      session.save("tokkhac", "admin");
    });

    expect(ctx?.token).toBe("tokkhac");
    expect(ctx?.role).toBe("admin");
  });

  it("unmount provider không rò listener — save sau unmount không ném", async () => {
    session.save("tok", "user");
    mockFetchSequence([{ status: 200, body: ME }]);

    const { unmount } = renderProvider();
    await waitFor(() => expect(ctx?.token).toBe("tok"));
    unmount();

    expect(() => session.save("tokmoi", "user")).not.toThrow();
    expect(() => session.clear()).not.toThrow();
  });

  it("cleanup subscription thật sự: subscribe khi mount, unsubscribe khi unmount", async () => {
    // Giữ hành vi thật: wrap unsubscribe mà subscribe() trả về bằng spy có
    // đếm, phần còn lại gọi nguyên bản — provider vẫn chạy cơ chế production.
    const realSubscribe = session.subscribe.bind(session);
    const subscribeSpy = vi.spyOn(session, "subscribe");
    const unsubscribers: Array<ReturnType<typeof vi.fn>> = [];
    subscribeSpy.mockImplementation((listener) => {
      const realUnsub = realSubscribe(listener);
      const tracked = vi.fn(() => realUnsub());
      unsubscribers.push(tracked);
      return tracked;
    });

    try {
      session.save("tok", "user");
      mockFetchSequence([{ status: 200, body: ME }]);

      const { unmount } = renderProvider();
      await waitFor(() => expect(ctx?.token).toBe("tok"));

      // Mount đã đăng ký ít nhất một listener...
      expect(subscribeSpy).toHaveBeenCalled();
      expect(unsubscribers.length).toBeGreaterThanOrEqual(1);
      // ...và CHƯA gỡ.
      expect(unsubscribers[0]).not.toHaveBeenCalled();

      unmount();

      // Unmount gọi đúng hàm unsubscribe mà subscribe() trả về.
      expect(unsubscribers[0]).toHaveBeenCalledTimes(1);
      // Gỡ xong thì notify sau đó phải vô hại với provider đã chết.
      expect(() => session.save("tokmoi", "user")).not.toThrow();
    } finally {
      subscribeSpy.mockRestore();
    }
  });
});
