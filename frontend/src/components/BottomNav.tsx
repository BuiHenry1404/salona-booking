import { NavLink } from "react-router-dom";
import "./BottomNav.css";

/**
 * Chỉ hai mục, luôn hiện, chữ to. Không hamburger, không menu ẩn: thứ gì
 * phải bấm mới thấy thì với người lớn tuổi coi như không tồn tại.
 *
 * Trạng thái active nói bằng aria-current (NavLink tự đặt) kèm gạch chân —
 * không phụ thuộc duy nhất vào màu.
 */
export function BottomNav() {
  return (
    <nav className="bottomnav">
      <NavLink to="/" end className={({ isActive }) => (isActive ? "bn bn--on" : "bn")}>
        Nhắn tin
      </NavLink>
      <NavLink
        to="/lich-cua-toi"
        className={({ isActive }) => (isActive ? "bn bn--on" : "bn")}
      >
        Lịch của tôi
      </NavLink>
    </nav>
  );
}
