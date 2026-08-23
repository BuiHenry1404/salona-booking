import { useEffect, useState } from "react";
import type { CSSProperties } from "react";
import { Link, useNavigate } from "react-router-dom";
import { Button } from "../components/Button";
import { api } from "../lib/api";
import { formatViDate } from "../lib/viDate";

/**
 * Màn danh sách các ngày đã trò chuyện — route `/lich-su`.
 *
 * Mỗi ngày một thẻ to, mới nhất trên cùng; ngày viết đủ chữ tiếng Việt
 * ("Thứ Tư, 12 tháng 8"), KHÔNG dạng 12/08. Dòng thứ hai là câu mở đầu của
 * khách để nhận ra hôm ấy nói chuyện gì. Chọn một ngày thì mở màn xem lại
 * `/lich-su/{day}` (chỉ đọc).
 *
 * API: `GET /api/v1/conversations/days` → `{ days: [{ day, message_count, preview }] }`
 * (`DayListResponse`). user_id lấy từ JWT ở backend, client không gửi kèm.
 */

interface DaySummary {
  day: string;
  message_count: number;
  preview: string;
}

interface DayListResponse {
  days: DaySummary[];
}

const cardStyle: CSSProperties = {
  display: "block",
  minHeight: "var(--tap)",
  padding: "var(--s3)",
  borderRadius: "var(--r)",
  background: "var(--color-surface)",
  border: "2px solid var(--color-border)",
  textDecoration: "none",
  color: "inherit",
  textAlign: "left",
};

export function ChatHistoryScreen() {
  const navigate = useNavigate();
  const [days, setDays] = useState<DaySummary[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    api
      .get<DayListResponse>("/api/v1/conversations/days")
      .then((res) => {
        if (!cancelled) setDays(res.days);
      })
      .catch((err) => {
        // Lỗi đã được api client dịch sẵn sang câu tiếng Việt thân thiện.
        if (!cancelled) setError(err instanceof Error ? err.message : "Không mở được lịch sử ạ.");
      });
    return () => {
      cancelled = true;
    };
  }, []);

  return (
    <main className="screen">
      <h1 style={{ fontSize: 28, marginBottom: "var(--s4)" }}>Các lần trò chuyện trước</h1>

      {/* Đường quay về hôm nay phải to, rõ, dễ bấm — không nhét vào menu ẩn. */}
      <Button variant="ghost" onClick={() => navigate("/")}>
        Quay về hôm nay
      </Button>

      {/* role="alert" để trình đọc màn hình đọc ngay; trạng thái nói bằng chữ. */}
      {error && (
        <p role="alert" className="alert alert--danger" style={{ marginTop: "var(--s4)" }}>
          {error}
        </p>
      )}

      {!error && days === null && (
        <p role="status" style={{ fontSize: 19, marginTop: "var(--s4)" }}>
          Đang mở lịch sử…
        </p>
      )}

      {!error && days !== null && days.length === 0 && (
        <p style={{ fontSize: 19, marginTop: "var(--s4)" }}>
          Chưa có cuộc trò chuyện nào.
        </p>
      )}

      {!error && days !== null && days.length > 0 && (
        <ul
          style={{
            listStyle: "none",
            display: "flex",
            flexDirection: "column",
            gap: "var(--s3)",
            marginTop: "var(--s4)",
          }}
        >
          {days.map((d) => (
            <li key={d.day}>
              <Link to={`/lich-su/${d.day}`} style={cardStyle}>
                <span style={{ display: "block", fontSize: 21, fontWeight: 600 }}>
                  {formatViDate(d.day)}
                </span>
                <span style={{ display: "block", fontSize: 19, color: "var(--color-fg-muted)" }}>
                  {d.preview}
                </span>
              </Link>
            </li>
          ))}
        </ul>
      )}
    </main>
  );
}
