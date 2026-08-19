import { useState } from "react";
import type { AppointmentResponse } from "../lib/api";
import { formatViDateTime } from "../lib/viDate";
import { Button } from "./Button";
import "./AppointmentCard.css";

interface Props {
  appointment: AppointmentResponse;
  onCancel?: (id: string) => Promise<void> | void;
}

/** Một thẻ lịch hẹn: giờ viết đủ chữ tiếng Việt, ghi chú, và nút hủy có
 * xác nhận TẠI CHỖ — không modal che mất thẻ đang xem. */
export function AppointmentCard({ appointment, onCancel }: Props) {
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
      <p className="appt__when">{formatViDateTime(appointment.start_at)}</p>
      {appointment.note && <p className="appt__note">{appointment.note}</p>}

      {onCancel &&
        (confirming ? (
          <div className="appt__confirm">
            <p className="appt__ask">Cô chú chắc chưa ạ? Hủy rồi là mất chỗ này.</p>
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
