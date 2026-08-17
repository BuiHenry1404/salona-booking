import { render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";
import { RequireAuth } from "./RequireAuth";
import { useAuth } from "./AuthContext";

vi.mock("./AuthContext", () => ({
  useAuth: vi.fn(),
}));

const mockedUseAuth = vi.mocked(useAuth);

function renderAt(path: string, initial: string, props: { role?: "admin" } = {}) {
  return render(
    <MemoryRouter initialEntries={[initial]}>
      <Routes>
        <Route path="/dang-nhap" element={<div>màn đăng nhập</div>} />
        <Route
          path={path}
          element={
            <RequireAuth role={props.role}>
              <div>nội dung được bảo vệ</div>
            </RequireAuth>
          }
        />
      </Routes>
    </MemoryRouter>,
  );
}

describe("RequireAuth", () => {
  it("đang loading → hiện chữ Đang mở…", () => {
    mockedUseAuth.mockReturnValue({
      token: null,
      role: null,
      fullName: null,
      loading: true,
      login: vi.fn(),
      logout: vi.fn(),
    });

    renderAt("/roi-di", "/roi-di");

    expect(screen.getByText(/đang mở/i)).toBeInTheDocument();
    expect(screen.queryByText(/nội dung được bảo vệ/i)).not.toBeInTheDocument();
  });

  it("chưa đăng nhập → đẩy sang /dang-nhap", () => {
    mockedUseAuth.mockReturnValue({
      token: null,
      role: null,
      fullName: null,
      loading: false,
      login: vi.fn(),
      logout: vi.fn(),
    });

    renderAt("/roi-di", "/roi-di");

    expect(screen.getByText(/màn đăng nhập/i)).toBeInTheDocument();
  });

  it("user thường vào route admin → đẩy về /", () => {
    mockedUseAuth.mockReturnValue({
      token: "tok",
      role: "user",
      fullName: "Cô Lan",
      loading: false,
      login: vi.fn(),
      logout: vi.fn(),
    });

    render(
      <MemoryRouter initialEntries={["/chu-tiem"]}>
        <Routes>
          <Route path="/" element={<div>màn chat</div>} />
          <Route path="/dang-nhap" element={<div>màn đăng nhập</div>} />
          <Route
            path="/chu-tiem"
            element={
              <RequireAuth role="admin">
                <div>chỉ chủ tiệm</div>
              </RequireAuth>
            }
          />
        </Routes>
      </MemoryRouter>,
    );

    expect(screen.getByText(/màn chat/i)).toBeInTheDocument();
    expect(screen.queryByText(/chỉ chủ tiệm/i)).not.toBeInTheDocument();
  });

  it("user hợp lệ → render children", () => {
    mockedUseAuth.mockReturnValue({
      token: "tok",
      role: "user",
      fullName: "Cô Lan",
      loading: false,
      login: vi.fn(),
      logout: vi.fn(),
    });

    renderAt("/roi-di", "/roi-di");

    expect(screen.getByText(/nội dung được bảo vệ/i)).toBeInTheDocument();
  });

  it("admin hợp lệ vào route admin → render admin children", () => {
    mockedUseAuth.mockReturnValue({
      token: "tok",
      role: "admin",
      fullName: "Chủ tiệm",
      loading: false,
      login: vi.fn(),
      logout: vi.fn(),
    });

    renderAt("/chu-tiem", "/chu-tiem", { role: "admin" });

    expect(screen.getByText(/nội dung được bảo vệ/i)).toBeInTheDocument();
  });
});
