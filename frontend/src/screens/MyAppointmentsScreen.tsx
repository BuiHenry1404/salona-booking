import { useCallback, useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { AppointmentCard } from "../components/AppointmentCard";
import { BottomNav } from "../components/BottomNav";
import { api } from "../lib/api";
import type { AppointmentResponse } from "../lib/api";

/**
 * Màn "Lịch của tôi" — route `/lich-cua-toi`.
 *
 * API: `GET /api/v1/appointments/mine` → bare array `AppointmentResponse[]`.
 * Hủy xong luôn NẠP LẠI từ server thay vì tự bỏ thẻ khỏi mảng: nếu backend
 * từ chối hủy (lịch đã qua chẳng hạn) thì màn hình phải phản ánh đúng sự thật.
 */
export function MyAppointmentsScreen() {
  const [appointments, setAppointments] = useState<AppointmentResponse[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      setAppointments(await api.get<AppointmentResponse[]>("/api/v1/appointments/mine"));
      setError(null);
    } catch (err) {
      // Lỗi đã được api client dịch sẵn sang câu tiếng Việt thân thiện.
      setError(err instanceof Error ? err.message : "Không xem được lịch ạ.");
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  async function cancel(id: string) {
    try {
      await api.del(`/api/v1/appointments/${encodeURIComponent(id)}`);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Hủy không được ạ.");
      return;
    }
    await load();
  }

  return (
    <div className="screen" style={{ paddingBottom: 96 }}>
      <h1 style={{ fontSize: 28, marginBottom: "var(--s4)" }}>Lịch của tôi</h1>

      {/* role="alert" để trình đọc màn hình đọc ngay; trạng thái nói bằng chữ. */}
      {error && (
        <p role="alert" className="alert alert--danger">
          {error}
        </p>
      )}

      {appointments === null && !error && <p style={{ fontSize: 19 }}>Đang xem lịch…</p>}

      {appointments !== null && appointments.length === 0 && !error && (
        <div>
          <p style={{ fontSize: 20, marginBottom: "var(--s3)" }}>Anh chị chưa có lịch nào ạ.</p>
          <Link to="/" className="btn btn--primary btn--full" style={{ textDecoration: "none" }}>
            Nhắn cho tiệm
          </Link>
        </div>
      )}

      {appointments?.map((appointment) => (
        <AppointmentCard key={appointment.id} appointment={appointment} onCancel={cancel} />
      ))}

      <BottomNav />
    </div>
  );
}
