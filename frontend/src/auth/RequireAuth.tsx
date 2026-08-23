import { Navigate } from "react-router-dom";
import type { ReactNode } from "react";
import { useAuth } from "./AuthContext";

/**
 * Chỉ để giao diện gọn, KHÔNG phải bảo mật. Mọi route admin đã bị
 * `require_admin` chặn ở backend; ai đổi state để thành "admin" cũng chỉ thấy
 * màn hình trống vì API trả 403.
 */
export function RequireAuth({ role, children }: { role?: "admin"; children: ReactNode }) {
  const { token, role: myRole, loading } = useAuth();

  if (loading) return <p style={{ padding: "var(--s4)" }}>Đang mở…</p>;
  if (!token) return <Navigate to="/dang-nhap" replace />;
  if (role === "admin" && myRole !== "admin") return <Navigate to="/" replace />;
  return <>{children}</>;
}
