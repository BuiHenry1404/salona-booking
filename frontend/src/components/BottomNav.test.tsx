import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it } from "vitest";
import { BottomNav } from "./BottomNav";

function renderAt(path: string) {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <BottomNav />
    </MemoryRouter>,
  );
}

describe("BottomNav", () => {
  it("có mục Nhắn tin trỏ về trang chính", () => {
    renderAt("/");
    expect(screen.getByRole("link", { name: /nhắn tin/i })).toHaveAttribute("href", "/");
  });

  it("có mục Lịch của tôi (route task 5 sẽ dựng)", () => {
    renderAt("/");
    expect(screen.getByRole("link", { name: /lịch của tôi/i })).toHaveAttribute(
      "href",
      "/lich-cua-toi",
    );
  });

  it("ở trang chat: mục chat active bằng aria-current, mục kia không", () => {
    renderAt("/");
    expect(screen.getByRole("link", { name: /nhắn tin/i })).toHaveAttribute(
      "aria-current",
      "page",
    );
    expect(screen.getByRole("link", { name: /lịch của tôi/i })).not.toHaveAttribute(
      "aria-current",
    );
  });

  it("ở /lich-cua-toi: mục Lịch của tôi mới là active — nhờ `end` trên mục chat", () => {
    renderAt("/lich-cua-toi");
    expect(screen.getByRole("link", { name: /lịch của tôi/i })).toHaveAttribute(
      "aria-current",
      "page",
    );
    expect(screen.getByRole("link", { name: /nhắn tin/i })).not.toHaveAttribute(
      "aria-current",
    );
  });

  it("là navigation landmark", () => {
    renderAt("/");
    expect(screen.getByRole("navigation")).toBeInTheDocument();
  });
});
