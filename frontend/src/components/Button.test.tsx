/// <reference types="node" />
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { Button } from "./Button";

// jsdom không phân giải var() trong getComputedStyle nên không assert được
// min-height runtime; đọc thẳng nguồn (cùng cách với tokens.test.ts của task 8)
// và kiểm hai hợp đồng: token đủ cao + nút ăn token.
const read = (rel: string) => readFileSync(resolve(process.cwd(), rel), "utf8");
const btnCss = read("src/components/Button.css");
const tokensCss = read("src/styles/tokens.css");

describe("Button", () => {
  it("hiện đúng nhãn", () => {
    render(<Button>Đăng nhập</Button>);
    expect(screen.getByRole("button", { name: "Đăng nhập" })).toBeInTheDocument();
  });

  it("gọi onClick khi bấm", async () => {
    const onClick = vi.fn();
    render(<Button onClick={onClick}>Bấm</Button>);
    await userEvent.click(screen.getByRole("button"));
    expect(onClick).toHaveBeenCalledOnce();
  });

  it("cao ít nhất 56px — ngón tay người lớn tuổi kém chính xác", () => {
    // Hợp đồng 1: token --tap (nguồn duy nhất của chiều cao vùng chạm) >= 56px.
    const tap = tokensCss.match(/--tap:\s*(\d+)px/);
    expect(tap).not.toBeNull();
    expect(parseInt(tap![1])).toBeGreaterThanOrEqual(56);
    // Hợp đồng 2: .btn thực sự dùng token, không hardcode con số riêng.
    expect(btnCss).toMatch(/\.btn\s*\{[^}]*min-height:\s*var\(--tap\)/s);
  });

  it("không gọi onClick khi bị vô hiệu hóa", async () => {
    const onClick = vi.fn();
    render(<Button onClick={onClick} disabled>Bấm</Button>);
    await userEvent.click(screen.getByRole("button"));
    expect(onClick).not.toHaveBeenCalled();
  });

  it("khi đang chạy thì báo bằng CHỮ, không chỉ bằng màu", () => {
    render(<Button loading>Lưu</Button>);
    expect(screen.getByRole("button")).toHaveTextContent(/đang/i);
  });
});
