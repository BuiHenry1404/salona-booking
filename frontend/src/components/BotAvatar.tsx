/**
 * Ảnh đại diện của Salona — trợ lý ảo của tiệm. Dùng ở bong bóng bot và ở
 * lời chào đầu màn chat, để khách (phần lớn lớn tuổi) nhìn là biết ngay câu
 * nào của tiệm, câu nào của mình.
 *
 * File nằm trong `public/brand/` chứ không import qua bundler: ảnh này còn
 * dùng lại cho favicon/icon app, giữ MỘT nguồn ở `public/` cho khỏi lệch.
 *
 * `alt=""` + `aria-hidden`: ảnh chỉ trang trí, vai trò người nói đã nằm ở
 * chữ và ở class bong bóng — trình đọc màn hình đọc thêm chỉ tổ ồn.
 */
export function BotAvatar({ className = "avatar" }: { className?: string }) {
  return (
    <img
      className={className}
      src="/brand/salona-avatar.png"
      alt=""
      aria-hidden="true"
      width={44}
      height={44}
      loading="lazy"
      decoding="async"
    />
  );
}
