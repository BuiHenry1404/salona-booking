import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { Button } from "../components/Button";
import { Field } from "../components/Field";
import { api } from "../lib/api";
import type { ShopHours } from "../lib/api";
import "./ShopHoursScreen.css";

// Thứ tự khớp Python `date.weekday()`: Thứ Hai là 0, Chủ Nhật là 6.
// Đảo nhầm chỗ này thì tiệm nghỉ sai ngày và khách đến gặp cửa đóng.
const DAYS = ["Thứ Hai", "Thứ Ba", "Thứ Tư", "Thứ Năm", "Thứ Sáu", "Thứ Bảy", "Chủ Nhật"];

/**
 * Màn giờ mở cửa — route `/chu-tiem/gio-mo-cua`.
 *
 * API: `GET /api/v1/shop/hours` → ShopHours; `PUT` trả lại đúng cái vừa lưu.
 * Sau Lưu dùng RESPONSE SERVER làm state mới — không tự coi input là sự thật.
 */
export function ShopHoursScreen() {
  const [hours, setHours] = useState<ShopHours | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    api
      .get<ShopHours>("/api/v1/shop/hours")
      .then((h) => setHours(h))
      .catch((err) => setError(err instanceof Error ? err.message : "Không xem được giờ ạ."));
  }, []);

  function toggleDay(index: number) {
    if (!hours) return;
    const closed = new Set(hours.closed_days);
    if (closed.has(index)) closed.delete(index);
    else closed.add(index);
    // Sắp xếp để body PUT ổn định so sánh được với representation backend.
    // Sửa dữ liệu là mất trạng thái "đã lưu" — chữ đó chỉ nói sự thật.
    setSaved(false);
    setHours({ ...hours, closed_days: [...closed].sort((a, b) => a - b) });
  }

  async function save() {
    if (!hours) return;
    setBusy(true);
    setError(null);
    try {
      setHours(await api.put<ShopHours>("/api/v1/shop/hours", hours));
      // Trạng thái thành công phải là CHỮ — đổi màu thôi thì người mù màu
      // không biết đã lưu chưa.
      setSaved(true);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Lưu không được ạ.");
    } finally {
      setBusy(false);
    }
  }

  if (!hours) {
    return (
      <div className="screen">
        {error ? (
          <p role="alert" className="alert alert--danger">
            {error}
          </p>
        ) : (
          <p className="shop-hours__loading">Đang xem…</p>
        )}
      </div>
    );
  }

  return (
    <div className="screen">
      <Link to="/chu-tiem" className="shop-hours__back">
        ← Về bảng điều khiển
      </Link>
      <h1 className="shop-hours__title">Giờ mở cửa</h1>

      {error && (
        <p role="alert" className="alert alert--danger">
          {error}
        </p>
      )}
      {saved && <p className="shop-hours__saved">Đã lưu ạ.</p>}

      <Field
        label="Mở cửa"
        type="time"
        value={hours.open_time}
        onChange={(e) => {
          setSaved(false);
          setHours({ ...hours, open_time: e.target.value });
        }}
      />
      <Field
        label="Đóng cửa"
        type="time"
        value={hours.close_time}
        onChange={(e) => {
          setSaved(false);
          setHours({ ...hours, close_time: e.target.value });
        }}
      />

      <p className="shop-hours__section">Ngày nghỉ</p>
      {DAYS.map((label, index) => (
        <label key={label} className="shop-hours__day">
          <input
            type="checkbox"
            className="shop-hours__checkbox"
            checked={hours.closed_days.includes(index)}
            onChange={() => toggleDay(index)}
          />
          {label}
        </label>
      ))}

      <div className="shop-hours__save">
        <Button loading={busy} onClick={() => void save()}>
          Lưu
        </Button>
      </div>
    </div>
  );
}
