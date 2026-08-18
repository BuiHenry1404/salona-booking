import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "../lib/api";
import { acquireSocket, releaseSocket } from "../lib/socket";
import { useAuth } from "../auth/AuthContext";

export interface ShopStatus {
  is_busy: boolean;
  busy_until: string | null;
  minutes_left: number | null;
}

/**
 * Thẻ trạng thái tiệm. Nạp một lần bằng REST rồi để `shop_status_changed`
 * (broadcast tới MỌI user) cập nhật — nhờ vậy phần lớn khách biết tiệm bận hay
 * rảnh mà không cần hỏi AI.
 *
 * `minutes_left` KHÔNG bao giờ được hiển thị. Màn hình chỉ nói giờ xong cụ thể
 * (`busy_until`). Trường này chỉ dùng để hẹn giờ lật thẻ, vì nó là một KHOẢNG
 * nên không dính lệch đồng hồ máy khách.
 *
 * Dùng chung socket với `useAgentStream` qua `acquireSocket()` đếm tham chiếu:
 * màn hình chủ tiệm cũng dùng hook này mà không có khung chat.
 */
export function useShopStatus() {
  const { token } = useAuth();
  const [status, setStatus] = useState<ShopStatus | null>(null);
  const [loading, setLoading] = useState(true);
  const alive = useRef(true);
  // Số thứ tự của cập nhật MỚI NHẤT đã được áp. Mỗi lần socket/apply áp một
  // trạng thái thì tăng. Request REST ghi lại revision lúc BẮT ĐẦU và chỉ
  // được áp response khi revision chưa đổi kể từ đó — response cũ không bao
  // giờ được ghi đè realtime mới hơn. Xét theo THỨ TỰ TỚI, không theo
  // timestamp hay busy_until (đồng hồ máy khách không đáng tin).
  const revision = useRef(0);

  // MỘT logic nhận duy nhất cho REST, socket và `apply` (task admin sau này áp
  // thẳng phản hồi HTTP của POST /shop/busy) — mọi đường vào đều đi qua đây
  // để đồng hồ đếm được gieo lại từ đầu như nhau.
  const receive = useCallback((next: ShopStatus) => {
    if (!alive.current) return;
    revision.current += 1;
    setStatus(next);
  }, []);

  useEffect(() => {
    alive.current = true;
    return () => {
      alive.current = false;
    };
  }, []);

  useEffect(() => {
    if (!token) return;
    let cancelled = false;
    const seenAtStart = revision.current;

    api
      .get<ShopStatus>("/api/v1/shop/status")
      .then((s) => {
        // Có realtime tới trong lúc GET còn chờ thì response này đã cũ — bỏ.
        if (cancelled || revision.current !== seenAtStart) return;
        receive(s);
      })
      // Lỗi thì giữ nguyên trạng thái đã biết (hoặc null), không retry.
      .catch(() => undefined)
      .finally(() => {
        if (!cancelled) setLoading(false);
      });

    const socket = acquireSocket();
    socket.on("shop_status_changed", receive);

    return () => {
      cancelled = true;
      // Gỡ ĐÚNG handler của mình rồi mới trả socket: socket dùng chung với
      // `useAgentStream` — wildcard off(event) sẽ gỡ cả listener của nó.
      socket.off("shop_status_changed", receive);
      releaseSocket();
    };
  }, [token, receive]);

  // Backend KHÔNG có timer nào phát sự kiện lúc `busy_until` hết hạn — nó chỉ
  // tính lại khi có ai đó gọi get_status(). Thiếu chỗ này thì hết giờ bận,
  // thẻ vẫn hiện "đang bận" cho tới khi khách tải lại trang.
  //
  // MỘT `setTimeout` duy nhất, không phải đồng hồ đếm từng phút: màn hình chỉ
  // hiện giờ xong, nên giữa hai mốc chẳng có gì để vẽ lại.
  //
  // Hẹn theo `minutes_left` (một KHOẢNG) chứ không theo `busy_until` trừ đi
  // `Date.now()` (hai MỐC): điện thoại của người lớn tuổi lệch giờ là chuyện
  // thường, mà khoảng thì miễn nhiễm với lệch đồng hồ.
  useEffect(() => {
    if (!status?.is_busy || status.minutes_left == null) return;

    const id = setTimeout(() => {
      // Đổi thẻ ngay cho khách thấy, rồi hỏi lại server đúng MỘT lần để xác
      // nhận — phòng khi có sự kiện rơi mất lúc mạng chập chờn. Lỗi thì giữ
      // trạng thái optimistic, không retry.
      setStatus((prev) =>
        prev ? { ...prev, is_busy: false, busy_until: null, minutes_left: null } : prev,
      );
      const seenAtStart = revision.current;
      api
        .get<ShopStatus>("/api/v1/shop/status")
        .then((s) => {
          // Realtime mới hơn tới trong lúc refetch còn chờ thì response này
          // đã cũ — bỏ, thẻ và đồng hồ của realtime đó là chuẩn.
          if (alive.current && revision.current === seenAtStart) receive(s);
        })
        .catch(() => undefined);
    }, status.minutes_left * 60_000);

    // Dọn khi trạng thái đổi — chủ tiệm bấm bận thêm lần nữa thì hẹn giờ cũ
    // phải bị hủy, nếu không nó lật thẻ sang rảnh giữa chừng.
    return () => clearTimeout(id);
  }, [status?.is_busy, status?.busy_until, status?.minutes_left, receive]);

  return { status, loading, apply: receive };
}
