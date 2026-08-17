export type Role = "user" | "admin";

interface SessionState {
  token: string | null;
  role: Role | null;
}

type Listener = (state: SessionState) => void;

/**
 * Access token chỉ sống trong BỘ NHỚ của tab (module scope) — CONTEXT.md chốt
 * rằng cất vào kho của trình duyệt là phá bỏ thiết kế an toàn của backend:
 * refresh token đã nằm trong cookie HttpOnly, access token nằm đâu JavaScript
 * đọc được thì một lỗ XSS là mất sạch phiên.
 *
 * Hệ quả: tải lại trang là mất access token — và đó là việc của refresh
 * (cookie HttpOnly vẫn còn, api.ts sẽ đổi lấy token mới lúc cần).
 *
 * session là NGUỒN CHÂN LÝ duy nhất về token/role: api client xoay token qua
 * save() ngay giữa một request. AuthContext đăng ký subscribe() để UI chạy
 * theo, tránh giữ bản sao cũ (token chết) trong React state.
 */
let state: SessionState = { token: null, role: null };
const listeners = new Set<Listener>();

function notify(): void {
  for (const listener of listeners) listener({ ...state });
}

export const session = {
  save(token: string, role: Role) {
    state = { token, role };
    notify();
  },

  load(): SessionState {
    return { ...state };
  },

  clear() {
    state = { token: null, role: null };
    notify();
  },

  /** AuthContext dùng để phản ánh mọi thay đổi token (kể cả từ api client
   * refresh giữa chừng) mà không polling. Trả hàm để dọn khi unmount. */
  subscribe(listener: Listener): () => void {
    listeners.add(listener);
    return () => listeners.delete(listener);
  },
};
