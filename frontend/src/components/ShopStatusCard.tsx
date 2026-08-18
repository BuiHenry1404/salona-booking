import type { ShopStatus } from "../hooks/useShopStatus";
import { formatViTime } from "../lib/viDate";
import "./ShopStatusCard.css";

/**
 * Backend (Mongo/motor) trả `busy_until` là UTC "trần" — chuỗi ISO không có
 * Z hay offset. Trình duyệt parse chuỗi không offset theo GIỜ MÁY, ở Việt Nam
 * là lệch đúng 7 tiếng. Chuẩn hóa về UTC rõ ràng trước khi định dạng.
 *
 * Có sẵn Z hoặc offset (+07:00 / -0500) thì giữ nguyên.
 */
function normalizeUtcIso(value: string): string {
  return /Z$|[+-]\d{2}:?\d{2}$/i.test(value) ? value : `${value}Z`;
}

/**
 * Thẻ trạng thái tiệm, luôn hiển thị trên đầu màn hình chat. Nhờ thẻ này
 * phần lớn khách biết tiệm bận hay rảnh mà không cần hỏi AI.
 *
 * Chữ trước, màu sau: mọi trạng thái nói bằng CHỮ — người mù màu nhẹ vẫn
 * đọc được. KHÔNG đếm ngược: "Xong lúc 3:30 chiều" là mốc, nhìn lại sau
 * bao lâu vẫn đúng, còn "còn 30 phút" sai ngay sau khi hiện ra.
 */
export function ShopStatusCard({
  status,
  loading,
}: {
  status: ShopStatus | null;
  loading: boolean;
}) {
  if (loading || !status) {
    return <div className="shopcard shopcard--loading">Đang xem tiệm…</div>;
  }

  const busy = status.is_busy;
  return (
    <div className={`shopcard ${busy ? "shopcard--busy" : "shopcard--free"}`}>
      <span className="shopcard__dot" aria-hidden="true" />
      <div>
        <strong className="shopcard__title">
          {busy ? "Chủ tiệm đang bận" : "Chủ tiệm đang rảnh"}
        </strong>
        {busy && status.busy_until && (
          <p className="shopcard__sub">
            Xong lúc <strong>{formatViTime(normalizeUtcIso(status.busy_until))}</strong>
          </p>
        )}
      </div>
    </div>
  );
}
