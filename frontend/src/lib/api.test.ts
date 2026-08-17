/// <reference types="node" />
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ApiError, api } from "./api";
import { session } from "../auth/session";

/** Trả về fetch mock chơi theo kịch bản: mỗi lượt gọi lấy một response theo thứ tự. */
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

beforeEach(() => session.clear());
afterEach(() => vi.unstubAllGlobals());

describe("api", () => {
  it("gắn token vào header khi session có token", async () => {
    session.save("abc123", "user");
    const fetchMock = mockFetchSequence([{ status: 200, body: { ok: true } }]);

    await api.get("/api/v1/auth/me");

    const headers = fetchMock.mock.calls[0][1].headers;
    expect(headers.Authorization).toBe("Bearer abc123");
  });

  it("không gắn Authorization khi chưa có token", async () => {
    const fetchMock = mockFetchSequence([{ status: 200, body: {} }]);

    await api.get("/api/v1/shop/status");

    expect(
      fetchMock.mock.calls[0][1].headers.Authorization,
    ).toBeUndefined();
  });

  it("KHÔNG dùng localStorage/sessionStorage ở bất kỳ đâu", () => {
    const read = (rel: string) => readFileSync(resolve(process.cwd(), rel), "utf8");
    for (const file of ["src/auth/session.ts", "src/lib/api.ts"]) {
      const source = read(file);
      expect(source, file).not.toContain("localStorage");
      expect(source, file).not.toContain("sessionStorage");
    }
  });

  it("request tới /auth/* phải mang cookie — credentials: include", async () => {
    const fetchMock = mockFetchSequence([
      { status: 200, body: { access_token: "t", token_type: "bearer", role: "user" } },
    ]);

    await api.post("/api/v1/auth/login", { phone: "0912345678", password: "x" });

    expect(fetchMock.mock.calls[0][1].credentials).toBe("include");
  });

  it("204 không có body vẫn trả về null", async () => {
    mockFetchSequence([{ status: 204 }]);

    await expect(api.del("/api/v1/appointments/abc")).resolves.toBeNull();
  });

  it("lỗi backend giữ câu detail tiếng Việt", async () => {
    mockFetchSequence([
      { status: 409, body: { detail: "Giờ này có người đặt mất rồi ạ" } },
    ]);

    await expect(api.post("/api/v1/appointments", {})).rejects.toThrow(
      "Giờ này có người đặt mất rồi ạ",
    );
  });

  it("mất mạng thì báo bằng câu người thường đọc được", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new TypeError("Failed to fetch")));

    await expect(api.get("/api/v1/shop/status")).rejects.toThrow(/mạng/i);
  });

  it("401 → gọi refresh ĐÚNG MỘT LẦN, rồi retry request gốc MỘT LẦN", async () => {
    session.save("tokcu", "user");
    const fetchMock = mockFetchSequence([
      { status: 401, body: { detail: "hết hạn" } }, // request gốc
      { status: 200, body: { access_token: "tokmoi", token_type: "bearer", role: "user" } }, // refresh
      { status: 200, body: { ok: true } }, // retry
    ]);

    await api.get("/api/v1/appointments/mine");

    expect(fetchMock).toHaveBeenCalledTimes(3);
    const [refreshUrl, refreshOpts] = fetchMock.mock.calls[1];
    expect(String(refreshUrl)).toContain("/api/v1/auth/refresh");
    expect(refreshOpts.credentials).toBe("include");
    expect(refreshOpts.method).toBe("POST");
    // Retry phải mang token mới, không phải token đã chết.
    expect(fetchMock.mock.calls[2][1].headers.Authorization).toBe("Bearer tokmoi");
    expect(session.load().token).toBe("tokmoi");
  });

  it("refresh fail → clear session và ném lỗi", async () => {
    session.save("tokcu", "user");
    const fetchMock = mockFetchSequence([
      { status: 401, body: { detail: "hết hạn" } },
      { status: 401, body: { detail: "refresh chết" } },
    ]);

    await expect(api.get("/api/v1/appointments/mine")).rejects.toBeInstanceOf(ApiError);
    expect(fetchMock).toHaveBeenCalledTimes(2); // không retry request gốc
    expect(session.load().token).toBeNull();
  });

  it("retry vẫn 401 → clear session, không lặp tiếp", async () => {
    session.save("tokcu", "user");
    const fetchMock = mockFetchSequence([
      { status: 401, body: {} },
      { status: 200, body: { access_token: "tokmoi", token_type: "bearer", role: "user" } },
      { status: 401, body: {} },
    ]);

    await expect(api.get("/api/v1/appointments/mine")).rejects.toBeInstanceOf(ApiError);
    expect(fetchMock).toHaveBeenCalledTimes(3); // gốc + refresh + retry, dừng ở đó
    expect(session.load().token).toBeNull();
  });

  it("/auth/refresh bị 401 thì KHÔNG tự refresh chính nó", async () => {
    const fetchMock = mockFetchSequence([{ status: 401, body: {} }]);

    await expect(api.post("/api/v1/auth/refresh")).rejects.toBeInstanceOf(ApiError);

    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(session.load().token).toBeNull();
  });

  it("/auth/login bị 401 thì không refresh — sai mật khẩu là sai mật khẩu", async () => {
    const fetchMock = mockFetchSequence([
      { status: 401, body: { detail: "Số điện thoại hoặc mật khẩu không đúng" } },
    ]);

    await expect(
      api.post("/api/v1/auth/login", { phone: "0912345678", password: "sai" }),
    ).rejects.toThrow("Số điện thoại hoặc mật khẩu không đúng");

    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it("logout gọi được với cookie và nuốt body rỗng (204)", async () => {
    const fetchMock = mockFetchSequence([{ status: 204 }]);

    await expect(api.post("/api/v1/auth/logout")).resolves.toBeNull();

    expect(String(fetchMock.mock.calls[0][0])).toContain("/api/v1/auth/logout");
    expect(fetchMock.mock.calls[0][1].credentials).toBe("include");
  });
});
