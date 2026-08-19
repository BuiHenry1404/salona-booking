import { render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import { PastChatScreen } from "./PastChatScreen";

/**
 * Màn xem lại một ngày cũ ở chế độ CHỈ ĐỌC — route `/lich-su/:day`.
 *
 * API thật (backend đã có sẵn, `DayMessagesResponse`):
 *   GET /api/v1/conversations/days/{day} → { day, is_today, messages: [...] }
 * `is_today=false` cho ngày cũ. "Chỉ xem" là thuộc tính cấu trúc: không vẽ ô
 * nhập, và diễn đạt điều đó BẰNG CHỮ chứ không bằng ô xám/bị khoá.
 *
 * Stub `fetch` toàn cục giống pattern của `useShopStatus.test.ts`.
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

function renderAt(day: string) {
  // Khai báo đúng route `/lich-su/:day` để `useParams` đọc được `day`.
  return render(
    <MemoryRouter initialEntries={[`/lich-su/${day}`]}>
      <Routes>
        <Route path="/lich-su/:day" element={<PastChatScreen />} />
      </Routes>
    </MemoryRouter>,
  );
}

const DAY_BODY = {
  day: "2026-08-12",
  is_today: false,
  messages: [
    { role: "user", content: "mai 3 giờ chiều làm tóc", created_at: "2026-08-12T08:00:00Z" },
    {
      role: "assistant",
      content: "Dạ được ạ, cô muốn đặt giờ nào?",
      created_at: "2026-08-12T08:00:05Z",
    },
  ],
};

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("PastChatScreen", () => {
  it("1. xem ngày cũ: KHÔNG tồn tại ô nhập (textbox/textarea)", async () => {
    mockFetch(DAY_BODY);

    renderAt("2026-08-12");

    // Đợi tin nhắn render xong rồi mới kết luận là không có ô nhập.
    await waitFor(() => {
      expect(screen.getByText(/mai 3 giờ chiều làm tóc/)).toBeInTheDocument();
    });
    expect(screen.queryByRole("textbox")).not.toBeInTheDocument();
  });

  it("2. nói bằng CHỮ rằng đây là ngày cũ, không chỉ ẩn ô nhập", async () => {
    mockFetch(DAY_BODY);

    renderAt("2026-08-12");

    await waitFor(() =>
      expect(screen.getByText(/cuộc trò chuyện ngày 12 tháng 8/i)).toBeInTheDocument(),
    );
  });

  it("3. có nút/link lớn 'Quay về hôm nay'", async () => {
    mockFetch(DAY_BODY);

    renderAt("2026-08-12");

    // Chấp nhận cả <button> lẫn <Link> — miễn là hành động quay về hôm nay.
    await waitFor(() => {
      const el =
        screen.queryByRole("link", { name: /quay về hôm nay/i }) ??
        screen.queryByRole("button", { name: /quay về hôm nay/i });
      expect(el).toBeInTheDocument();
    });
  });
});
