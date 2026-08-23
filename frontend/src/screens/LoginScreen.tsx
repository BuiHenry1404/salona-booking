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
