import { useState } from "react";
import { formatViDateTime } from "../lib/viDate";
import { Button } from "./Button";
import "./AppointmentCard.css";

/** Chỉ những field thẻ này THẬT SỰ vẽ. Payload socket `appointment_created`
 * (5 field — không có duration_minutes/status) và `AppointmentResponse` đầy
 * đủ của màn khách đều truyền vào được nhờ structural typing. */
export interface AppointmentCardData {
  id: string;
  start_at: string;
  note: string | null;
  user_name: string | null;
  phone: string | null;
}

interface Props {
  appointment: AppointmentCardData;
  onCancel?: (id: string) => Promise<void> | void;
  /** Màn chủ tiệm cần thấy tên và số điện thoại khách để gọi xác nhận. */
  showCustomer?: boolean;
  /** Lịch vừa được Socket.IO đẩy lên — nhãn chữ MỚI, không chỉ đổi màu. */
  isNew?: boolean;
}

/** Một thẻ lịch hẹn: giờ viết đủ chữ tiếng Việt, ghi chú, và nút hủy có
 * xác nhận TẠI CHỖ — không modal che mất thẻ đang xem. */
export function AppointmentCard({ appointment, onCancel, showCustomer, isNew }: Props) {
  const [confirming, setConfirming] = useState(false);
  const [busy, setBusy] = useState(false);

  async function confirmCancel() {
    setBusy(true);
    try {
      await onCancel?.(appointment.id);
    } finally {
      setBusy(false);
      setConfirming(false);
    }
  }

  return (
    <article className="appt">
      {isNew && <span className="appt__new">MỚI</span>}
      <p className="appt__when">{formatViDateTime(appointment.start_at)}</p>
      {appointment.note && <p className="appt__note">{appointment.note}</p>}
      {showCustomer && (
        <p className="appt__who">
          {appointment.user_name ?? "khách"}
          {appointment.phone ? ` · ${appointment.phone}` : ""}
        </p>
      )}

      {onCancel &&
        (confirming ? (
          <div className="appt__confirm">
            <p className="appt__ask">Anh chị chắc chưa ạ? Hủy rồi là mất chỗ này.</p>
            <div className="appt__row">
              <Button variant="danger" fullWidth={false} loading={busy} onClick={confirmCancel}>
                Hủy lịch này
              </Button>
              <Button variant="ghost" fullWidth={false} onClick={() => setConfirming(false)}>
                Giữ lịch
              </Button>
            </div>
          </div>
        ) : (
          <Button variant="ghost" onClick={() => setConfirming(true)}>
            Hủy lịch
          </Button>
        ))}
    </article>
  );
}
