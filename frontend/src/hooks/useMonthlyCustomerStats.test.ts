import { renderHook, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { useMonthlyCustomerStats } from "./useMonthlyCustomerStats";

const get = vi.fn();
vi.mock("../lib/api", () => ({ api: { get: (path: string) => get(path) } }));

const RESPONSE = {
  months: [
    { month: "2026-07", customer_count: 26 },
    { month: "2026-08", customer_count: 32 },
  ],
};

beforeEach(() => {
  get.mockReset();
});

describe("useMonthlyCustomerStats", () => {
  it("gọi đúng endpoint admin", async () => {
    get.mockResolvedValue(RESPONSE);
    renderHook(() => useMonthlyCustomerStats());

    await waitFor(() =>
      expect(get).toHaveBeenCalledWith("/api/v1/admin/stats/customers-by-month"),
    );
  });

  it("trả dữ liệu và tắt loading khi thành công", async () => {
    get.mockResolvedValue(RESPONSE);
    const { result } = renderHook(() => useMonthlyCustomerStats());

    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(result.current.data).toEqual(RESPONSE);
    expect(result.current.error).toBeNull();
  });

  it("giữ data null và nêu lỗi khi request hỏng", async () => {
    get.mockRejectedValue(new Error("Phần này chỉ chủ tiệm mới xem được ạ."));
    const { result } = renderHook(() => useMonthlyCustomerStats());

    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(result.current.data).toBeNull();
    expect(result.current.error).toBe("Phần này chỉ chủ tiệm mới xem được ạ.");
  });

  it("chỉ gọi API một lần cho mỗi lần mount", async () => {
    get.mockResolvedValue(RESPONSE);
    const { result } = renderHook(() => useMonthlyCustomerStats());

    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(get).toHaveBeenCalledTimes(1);
  });
});
