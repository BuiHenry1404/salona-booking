import { useCallback, useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { Button } from "../components/Button";
import { Field } from "../components/Field";
import { api } from "../lib/api";
import type { CreateUserRequest, UserResponse } from "../lib/api";
import "./CustomersScreen.css";

/**
 * Màn danh sách khách của chủ tiệm — route `/chu-tiem/khach`.
 *
 * API: `GET /api/v1/auth/users` → bare `UserResponse[]`. Chỉ hiện role
 * "user" — tài khoản admin không phải "khách" để đặt lịch hộ.
 *
 * Lọc TẠI CHỖ theo SĐT hoặc tên: một tiệm nhỏ có vài trăm khách, tải hết
 * một lần rồi lọc client rẻ hơn gọi API theo từng phím gõ.
 */
export function CustomersScreen() {
  const [users, setUsers] = useState<UserResponse[] | null>(null);
  const [query, setQuery] = useState("");
  const [creating, setCreating] = useState(false);
  const [form, setForm] = useState({ phone: "", full_name: "", password: "" });
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [note, setNote] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      setUsers(await api.get<UserResponse[]>("/api/v1/auth/users"));
      setError(null);
    } catch (err) {
      // Lỗi đã được api client dịch sẵn sang câu tiếng Việt thân thiện.
      setError(err instanceof Error ? err.message : "Không xem được danh sách ạ.");
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const shown = useMemo(() => {
    const needle = query.trim().toLowerCase();
    return (users ?? [])
      .filter((u) => u.role === "user")
      .filter(
        (u) =>
          !needle ||
          u.phone.includes(needle) ||
          (u.full_name ?? "").toLowerCase().includes(needle),
      );
  }, [users, query]);

  async function createUser() {
    if (!form.phone.trim() || !form.password) {
      setError("Cô chú nhập số điện thoại và mật khẩu ban đầu giúp con ạ.");
      return;
    }
    setBusy(true);
    setError(null);
    try {
      const created = await api.post<UserResponse>("/api/v1/auth/users", {
        phone: form.phone.trim(),
        full_name: form.full_name.trim() || null,
        password: form.password,
        role: "user",
      } satisfies CreateUserRequest);
      // Append ĐÚNG response server (có id thật) — không bịa id local, và
      // không nạp lại toàn bộ: khách vừa tạo phải còn trên màn hình ngay.
      setUsers((prev) => (prev ? [...prev, created] : [created]));
      // Hệ thống không có email/SMS: mật khẩu ban đầu chỉ đến được khách
      // qua MIỆNG chủ tiệm — phải nhắc ngay lúc này kẻo quên.
      setNote(
        `Đã tạo tài khoản cho ${created.full_name ?? created.phone}. Mật khẩu ban đầu là ${form.password} — cô chú đọc cho khách ghi nhớ ngay ạ.`,
      );
      setForm({ phone: "", full_name: "", password: "" });
      setCreating(false);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Tạo tài khoản không được ạ.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="screen">
      <Link to="/chu-tiem" className="cust__back">
        ← Về bảng điều khiển
      </Link>
      <h1 className="cust__title">Khách hàng</h1>

      {/* role="alert" để trình đọc màn hình đọc ngay; trạng thái nói bằng chữ. */}
      {error && (
        <p role="alert" className="alert alert--danger">
          {error}
        </p>
      )}
      {note && <p className="cust__note">{
        /* Ghi chú thành công cũng phải là CHỮ, không chỉ đổi màu nền */ note
      }</p>}

      {users === null && !error && <p className="cust__loading">Đang xem danh sách…</p>}

      {creating ? (
        <section className="cust__form">
          <Field
            label="Số điện thoại"
            inputMode="numeric"
            value={form.phone}
            onChange={(e) => setForm({ ...form, phone: e.target.value })}
          />
          <Field
            label="Tên khách"
            value={form.full_name}
            onChange={(e) => setForm({ ...form, full_name: e.target.value })}
          />
          <Field
            label="Mật khẩu ban đầu"
            hint="Đọc mật khẩu này cho khách ngay — hệ thống không gửi tin nhắn được."
            value={form.password}
            onChange={(e) => setForm({ ...form, password: e.target.value })}
          />
          <Button loading={busy} onClick={() => void createUser()}>
            Lưu khách mới
          </Button>
          <div className="cust__gap" />
          <Button variant="ghost" onClick={() => setCreating(false)}>
            Thôi, để sau
          </Button>
        </section>
      ) : (
        <Button onClick={() => setCreating(true)}>Tạo tài khoản</Button>
      )}

      <div className="cust__search">
        <Field
          label="Tìm theo số điện thoại"
          hint="Gõ vài số cuối cũng được, hoặc gõ tên khách."
          inputMode="numeric"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
        />
      </div>

      {users !== null && shown.length === 0 && (
        <p className="cust__empty">Không tìm thấy khách nào ạ.</p>
      )}

      {shown.map((user) => (
        <article key={user.id} className="cust__card">
          <p className="cust__name">{user.full_name ?? "Khách"}</p>
          <p className="cust__phone">{user.phone}</p>
          <Link
            to={`/chu-tiem/khach/${user.id}/dat-lich`}
            className="btn btn--ghost btn--full cust__book"
          >
            Đặt lịch hộ
          </Link>
        </article>
      ))}
    </div>
  );
}
