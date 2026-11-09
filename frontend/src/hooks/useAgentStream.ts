import { useCallback, useEffect, useRef, useState } from "react";
import { acquireSocket, releaseSocket } from "../lib/socket";
import type { Socket } from "../lib/socket";
import { newMessageId } from "../lib/id";

/** "answering" không còn được đặt (server không stream `token` nữa) — giữ
 * trong kiểu cho `ChatScreen` (caret) tới khi màn hình được dọn theo. */
export type Phase = "idle" | "thinking" | "tool" | "answering";

/** Câu chung khi server không gửi nhãn — không bao giờ hiện tên tool thô. */
const GENERIC_TOOL_LABEL = "Đang xử lý…";

export interface ChatMessage {
  id: string;
  role: "user" | "bot";
  text: string;
  /** Câu của khách gõ lúc mất mạng, còn nằm trong hàng đợi chưa gửi được. */
  pending?: boolean;
}

export interface ToolStep {
  name: string;
  label: string;
  done: boolean;
  ok: boolean;
}

/** Đề xuất đang chờ khách bấm Đồng ý / Không — chỉ MỘT cái tại một thời điểm,
 * luôn là đề xuất MỚI NHẤT (complete sau thay complete trước, không cộng dồn). */
export interface Proposal {
  id: string;
  kind: "book" | "move" | "cancel";
  expiresAt: string;
  whenSpoken: string;
}

/** `tool_started` / `tool_finished`: `label` là câu tiếng Việt server dựng sẵn
 * (`TOOL_LABELS` của gói salon); `name` chỉ để ghép cặp bắt đầu/xong. */
interface ToolEvent {
  name: string;
  label?: string;
  ok?: boolean;
}

/** Hình dạng `proposal` do backend gửi trong sự kiện `complete` (snake_case). */
interface RawProposal {
  id: string;
  kind: "book" | "move" | "cancel";
  expires_at: string;
  when_spoken: string;
}

let counter = 0;
const nextId = () => `m${++counter}`;

/**
 * Một máy trạng thái duy nhất cho cả khung chat. Màn hình chat chỉ việc vẽ
 * `messages` + `steps` + `phase`, không tự ghép sự kiện — đó là chỗ dễ sinh ra
 * hai bong bóng cho cùng một câu trả lời.
 *
 * Server không stream câu trả lời (không có `token`): câu tới khách chỉ đến
 * trong `complete`, sau khi đã qua kiểm tra — mỗi lượt đúng MỘT bong bóng.
 *
 * Socket lấy từ singleton `acquireSocket()` — auth/token do tầng đó lo (đọc
 * động từ session mỗi lần kết nối), hook không phụ thuộc token React và không
 * tự reconnect khi token đổi.
 */
