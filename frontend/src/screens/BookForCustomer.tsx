import { useCallback, useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { Button } from "../components/Button";
import { Field } from "../components/Field";
import { api, ApiError } from "../lib/api";
import type { AppointmentCreateRequest, AppointmentResponse } from "../lib/api";
import { todayInVn } from "../hooks/useAdminFeed";
import { formatViTime } from "../lib/viDate";
import "./BookForCustomer.css";

interface FreeSlotsResponse {
  slots: string[];
}

export function BookForCustomer() {
  const { userId = "" } = useParams();
  const [day, setDay] = useState(todayInVn());
  const [slots, setSlots] = useState<string[] | null>(null);
  const [selectedSlot, setSelectedSlot] = useState<string | null>(null);
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState<string | null>(null);

  const loadSlots = useCallback(async (whichDay: string) => {
    try {
      const data = await api.get<FreeSlotsResponse>(
        `/api/v1/appointments/free-slots/${whichDay}`,
      );
      setSlots(data.slots);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Không xem được giờ trống ạ.");
      setSlots([]);
    }
  }, []);

  useEffect(() => {
    void loadSlots(day);
  }, [day, loadSlots]);

  function changeDay(value: string) {
    setDay(value);
    setSelectedSlot(null);
    setSuccess(null);
    setError(null);
  }

  async function submit() {
    if (!selectedSlot) return;
    setBusy(true);
    setError(null);
    setSuccess(null);
    try {
      const body: AppointmentCreateRequest = {
        start_at: selectedSlot,
        note: note.trim() || null,
        for_user_id: userId,
      };
      const created = await api.post<AppointmentResponse>(
        "/api/v1/appointments",
        body,
      );
      setSuccess(`Đã đặt lịch lúc ${formatViTime(created.start_at)}.`);
      setSelectedSlot(null);
    } catch (err) {
      if (err instanceof ApiError && err.status === 409) {
        setError(err.message);
        setSelectedSlot(null);
        setSlots(null);
        void loadSlots(day);
      } else {
        setError(err instanceof Error ? err.message : "Đặt lịch không được ạ.");
      }
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="screen">
      <Link to="/chu-tiem/khach" className="book__back">
        ← Về danh sách khách
      </Link>
      <h1 className="book__title">Đặt lịch hộ</h1>

      {error && (
        <p role="alert" className="alert alert--danger">
          {error}
        </p>
      )}
      {success && <p className="book__success">{success}</p>}

      <Field
        label="Ngày"
        type="date"
        value={day}
        onChange={(e) => changeDay(e.target.value)}
      />

      <p className="book__section">Giờ trống</p>
      {slots === null && <p className="book__loading">Đang xem giờ trống…</p>}
      {slots !== null && slots.length === 0 && (
        <p className="book__empty">Hôm đó không còn giờ trống ạ.</p>
      )}
      {slots !== null && slots.length > 0 && (
        <div className="book__slots">
          {slots.map((slot) => (
            <button
              key={slot}
              className={`book__slot${selectedSlot === slot ? " book__slot--selected" : ""}`}
              aria-pressed={selectedSlot === slot}
              onClick={() => setSelectedSlot(slot)}
            >
              {formatViTime(slot)}
            </button>
          ))}
        </div>
      )}

      <Field
        label="Làm gì"
        hint="Vd: làm nail, gội đầu, uốn tóc…"
        value={note}
        onChange={(e) => setNote(e.target.value)}
      />

      <Button loading={busy} disabled={!selectedSlot} onClick={() => void submit()}>
        Đặt lịch
      </Button>
    </div>
  );
}
