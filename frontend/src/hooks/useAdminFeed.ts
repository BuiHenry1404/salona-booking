import { useCallback, useEffect, useState } from "react";
import { acquireSocket, releaseSocket } from "../lib/socket";
import { useAuth } from "../auth/AuthContext";
import { api } from "../lib/api";
import type { AppointmentResponse } from "../lib/api";

/** Payload THẬT của sự kiện socket `appointment_created` (SocketNotifier
 * gửi 5 field này, KHÔNG có duration_minutes/status — đừng bịa thêm). */
export interface AdminFeedItem {
  id: string;
  start_at: string;
  user_name: string | null;
  phone: string | null;
  note: string | null;
}

/** "2026-08-21" theo giờ Việt Nam — khớp format `date` của route
 * /appointments/day/{day} và khớp lịch đang xem. */
function vnDay(value: string | Date): string {
  return new Intl.DateTimeFormat("en-CA", { timeZone: "Asia/Ho_Chi_Minh" }).format(
    value instanceof Date ? value : new Date(value),
  );
}

export function todayInVn(): string {
  return vnDay(new Date());
}

/**
 * Lịch hôm nay của chủ tiệm: nạp một lần bằng REST rồi để Socket.IO đẩy
 * thêm/bớt. Socket dùng chung qua acquireSocket()/releaseSocket() — phải gỡ
 * ĐÚNG handler của mình khi dọn để không đụng listener của hook khác.
 */
export function useAdminFeed(day: string = todayInVn()) {
  const { token } = useAuth();
  const [appointments, setAppointments] = useState<AdminFeedItem[]>([]);
  const [newIds, setNewIds] = useState<Set<string>>(new Set());
  const [error, setError] = useState<string | null>(null);

  const reload = useCallback(async () => {
    try {
      // Bare array AppointmentResponse[] — đủ field cho AdminFeedItem.
      setAppointments(await api.get<AppointmentResponse[]>(`/api/v1/appointments/day/${day}`));
      setError(null);
    } catch (err) {
      // Lỗi đã được api client dịch sẵn sang câu tiếng Việt thân thiện.
      setError(err instanceof Error ? err.message : "Không xem được lịch ạ.");
    }
  }, [day]);

  useEffect(() => {
    void reload();
  }, [reload]);

  useEffect(() => {
    if (!token) return;
    const socket = acquireSocket();

    const onCreated = (data: AdminFeedItem) => {
      // Chỉ nhận lịch của đúng ngày đang xem — khách đặt cho tuần sau không
      // được nhảy vào danh sách hôm nay.
      if (vnDay(data.start_at) !== day) return;

      setAppointments((prev) =>
        prev.some((a) => a.id === data.id)
          ? prev
          : [...prev, data].sort((a, b) => a.start_at.localeCompare(b.start_at)),
      );
      setNewIds((prev) => {
        const next = new Set(prev);
        next.add(data.id);
        return next;
      });
    };

    const onCancelled = (data: { id: string; start_at: string }) => {
      setAppointments((prev) => prev.filter((a) => a.id !== data.id));
      setNewIds((prev) => {
        const next = new Set(prev);
        next.delete(data.id);
        return next;
      });
    };

    socket.on("appointment_created", onCreated);
    socket.on("appointment_cancelled", onCancelled);

    return () => {
      // Gỡ ĐÚNG handler rồi mới trả socket — socket dùng chung với
      // `useShopStatus`/`useAgentStream`.
      socket.off("appointment_created", onCreated);
      socket.off("appointment_cancelled", onCancelled);
      releaseSocket();
    };
  }, [token, day]);

  return { appointments, newIds, error, reload };
}