export function useAgentStream() {
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [steps, setSteps] = useState<ToolStep[]>([]);
  const [phase, setPhase] = useState<Phase>("idle");
  const [connected, setConnected] = useState(false);
  const [offline, setOffline] = useState(false);
  // Lượt vừa rồi AI trả lời không được. Màn hình dựa vào cờ này để hiện nút
  // gọi điện cho tiệm — khách vẫn đặt được lịch bằng cách nói chuyện với người.
  const [failed, setFailed] = useState(false);
  // Đề xuất GẦN NHẤT còn hiệu lực — null nghĩa là không có gì để Đồng ý/Không.
  const [proposal, setProposal] = useState<Proposal | null>(null);
  const socketRef = useRef<Socket | null>(null);
  // Backend không có turn_id — guard nội bộ chống complete kép của cùng turn.
  // Reset khi bắt đầu turn mới (gửi tin hoặc turn_started).
  const completed = useRef(false);
  // Hàng đợi câu gõ lúc mất mạng. Ref chứ không state: `onConnect` đọc nó và
  // sẽ thấy mảng cũ nếu để trong state.
  const queue = useRef<Array<{ id: string; message: string; messageId: string }>>([]);

  useEffect(() => {
    const socket = acquireSocket();
    socketRef.current = socket;
    setConnected(socket.connected);

    const beginTurn = () => {
      completed.current = false;
      setSteps([]);
      setFailed(false);
      setPhase("thinking");
    };

    const onConnect = () => {
      setConnected(true);
      setOffline(false);
      // Nối lại mạng thì gửi nốt những câu đang chờ, theo đúng thứ tự gõ.
      // Người lớn tuổi gõ xong là coi như đã nhắn — bắt họ gõ lại là mất câu.
      const waiting = queue.current;
      queue.current = [];
      if (waiting.length === 0) return;
      waiting.forEach(({ message, messageId }) =>
        socket.emit("send_message", { message, message_id: messageId }),
      );
      setMessages((prev) => prev.map((m) => (m.pending ? { ...m, pending: false } : m)));
      setPhase("thinking");
    };

    const onDisconnect = () => {
      setConnected(false);
      // KHÔNG xóa messages. Người lớn tuổi thấy chữ biến mất sẽ tưởng mình
      // vừa làm hỏng máy. Chỉ thêm một dòng báo.
      setOffline(true);
    };

    const onTurnStarted = () => beginTurn();

    const onToolStarted = (data: ToolEvent) => {
      setPhase("tool");
      const label = data.label || GENERIC_TOOL_LABEL;
      setSteps((prev) => [...prev, { name: data.name, label, done: false, ok: true }]);
    };

    const onToolFinished = (data: ToolEvent) => {
      // Thu thành dòng có dấu tích và GIỮ LẠI trên màn hình: khách phải thấy
      // máy đã làm gì, nếu không họ nghi câu trả lời là bịa.
      //
      // Cùng tool có thể chạy nhiều lần trong một turn — mỗi tool_finished
      // chỉ đích invocation CHƯA xong GẦN NHẤT, không đánh dấu đồng loạt.
      setSteps((prev) => {
        let targeted = -1;
        for (let i = prev.length - 1; i >= 0; i -= 1) {
          if (prev[i].name === data.name && !prev[i].done) {
            targeted = i;
            break;
          }
        }
        if (targeted < 0) return prev;
        return prev.map((step, i) =>
          i === targeted ? { ...step, done: true, ok: data.ok ?? true } : step,
        );
      });
    };

    const onComplete = (data: { answer?: string; proposal?: RawProposal | null }) => {
      if (completed.current) return; // complete kép của cùng turn không xử lý
      completed.current = true;

      const answer = data?.answer ?? "";
      // Mỗi complete THAY HẲN đề xuất cũ — kể cả bằng null. Không bao giờ
      // cộng dồn: chỉ đề xuất MỚI NHẤT mới được phép có nút Đồng ý/Không.
      const rawProposal = data?.proposal ?? null;
      setProposal(
        rawProposal
          ? {
              id: rawProposal.id,
              kind: rawProposal.kind,
              expiresAt: rawProposal.expires_at,
              whenSpoken: rawProposal.when_spoken,
            }
          : null,
      );
      // id tạo NGOÀI updater: StrictMode gọi updater hai lần, id phải giống nhau.
      const id = nextId();
      setMessages((prev) =>
        answer ? [...prev, { id, role: "bot" as const, text: answer }] : prev,
      );
      setSteps([]);
      setPhase("idle");
      // Lượt KẾT THÚC BÌNH THƯỜNG phải tự xóa cờ hỏng của lượt trước — không
      // thể dựa vào turn_started đi trước (lượt gửi lại có thể thiếu nó).
      setFailed(false);
    };

    const onError = (data: { message?: string }) => {
      setSteps([]);
      setPhase("idle");
      setFailed(true);
      const id = nextId();
      setMessages((prev) => [
        ...prev,
        {
          id,
          role: "bot" as const,
          text: data?.message ?? "Máy đang bận chút xíu, anh chị nhắn lại giúp em nhé.",
        },
      ]);
    };

    const handlers: Array<[string, (data?: unknown) => void]> = [
      ["connect", onConnect],
      ["disconnect", onDisconnect],
      ["turn_started", onTurnStarted],
      ["tool_started", onToolStarted as (data: unknown) => void],
      ["tool_finished", onToolFinished as (data: unknown) => void],
      ["complete", onComplete as (data: unknown) => void],
      ["error", onError as (data: unknown) => void],
    ];
    handlers.forEach(([event, handler]) => socket.on(event, handler));

    return () => {
      // Gỡ ĐÚNG handler của mình rồi mới trả socket: socket dùng chung với
      // `useShopStatus` — wildcard off(event) sẽ gỡ cả listener của nó.
      handlers.forEach(([event, handler]) => socket.off(event, handler));
      socketRef.current = null;
      releaseSocket();
    };
  }, []);

  const send = useCallback((text: string) => {
    const message = text.trim();
    if (!message) return;
    const id = nextId();
    const messageId = newMessageId();
    const socket = socketRef.current;
    const live = socket?.connected ?? false;

    // Bong bóng của khách hiện NGAY dù có mạng hay không. Chữ vừa gõ mà biến
    // mất là cảm giác "máy nuốt mất câu của mình".
    setMessages((prev) => [...prev, { id, role: "user", text: message, pending: !live }]);
    // Khách gõ câu mới coi như thay cho đề xuất đang treo — server sẽ tự
    // supersede, phía này không chờ complete mới để xoá nút.
    setProposal(null);

    if (!live) {
      queue.current.push({ id, message, messageId });
      return;
    }
    completed.current = false; // turn mới bắt đầu từ câu này
    setFailed(false);
    setPhase("thinking");
    socket!.emit("send_message", { message, message_id: messageId });
  }, []);

  // Bấm Đồng ý/Không cho đề xuất GẦN NHẤT. Xoá `proposal` NGAY (trước khi
  // socket trả lời) để lỡ bấm đúp không gửi lại hai lần.
  //
  // KHÔNG xếp hàng đợi như `send()` khi mất mạng: xác nhận đặt/huỷ lịch mà
  // gửi trễ, khách sẽ tưởng đã xong trong khi máy chưa nhận được gì — màn
  // hình phải để khách tự bấm lại lúc có mạng, không âm thầm "giữ hộ".
  // UI (ChatScreen) đã disable nút khi mất mạng; đây là phòng vệ ở tầng dưới.
  const confirm = useCallback(
    (decision: "yes" | "no") => {
      if (!proposal) return;
      const socket = socketRef.current;
      const live = socket?.connected ?? false;
      if (!live) return; // không emit, không thêm bong bóng, proposal giữ nguyên

      const id = nextId();
      const text = decision === "yes" ? "Đồng ý" : "Không";

      setMessages((prev) => [...prev, { id, role: "user", text }]);
      setProposal(null);
      completed.current = false;
      setFailed(false);
      setPhase("thinking");
      socket!.emit("confirm_proposal", {
        proposal_id: proposal.id,
        decision,
        message_id: newMessageId(),
      });
    },
    [proposal],
  );

  return { messages, steps, phase, connected, offline, failed, proposal, send, confirm };
}
