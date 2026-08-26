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
  const { months, maxCount, currentMonthKey } = useMemo(() => {
    const months = data.months;
    const counts = months.map((m) => m.customer_count);
    const maxCount = Math.max(0, ...counts);
    const currentMonthKey = getCurrentVnMonthKey();
    return { months, maxCount, currentMonthKey };
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
          const isCurrent = month === currentMonthKey;
          const isZero = customer_count === 0;
          return (
            <li
              key={month}
              className={["stats__row", isCurrent ? "stats__row--current" : ""]
                .filter(Boolean)
                .join(" ")}
              aria-label={label.full}
              title={`${label.full}: ${customer_count} khách`}
            >
              <span className="stats__month">{label.short}</span>
              <div className="stats__bar-cell">
                <span
                  className={["stats__bar", isZero ? "stats__bar--zero" : ""]
                    .filter(Boolean)
                    .join(" ")}
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

/** Trả về tháng hiện tại theo giờ Việt Nam dạng `YYYY-MM` (không cần dependency). */
function getCurrentVnMonthKey(): string {
  const formatter = new Intl.DateTimeFormat("en-CA", {
    timeZone: "Asia/Ho_Chi_Minh",
    year: "numeric",
    month: "2-digit",
  });
  const parts = formatter.formatToParts(new Date());
  const year = parts.find((p) => p.type === "year")?.value;
  const month = parts.find((p) => p.type === "month")?.value;
  return `${year}-${month}`;
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
