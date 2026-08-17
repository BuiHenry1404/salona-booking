export interface ToolLabel {
  running: string;
  done: string;
}

/**
 * Bảng ánh xạ nằm ở FRONTEND, không ở backend: đổi câu chữ cho dễ hiểu hơn là
 * việc sẽ làm nhiều lần sau khi tiệm dùng thật, và không đáng phải deploy lại API.
 */
export const TOOL_LABELS: Record<string, ToolLabel> = {
  get_shop_status: {
    running: "Đang xem chủ tiệm có rảnh không…",
    done: "Đã xem trạng thái tiệm",
  },
  // Khách không cần biết máy đang "phân tích thời gian" — họ chỉ cần biết
  // máy đang làm việc với cái lịch.
  parse_time: { running: "Đang xem lịch…", done: "Đã xem lịch" },
  find_free_slots: { running: "Đang xem lịch trống…", done: "Đã xem lịch trống" },
  // Agent không ghi lịch trực tiếp — nó giữ chỗ rồi hỏi khách xác nhận.
  propose_appointment: { running: "Đang giữ chỗ cho cô…", done: "Đã giữ chỗ" },
  list_my_appointments: { running: "Đang xem lịch của cô…", done: "Đã xem lịch của cô" },
  cancel_appointment: { running: "Đang hủy lịch…", done: "Đã hủy lịch" },
};

const GENERIC: ToolLabel = { running: "Đang xử lý…", done: "Đã xong" };

/** Tool lạ (backend thêm tool mới trước khi frontend kịp cập nhật) rơi vào câu
 * chung. Hiện `find_free_slots` cho một cụ 70 tuổi là hỏng cả màn hình. */
export function toolLabel(name: string): ToolLabel {
  return TOOL_LABELS[name] ?? GENERIC;
}
