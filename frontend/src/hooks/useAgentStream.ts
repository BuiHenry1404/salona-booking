import { useCallback, useEffect, useRef, useState } from "react";
import { acquireSocket, releaseSocket } from "../lib/socket";
import type { Socket } from "../lib/socket";
import { toolLabel } from "../lib/toolLabels";

export type Phase = "idle" | "thinking" | "tool" | "answering";

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

let counter = 0;
const nextId = () => `m${++counter}`;

/**
 * Một máy trạng thái duy nhất cho cả khung chat. Màn hình chat chỉ việc vẽ
 * `messages` + `steps` + `phase`, không tự ghép sự kiện — đó là chỗ dễ sinh ra
 * hai bong bóng cho cùng một câu trả lời.
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
  const socketRef = useRef<Socket | null>(null);
  // id của bong bóng bot đang được stream. Dùng ref chứ không dùng state vì
  // handler socket đóng gói giá trị cũ nếu đọc từ state.
  const streamingId = useRef<string | null>(null);
  // Backend không có turn_id — guard nội bộ chống complete kép của cùng turn.
  // Reset khi bắt đầu turn mới (gửi tin hoặc turn_started).
  const completed = useRef(false);
  // Hàng đợi câu gõ lúc mất mạng. Ref chứ không state: `onConnect` đọc nó và
  // sẽ thấy mảng cũ nếu để trong state.
  const queue = useRef<Array<{ id: string; message: string }>>([]);

  useEffect(() => {
    const socket = acquireSocket();
    socketRef.current = socket;
    setConnected(socket.connected);

    const beginTurn = () => {
      streamingId.current = null;
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
      waiting.forEach(({ message }) => socket.emit("send_message", { message }));
      setMessages((prev) => prev.map((m) => (m.pending ? { ...m, pending: false } : m)));
      setPhase("thinking");
    };

    const onDisconnect = () => {
      setConnected(false);
      // KHÔNG xóa messages, KHÔNG reset streaming. Người lớn tuổi thấy chữ
      // biến mất sẽ tưởng mình vừa làm hỏng máy. Chỉ thêm một dòng báo.
      setOffline(true);
    };

    const onTurnStarted = () => beginTurn();

    const onToolStarted = (data: { name: string }) => {
      setPhase("tool");
      setSteps((prev) => [
        ...prev,
        { name: data.name, label: toolLabel(data.name).running, done: false, ok: true },
      ]);
    };

    const onToolFinished = (data: { name: string; ok: boolean }) => {
      // Thu thành dòng có dấu tích và GIỮ LẠI trên màn hình: khách phải thấy
      // máy đã làm gì, nếu không họ nghi câu trả lời là bịa.
      //
      // Cùng tool có thể chạy nhiều lần trong một turn — mỗi tool_finished
      // chỉ đích invocation CHƯA xong GẦN NHẤT, không đánh dấu đồng loạt.
      setSteps((prev) => {
        const label = toolLabel(data.name).done;
        let targeted = -1;
        for (let i = prev.length - 1; i >= 0; i -= 1) {
          if (prev[i].name === data.name && !prev[i].done) {
            targeted = i;
            break;
          }
        }
        if (targeted < 0) return prev;
        return prev.map((step, i) =>
          i === targeted ? { ...step, done: true, ok: data.ok, label } : step,
        );
      });
    };

    const onToken = (data: { text: string }) => {
      setPhase("answering");
      // Đọc/ghi ref ở ĐÂY (trong handler), KHÔNG trong updater của setState.
      // StrictMode gọi updater HAI LẦN với cùng một `prev`: nếu gán
      // `streamingId.current` trong updater thì lần gọi thứ hai thấy ref đã có,
      // đi nhầm nhánh append trên `prev` chưa chứa bubble → trả `prev` nguyên vẹn
      // → React commit kết quả lần hai → bong bóng bot biến mất hẳn.
      if (streamingId.current) {
        const id = streamingId.current;
        setMessages((prev) =>
          prev.map((m) => (m.id === id ? { ...m, text: m.text + data.text } : m)),
        );
        return;
      }
      const id = nextId();
      streamingId.current = id;
      setMessages((prev) => [...prev, { id, role: "bot" as const, text: data.text }]);
    };

    const onComplete = (data: { answer?: string }) => {
      if (completed.current) return; // complete kép của cùng turn không xử lý
      completed.current = true;

      const answer = data?.answer ?? "";
      // Capture TRƯỚC setState: updater có thể chạy sau dòng dưới, đọc ref
      // lúc đó là null và rơi nhầm sang nhánh tạo bubble mới.
      const streamId = streamingId.current;
      streamingId.current = null;

      setMessages((prev) => {
        if (streamId) {
          // Câu chốt của backend là nguồn đúng nhất: nếu vài token rơi mất
          // giữa đường thì đây là lúc chữa lại.
          return prev.map((m) => (m.id === streamId && answer ? { ...m, text: answer } : m));
        }
        return answer ? [...prev, { id: nextId(), role: "bot" as const, text: answer }] : prev;
      });
      setSteps([]);
      setPhase("idle");
      // Lượt KẾT THÚC BÌNH THƯỜNG phải tự xóa cờ hỏng của lượt trước — không
      // thể dựa vào turn_started đi trước (lượt gửi lại có thể thiếu nó).
      setFailed(false);
    };

    const onError = (data: { message?: string }) => {
      streamingId.current = null;
      setSteps([]);
      setPhase("idle");
      setFailed(true);
      setMessages((prev) => [
        ...prev,
        {
          id: nextId(),
          role: "bot" as const,
          text: data?.message ?? "Máy đang bận chút xíu, cô chú nhắn lại giúp con nhé.",
        },
      ]);
    };

    const handlers: Array<[string, (data?: unknown) => void]> = [
      ["connect", onConnect],
      ["disconnect", onDisconnect],
      ["turn_started", onTurnStarted],
      ["tool_started", onToolStarted as (data: unknown) => void],
      ["tool_finished", onToolFinished as (data: unknown) => void],
      ["token", onToken as (data: unknown) => void],
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
    const socket = socketRef.current;
    const live = socket?.connected ?? false;

    // Bong bóng của khách hiện NGAY dù có mạng hay không. Chữ vừa gõ mà biến
    // mất là cảm giác "máy nuốt mất câu của mình".
    setMessages((prev) => [...prev, { id, role: "user", text: message, pending: !live }]);

    if (!live) {
      queue.current.push({ id, message });
      return;
    }
    completed.current = false; // turn mới bắt đầu từ câu này
    setFailed(false);
    setPhase("thinking");
    socket!.emit("send_message", { message });
  }, []);

  return { messages, steps, phase, connected, offline, failed, send };
}
