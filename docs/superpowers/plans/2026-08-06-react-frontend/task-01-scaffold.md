# Task 1 · Khởi tạo dự án và design token

> Thuộc plan [Frontend React](README.md). **Đọc [Ràng buộc toàn cục](README.md#ràng-buộc-toàn-cục) trước khi bắt đầu** — chúng áp cho mọi task, kể cả khi không nhắc lại ở đây.

**Files:**
- Create: `frontend/` (Vite scaffold), `frontend/src/styles/tokens.css`, `frontend/src/components/Button.tsx`, `frontend/src/components/Button.test.tsx`, `frontend/vitest.config.ts`, `frontend/src/setupTests.ts`
- Modify: `frontend/index.html`, `frontend/src/main.tsx`, `.gitignore`

**Interfaces:**
- Consumes: —
- Produces:
  - `tokens.css`: biến `--color-primary`, `--color-accent`, `--color-danger`, `--color-bg`, `--color-surface`, `--color-fg`, `--color-fg-muted`, `--color-border`, `--tap`, `--r`, `--s1`…`--s5`
  - `Button.tsx`: `<Button variant="primary" | "accent" | "danger" | "ghost" onClick fullWidth>`

- [ ] **Step 1: Tạo dự án Vite**

```bash
cd /home/henryb1/Desktop/HenryB1/projects/agentbox-eco-system/personal-project/fastapi-agent-template
npm create vite@latest frontend -- --template react-ts
cd frontend
npm install
npm install socket.io-client
npm install -D vitest @testing-library/react @testing-library/jest-dom @testing-library/user-event jsdom
```

- [ ] **Step 2: Bỏ qua thư mục build trong git**

Thêm vào `.gitignore` ở gốc repo:

```
frontend/node_modules/
frontend/dist/
```

- [ ] **Step 3: Cấu hình Vitest**

Tạo `frontend/vitest.config.ts`:

```ts
import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: "./src/setupTests.ts",
  },
});
```

Tạo `frontend/src/setupTests.ts`:

```ts
import "@testing-library/jest-dom";
```

Thêm script vào `frontend/package.json`:

```json
"scripts": {
  "dev": "vite",
  "build": "tsc -b && vite build",
  "preview": "vite preview",
  "test": "vitest run"
}
```

- [ ] **Step 4: Viết test cho Button (sẽ fail)**

Tạo `frontend/src/components/Button.test.tsx`:

```tsx
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { Button } from "./Button";

describe("Button", () => {
  it("hiện đúng nhãn", () => {
    render(<Button>Đăng nhập</Button>);
    expect(screen.getByRole("button", { name: "Đăng nhập" })).toBeInTheDocument();
  });

  it("gọi onClick khi bấm", async () => {
    const onClick = vi.fn();
    render(<Button onClick={onClick}>Bấm</Button>);
    await userEvent.click(screen.getByRole("button"));
    expect(onClick).toHaveBeenCalledOnce();
  });

  it("cao ít nhất 56px — ngón tay người lớn tuổi kém chính xác", () => {
    render(<Button>Bấm</Button>);
    const style = getComputedStyle(screen.getByRole("button"));
    expect(parseInt(style.minHeight)).toBeGreaterThanOrEqual(56);
  });

  it("không gọi onClick khi bị vô hiệu hóa", async () => {
    const onClick = vi.fn();
    render(<Button onClick={onClick} disabled>Bấm</Button>);
    await userEvent.click(screen.getByRole("button"));
    expect(onClick).not.toHaveBeenCalled();
  });

  it("khi đang chạy thì báo bằng CHỮ, không chỉ bằng màu", () => {
    render(<Button loading>Lưu</Button>);
    expect(screen.getByRole("button")).toHaveTextContent(/đang/i);
  });
});
```

- [ ] **Step 5: Chạy test để xác nhận fail**

Run: `cd frontend && npm test`
Expected: FAIL — không tìm thấy module `./Button`

- [ ] **Step 6: Viết `frontend/src/styles/tokens.css`**

```css
@import url("https://fonts.googleapis.com/css2?family=Be+Vietnam+Pro:wght@400;500;600;700&display=swap");

:root {
  /* Màu đã kiểm bằng công thức WCAG. Không dùng #0284C7 (4.10:1) hay
     #059669 (3.77:1) cho chữ — cả hai trượt AA cho chữ thường. */
  --color-primary: #0369a1;      /* 5.93:1 trên nền sáng */
  --color-primary-weak: #e0f2fe;
  --color-accent: #047857;       /* 5.48:1 */
  --color-accent-weak: #ecfdf5;
  --color-danger: #b91c1c;       /* 5.91:1 */
  --color-danger-weak: #fef2f2;
  --color-warn-weak: #fffbeb;
  --color-bg: #f0f9ff;
  --color-surface: #ffffff;
  --color-fg: #0f172a;
  --color-fg-muted: #475569;
  --color-border: #cbe3f0;

  --tap: 56px;   /* chiều cao tối thiểu mọi thứ bấm được */
  --r: 14px;
  --s1: 8px; --s2: 12px; --s3: 16px; --s4: 24px; --s5: 32px;
}

* { box-sizing: border-box; margin: 0; padding: 0; }

body {
  font-family: "Be Vietnam Pro", system-ui, sans-serif;
  font-size: 19px;            /* nền, không phải 16px */
  line-height: 1.55;
  background: var(--color-bg);
  color: var(--color-fg);
  -webkit-font-smoothing: antialiased;
}

:focus-visible { outline: 3px solid var(--color-fg); outline-offset: 3px; }

@media (prefers-reduced-motion: reduce) {
  *, *::before, *::after {
    animation-duration: 0.01ms !important;
    animation-iteration-count: 1 !important;
    transition-duration: 0.01ms !important;
  }
}
```

- [ ] **Step 7: Viết `frontend/src/components/Button.tsx`**

```tsx
import type { ButtonHTMLAttributes, ReactNode } from "react";
import "./Button.css";

type Variant = "primary" | "accent" | "danger" | "ghost";

interface Props extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: Variant;
  fullWidth?: boolean;
  loading?: boolean;
  children: ReactNode;
}

export function Button({
  variant = "primary",
  fullWidth = true,
  loading = false,
  disabled,
  children,
  ...rest
}: Props) {
  return (
    <button
      className={`btn btn--${variant}${fullWidth ? " btn--full" : ""}`}
      disabled={disabled || loading}
      aria-busy={loading || undefined}
      {...rest}
    >
      {/* Trạng thái chạy phải nói bằng chữ: người lớn tuổi không đọc được
          spinner, và mù màu nhẹ thì đổi màu cũng vô nghĩa. */}
      {loading ? "Đang xử lý…" : children}
    </button>
  );
}
```

Tạo `frontend/src/components/Button.css`:

```css
.btn {
  min-height: var(--tap);
  padding: 0 var(--s4);
  border: none;
  border-radius: var(--r);
  font-family: inherit;
  font-size: 20px;
  font-weight: 600;
  cursor: pointer;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: 10px;
  transition: filter 0.18s ease;
}
.btn--full { width: 100%; }
.btn:hover:not(:disabled) { filter: brightness(0.92); }
.btn:disabled { opacity: 0.55; cursor: not-allowed; }

.btn--primary { background: var(--color-primary); color: #fff; }
.btn--accent  { background: var(--color-accent);  color: #fff; }
.btn--danger  { background: var(--color-danger);  color: #fff; }
.btn--ghost {
  background: var(--color-surface);
  color: var(--color-primary);
  border: 2px solid var(--color-primary);
}
```

- [ ] **Step 8: Nạp token và đặt tiêu đề**

Trong `frontend/src/main.tsx`, thêm dòng import đầu tiên:

```tsx
import "./styles/tokens.css";
```

Trong `frontend/index.html`, đổi `<html lang="en">` thành `<html lang="vi">` và `<title>` thành `Tiệm Nail & Tóc`.

- [ ] **Step 9: Chạy test để xác nhận pass**

Run: `cd frontend && npm test`
Expected: PASS (5 passed)

- [ ] **Step 10: Commit**

```bash
git add frontend .gitignore
git commit -m "feat(fe): scaffold Vite app with accessibility-first design tokens"
```
