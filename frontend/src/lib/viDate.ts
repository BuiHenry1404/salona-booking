const WEEKDAYS = ["Chủ Nhật", "Thứ Hai", "Thứ Ba", "Thứ Tư", "Thứ Năm", "Thứ Sáu", "Thứ Bảy"];

/**
 * Ép về giờ Việt Nam thay vì dùng giờ máy: khách có thể đang ở nước ngoài gọi
 * về đặt lịch cho hôm sau, mà lịch thì luôn theo giờ tiệm.
 */
function vnParts(iso: string) {
  const date = new Date(iso);
  const parts = new Intl.DateTimeFormat("en-US", {
    timeZone: "Asia/Ho_Chi_Minh",
    weekday: "short",
    day: "numeric",
    month: "numeric",
    hour: "numeric",
    minute: "2-digit",
    hour12: false,
  }).formatToParts(date);
  const get = (type: string) => parts.find((p) => p.type === type)?.value ?? "";
  return {
    weekday: ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"].indexOf(get("weekday")),
    day: Number(get("day")),
    month: Number(get("month")),
    hour: Number(get("hour")) % 24, // Intl trả "24" cho nửa đêm ở một số runtime
    minute: get("minute"),
  };
}

/** "3:00 chiều" — cách người Việt lớn tuổi thực sự nói giờ, không phải "15:00". */
export function formatViTime(iso: string): string {
  const { hour, minute } = vnParts(iso);
  let period: string;
  let display: number;
  if (hour < 12) {
    period = "sáng";
    display = hour === 0 ? 12 : hour;
  } else if (hour < 18) {
    period = "chiều";
    display = hour === 12 ? 12 : hour - 12;
  } else {
    period = "tối";
    display = hour - 12;
  }
  return `${display}:${minute} ${period}`;
}

export function formatViDateTime(iso: string): string {
  const { weekday, day, month } = vnParts(iso);
  return `${WEEKDAYS[weekday]}, ${day}/${month} — ${formatViTime(iso)}`;
}

/** "12 tháng 8" — ngày tháng viết đủ chữ, dành cho chỗ cần câu tự nhiên
 * ("cuộc trò chuyện ngày 12 tháng 8"). Không bao giờ dùng dạng 12/8 cho khách. */
export function formatViDayMonth(iso: string): string {
  const { day, month } = vnParts(iso);
  return `${day} tháng ${month}`;
}

/** "Thứ Tư, 12 tháng 8" — ngày viết đủ chữ tiếng Việt kèm tên thứ, dùng ở
 * màn lịch sử. Chuỗi "2026-08-12" (không giờ) được hiểu là nửa đêm UTC,
 * vnParts đổi sang giờ Việt Nam vẫn đúng ngày nên không lệch thứ. */
export function formatViDate(iso: string): string {
  const { weekday } = vnParts(iso);
  return `${WEEKDAYS[weekday]}, ${formatViDayMonth(iso)}`;
}
