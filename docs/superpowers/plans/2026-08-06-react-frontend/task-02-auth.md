# Task 2 · API client, phiên đăng nhập và route bảo vệ

> **Vai xưng hô đã đổi sau tài liệu này.** Từ 2026-08-23 lễ tân xưng "em",
> gọi khách "anh"/"chị". Mọi câu "con", "cô", "chú", "bác" dưới đây là
> nguyên văn của thời điểm đó, giữ lại làm biên bản chứ không phải mẫu để
> chép theo. Vai hiện hành: mục "Xưng hô" trong [`CONTEXT.md`](../../../../CONTEXT.md).

> Thuộc plan [Frontend React](README.md). **Đọc [Ràng buộc toàn cục](README.md#ràng-buộc-toàn-cục) trước khi bắt đầu** — chúng áp cho mọi task, kể cả khi không nhắc lại ở đây.

**Files:**
- Create: `frontend/.env.development`, `frontend/src/lib/api.ts`, `frontend/src/lib/api.test.ts`, `frontend/src/auth/session.ts`, `frontend/src/auth/AuthContext.tsx`, `frontend/src/auth/RequireAuth.tsx`, `frontend/src/screens/LoginScreen.tsx`, `frontend/src/screens/LoginScreen.test.tsx`, `frontend/src/components/Field.tsx`, `frontend/src/components/Field.css`
- Modify: `frontend/src/App.tsx`, `frontend/src/main.tsx`

**Interfaces:**
- Consumes: `Button` (task 1)
- Produces:
  - `api.get/post/put/del<T>(path, body?) -> Promise<T>` — tự gắn `Authorization`, ném `ApiError { status, message }`
  - `session.save(token, role)` / `session.load()` / `session.clear()`
  - `useAuth() -> { token, role, fullName, login, logout, loading }`
  - `<RequireAuth role?="admin">` — bọc route
  - Tất cả kiểu dữ liệu API: `UserResponse`, `AppointmentResponse`, `ShopStatus`, `ShopHours`

- [ ] **Step 1: Cài router và khai báo địa chỉ API**

```bash
cd frontend
npm install react-router-dom
```

Tạo `frontend/.env.development`:

```
VITE_API_BASE=http://localhost:8000
# SĐT tiệm. Khi AI lỗi, màn hình chat hiện nút bấm gọi thẳng số này — người
# lớn tuổi vẫn quen gọi điện hơn là gõ lại câu hỏi.
VITE_SHOP_PHONE=0287654321
```

- [ ] **Step 2: Viết test cho API client (sẽ fail)**

Tạo `frontend/src/lib/api.test.ts`:

```ts
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ApiError, api } from "./api";
import { session } from "../auth/session";

function mockFetch(status: number, body: unknown) {
  const fn = vi.fn().mockResolvedValue({
    ok: status < 400,
    status,
    json: async () => body,
    text: async () => JSON.stringify(body),
  });
  vi.stubGlobal("fetch", fn);
  return fn;
}

describe("api", () => {
  beforeEach(() => session.clear());
  afterEach(() => vi.unstubAllGlobals());

  it("gắn token vào header khi đã đăng nhập", async () => {
    session.save("abc123", "user");
    const fetchMock = mockFetch(200, { ok: true });

    await api.get("/api/v1/auth/me");

    const headers = fetchMock.mock.calls[0][1].headers;
    expect(headers.Authorization).toBe("Bearer abc123");
  });

  it("không gắn header khi chưa đăng nhập", async () => {
    const fetchMock = mockFetch(200, {});
    await api.get("/api/v1/shop/status");
    expect(fetchMock.mock.calls[0][1].headers.Authorization).toBeUndefined();
  });

  it("ném ApiError kèm câu tiếng Việt từ backend", async () => {
    mockFetch(409, { detail: "Giờ này có người đặt mất rồi ạ" });
    await expect(api.post("/api/v1/appointments", {})).rejects.toThrow(
      "Giờ này có người đặt mất rồi ạ",
    );
  });

  it("401 thì xóa phiên — token hết hạn không được giữ lại", async () => {
    session.save("cu", "user");
    mockFetch(401, { detail: "Phiên đăng nhập đã hết hạn" });

    await expect(api.get("/api/v1/auth/me")).rejects.toBeInstanceOf(ApiError);
    expect(session.load().token).toBeNull();
  });

  it("mất mạng thì báo bằng câu người thường đọc được", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new TypeError("Failed to fetch")));
    await expect(api.get("/api/v1/shop/status")).rejects.toThrow(/mạng/i);
  });

  it("204 không có body vẫn trả về bình thường", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({ ok: true, status: 204, json: async () => {
        throw new Error("no body");
      } }),
    );
    await expect(api.del("/api/v1/appointments/abc")).resolves.toBeNull();
  });
});
```

- [ ] **Step 3: Chạy test để xác nhận fail**

Run: `cd frontend && npm test`
Expected: FAIL — không tìm thấy `./api`

- [ ] **Step 4: Viết `frontend/src/auth/session.ts`**

```ts
export type Role = "user" | "admin";

const TOKEN_KEY = "nailtoc.token";
const ROLE_KEY = "nailtoc.role";

/**
 * Lưu trong localStorage chứ không phải sessionStorage: khách lớn tuổi đóng
 * trình duyệt liên tục và không nhớ mật khẩu. Đổi lại phải xóa sạch ngay khi
 * gặp 401 — xem `api.ts`.
 */
export const session = {
  save(token: string, role: Role) {
    localStorage.setItem(TOKEN_KEY, token);
    localStorage.setItem(ROLE_KEY, role);
  },
  load(): { token: string | null; role: Role | null } {
    return {
      token: localStorage.getItem(TOKEN_KEY),
      role: localStorage.getItem(ROLE_KEY) as Role | null,
    };
  },
  clear() {
    localStorage.removeItem(TOKEN_KEY);
    localStorage.removeItem(ROLE_KEY);
  },
};
```

- [ ] **Step 5: Viết `frontend/src/lib/api.ts`**

```ts
import { session } from "../auth/session";

const BASE = import.meta.env.VITE_API_BASE ?? "";

export class ApiError extends Error {
  constructor(public status: number, message: string) {
    super(message);
    this.name = "ApiError";
  }
}

const FALLBACK: Record<number, string> = {
  401: "Phiên đăng nhập đã hết hạn, cô chú đăng nhập lại giúp con ạ.",
  403: "Phần này chỉ chủ tiệm mới xem được ạ.",
  404: "Không tìm thấy ạ.",
  409: "Giờ này vừa có người đặt mất rồi ạ.",
  429: "Cô chú thử lại sau ít phút giúp con ạ.",
};

async function request<T>(method: string, path: string, body?: unknown): Promise<T> {
  const { token } = session.load();
  const headers: Record<string, string> = { "Content-Type": "application/json" };
  if (token) headers.Authorization = `Bearer ${token}`;

  let response: Response;
  try {
    response = await fetch(`${BASE}${path}`, {
      method,
      headers,
      body: body === undefined ? undefined : JSON.stringify(body),
    });
  } catch {
    // TypeError từ fetch nghĩa là không tới được server. Không bao giờ hiện
    // "Failed to fetch" cho khách.
    throw new ApiError(0, "Máy không vào được mạng, cô chú kiểm tra wifi giúp con ạ.");
  }

  if (!response.ok) {
    // 401 nghĩa là token chết. Giữ lại thì mọi request sau đều hỏng theo,
    // và khách sẽ thấy màn hình trống mà không hiểu vì sao.
    if (response.status === 401) session.clear();

    let detail: string | undefined;
    try {
      const data = await response.json();
      detail = typeof data?.detail === "string" ? data.detail : undefined;
    } catch {
      /* body rỗng hoặc không phải JSON */
    }
    throw new ApiError(
      response.status,
      detail ?? FALLBACK[response.status] ?? "Có lỗi xảy ra, cô chú thử lại giúp con ạ.",
    );
  }

  if (response.status === 204) return null as T;
  try {
    return (await response.json()) as T;
  } catch {
    return null as T;
  }
}

export const api = {
  get: <T>(path: string) => request<T>("GET", path),
  post: <T>(path: string, body?: unknown) => request<T>("POST", path, body ?? {}),
  put: <T>(path: string, body: unknown) => request<T>("PUT", path, body),
  del: <T>(path: string) => request<T>("DELETE", path),
};

/* Kiểu dữ liệu — khớp `app/api/v1/schemas.py` của Plan 1. */

export interface TokenResponse {
  access_token: string;
  token_type: string;
  role: "user" | "admin";
}

export interface UserResponse {
  id: string;
  phone: string;
  full_name: string | null;
  role: "user" | "admin";
  is_active: boolean;
}

export interface AppointmentResponse {
  id: string;
  start_at: string;
  duration_minutes: number;
  note: string | null;
  status: "booked" | "cancelled";
  user_name: string | null;
  phone: string | null;
}

export interface ShopStatus {
  is_busy: boolean;
  busy_until: string | null;
  minutes_left: number | null;
}

export interface ShopHours {
  open_time: string;
  close_time: string;
  closed_days: number[];
}
```

- [ ] **Step 6: Viết `frontend/src/components/Field.tsx`**

```tsx
import type { InputHTMLAttributes } from "react";
import "./Field.css";

interface Props extends InputHTMLAttributes<HTMLInputElement> {
  label: string;
  hint?: string;
}

export function Field({ label, hint, id, ...rest }: Props) {
  const inputId = id ?? `f-${label.replace(/\s+/g, "-").toLowerCase()}`;
  return (
    <div className="field">
      {/* Nhãn luôn hiện, không dùng placeholder thay nhãn: placeholder biến mất
          ngay khi gõ chữ đầu tiên và người lớn tuổi quên mất ô này là ô gì. */}
      <label className="field__label" htmlFor={inputId}>
        {label}
      </label>
      <input className="field__input" id={inputId} {...rest} />
      {hint && <p className="field__hint">{hint}</p>}
    </div>
  );
}
```

Tạo `frontend/src/components/Field.css`:

```css
.field { margin-bottom: var(--s4); }
.field__label {
  display: block;
  font-size: 19px;
  font-weight: 600;
  margin-bottom: var(--s1);
}
.field__input {
  width: 100%;
  min-height: var(--tap);
  padding: 0 var(--s3);
  font-family: inherit;
  font-size: 21px;
  color: var(--color-fg);
  background: var(--color-surface);
  border: 2px solid var(--color-border);
  border-radius: var(--r);
}
.field__input:focus { border-color: var(--color-primary); }
.field__hint { font-size: 17px; color: var(--color-fg-muted); margin-top: 6px; }
```

- [ ] **Step 7: Viết `frontend/src/auth/AuthContext.tsx`**

```tsx
import { createContext, useCallback, useContext, useEffect, useState } from "react";
import type { ReactNode } from "react";
import { ApiError, api } from "../lib/api";
import type { TokenResponse, UserResponse } from "../lib/api";
import { session } from "./session";
import type { Role } from "./session";

interface AuthValue {
  token: string | null;
  role: Role | null;
  fullName: string | null;
  loading: boolean;
  login: (phone: string, password: string) => Promise<Role>;
  logout: () => void;
}

const AuthContext = createContext<AuthValue | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [{ token, role }, setState] = useState(() => session.load());
  const [fullName, setFullName] = useState<string | null>(null);
  const [loading, setLoading] = useState(Boolean(session.load().token));

  const logout = useCallback(() => {
    session.clear();
    setState({ token: null, role: null });
    setFullName(null);
  }, []);

  // Token trong localStorage có thể đã hết hạn từ hôm qua. Hỏi lại backend
  // một lần lúc mở app, thay vì để khách bấm rồi mới thấy lỗi.
  useEffect(() => {
    if (!token) return;
    let cancelled = false;
    api
      .get<UserResponse>("/api/v1/auth/me")
      .then((me) => {
        if (cancelled) return;
        setFullName(me.full_name);
        setState({ token, role: me.role });
        session.save(token, me.role);
      })
      .catch((err) => {
        if (!cancelled && err instanceof ApiError && err.status === 401) logout();
      })
      .finally(() => !cancelled && setLoading(false));
    return () => {
      cancelled = true;
    };
  }, [token, logout]);

  const login = useCallback(async (phone: string, password: string) => {
    const res = await api.post<TokenResponse>("/api/v1/auth/login", { phone, password });
    session.save(res.access_token, res.role);
    setState({ token: res.access_token, role: res.role });
    return res.role;
  }, []);

  return (
    <AuthContext.Provider value={{ token, role, fullName, loading, login, logout }}>
      {children}
    </AuthContext.Provider>
  );
}

export function useAuth(): AuthValue {
  const value = useContext(AuthContext);
  if (!value) throw new Error("useAuth phải nằm trong <AuthProvider>");
  return value;
}
```

- [ ] **Step 8: Viết `frontend/src/auth/RequireAuth.tsx`**

```tsx
import { Navigate } from "react-router-dom";
import type { ReactNode } from "react";
import { useAuth } from "./AuthContext";

/**
 * Chỉ để giao diện gọn, KHÔNG phải bảo mật. Mọi route admin đã bị
 * `require_admin` chặn ở backend; ai sửa localStorage thành "admin" cũng chỉ
 * thấy màn hình trống vì API trả 403.
 */
export function RequireAuth({ role, children }: { role?: "admin"; children: ReactNode }) {
  const { token, role: myRole, loading } = useAuth();

  if (loading) return <p style={{ padding: "var(--s4)" }}>Đang mở…</p>;
  if (!token) return <Navigate to="/dang-nhap" replace />;
  if (role === "admin" && myRole !== "admin") return <Navigate to="/" replace />;
  return <>{children}</>;
}
```

- [ ] **Step 9: Viết test cho màn hình đăng nhập (sẽ fail)**

Tạo `frontend/src/screens/LoginScreen.test.tsx`:

```tsx
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import { AuthProvider } from "../auth/AuthContext";
import { session } from "../auth/session";
import { LoginScreen } from "./LoginScreen";

function renderScreen() {
  return render(
    <MemoryRouter>
      <AuthProvider>
        <LoginScreen />
      </AuthProvider>
    </MemoryRouter>,
  );
}

afterEach(() => {
  vi.unstubAllGlobals();
  session.clear();
});

function mockFetch(status: number, body: unknown) {
  vi.stubGlobal(
    "fetch",
    vi.fn().mockResolvedValue({ ok: status < 400, status, json: async () => body }),
  );
}

describe("LoginScreen", () => {
  it("đăng nhập thành công thì lưu token", async () => {
    mockFetch(200, { access_token: "tok", token_type: "bearer", role: "user" });
    renderScreen();

    await userEvent.type(screen.getByLabelText(/số điện thoại/i), "0912345678");
    await userEvent.type(screen.getByLabelText(/mật khẩu/i), "matkhau123");
    await userEvent.click(screen.getByRole("button", { name: /đăng nhập/i }));

    expect(await screen.findByText(/./)).toBeInTheDocument();
    expect(session.load().token).toBe("tok");
  });

  it("sai mật khẩu thì hiện câu backend trả về, không hiện mã lỗi", async () => {
    mockFetch(401, { detail: "Số điện thoại hoặc mật khẩu chưa đúng ạ" });
    renderScreen();

    await userEvent.type(screen.getByLabelText(/số điện thoại/i), "0912345678");
    await userEvent.type(screen.getByLabelText(/mật khẩu/i), "sai");
    await userEvent.click(screen.getByRole("button", { name: /đăng nhập/i }));

    expect(await screen.findByRole("alert")).toHaveTextContent("chưa đúng");
    expect(screen.queryByText(/401/)).not.toBeInTheDocument();
  });

  it("ô số điện thoại dùng bàn phím số", () => {
    renderScreen();
    expect(screen.getByLabelText(/số điện thoại/i)).toHaveAttribute("inputMode", "numeric");
  });

  it("bỏ trống thì báo ngay, không gọi API", async () => {
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);
    renderScreen();

    await userEvent.click(screen.getByRole("button", { name: /đăng nhập/i }));
    expect(await screen.findByRole("alert")).toBeInTheDocument();
    expect(fetchMock).not.toHaveBeenCalled();
  });
});
```

- [ ] **Step 10: Chạy test để xác nhận fail**

Run: `cd frontend && npm test`
Expected: FAIL — không tìm thấy `./LoginScreen`

- [ ] **Step 11: Viết `frontend/src/screens/LoginScreen.tsx`**

```tsx
import { useState } from "react";
import type { FormEvent } from "react";
import { useNavigate } from "react-router-dom";
import { Button } from "../components/Button";
import { Field } from "../components/Field";
import { useAuth } from "../auth/AuthContext";

export function LoginScreen() {
  const { login } = useAuth();
  const navigate = useNavigate();
  const [phone, setPhone] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (!phone.trim() || !password) {
      setError("Cô chú nhập đủ số điện thoại và mật khẩu giúp con ạ.");
      return;
    }
    setError(null);
    setBusy(true);
    try {
      const role = await login(phone.trim(), password);
      navigate(role === "admin" ? "/chu-tiem" : "/", { replace: true });
    } catch (err) {
      setError(err instanceof Error ? err.message : "Đăng nhập không được ạ.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <main className="screen screen--narrow">
      <h1 style={{ fontSize: 30, marginBottom: "var(--s4)" }}>Tiệm Nail &amp; Tóc</h1>

      <form onSubmit={submit} noValidate>
        <Field
          label="Số điện thoại"
          hint="Số cô chú vẫn dùng, ví dụ 0912345678"
          inputMode="numeric"
          autoComplete="tel"
          value={phone}
          onChange={(e) => setPhone(e.target.value)}
        />
        <Field
          label="Mật khẩu"
          type="password"
          autoComplete="current-password"
          value={password}
          onChange={(e) => setPassword(e.target.value)}
        />

        {/* role="alert" để trình đọc màn hình đọc lên ngay, không phải chỉ đổi màu. */}
        {error && (
          <p role="alert" className="alert alert--danger">
            {error}
          </p>
        )}

        <Button type="submit" loading={busy}>
          Đăng nhập
        </Button>
      </form>

      <p style={{ marginTop: "var(--s4)", fontSize: 19 }}>
        Quên mật khẩu thì cô chú gọi cho tiệm, chủ tiệm đặt lại giúp ạ.
      </p>
    </main>
  );
}
```

Thêm vào cuối `frontend/src/styles/tokens.css`:

```css
.screen { padding: var(--s4) var(--s3) var(--s5); max-width: 560px; margin: 0 auto; }
.screen--narrow { max-width: 460px; }

.alert {
  padding: var(--s2) var(--s3);
  border-radius: var(--r);
  font-size: 19px;
  margin-bottom: var(--s3);
}
.alert--danger { background: var(--color-danger-weak); color: var(--color-danger); }
.alert--info   { background: var(--color-primary-weak); color: var(--color-primary); }
.alert--warn   { background: var(--color-warn-weak); color: var(--color-fg); }
```

- [ ] **Step 12: Nối route trong `frontend/src/App.tsx`**

Viết lại toàn bộ file (các màn hình sau sẽ được thêm dần ở task 4–7):

```tsx
import { BrowserRouter, Navigate, Route, Routes } from "react-router-dom";
import { AuthProvider } from "./auth/AuthContext";
import { RequireAuth } from "./auth/RequireAuth";
import { LoginScreen } from "./screens/LoginScreen";

export default function App() {
  return (
    <BrowserRouter>
      <AuthProvider>
        <Routes>
          <Route path="/dang-nhap" element={<LoginScreen />} />
          <Route
            path="/"
            element={
              <RequireAuth>
                <p style={{ padding: "var(--s4)" }}>Màn hình chat — task 4</p>
              </RequireAuth>
            }
          />
          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
      </AuthProvider>
    </BrowserRouter>
  );
}
```

Trong `frontend/src/main.tsx`, xóa `import "./index.css"` nếu Vite sinh sẵn — token đã thay nó.

- [ ] **Step 13: Chạy test để xác nhận pass**

Run: `cd frontend && npm test`
Expected: PASS (15 passed)

- [ ] **Step 14: Bật CORS ở backend**

Kiểm tra `main.py` đã có `CORSMiddleware` với `allow_origins` chứa `http://localhost:5173`. Nếu chưa, thêm `5173` vào `ALLOWED_ORIGINS` trong `.env`. Thiếu bước này thì mọi request từ Vite dev server đều bị chặn và console chỉ hiện lỗi CORS mơ hồ.

- [ ] **Step 15: Kiểm tay**

Chạy backend, rồi `npm run dev`. Mở `http://localhost:5173/dang-nhap`, đăng nhập bằng tài khoản đã tạo ở Plan 1. Tải lại trang — phải **vẫn** đăng nhập. Xóa `nailtoc.token` trong localStorage rồi tải lại — phải quay về màn hình đăng nhập.

- [ ] **Step 16: Commit**

```bash
git add frontend
git commit -m "feat(fe): API client, phone login and route guards"
```
