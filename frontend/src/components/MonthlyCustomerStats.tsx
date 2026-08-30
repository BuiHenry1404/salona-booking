import { useMemo } from "react";
import type { MonthlyCustomerStatsResponse } from "../mocks/monthlyCustomerStats";
import "./MonthlyCustomerStats.css";

interface MonthlyCustomerStatsProps {
  data: MonthlyCustomerStatsResponse;
}

/**
 * Hiển thị bar chart HTML/CSS thuần cho số khách duy nhất theo tháng.
 *
 * - Không dùng canvas/SVG/library.
 * - Responsive, không overflow ngang.
 * - Zero count hiển thị an toàn.
 * - Bar width scale theo tỷ lệ max count.
 */
export function MonthlyCustomerStats({ data }: MonthlyCustomerStatsProps) {
  const { months, maxCount } = useMemo(() => {
    const months = data.months;
    const counts = months.map((m) => m.customer_count);
    const maxCount = Math.max(0, ...counts);
    return { months, maxCount };
  }, [data.months]);

  return (
    <section className="stats" aria-labelledby="stats-title">
      <h2 id="stats-title" className="stats__title">
        Khách theo tháng
      </h2>
      <p className="stats__hint">
        Số khách duy nhất có lịch trong mỗi tháng (không tính đã hủy).
      </p>
      <ul className="stats__list" role="list">
        {months.map(({ month, customer_count }) => {
          const label = formatMonthLabel(month);
          const widthPct =
            maxCount > 0 ? Math.round((customer_count / maxCount) * 100) : 0;
          return (
            <li
              key={month}
              className="stats__row"
              aria-label={label.full}
              title={`${label.full}: ${customer_count} khách`}
            >
              <span className="stats__month">{label.short}</span>
              <div className="stats__bar-cell">
                <span
                  className="stats__bar"
                  style={{ width: `${widthPct}%` }}
                />
              </div>
              <span className="stats__count">{customer_count}</span>
            </li>
          );
        })}
      </ul>
    </section>
  );
}

/**
 * Chuyển `YYYY-MM` thành nhãn tiếng Việt.
 *
 * - `full`: "Tháng 9/2025"
 * - `short`: "T9/2025" (để không chiếm chỗ trên mobile)
 */
function formatMonthLabel(isoMonth: string): { full: string; short: string } {
  const [year, month] = isoMonth.split("-");
  const monthNum = Number(month);
  return {
    full: `Tháng ${monthNum}/${year}`,
    short: `T${monthNum}/${year.slice(2)}`,
  };
}
