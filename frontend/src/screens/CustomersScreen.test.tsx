import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import { CustomersScreen } from "./CustomersScreen";

/**
 * Màn danh sách khách của chủ tiệm — route `/chu-tiem/khach`.
 *
 * API thật (backend đã có sẵn):
 *   GET /api/v1/auth/users → bare array UserResponse[]
 *     { id, phone, full_name: string|null, role: "user"|"admin", is_active }
 *   POST /api/v1/auth/users → 201 UserResponse
 *     body CreateUserRequest { phone, password (>=4), full_name?, role }
 *
 * Stub `fetch` toàn cục theo pattern test_api_flow — không mock module `api`
 * để còn phủ cả đường refresh 401 bên trong.
 */

vi.mock("../auth/AuthContext", () => ({
  useAuth: () => ({ token: "tok", role: "admin" as const, fullName: "Chủ tiệm", logout: vi.fn() }),
}));

/** role lọc: màn khách hàng không liệt kê tài khoản admin. */
const users = [
  { id: "u1", phone: "0912345678", full_name: "Cô Lan", role: "user", is_active: true },
  { id: "u2", phone: "0987654321", full_name: "Cô Hoa", role: "user", is_active: true },
  { id: "a1", phone: "0900000000", full_name: "Chủ tiệm", role: "admin", is_active: true },
];

const CREATED = { id: "u3", phone: "0900111222", full_name: "Cô Mai", role: "user", is_active: true };

function mockApi(onPost?: (body: unknown) => unknown) {
  const fn = vi.fn().mockImplementation(async (_url: string, init?: { method?: string; body?: string }) => {
    if (init?.method === "POST") {
      const created = onPost?.(JSON.parse(init.body ?? "{}")) ?? CREATED;
      return { ok: true, status: 201, json: async () => created };
    }
    return { ok: true, status: 200, json: async () => users };
  });
  vi.stubGlobal("fetch", fn);
  return fn;
}

afterEach(() => vi.unstubAllGlobals());

const renderScreen = () =>
  render(
    <MemoryRouter initialEntries={["/chu-tiem/khach"]}>
      <CustomersScreen />
    </MemoryRouter>,
  );

describe("CustomersScreen", () => {
  it("1. nạp GET /api/v1/auth/users: liệt kê khách kèm SĐT, KHÔNG liệt kê admin", async () => {
    const fetchMock = mockApi();
    renderScreen();

    expect(await screen.findByText("Cô Lan")).toBeInTheDocument();
    expect(screen.getByText("0987654321")).toBeInTheDocument();
    expect(screen.queryByText("0900000000")).not.toBeInTheDocument();

    const url = String(fetchMock.mock.calls[0]?.[0]);
    expect(url).toContain("/api/v1/auth/users");
  });

  it("2. lọc TẠI CHỖ theo SĐT: gõ vài số là còn đúng khách đó, không gọi API mới", async () => {
    const fetchMock = mockApi();
    renderScreen();
    await screen.findByText("Cô Lan");
    const callsBefore = fetchMock.mock.calls.length;

    await userEvent.type(screen.getByLabelText(/tìm theo số điện thoại/i), "0987");

    expect(screen.queryByText("Cô Lan")).not.toBeInTheDocument();
    expect(screen.getByText("Cô Hoa")).toBeInTheDocument();
    expect(fetchMock.mock.calls.length).toBe(callsBefore); // không thêm request nào
  });

  it("3. lọc được cả theo TÊN, vì chủ tiệm nhớ tên hơn nhớ số", async () => {
    mockApi();
    renderScreen();
    await screen.findByText("Cô Lan");

    await userEvent.type(screen.getByLabelText(/tìm theo số điện thoại/i), "hoa");

    expect(screen.getByText("Cô Hoa")).toBeInTheDocument();
    expect(screen.queryByText("Cô Lan")).not.toBeInTheDocument();
  });

  it("4. form tạo khách: SĐT + tên + mật khẩu ban đầu; POST đúng body schema thật", async () => {
    const seen: unknown[] = [];
    mockApi((body) => {
      seen.push(body);
      return CREATED;
    });
    renderScreen();

    await userEvent.click(await screen.findByRole("button", { name: /tạo tài khoản/i }));
    // Khớp CHÍNH XÁC label: ô tìm "Tìm theo số điện thoại" cũng khớp regex
    // /số điện thoại/i và làm getByLabelText ném lỗi trùng.
    await userEvent.type(screen.getByLabelText("Số điện thoại"), "0900111222");
    await userEvent.type(screen.getByLabelText(/tên khách/i), "Cô Mai");
    await userEvent.type(screen.getByLabelText(/mật khẩu ban đầu/i), "1234");
    await userEvent.click(screen.getByRole("button", { name: /lưu khách mới/i }));

    await waitFor(() => expect(seen).toHaveLength(1));
    expect(seen[0]).toEqual({
      phone: "0900111222",
      full_name: "Cô Mai",
      password: "1234",
      role: "user",
    });
    // Khách mới xuất hiện trong danh sách sau khi tạo.
    expect(await screen.findByText("Cô Mai")).toBeInTheDocument();
  });

  it("5. sau khi tạo: NHẮC ĐỌC MẬT KHẨU cho khách bằng chữ — không có email/SMS để gửi", async () => {
    mockApi();
    renderScreen();

    await userEvent.click(await screen.findByRole("button", { name: /tạo tài khoản/i }));
    await userEvent.type(screen.getByLabelText("Số điện thoại"), "0900111222");
    await userEvent.type(screen.getByLabelText(/tên khách/i), "Cô Mai");
    await userEvent.type(screen.getByLabelText(/mật khẩu ban đầu/i), "1234");
    await userEvent.click(screen.getByRole("button", { name: /lưu khách mới/i }));

    expect(await screen.findByText(/đọc.*cho khách/i)).toBeInTheDocument();
  });

  it("6. mỗi khách có link 'Đặt lịch hộ' trỏ /chu-tiem/khach/{userId}/dat-lich", async () => {
    mockApi();
    renderScreen();

    const links = await screen.findAllByRole("link", { name: /đặt lịch hộ/i });
    expect(links[0]).toHaveAttribute("href", "/chu-tiem/khach/u1/dat-lich");
    expect(links[1]).toHaveAttribute("href", "/chu-tiem/khach/u2/dat-lich");
  });
});
