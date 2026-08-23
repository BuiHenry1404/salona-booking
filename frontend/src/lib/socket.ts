import { io } from "socket.io-client";
import type { Socket } from "socket.io-client";
import { api } from "./api";
import { session } from "../auth/session";

const BASE = import.meta.env.VITE_API_BASE ?? "";

let shared: Socket | null = null;
let refCount = 0;
let sharedConnectErrorHandler: ((err: Error) => void) | null = null;

/**
 * Gắn cơ chế phục hồi auth cho RIÊNG một socket instance.
 *
 * socket.io-client: `connect_error` mà socket còn `active` là lỗi tạm thời và
 * nó tự reconnect — can thiệp lúc đó chỉ thêm vòng lặp. Chỉ khi `active`
 * === false (server trả lỗi ngay lúc handshake, ví dụ JWT chết) mới cần
 * xoay token rồi chủ động connect lại.
 *
 * Đi qua /auth/me thay vì gọi /auth/refresh trực tiếp: token Bearer chết →
 * api.ts tự refresh bằng cookie HttpOnly → session.save(token mới) → retry.
 * Thất bại thì thôi — api/AuthContext đã quản lý session, không tự clear và
 * không loop vô hạn.
 *
 * `recoveryInFlight` nằm trong closure của TỪNG socket: socket cũ bị release
 * giữa lúc recovery còn treo không được chặn Recovery của socket mới.
 *
 * Trả về handler để caller giữ lại và off CHÍNH XÁC handler đó khi release.
 */
function installAuthRecovery(socket: Socket): (err: Error) => void {
  let recoveryInFlight = false;

  const handler = (): void => {
    if (socket.active) return; // Socket.IO đang tự lo phần reconnect

    if (recoveryInFlight) return;
    recoveryInFlight = true;

    api
      .get("/api/v1/auth/me")
      .then(() => {
        // Auth callback của lần connect kế tiếp sẽ đọc token mới từ session.
        // Chỉ socket đã khởi tạo recovery này được connect — và chỉ khi nó
        // vẫn còn là shared hiện tại (chưa bị release thay bằng socket khác).
        if (shared === socket && !socket.connected) socket.connect();
      })
      .catch(() => {
        /* hết phiên thật — dừng ở đây, không thử lại */
      })
      .finally(() => {
        recoveryInFlight = false;
      });
  };

  socket.on("connect_error", handler);
  return handler;
}

/**
 * MỘT kết nối duy nhất cho cả app, đếm tham chiếu — app CHỦ ĐỘNG giữ
 * singleton bằng chính mình, không dựa vào việc socket.io-client gộp kết nối
 * theo URL. Mục tiêu: không tạo nhiều connection thừa, và không để một
 * consumer `disconnect()` cái socket mà consumer khác vẫn đang dùng.
 *
 * Đổi lại, mỗi hook PHẢI tự `socket.off(event, handler)` handler của mình khi
 * dọn dẹp, nếu không handler chồng lên nhau sau vài lần chuyển màn hình.
 */
export function acquireSocket(): Socket {
  if (!shared) {
    shared = io(BASE, {
      // KHÔNG pin token: hàm này được gọi lại mỗi lần (re)connect, nên
      // socket không bao giờ xin kết nối bằng JWT đã bị api.ts xoay thay.
      auth: (cb: (payload: { token: string | null }) => void) => {
        cb({ token: session.load().token });
      },
      transports: ["websocket", "polling"],
      reconnection: true,
      reconnectionDelay: 1000,
      reconnectionDelayMax: 5000,
    });
    sharedConnectErrorHandler = installAuthRecovery(shared);
  }
  refCount += 1;
  return shared;
}

export function releaseSocket(): void {
  refCount -= 1;
  if (refCount <= 0 && shared) {
    const s = shared;
    // Gỡ ĐÚNG handler nội bộ mình đã đăng ký — không wildcard off(event),
    // để không vô tình gỡ listener của consumer khác cùng nghe sự kiện này.
    if (sharedConnectErrorHandler) {
      s.off("connect_error", sharedConnectErrorHandler);
      sharedConnectErrorHandler = null;
    }
    s.disconnect();
    shared = null;
    refCount = 0;
  }
}

export type { Socket };
