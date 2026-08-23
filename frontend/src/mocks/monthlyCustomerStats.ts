/**
 * Mock dữ liệu thống kê khách theo tháng cho Admin Dashboard.
 *
 * Đây là dữ liệu demo. Khi backend endpoint
 * `GET /api/v1/admin/stats/customers-by-month` sẵn sàng, chỉ cần thay thế
 * mock này bằng fetch từ API. Shape này khớp với contract trong
 * `docs/ADMIN_MONTHLY_CUSTOMER_STATS_API.md`.
 */

export type MonthlyCustomerStat = {
  month: string; // YYYY-MM
  customer_count: number;
};

export type MonthlyCustomerStatsResponse = {
  months: MonthlyCustomerStat[];
};

/**
 * 12 tháng gần nhất bao gồm tháng hiện tại, với số khách giả lập.
 * Giá trị là cố định (không random) để test dễ dự đoán.
 *
 * Tháng 8/2026 là tháng hiện tại theo mock.
 */
export const mockMonthlyCustomerStats: MonthlyCustomerStatsResponse = {
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
