import { Link } from "react-router-dom";
import { useAuth } from "../auth/AuthContext";
import { AppointmentCard } from "../components/AppointmentCard";
import { BusySwitch } from "../components/BusySwitch";
import { MonthlyCustomerStats } from "../components/MonthlyCustomerStats";
import { useAdminFeed } from "../hooks/useAdminFeed";
import { useShopStatus } from "../hooks/useShopStatus";
import { mockMonthlyCustomerStats } from "../mocks/monthlyCustomerStats";
import "./AdminDashboard.css";

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
    <div className="screen screen--admin">
      <h1 className="admin__title">Tiệm của tôi</h1>

      {loading ? (
        <p className="admin__loading">Đang xem…</p>
      ) : (
        <BusySwitch status={status} onApplied={apply} />
      )}

      <MonthlyCustomerStats data={mockMonthlyCustomerStats} />

      <h2 className="admin__section-title">Lịch hôm nay</h2>

      {/* role="alert" để trình đọc màn hình đọc ngay; trạng thái nói bằng chữ. */}
      {error && (
        <p role="alert" className="alert alert--danger">
          {error}
        </p>
      )}

      {appointments.length === 0 && !error && (
        <p className="admin__empty">Hôm nay chưa có lịch nào ạ.</p>
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
      <nav className="admin__actions" aria-label="Quản lý tiệm">
        <Link to="/chu-tiem/khach" className="admin__action">
          Khách hàng
        </Link>
        <Link to="/chu-tiem/gio-mo-cua" className="admin__action">
          Giờ mở cửa
        </Link>
        {/* logout chỉ xóa phiên — RequireAuth thấy mất token tự đưa về
            /dang-nhap, màn này KHÔNG tự navigate. */}
        <button
          onClick={() => void logout()}
          className="admin__action admin__action--logout"
        >
          Đăng xuất
        </button>
      </nav>
    </div>
  );
}
