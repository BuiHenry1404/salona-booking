import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { MonthlyCustomerStats } from "./MonthlyCustomerStats";
import type { MonthlyCustomerStatsResponse } from "../mocks/monthlyCustomerStats";

const allZeroData: MonthlyCustomerStatsResponse = {
  months: Array.from({ length: 12 }, (_, i) => ({
    month: `2026-${String(i + 1).padStart(2, "0")}`,
    customer_count: 0,
  })),
};

const mixedData: MonthlyCustomerStatsResponse = {
  months: [
    { month: "2025-09", customer_count: 14 },
    { month: "2025-10", customer_count: 21 },
    { month: "2025-11", customer_count: 18 },
    { month: "2025-12", customer_count: 27 },
    { month: "2026-01", customer_count: 24 },
    { month: "2026-02", customer_count: 19 },
    { month: "2026-03", customer_count: 31 },
    { month: "2026-04", customer_count: 28 },
    { month: "2026-05", customer_count: 35 },
    { month: "2026-06", customer_count: 30 },
    { month: "2026-07", customer_count: 26 },
    { month: "2026-08", customer_count: 32 },
  ],
};

describe("MonthlyCustomerStats", () => {
  it("hiển thị đúng 12 tháng", () => {
    render(<MonthlyCustomerStats data={mixedData} />);
    expect(screen.getAllByRole("listitem")).toHaveLength(12);
  });

  it("hiển thị đúng số khách cho một tháng cụ thể", () => {
    render(<MonthlyCustomerStats data={mixedData} />);
    const row = screen.getByTitle(/tháng 5\/2026/i);
    expect(row).toHaveTextContent("35");
  });

  it("tháng hiện tại (mock) nằm cuối danh sách", () => {
    render(<MonthlyCustomerStats data={mixedData} />);
    const rows = screen.getAllByRole("listitem");
    const lastRow = rows[rows.length - 1];
    expect(lastRow).toHaveTextContent("T8/26");
    expect(lastRow).toHaveTextContent("32");
  });

  it("cross-year month labels render", () => {
    render(<MonthlyCustomerStats data={mixedData} />);
    expect(screen.getByTitle(/tháng 9\/2025/i)).toBeInTheDocument();
    expect(screen.getByTitle(/tháng 8\/2026/i)).toBeInTheDocument();
  });

  it("không crash khi tất cả các tháng đều 0", () => {
    const { container } = render(<MonthlyCustomerStats data={allZeroData} />);
    expect(container.querySelector(".stats")).toBeInTheDocument();
    // Không có vạch bar nào lấp lánh quá 100% hoặc negative width
    const bars = container.querySelectorAll(".stats__bar");
    bars.forEach((bar) => {
      const width = getComputedStyle(bar as Element).width;
      expect(width).not.toContain("NaN");
      expect(width).not.toContain("Infinity");
    });
  });
});
