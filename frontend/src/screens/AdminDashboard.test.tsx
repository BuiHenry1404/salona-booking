import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { AdminDashboard } from "./AdminDashboard";

/**
 * Màn chủ tiệm — route `/chu-tiem`.
 *
 * Mock cả ba hook (useShopStatus / useAdminFeed / useAuth) để test chỉ tập
 * trung vào CÁCH màn hình tiêu thụ chúng, đúng pattern ChatScreen.test.tsx.
 *
 * Contract backend đã xác minh (source of truth là code):
 *   POST /api/v1/shop/busy  body {minutes: 5..480} → ShopStatus 3 field
 *   POST /api/v1/shop/free  không body            → ShopStatus 3 field
 * Cả hai trả thẳng ShopStatusResponse nên áp NGAY bằng apply(), không chờ
 * broadcast `shop_status_changed` (kênh đó để đồng bộ máy KHÁC).
 */

/** Hình dạng thật của useShopStatus() — status: ShopStatus | null (null khi
 * GET /shop/status lỗi và loading đã xong). */
const shop = {
  status: { is_busy: false, busy_until: null, minutes_left: null } as {
    is_busy: boolean;
    busy_until: string | null;
    minutes_left: number | null;
  } | null,
  loading: false,
  apply: vi.fn(),
};

/** Hình dạng thật của useAdminFeed() — payload `appointment_created` chỉ có
 * 5 field (id, start_at, user_name, phone, note) nên mảng appointments này
 * cũng chỉ dùng đúng những field đó. */
const feed = {
  appointments: [] as Array<{
    id: string;
    start_at: string;
    user_name: string | null;
    phone: string | null;
    note: string | null;
  }>,
  newIds: new Set<string>(),
  error: null as string | null,
  reload: vi.fn(),
};

const auth = {
  token: "tok",
  role: "admin" as const,
  fullName: "Chủ tiệm",
  logout: vi.fn(),
};

vi.mock("../hooks/useShopStatus", () => ({ useShopStatus: () => shop }));
vi.mock("../hooks/useAdminFeed", () => ({ useAdminFeed: () => feed }));
vi.mock("../auth/AuthContext", () => ({ useAuth: () => auth }));

/** Lịch hẹn mẫu — đúng payload 5 field của appointment_created. */
const appointment = {
  id: "a1",
  start_at: "2026-08-21T08:00:00Z",
  user_name: "Cô Lan",
  phone: "0912345678",
  note: "làm tóc",
};

/** Response thật của POST /shop/busy và /shop/free: ShopStatusResponse. */
function mockStatusResponse(body: {
  is_busy: boolean;
  busy_until: string | null;
  minutes_left: number | null;
}) {
  const fn = vi.fn().mockResolvedValue({
    ok: true,
    status: 200,
    json: async () => body,
  });
  vi.stubGlobal("fetch", fn);
  return fn;
}

beforeEach(() => {
  shop.status = { is_busy: false, busy_until: null, minutes_left: null };
  shop.loading = false;
  shop.apply.mockClear();
  feed.appointments = [];
  feed.newIds = new Set();
  feed.error = null;
  auth.logout.mockClear();
});

afterEach(() => {
  vi.unstubAllGlobals();
});

const renderScreen = () =>
  render(
    <MemoryRouter initialEntries={["/chu-tiem"]}>
      <AdminDashboard />
    </MemoryRouter>,
  );

