import type { Proposal } from "../hooks/useAgentStream";
import { Button } from "./Button";
import "./ConfirmButtons.css";

/**
 * Hai nút Đồng ý / Không cho ĐỀ XUẤT GẦN NHẤT. `ChatScreen` chỉ được render
 * component này MỘT LẦN, ngay dưới danh sách tin nhắn — KHÔNG BAO GIỜ bên
 * trong bong bóng lịch sử, để không có hai bộ nút cùng lúc trên màn hình.
 *
 * `expired` do nơi gọi tự tính lại theo đồng hồ (Date.now() > expiresAt) và
 * truyền vào — component này chỉ vẽ, không tự đếm giờ.
 */
export function ConfirmButtons({
  proposal,
  onConfirm,
  expired,
  disabled = false,
}: {
  proposal: Proposal;
  onConfirm: (decision: "yes" | "no") => void;
  expired: boolean;
  /** Mất mạng/chưa kết nối lại được — khác với `expired`, chỉ là tạm thời.
   * Không tự gửi khi mất mạng (không xếp hàng đợi như `send`): khách phải
   * bấm lại khi có mạng để chắc chắn máy thật sự nhận được. */
  disabled?: boolean;
}) {
  const blocked = expired || disabled;
  return (
    <div className="confirm" role="group" aria-label="Xác nhận đề xuất">
      {expired ? (
        <p className="confirm__msg confirm__msg--expired" role="status">
          Đề xuất đã hết hạn, anh chị nhắn lại giúp em
        </p>
      ) : disabled ? (
        <p className="confirm__msg confirm__msg--expired" role="status">
          Đang mất mạng, anh chị chờ chút rồi bấm lại nhé
        </p>
      ) : (
        <p className="confirm__msg">{proposal.whenSpoken}</p>
      )}
      {/* Xếp DỌC, mỗi nút rộng hết chiều ngang — đúng quy tắc CONTEXT.md
          (nút cao tối thiểu 56px, rộng hết chiều ngang), không xếp ngang
          chia đôi bề rộng như trước. */}
      <div className="confirm__col">
        <Button variant="primary" disabled={blocked} onClick={() => onConfirm("yes")}>
          Đồng ý
        </Button>
        <Button variant="ghost" disabled={blocked} onClick={() => onConfirm("no")}>
          Không
        </Button>
      </div>
    </div>
  );
}
