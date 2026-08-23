/**
 * Ảnh đại diện của Salona — trợ lý ảo của tiệm. Dùng ở bong bóng bot và ở
 * lời chào đầu màn chat, để khách (phần lớn lớn tuổi) nhìn là biết ngay câu
 * nào của tiệm, câu nào của mình.
 *
 * Dùng NGUYÊN huy hiệu tròn (cả người, áo, máy tính bảng), không cắt vào
 * khuôn mặt — ở 44px vẫn đọc ra. Favicon 32px thì ngược lại, phải cắt mặt vì
 * cỡ đó huy hiệu nát thành nhiễu; hai vùng cắt khác nhau là có chủ ý, xem
 * `docs/brand/README.md`.
 *
 * File nằm trong `public/brand/` chứ không import qua bundler, để mockup
 * trong `docs/superpowers/specs/` dùng chung một ảnh mà không phải đi qua
 * Vite. Xuất ở 128px cho vừa màn retina, không nạp bản 512px vào chỗ hiển
 * thị 44px — khách dùng 3G.
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
