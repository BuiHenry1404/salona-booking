import { createContext, useCallback, useContext, useEffect, useState } from "react";
import type { ReactNode } from "react";
import { api } from "../lib/api";
import { session } from "./session";
import type { Role } from "./session";

// api.ts không xuất kiểu response — định nghĩa tại chỗ, khớp schemas.py của backend.
interface TokenResponse {
  access_token: string;
  token_type: string;
  role: Role;
}

interface MeResponse {
  id: string;
  phone: string;
  full_name: string | null;
  role: Role;
  is_active: boolean;
}

interface AuthValue {
  token: string | null;
  role: Role | null;
  fullName: string | null;
  loading: boolean;
  login: (phone: string, password: string) => Promise<Role>;
  logout: () => Promise<void>;
}

const AuthContext = createContext<AuthValue | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [{ token, role }, setState] = useState(() => session.load());
  const [fullName, setFullName] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  // session là nguồn chân lý về token/role — api client có thể xoay token
  // ngay giữa một request (401 → refresh). Subscribe để context (và mọi
  // consumer như useAgentStream của task 3) luôn thấy token còn sống.
  useEffect(() => session.subscribe((next) => setState(next)), []);

  // Bootstrap: dựng lại phiên sau khi tải lại trang. Access token sống trong
  // bộ nhớ nên reload là mất — nhưng cookie HttpOnly có thể còn. Không có
  // header Bearer thì backend trả 403 chứ không 401, api client không thể tự
  // refresh hộ, nên phải chủ động đổi cookie lấy token trước khi hỏi /me.
  useEffect(() => {
    let cancelled = false;

    (async () => {
      try {
        if (!session.load().token) {
          try {
            const refreshed = await api.post<TokenResponse>("/api/v1/auth/refresh");
            session.save(refreshed.access_token, refreshed.role);
          } catch {
            return; // chưa từng đăng nhập, hoặc hết phiên thật — anonymous
          }
        }

        // Token có thể vừa được api client xoay vòng trong lượt 401 → đọc lại
        // từ session thay vì dùng giá trị closure cũ.
        const me = await api.get<MeResponse>("/api/v1/auth/me");
        if (cancelled) return;
        const current = session.load();
        setState({ token: current.token, role: me.role });
        setFullName(me.full_name);
        if (current.token) session.save(current.token, me.role);
      } catch {
        // Hết phiên thật sự (refresh cũng chết) — không giữ lại token rác.
        if (!cancelled) {
          session.clear();
          setState({ token: null, role: null });
        }
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();

    return () => {
      cancelled = true;
    };
  }, []);

  const login = useCallback(async (phone: string, password: string): Promise<Role> => {
    const res = await api.post<TokenResponse>("/api/v1/auth/login", { phone, password });
    session.save(res.access_token, res.role);
    setState({ token: res.access_token, role: res.role });
    setFullName(null);

    // Lấy tên hiển thị; thất bại không được làm hỏng việc đăng nhập đã xong.
    try {
      const me = await api.get<MeResponse>("/api/v1/auth/me");
      setFullName(me.full_name);
    } catch {
      /* fullName để null, màn hình sẽ dùng câu chào chung */
    }
    return res.role;
  }, []);

  const logout = useCallback(async () => {
    try {
      // Backend thu hồi cả refresh-token family và xoá cookie HttpOnly —
      // chỉ clear client thì cookie còn sống 30 ngày trên máy bị mất.
      await api.post("/api/v1/auth/logout");
    } catch {
      // Server chết hay mạng đứt vẫn phải đăng xuất phía máy khách.
    } finally {
      session.clear();
      setState({ token: null, role: null });
      setFullName(null);
    }
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