describe("AdminDashboard", () => {
  it("1. đang rảnh: nút chính to rõ là 'Tôi đang bận'", () => {
    renderScreen();
    expect(screen.getByRole("button", { name: /tôi đang bận/i })).toBeInTheDocument();
  });

  it("2. bấm 'Tôi đang bận' hiện đúng bốn lựa chọn thời lượng", async () => {
    renderScreen();
    await userEvent.click(screen.getByRole("button", { name: /tôi đang bận/i }));

    for (const label of ["15 phút", "30 phút", "1 tiếng", "2 tiếng"]) {
      expect(screen.getByRole("button", { name: label })).toBeInTheDocument();
    }
  });

  it("3. chọn '1 tiếng' gửi đúng 60 phút tới /api/v1/shop/busy, không phải 1", async () => {
    const fetchMock = mockStatusResponse({
      is_busy: true,
      busy_until: "2026-08-21T09:00:00Z",
      minutes_left: 60,
    });
    renderScreen();

    await userEvent.click(screen.getByRole("button", { name: /tôi đang bận/i }));
    await userEvent.click(screen.getByRole("button", { name: "1 tiếng" }));

    await waitFor(() => expect(fetchMock).toHaveBeenCalled());
    const call = fetchMock.mock.calls.at(-1)!;
    expect(call[0]).toContain("/api/v1/shop/busy");
    expect(call[1].method).toBe("POST");
    expect(JSON.parse(call[1].body)).toEqual({ minutes: 60 });
  });

  it("4. KHÔNG chờ broadcast: áp NGAY ShopStatus server trả về bằng apply()", async () => {
    // Broadcast `shop_status_changed` để đồng bộ máy KHÁC — máy vừa bấm phải
    // thấy kết quả từ chính response HTTP của nó, không ngồi chờ socket vọng
    // lại (nếu không màn hình đứng im, chủ tiệm tưởng hỏng rồi bấm lại).
    const status = { is_busy: true, busy_until: null, minutes_left: 30 };
    mockStatusResponse(status);
    renderScreen();

    await userEvent.click(screen.getByRole("button", { name: /tôi đang bận/i }));
    await userEvent.click(screen.getByRole("button", { name: "30 phút" }));

    await waitFor(() => expect(shop.apply).toHaveBeenCalledWith(status));
  });

  it("5. đang bận: chữ 'Đang bận' + giờ xong bằng formatViTime, KHÔNG countdown phút", () => {
    // 08:00 UTC = 3:00 chiều giờ Việt Nam.
    shop.status = { is_busy: true, busy_until: "2026-08-21T08:00:00Z", minutes_left: 25 };
    renderScreen();

    expect(screen.getByText(/^đang bận$/i)).toBeInTheDocument();
    expect(screen.getByText(/3:00 chiều/)).toBeInTheDocument();
    expect(screen.queryByText(/25 phút/)).not.toBeInTheDocument();
    expect(screen.queryByText(/còn .* phút/i)).not.toBeInTheDocument();
  });

  it("6. bấm 'Tôi rảnh rồi' gọi POST /api/v1/shop/free", async () => {
    shop.status = { is_busy: true, busy_until: "2026-08-21T08:00:00Z", minutes_left: 25 };
    const fetchMock = mockStatusResponse({
      is_busy: false,
      busy_until: null,
      minutes_left: null,
    });
    renderScreen();

    await userEvent.click(screen.getByRole("button", { name: /tôi rảnh rồi/i }));

    await waitFor(() => expect(fetchMock).toHaveBeenCalled());
    const call = fetchMock.mock.calls.at(-1)!;
    expect(call[0]).toContain("/api/v1/shop/free");
    expect(call[1].method).toBe("POST");
    // /shop/free không nhận body — endpoint không có request model.
    expect(call[1]?.body).toBeUndefined();
  });

  it("7. sau POST free: áp ngay response — apply() nhận is_busy false", async () => {
    shop.status = { is_busy: true, busy_until: "2026-08-21T08:00:00Z", minutes_left: 25 };
    const status = { is_busy: false, busy_until: null, minutes_left: null };
    mockStatusResponse(status);
    renderScreen();

    await userEvent.click(screen.getByRole("button", { name: /tôi rảnh rồi/i }));

    await waitFor(() => expect(shop.apply).toHaveBeenCalledWith(status));
  });

  it("8. lịch hôm nay: hiện tên và số điện thoại khách (người đặt phải gọi được)", () => {
    feed.appointments = [appointment];
    renderScreen();
    expect(screen.getByText(/Cô Lan/)).toBeInTheDocument();
    expect(screen.getByText(/0912345678/)).toBeInTheDocument();
  });

  it("9. lịch vừa được Socket.IO đẩy lên có nhãn MỚI", () => {
    feed.appointments = [appointment];
    feed.newIds = new Set(["a1"]);
    renderScreen();
    expect(screen.getByText("MỚI")).toBeInTheDocument();
  });

  it("10. có link sang Khách hàng và Giờ mở cửa (Task 7)", () => {
    renderScreen();
    expect(screen.getByRole("link", { name: /khách hàng/i })).toHaveAttribute(
      "href",
      "/chu-tiem/khach",
    );
    expect(screen.getByRole("link", { name: /giờ mở cửa/i })).toHaveAttribute(
      "href",
      "/chu-tiem/gio-mo-cua",
    );
  });

  it("11. bấm 'Đăng xuất' gọi logout() — RequireAuth tự lo redirect", async () => {
    renderScreen();
    await userEvent.click(screen.getByRole("button", { name: /đăng xuất/i }));
    expect(auth.logout).toHaveBeenCalledTimes(1);
  });

  it("12. chưa biết tình trạng (status=null): KHÔNG phịa 'Đang rảnh' hay nút bận", () => {
    // GET /shop/status lỗi thì loading=false + status=null — nói sai "Đang
    // rảnh" một lần là chủ tiệm thôi tin bảng này.
    shop.status = null;
    renderScreen();

    expect(screen.getByText(/chưa biết tình trạng tiệm/i)).toBeInTheDocument();
    expect(screen.queryByText(/đang rảnh/i)).not.toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: /tôi đang bận/i }),
    ).not.toBeInTheDocument();
  });

  it("13. hiển thị section 'Khách theo tháng'", () => {
    renderScreen();
    expect(
      screen.getByRole("heading", { name: /khách theo tháng/i }),
    ).toBeInTheDocument();
  });

  it("14. hiển thị đúng 12 tháng", () => {
    renderScreen();
    const rows = screen.getAllByRole("listitem");
    expect(rows).toHaveLength(12);
  });

  it("15. tháng hiện tại (mock) nằm cuối danh sách", () => {
    renderScreen();
    const rows = screen.getAllByRole("listitem");
    const lastRow = rows[rows.length - 1];
    expect(lastRow).toHaveTextContent("T8/26");
    expect(lastRow).toHaveTextContent("32");
  });

  it("16. hiển thị đúng số khách cho một tháng cụ thể", () => {
    renderScreen();
    // T5/2026 có 35 khách theo mock
    const row = screen.getByTitle(/tháng 5\/2026/i);
    expect(row).toHaveTextContent("35");
  });
});
