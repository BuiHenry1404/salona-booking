import { useEffect, useState } from "react";
import { api } from "../lib/api";
import type { MonthlyCustomerStatsResponse } from "../lib/api";

/**
 * Khách theo tháng cho bảng chủ tiệm. Nạp MỘT lần lúc mở màn — số liệu tổng
 * hợp cả tháng không nhúc nhích theo từng lịch mới, nên không cần socket.
 *
 * Lỗi thì để `data` là null chứ không rơi về mảng rỗng: biểu đồ 12 cột số 0
 * trông y hệt "tháng nào cũng ế", chủ tiệm không phân biệt được với "không
 * tải được".
 */
export function useMonthlyCustomerStats() {
  const [data, setData] = useState<MonthlyCustomerStatsResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;

    (async () => {
      try {
        const res = await api.get<MonthlyCustomerStatsResponse>(
          "/api/v1/admin/stats/customers-by-month",
        );
        if (!alive) return;
        setData(res);
        setError(null);
      } catch (err) {
        if (!alive) return;
        setError(
          err instanceof Error ? err.message : "Chưa xem được thống kê ạ.",
        );
      } finally {
        if (alive) setLoading(false);
      }
    })();

    return () => {
      alive = false;
    };
  }, []);

  return { data, loading, error };
}
