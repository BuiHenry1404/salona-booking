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
| `frontend/public/brand/salona-avatar.png` | 128×128 | ảnh đại diện bot trong khung chat (hiển thị 44px) |
| `frontend/public/brand/salona-logo.png` | 512×512 | logo tròn đầy đủ, để dành cho màn đăng nhập / tài liệu |
| `docs/.../ui-mockup*.html` → `ai-avatar.png` | 176×176 | avatar trong hai file mockup |

## Hai vùng cắt, chia theo cỡ hiển thị

- **Huy hiệu tròn đầy đủ** — avatar trong chat (44px), avatar mockup, logo. Ở 44px vẫn đọc ra
  cả người, áo và máy tính bảng, nên giữ nguyên hình cho khách nhận ra nhân vật.
- **Khuôn mặt** — favicon 32px và apple-touch. Ở cỡ đó nguyên huy hiệu (chữ SALONA, đường
  mạch, mấy khung hội thoại) nát thành nhiễu, chỉ còn khuôn mặt là đọc được.

Ranh giới nằm đâu đó quanh 40px. Thêm chỗ hiển thị mới thì chọn vùng cắt theo cỡ thật, đừng
chọn theo thói quen.

Cỡ file cũng chọn theo chỗ hiển thị, không lấy bản to nhất cho tiện: avatar xuất 128px là đủ
cho màn retina ở 44px — nạp bản 512px vào đó thì tốn 216KB thay vì 12KB, khách dùng 3G.

```bash
cd <repo root>
SRC=docs/brand/salona-source.png
FACE="-crop 1160x1160+440+230 +repage"
BADGE="-crop 1740x1740+160+140 +repage"
SPEC=docs/superpowers/specs/2026-08-06-booking-nail-toc

convert $SRC $FACE  -resize  32x32              -strip frontend/public/favicon-32.png
convert $SRC $FACE  -resize 180x180 -colors 128 -strip frontend/public/apple-touch-icon.png
convert $SRC $BADGE -resize 128x128 -colors 128 -strip frontend/public/brand/salona-avatar.png
convert $SRC $BADGE -resize 176x176 -colors 128 -strip $SPEC/ai-avatar.png
convert $SRC $BADGE -resize 512x512 -colors 192 -strip frontend/public/brand/salona-logo.png
```

`-colors` là để nén: không giảm bảng màu thì avatar nặng gấp ba, mà mắt thường không thấy
khác — khách dùng 3G, mỗi KB đều đáng.
