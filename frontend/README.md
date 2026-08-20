# Giao diện web — Tiệm Nail & Tóc

React + TypeScript + Vite. Người dùng chính là khách lớn tuổi, nên mọi lựa chọn
giao diện đều nghiêng về dễ nhìn dễ bấm hơn là gọn đẹp.

## Chạy

```bash
npm install
npm run dev      # http://localhost:5173
npm test
npm run build
```

`VITE_API_BASE` trỏ tới backend, `VITE_SHOP_PHONE` là số hiện trên nút gọi lúc AI
lỗi. Dev đọc từ `.env.development`, build đọc từ `.env.production`.

## Ràng buộc không được phá

| Ràng buộc | Chốt bằng |
|---|---|
| Tương phản ≥ 4.5:1 | `src/styles/tokens.test.ts` |
| Cỡ chữ nền ≥ 19px, nút ≥ 56px | cùng file test trên |
| Không hiện tên tool thô cho khách | `src/lib/toolLabels.test.ts` + grep ở task 8 |
| Mất mạng không xóa chữ đã stream | `src/hooks/useAgentStream.test.ts` |
| Hủy lịch phải xác nhận | `src/screens/MyAppointmentsScreen.test.tsx` |
| Câu gõ lúc mất mạng không được biến mất | `src/hooks/useAgentStream.test.ts` — `có mạng lại thì tự gửi nốt hàng đợi` |
| AI lỗi thì luôn còn nút gọi cho tiệm | `src/screens/ChatScreen.test.tsx` — `AI lỗi thì hiện nút gọi thẳng cho tiệm` |

## Deploy

`npm run build` sinh tĩnh trong `dist/`, đưa lên bất kỳ static host nào. Ba thứ
phải đúng ở phía server:

1. **SPA fallback** — mọi đường dẫn không khớp file đều trả `index.html`, nếu
   không thì tải lại trang `/lich-cua-toi` sẽ ra 404.
2. **CORS** — `ALLOWED_ORIGINS` của backend phải chứa domain của giao diện, kể
   cả cho Socket.IO.
3. **HTTPS** — Web Speech API (nút micro) chỉ chạy trên HTTPS hoặc localhost.
4. **`VITE_SHOP_PHONE` là số thật.** Nút gọi lúc AI hỏng mà quay nhầm số thì
   tệ hơn là không có nút.
