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
}: {
  proposal: Proposal;
  onConfirm: (decision: "yes" | "no") => void;
  expired: boolean;
}) {
  return (
    <div className="confirm" role="group" aria-label="Xác nhận đề xuất">
      {expired ? (
        <p className="confirm__msg confirm__msg--expired" role="status">
          Đề xuất đã hết hạn, anh chị nhắn lại giúp em
        </p>
      ) : (
        <p className="confirm__msg">{proposal.whenSpoken}</p>
      )}
      <div className="confirm__row">
        <Button
          variant="primary"
          fullWidth={false}
          disabled={expired}
          onClick={() => onConfirm("yes")}
        >
          Đồng ý
        </Button>
        <Button
          variant="ghost"
          fullWidth={false}
          disabled={expired}
          onClick={() => onConfirm("no")}
        >
          Không
        </Button>
      </div>
    </div>
  );
}
