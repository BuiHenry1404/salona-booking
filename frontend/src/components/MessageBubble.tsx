import { BotAvatar } from "./BotAvatar";
import "./MessageBubble.css";

export interface MessageBubbleProps {
  role: "user" | "bot";
  text: string;
  /** Câu của khách gõ lúc mất mạng, còn nằm trong hàng đợi chưa gửi được. */
  pending?: boolean;
  /** Bong bóng bot đang được stream token — hiện con trỏ nhấp nháy cuối dòng. */
  streaming?: boolean;
}

/**
 * MỘT bong bóng chat, tách riêng để màn hình chat hôm nay và màn xem lại
 * ngày cũ (task 4b) vẽ giống hệt nhau — khách không phải học lại cách đọc.
 *
 * Chỉ vẽ — không tự xử lý stream/token/queue, việc đó của `useAgentStream`.
 */
export function MessageBubble({ role, text, pending, streaming }: MessageBubbleProps) {
  return (
    <div className={role === "bot" ? "bubble__wrap bubble__wrap--bot" : "bubble__wrap"}>
      {/* Ảnh đại diện Salona đứng cạnh bong bóng bot; phía khách không có
          ảnh — bên phải đã đủ tín hiệu "câu này của mình". */}
      {role === "bot" && <BotAvatar />}
      <p className={role === "user" ? "bubble bubble--me" : "bubble bubble--bot"}>
        {text}
        {role === "bot" && streaming && <span className="caret" aria-hidden="true" />}
      </p>
      {/* Câu gõ lúc mất mạng: giữ nguyên trên màn hình, nói rõ là chưa gửi
          được và máy đang tự lo. Khách không phải gõ lại. */}
      {pending && <p className="bubble__pending">Đang gửi lại…</p>}
    </div>
  );
}
