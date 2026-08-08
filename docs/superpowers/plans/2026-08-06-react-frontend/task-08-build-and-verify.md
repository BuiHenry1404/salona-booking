# Task 8 · Kiểm tra, đóng gói và deploy

> Thuộc plan [Frontend React](README.md). **Đọc [Ràng buộc toàn cục](README.md#ràng-buộc-toàn-cục) trước khi bắt đầu** — chúng áp cho mọi task, kể cả khi không nhắc lại ở đây.

**Files:**
- Create: `frontend/.env.production`, `frontend/src/components/ErrorBoundary.tsx`, `frontend/src/styles/tokens.test.ts`, `frontend/README.md`
- Modify: `frontend/src/App.tsx`, `frontend/vite.config.ts`, `README.md` (gốc repo)

**Interfaces:**
- Consumes: mọi thứ từ task 1–7
- Produces: bản build production trong `frontend/dist/` và hướng dẫn deploy

- [ ] **Step 1: Viết test kiểm tương phản màu (sẽ fail)**

Bảng màu là thứ dễ bị sửa "cho đẹp" rồi trượt WCAG mà không ai biết. Buộc nó bằng test.

Tạo `frontend/src/styles/tokens.test.ts`:

```ts
import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

const css = readFileSync(new URL("./tokens.css", import.meta.url), "utf8");

function readVar(name: string): string {
  const match = css.match(new RegExp(`${name}:\\s*(#[0-9a-fA-F]{6})`));
  if (!match) throw new Error(`Không tìm thấy biến ${name} trong tokens.css`);
  return match[1];
}

function luminance(hex: string): number {
  const channels = [1, 3, 5].map((i) => {
    const value = parseInt(hex.slice(i, i + 2), 16) / 255;
    return value <= 0.03928 ? value / 12.92 : ((value + 0.055) / 1.055) ** 2.4;
  });
  return 0.2126 * channels[0] + 0.7152 * channels[1] + 0.0722 * channels[2];
}

function contrast(a: string, b: string): number {
  const [hi, lo] = [luminance(a), luminance(b)].sort((x, y) => y - x);
  return (hi + 0.05) / (lo + 0.05);
}

describe("bảng màu", () => {
  const bg = "#ffffff";

  it.each(["--color-primary", "--color-accent", "--color-danger", "--color-fg", "--color-fg-muted"])(
    "%s đạt tối thiểu 4.5:1 trên nền trắng",
    (name) => {
      expect(contrast(readVar(name), bg)).toBeGreaterThanOrEqual(4.5);
    },
  );

  it("chữ trắng trên nền primary/accent/danger cũng đạt 4.5:1", () => {
    for (const name of ["--color-primary", "--color-accent", "--color-danger"]) {
      expect(contrast("#ffffff", readVar(name))).toBeGreaterThanOrEqual(4.5);
    }
  });

  it("không được lén dùng hai màu đã trượt AA", () => {
    expect(css.toLowerCase()).not.toContain("#0284c7"); // 4.10:1
    expect(css.toLowerCase()).not.toContain("#059669"); // 3.77:1
  });

  it("cỡ chữ nền tối thiểu 18px", () => {
    const size = css.match(/body\s*\{[^}]*font-size:\s*(\d+)px/);
    expect(Number(size?.[1])).toBeGreaterThanOrEqual(18);
  });

  it("nút cao tối thiểu 56px", () => {
    expect(css).toMatch(/--tap:\s*(5[6-9]|[6-9]\d)px/);
  });

  it("có nhánh prefers-reduced-motion", () => {
    expect(css).toContain("prefers-reduced-motion");
  });
});
```

- [ ] **Step 2: Chạy test để xác nhận pass**

Run: `cd frontend && npm test -- tokens`
Expected: PASS (10 passed). Nếu **fail**, sửa `tokens.css` chứ đừng nới ngưỡng trong test.

- [ ] **Step 3: Thêm ErrorBoundary**

Một lỗi render lẻ trong React làm trắng cả màn hình. Với khách lớn tuổi, màn hình trắng nghĩa là "máy hỏng rồi" và họ không bao giờ mở lại app.

Tạo `frontend/src/components/ErrorBoundary.tsx`:

```tsx
import { Component } from "react";
import type { ErrorInfo, ReactNode } from "react";

interface State {
  crashed: boolean;
}

export class ErrorBoundary extends Component<{ children: ReactNode }, State> {
  state: State = { crashed: false };

  static getDerivedStateFromError(): State {
    return { crashed: true };
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    console.error("Màn hình hỏng:", error, info.componentStack);
  }

  render() {
    if (!this.state.crashed) return this.props.children;
    return (
      <main className="screen screen--narrow">
        <h1 style={{ fontSize: 26, marginBottom: "var(--s3)" }}>Máy đang trục trặc</h1>
        <p style={{ fontSize: 20, marginBottom: "var(--s4)" }}>
          Cô chú bấm nút bên dưới để mở lại giúp con ạ. Nếu vẫn không được thì gọi cho tiệm nhé.
        </p>
        <button className="btn btn--primary btn--full" onClick={() => window.location.reload()}>
          Mở lại
        </button>
      </main>
    );
  }
}
```

Bọc quanh `<Routes>` trong `frontend/src/App.tsx`:

```tsx
import { ErrorBoundary } from "./components/ErrorBoundary";
```

```tsx
      <AuthProvider>
        <ErrorBoundary>
          <Routes>{/* … */}</Routes>
        </ErrorBoundary>
      </AuthProvider>
```

- [ ] **Step 4: Rà lại toàn bộ ràng buộc bằng grep**

Chạy từng lệnh, mỗi lệnh phải **không ra kết quả nào**:

```bash
cd frontend/src

# 1. Không được render tên tool thô. Chỉ toolLabels.ts được nhắc tên tool.
grep -rn "find_free_slots\|create_appointment\|list_my_appointments\|cancel_appointment\|get_shop_status" \
  --include=*.tsx . | grep -v ".test."

# 2. Không dùng emoji làm icon
grep -rnP "[\x{1F300}-\x{1FAFF}]" --include=*.tsx . | grep -v ".test."

# 3. Không còn màu trượt AA
grep -rni "0284c7\|059669" .

# 4. Không hardcode localhost ngoài file cấu hình
grep -rn "localhost:8000" --include=*.ts --include=*.tsx . | grep -v ".test."

# 5. Không lọt chuỗi tiếng Anh kỹ thuật ra giao diện
grep -rn "Failed to fetch\|Internal Server Error\|undefined\b" --include=*.tsx . | grep -v ".test."
```

Cái nào ra kết quả thì sửa rồi chạy lại.

- [ ] **Step 5: Kiểm cỡ vùng bấm bằng mắt trên máy thật**

Mở DevTools ở chế độ điện thoại (iPhone SE, rộng 375px) và kiểm:

- Mọi nút và link điều hướng cao ≥ 56px.
- Không có thanh cuộn ngang trên bất kỳ màn hình nào.
- Bàn phím ảo bật lên **không** che ô nhập ở màn hình chat.
- Bật "Reduce motion" trong hệ điều hành: ba chấm, vòng xoay và con trỏ đứng yên, nhưng chữ mô tả trạng thái **vẫn còn**.
- Phóng chữ hệ thống lên 200%: không chữ nào bị cắt hoặc chồng lên nhau.

- [ ] **Step 6: Kiểm bằng bàn phím và trình đọc màn hình**

Tab qua toàn bộ màn hình đăng nhập và màn hình chat: mọi thứ bấm được đều nhận được focus, và viền focus **nhìn thấy rõ** (`:focus-visible` đã đặt ở task 1). Bật VoiceOver/TalkBack, gửi một tin nhắn: phải nghe đọc "Con đang đọc tin nhắn ạ" rồi tới câu mô tả tool, chứ không im lặng.

- [ ] **Step 7: Build production**

Tạo `frontend/.env.production`:

```
VITE_API_BASE=https://api.tiemnailtoc.example
VITE_SHOP_PHONE=0287654321
```

Đổi sang domain thật và **số điện thoại thật của tiệm** khi deploy. Số sai còn tệ
hơn không có nút gọi. Rồi:

```bash
cd frontend
npm run build
```

Expected: build xong, không lỗi TypeScript. Nếu `tsc -b` báo lỗi kiểu, sửa kiểu — **không** thêm `// @ts-ignore`.

- [ ] **Step 8: Thử bản build**

```bash
cd frontend && npm run preview
```

Mở `http://localhost:4173`, đăng nhập và chat một lượt. Đây là lần duy nhất bắt được lỗi chỉ xảy ra ở bản build: biến `VITE_*` thiếu, đường dẫn tài nguyên sai, hoặc CORS/Socket.IO chỉ cho phép origin của dev server.

- [ ] **Step 9: Viết `frontend/README.md`**

```markdown
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
| Cỡ chữ nền ≥ 18px, nút ≥ 56px | cùng file test trên |
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
```

- [ ] **Step 10: Ghi phần giao diện vào README gốc**

Thêm vào `README.md` ở gốc repo, dưới phần hướng dẫn chạy backend:

```markdown
## Giao diện web

```bash
cd frontend && npm install && npm run dev
```

Chi tiết xem [`frontend/README.md`](frontend/README.md). Nhớ thêm
`http://localhost:5173` vào `ALLOWED_ORIGINS` trong `.env`, nếu không mọi
request từ giao diện đều bị CORS chặn.
```

- [ ] **Step 11: Chạy toàn bộ kiểm tra lần cuối**

```bash
cd frontend && npm test && npm run build
cd .. && pytest -v
```

Expected: PASS toàn bộ hai phía.

- [ ] **Step 12: Kiểm tay đầu-cuối trên điện thoại thật**

Trên điện thoại thật, cùng mạng wifi với máy chạy backend:

1. Đăng nhập bằng SĐT.
2. Gõ "mai 3h chiều làm tóc được không con" → thấy ba chấm → "Đang xem lịch trống…" → dấu tích → chữ hiện dần.
3. Xác nhận đặt → mở "Lịch của tôi" phải thấy lịch vừa đặt.
4. Ở máy admin bấm "Tôi đang bận" 30 phút → thẻ trạng thái trên máy khách đổi **ngay**, không tải lại.
5. Trong Telegram bấm "Tôi rảnh rồi" → thẻ trên máy khách lại đổi.
6. Bấm "Hủy lịch" → phải hỏi lại một lần trước khi hủy thật.
7. Tắt wifi giữa lúc chữ đang chạy → chữ đã hiện **còn nguyên** kèm dòng "Mất mạng, đang thử lại…"; bật lại wifi thì dòng đó biến mất.
8. Vẫn đang tắt wifi, gõ thêm một câu rồi bấm Gửi → bong bóng hiện ngay kèm "Đang gửi lại…". Bật wifi lại → câu đó tự gửi đi, **không phải gõ lại**.
9. Tắt backend rồi gõ một câu → hiện nút "Gọi cho tiệm: …", bấm vào máy phải mở bàn quay số với đúng số trong `VITE_SHOP_PHONE`.
10. Tắt MongoDB (`docker compose stop mongo`) rồi tải lại trang → thấy câu tiếng Việt kèm số điện thoại tiệm, **không** thấy chữ tiếng Anh hay mã lỗi.

- [ ] **Step 13: Commit**

```bash
git add -A
git commit -m "chore(fe): accessibility guardrail tests, error boundary and deploy notes"
```
