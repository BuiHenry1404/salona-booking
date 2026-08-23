import { useEffect, useState } from "react";
import type { CSSProperties } from "react";
import { Navigate, useNavigate, useParams } from "react-router-dom";
import { Button } from "../components/Button";
import { MessageBubble } from "../components/MessageBubble";
import { api } from "../lib/api";
import { formatViDayMonth } from "../lib/viDate";

/**
 * Màn xem lại MỘT ngày cũ — route `/lich-su/:day`. CHỈ ĐỌC.
 *
 * "Chỉ xem" là thuộc tính cấu trúc: backend không có đường nào nhắn vào ngày
 * cũ, nên màn này đơn giản là KHÔNG vẽ ô nhập — và diễn đạt điều đó BẰNG CHỮ
 * ("Đây là cuộc trò chuyện ngày … Anh chị muốn nhắn thì quay về hôm nay."),
 * không dùng ô nhập xám/bị khoá. Không streaming, không tiến trình tool,
 * không mic — bong bóng y hệt màn chat để khách không phải học lại cách đọc.
 *
 * API: `GET /api/v1/conversations/days/{day}` → `{ day, is_today, messages }`
 * (`DayMessagesResponse`). Role backend "user"/"assistant" → bong bóng
 * "user"/"bot".
 */

interface HistoryMessage {
  role: "user" | "assistant";
  content: string;
  created_at: string;
}

interface DayMessagesResponse {
  day: string;
  is_today: boolean;
  messages: HistoryMessage[];
}

const noteStyle: CSSProperties = {
  fontSize: 19,
  padding: "var(--s3)",
  color: "var(--color-fg-muted)",
};

export function PastChatScreen() {
  const navigate = useNavigate();
  const { day } = useParams<"day">();
  const [data, setData] = useState<DayMessagesResponse | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!day) {
      setError("Không biết ngày nào để mở ạ.");
      return;
    }
    let cancelled = false;
    // encodeURIComponent: day đến từ URL người dùng, không giả định nó sạch.
    api
      .get<DayMessagesResponse>(`/api/v1/conversations/days/${encodeURIComponent(day)}`)
      .then((res) => {
        if (!cancelled) setData(res);
      })
      .catch((err) => {
        if (!cancelled)
          setError(err instanceof Error ? err.message : "Không mở được cuộc trò chuyện ạ.");
      });
    return () => {
      cancelled = true;
    };
  }, [day]);

  // Backend báo is_today: hôm nay thì đưa thẳng về khung chat thật (có ô
  // nhập) — màn này chỉ dành cho ngày cũ.
  if (data?.is_today) return <Navigate to="/" replace />;

  return (
    <div style={{ display: "flex", flexDirection: "column", minHeight: "100dvh" }}>
      <main style={{ flex: 1, padding: "var(--s3)" }}>
        {/* role="alert"/"status": mọi trạng thái diễn đạt bằng chữ, không chỉ màu. */}
        {error && (
          <p role="alert" className="alert alert--danger">
            {error}
          </p>
        )}

        {!error && data === null && (
          <p role="status" style={noteStyle}>
            Đang mở lại cuộc trò chuyện…
          </p>
        )}

        {data !== null &&
          data.messages.map((m, i) => (
            // Ngày cũ không có streaming: không bao giờ truyền streaming/pending.
            <MessageBubble
              key={`${m.created_at}-${i}`}
              role={m.role === "assistant" ? "bot" : "user"}
              text={m.content}
            />
          ))}
      </main>

      {/* Chỗ ô nhập mọi khi là một dòng CHỮ nói rõ đây là ngày cũ, kèm nút to
          quay về hôm nay. */}
      {(data !== null || error !== null) && (
        <div
          style={{
            padding: "var(--s2) var(--s3) calc(var(--s3) + env(safe-area-inset-bottom))",
            background: "var(--color-surface)",
            borderTop: "2px solid var(--color-border)",
          }}
        >
          {data !== null && (
            <p style={{ fontSize: 19, marginBottom: "var(--s2)" }}>
              Đây là cuộc trò chuyện ngày {formatViDayMonth(data.day)}. Anh chị muốn nhắn thì quay
              về hôm nay.
            </p>
          )}
          <Button onClick={() => navigate("/")}>Quay về hôm nay</Button>
        </div>
      )}
    </div>
  );
}
