export type Role = "user" | "admin";

interface SessionState {
  token: string | null;
  role: Role | null;
}

/**
 * Access token chỉ sống trong BỘ NHỚ của tab (module scope) — CONTEXT.md chốt
 * rằng cất vào kho của trình duyệt là phá bỏ thiết kế an toàn của backend:
 * refresh token đã nằm trong cookie HttpOnly, access token nằm đâu JavaScript
 * đọc được thì một lỗ XSS là mất sạch phiên.
 *
 * Hệ quả: tải lại trang là mất access token — và đó là việc của refresh
 * (cookie HttpOnly vẫn còn, api.ts sẽ đổi lấy token mới lúc cần).
 */
let state: SessionState = { token: null, role: null };

export const session = {
  save(token: string, role: Role) {
    state = { token, role };
  },

  load(): SessionState {
    return { ...state };
  },

  clear() {
    state = { token: null, role: null };
  },
};
