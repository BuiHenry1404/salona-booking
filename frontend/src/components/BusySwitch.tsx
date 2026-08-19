import { useEffect, useState } from "react";
import { api } from "../lib/api";
import type { ShopStatus } from "../hooks/useShopStatus";
import { formatViTime } from "../lib/viDate";
import { Button } from "./Button";
import "./BusySwitch.css";

const DURATIONS: Array<{ label: string; minutes: number }> = [
  { label: "15 phút", minutes: 15 },
  { label: "30 phút", minutes: 30 },
  { label: "1 tiếng", minutes: 60 },
  { label: "2 tiếng", minutes: 120 },
];

/** Nút khổng lồ chiếm nửa trên màn hình. Đây là thứ chủ tiệm bấm nhiều nhất
 *  trong ngày, thường bằng một tay khi tay kia đang cầm kéo. */
export function BusySwitch({
  status,
  onApplied,
}: {
  status: ShopStatus | null;
  onApplied: (next: ShopStatus) => void;
}) {
  const [picking, setPicking] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Đóng bảng chọn khi trạng thái đã đổi, kể cả khi lệnh đến từ máy khác
  // qua broadcast `shop_status_changed`.
  useEffect(() => {
    if (status?.is_busy) setPicking(false);
  }, [status?.is_busy]);

  async function run(call: () => Promise<ShopStatus>) {
    setBusy(true);
    setError(null);
    try {
      // POST /shop/busy và /shop/free đều trả thẳng ShopStatusResponse — áp
      // NGAY kết quả HTTP, không ngồi chờ broadcast vọng lại (kênh đó để đồng
      // bộ MÁY KHÁC; chờ nó là chủ tiệm thấy màn hình đứng im rồi bấm lại).
      onApplied(await call());
      setPicking(false);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Đổi trạng thái không được ạ.");
    } finally {
      setBusy(false);
    }
  }

  // Chưa biết tình trạng (GET /shop/status lỗi) thì KHÔNG được phịa ra
  // "Đang rảnh" — nói sai một lần là chủ tiệm thôi tin cái bảng này.
  if (status === null) {
    return (
      <section className="switch">
        <p className="switch__state">Chưa biết tình trạng tiệm</p>
        <p className="switch__count">Cô chú thử lại sau một chút ạ.</p>
      </section>
    );
  }

  if (status.is_busy) {
    return (
      <section className="switch switch--busy">
        <p className="switch__state">Đang bận</p>
        {/* Một con số duy nhất là giờ xong — đối chiếu thẳng với đồng hồ treo
            tường. KHÔNG bao giờ đếm ngược phút (minutes_left chỉ để hẹn lật thẻ
            trong useShopStatus). */}
        {status.busy_until && (
          <p className="switch__count">
            Xong lúc <strong>{formatViTime(status.busy_until)}</strong>
          </p>
        )}
        {error && (
          <p role="alert" className="alert alert--danger">
            {error}
          </p>
        )}
        <Button
          variant="accent"
          loading={busy}
          onClick={() => void run(() => api.postNoBody<ShopStatus>("/api/v1/shop/free"))}
        >
          Tôi rảnh rồi
        </Button>
      </section>
    );
  }

  return (
    <section className="switch switch--free">
      <p className="switch__state">Đang rảnh</p>
      {error && (
        <p role="alert" className="alert alert--danger">
          {error}
        </p>
      )}

      {picking ? (
        <>
          <p className="switch__ask">Bận khoảng bao lâu ạ?</p>
          <div className="switch__grid">
            {DURATIONS.map((d) => (
              <Button
                key={d.minutes}
                variant="primary"
                fullWidth={false}
                loading={busy}
                onClick={() =>
                  void run(() => api.post<ShopStatus>("/api/v1/shop/busy", { minutes: d.minutes }))
                }
              >
                {d.label}
              </Button>
            ))}
          </div>
          <Button variant="ghost" onClick={() => setPicking(false)}>
            Thôi, để sau
          </Button>
        </>
      ) : (
        <Button variant="danger" onClick={() => setPicking(true)}>
          Tôi đang bận
        </Button>
      )}
    </section>
  );
}
