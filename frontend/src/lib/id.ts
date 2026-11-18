/**
 * `message_id` duy nhất cho mỗi lượt gửi (send_message/confirm_proposal) —
 * server dùng để dedupe ở một task sau. Ưu tiên `crypto.randomUUID()`; một số
 * môi trường test (jsdom cũ) không có hàm này nên cần phương án dự phòng thay
 * vì polyfill `crypto` toàn cục.
 */
export function newMessageId(): string {
  if (typeof crypto !== "undefined" && typeof crypto.randomUUID === "function") {
    return crypto.randomUUID();
  }
  return `id-${Date.now()}-${Math.random().toString(36).slice(2)}`;
}
