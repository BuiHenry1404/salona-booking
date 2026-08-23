import { Link } from "react-router-dom";
import { useAuth } from "../auth/AuthContext";
import { AppointmentCard } from "../components/AppointmentCard";
import { BusySwitch } from "../components/BusySwitch";
import { useAdminFeed } from "../hooks/useAdminFeed";
import { useShopStatus } from "../hooks/useShopStatus";

/**
 * Bảng điều khiển chủ tiệm — route `/chu-tiem` (RequireAuth role="admin").
 *
 * Nửa trên là nút bận/rảnh khổng lồ (thứ bấm nhiều nhất trong ngày), nửa
 * dưới là lịch hôm nay realtime. Không nút hủy lịch của khách ở đây — khách
 * tự hủy ở màn "Lịch của tôi".
 */
export function AdminDashboard() {
  const { status, loading, apply } = useShopStatus();
  const { appointments, newIds, error } = useAdminFeed();
  const { logout } = useAuth();

  return (
    <div className="screen">
      <h1 style={{ fontSize: 28, marginBottom: "var(--s3)" }}>Tiệm của tôi</h1>

      {loading ? <p style={{ fontSize: 19 }}>Đang xem…</p> : <BusySwitch status={status} onApplied={apply} />}

      <h2 style={{ fontSize: 24, marginBottom: "var(--s3)" }}>Lịch hôm nay</h2>

      {/* role="alert" để trình đọc màn hình đọc ngay; trạng thái nói bằng chữ. */}
      {error && (
        <p role="alert" className="alert alert--danger">
          {error}
        </p>
      )}

      {appointments.length === 0 && !error && (
        <p style={{ fontSize: 20, color: "var(--color-fg-muted)" }}>Hôm nay chưa có lịch nào ạ.</p>
      )}

      {appointments.map((appointment) => (
        <AppointmentCard
          key={appointment.id}
          appointment={appointment}
          showCustomer
          isNew={newIds.has(appointment.id)}
        />
      ))}

      {/* Hai việc ít làm nên nằm dưới cùng dạng link, không chiếm chỗ của nút
          bận/rảnh. Task 7 sẽ dựng màn tương ứng — giờ route mới là đích. */}
      <div style={{ marginTop: "var(--s5)", display: "grid", gap: "var(--s2)" }}>
        <Link
          to="/chu-tiem/khach"
          style={{
            fontSize: 20,
            color: "var(--color-primary)",
            minHeight: "var(--tap)",
            display: "flex",
            alignItems: "center",
          }}
        >
          Khách hàng
        </Link>
        <Link
          to="/chu-tiem/gio-mo-cua"
          style={{
            fontSize: 20,
            color: "var(--color-primary)",
            minHeight: "var(--tap)",
            display: "flex",
            alignItems: "center",
          }}
        >
          Giờ mở cửa
        </Link>
        {/* logout chỉ xóa phiên — RequireAuth thấy mất token tự đưa về
            /dang-nhap, màn này KHÔNG tự navigate. */}
        <button
          onClick={() => void logout()}
          style={{
            background: "none",
            border: "none",
            font: "inherit",
            fontSize: 20,
            color: "var(--color-fg-muted)",
            textAlign: "left",
            minHeight: "var(--tap)",
            cursor: "pointer",
          }}
        >
          Đăng xuất
        </button>
      </div>
    </div>
  );
}
