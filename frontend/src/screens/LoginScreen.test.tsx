import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { AuthProvider } from "../auth/AuthContext";
import { session } from "../auth/session";
import { LoginScreen } from "./LoginScreen";

function mockFetchSequence(steps: Array<{ status: number; body?: unknown }>) {
  let i = 0;
  const fn = vi.fn().mockImplementation(async () => {
    const step = steps[Math.min(i, steps.length - 1)];
    i += 1;
    return {
      ok: step.status < 400,
      status: step.status,
      json: async () => step.body,
    };
  });
  vi.stubGlobal("fetch", fn);
  return fn;
}

const ME_USER = {
  id: "u1",
  phone: "0912345678",
  full_name: "Cô Lan",
  role: "user",
  is_active: true,
};

function renderScreen() {
  // Render đúng cấu trúc route của App để navigate() sau login có đích đến.
  return render(
    <MemoryRouter initialEntries={["/dang-nhap"]}>
      <AuthProvider>
        <Routes>
          <Route path="/dang-nhap" element={<LoginScreen />} />
          <Route path="/" element={<div>màn hình chat</div>} />
          <Route path="/chu-tiem" element={<div>màn hình chủ tiệm</div>} />
        </Routes>
      </AuthProvider>
    </MemoryRouter>,
  );
}

beforeEach(() => {
  session.clear();
});
afterEach(() => {
  vi.unstubAllGlobals();
  session.clear();
});

async function fillAndSubmit(phone: string, password: string) {
  await userEvent.type(screen.getByLabelText(/số điện thoại/i), phone);
  await userEvent.type(screen.getByLabelText(/mật khẩu/i), password);
  await userEvent.click(screen.getByRole("button", { name: /đăng nhập/i }));
}

describe("LoginScreen", () => {
  it("đăng nhập user thành công → session có token, điều hướng về /", async () => {
    // Anonymous bootstrap: refresh bằng cookie chết → anonymous, không /me.
    // Sau submit: login → /me.
    mockFetchSequence([
      { status: 401, body: {} },
      { status: 200, body: { access_token: "tok", token_type: "bearer", role: "user" } },
      { status: 200, body: ME_USER },
    ]);

    renderScreen();

    await fillAndSubmit("0912345678", "matkhau123");

    await vi.waitFor(() => expect(session.load().token).toBe("tok"));
    expect(session.load().role).toBe("user");
    // Điều hướng rời /dang-nhap — form đăng nhập biến mất, tới màn hình chat.
    await vi.waitFor(() =>
      expect(screen.getByText(/màn hình chat/i)).toBeInTheDocument(),
    );
    expect(screen.queryByRole("button", { name: /đăng nhập/i })).not.toBeInTheDocument();
  });

  it("đăng nhập admin thành công → điều hướng /chu-tiem", async () => {
    mockFetchSequence([
      { status: 401, body: {} },
      { status: 200, body: { access_token: "tokadmin", token_type: "bearer", role: "admin" } },
      { status: 200, body: { ...ME_USER, role: "admin", full_name: "Chủ tiệm" } },
    ]);

    renderScreen();

    await fillAndSubmit("0901234567", "chutiem123");

    await vi.waitFor(() => expect(session.load().role).toBe("admin"));
    await vi.waitFor(() =>
      expect(screen.getByText(/màn hình chủ tiệm/i)).toBeInTheDocument(),
    );
    expect(screen.queryByRole("button", { name: /đăng nhập/i })).not.toBeInTheDocument();
  });

  it("sai mật khẩu → hiện câu backend, KHÔNG hiện mã lỗi", async () => {
    const fetchMock = mockFetchSequence([
      { status: 401, body: {} }, // bootstrap refresh chết
      { status: 401, body: { detail: "Số điện thoại hoặc mật khẩu không đúng" } }, // login sai
    ]);

    renderScreen();

    await fillAndSubmit("0912345678", "sai");

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Số điện thoại hoặc mật khẩu không đúng",
    );
    expect(screen.queryByText(/401/)).not.toBeInTheDocument();
    expect(session.load().token).toBeNull();
    // Đúng 2 request: refresh bootstrap + login sai.
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });

  it("ô số điện thoại dùng bàn phím số", () => {
    renderScreen();

    expect(screen.getByLabelText(/số điện thoại/i)).toHaveAttribute("inputMode", "numeric");
  });

  it("bỏ trống → báo ngay bằng chữ, KHÔNG gọi login", async () => {
    // AuthProvider bootstrap vẫn gọi /auth/refresh lúc mount — đó là flow
    // thật. Điều kiện phải kiểm là: KHÔNG có request /auth/login nào.
    const fetchMock = vi.fn().mockResolvedValue({
      ok: false,
      status: 401,
      json: async () => ({}),
    });
    vi.stubGlobal("fetch", fetchMock);

    renderScreen();
    // Để bootstrap xong bớt nhiễu trước khi bấm.
    await vi.waitFor(() =>
      expect(
        fetchMock.mock.calls.some(([url]) => String(url).includes("/auth/refresh")),
      ).toBe(true),
    );
    const callsAfterBootstrap = fetchMock.mock.calls.length;

    await userEvent.click(screen.getByRole("button", { name: /đăng nhập/i }));

    expect(await screen.findByRole("alert")).toBeInTheDocument();
    expect(fetchMock.mock.calls.length).toBe(callsAfterBootstrap);
    expect(
      fetchMock.mock.calls.some(([url]) => String(url).includes("/auth/login")),
    ).toBe(false);
  });
});
