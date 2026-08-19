import { render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ChatHistoryScreen } from "./ChatHistoryScreen";

/**
 * Màn danh sách các ngày đã trò chuyện — route `/lich-su`.
 *
 * API thật (backend đã có sẵn, `DayListResponse`):
 *   GET /api/v1/conversations/days → { days: [{ day, message_count, preview }] }
 * Lấy `user_id` từ JWT; khách chỉ thấy dữ liệu của chính mình.
 *
 * Stub `fetch` toàn cục giống pattern của `useShopStatus.test.ts` — KHÔNG mock
 * module `api` để còn phủ luôn đường `api.get` + `session`.
 */
function mockFetch(body: unknown, status = 200) {
  const fn = vi.fn().mockResolvedValue({
    ok: status < 400,
    status,
    json: async () => body,
  });
  vi.stubGlobal("fetch", fn);
  return fn;
}

function renderAt() {
  // Màn này chỉ có Link nội bộ; bọc MemoryRouter để điều hướng được định nghĩa.
  return render(
    <MemoryRouter initialEntries={["/lich-su"]}>
      <ChatHistoryScreen />
    </MemoryRouter>,
  );
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("ChatHistoryScreen", () => {
  it("1. hiện đầy đủ tên THỨ + ngày bằng CHỮ tiếng Việt, KHÔNG dùng dạng 12/08", async () => {
    // 2026-08-12 là Thứ Tư — thứ phải do code tính ra từ ngày, không hard-code bừa.
    mockFetch({
      days: [{ day: "2026-08-12", message_count: 6, preview: "mai 3 giờ chiều làm tóc" }],
    });

    renderAt();

    await waitFor(() =>
      expect(screen.getByText(/thứ tư, 12 tháng 8/i)).toBeInTheDocument(),
    );
    // Không chấp nhận bất kỳ dạng số nào "12/8" hay "12/08".
    expect(screen.queryByText(/12\/0?8/)).not.toBeInTheDocument();
  });

  it("2. hiện câu mở đầu của khách để nhận ra hôm đó nói chuyện gì", async () => {
    mockFetch({
      days: [{ day: "2026-08-12", message_count: 6, preview: "mai 3 giờ chiều làm tóc" }],
    });

    renderAt();

    await waitFor(() =>
      expect(screen.getByText(/mai 3 giờ chiều làm tóc/)).toBeInTheDocument(),
    );
  });

  it("3. chưa từng trò chuyện thì nói bằng chữ, không để trống trơn", async () => {
    mockFetch({ days: [] });

    renderAt();

    await waitFor(() =>
      expect(screen.getByText(/chưa có cuộc trò chuyện nào/i)).toBeInTheDocument(),
    );
  });
});
