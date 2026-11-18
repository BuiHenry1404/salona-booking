import { useState } from "react";
import { formatViDateTime, formatViTime } from "../lib/viDate";
import { Button } from "./Button";
import "./AppointmentCard.css";

/** Một dịch vụ của lịch — chỉ tên, đủ để hiện cho khách/chủ tiệm đọc. */
export interface AppointmentCardService {
  id: string;
  name: string;
}

/** Chỉ những field thẻ này THẬT SỰ vẽ. Payload socket `appointment_created`
 * (7 field — không có duration_minutes/status) và `AppointmentResponse` đầy
 * đủ của màn khách đều truyền vào được nhờ structural typing. */
export interface AppointmentCardData {
  id: string;
  start_at: string;
  /** Giờ xong — MỐC, không phải khoảng thời gian còn lại (ràng buộc giao
   * diện cho khách lớn tuổi: "Xong lúc 5 giờ chiều", không "còn 30 phút"). */
  end_at: string;
  note: string | null;
  user_name: string | null;
  phone: string | null;
  services: AppointmentCardService[];
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
      {appointment.services.length > 0 && (
        <p className="appt__services">{appointment.services.map((s) => s.name).join(", ")}</p>
      )}
      <p className="appt__end">
        Xong lúc <strong>{formatViTime(appointment.end_at)}</strong>
      </p>
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
