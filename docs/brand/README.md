# Bộ nhận diện Salona

Thiết kế gốc nằm ở mockup `docs/superpowers/specs/2026-08-06-booking-nail-toc/ui-mockup.html`
(và bản `ui-mockup-streaming.html`) — avatar 44px cạnh bong bóng bot đã được chốt ở đó từ
đầu, frontend chỉ là hiện thực lại. Sửa cách bày avatar thì sửa cả hai nơi, đừng để lệch.

## Ảnh gốc KHÔNG nằm trong git

`docs/brand/salona-source.png` (2048×2048, 6MB) bị `.gitignore` loại có chủ ý: repo chỉ
chứa các file nhỏ cắt ra từ nó. Ai clone mới sẽ **không** có ảnh gốc — cần xuất lại cỡ khác
thì xin file từ người giữ bản gốc rồi đặt đúng đường dẫn trên, sau đó chạy lệnh bên dưới.

## Các file sinh ra

| File | Cỡ | Dùng ở đâu |
|---|---|---|
| `frontend/public/favicon-32.png` | 32×32 | icon trên tab trình duyệt |
| `frontend/public/apple-touch-icon.png` | 180×180 | icon khi khách "thêm vào màn hình chính" trên iPhone |
| `frontend/public/brand/salona-avatar.png` | 192×192 | ảnh đại diện bot trong khung chat (hiển thị 44px) |
| `frontend/public/brand/salona-logo.png` | 512×512 | logo tròn đầy đủ, để dành cho màn đăng nhập / tài liệu |
| `docs/.../ui-mockup*.html` → `ai-avatar.png` | 176×176 | avatar trong hai file mockup |

Hai vùng cắt: **mặt** (icon + avatar) và **huy hiệu tròn** đầy đủ (logo). Ở 32px thì nguyên
huy hiệu — chữ SALONA, đường mạch, mấy khung hội thoại — nát thành nhiễu, chỉ khuôn mặt mới
đọc được; nên mọi thứ hiển thị nhỏ đều cắt vào mặt.

```bash
cd <repo root>
SRC=docs/brand/salona-source.png
FACE="-crop 1160x1160+440+230 +repage"
BADGE="-crop 1740x1740+160+140 +repage"
SPEC=docs/superpowers/specs/2026-08-06-booking-nail-toc

convert $SRC $FACE  -resize 192x192 -colors 128 -strip frontend/public/brand/salona-avatar.png
convert $SRC $FACE  -resize  32x32              -strip frontend/public/favicon-32.png
convert $SRC $FACE  -resize 180x180 -colors 128 -strip frontend/public/apple-touch-icon.png
convert $SRC $FACE  -resize 176x176 -colors 128 -strip $SPEC/ai-avatar.png
convert $SRC $BADGE -resize 512x512 -colors 192 -strip frontend/public/brand/salona-logo.png
```

`-colors` là để nén: 192px mà không giảm bảng màu thì nặng 70KB, giảm còn 20KB mà mắt thường
không thấy khác — khách dùng 3G, mỗi KB đều đáng.
