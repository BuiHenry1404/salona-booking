import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { ToolProgress } from "./ToolProgress";
import type { ToolStep } from "../hooks/useAgentStream";

describe("ToolProgress", () => {
  it("không có step thì không render gì", () => {
    const { container } = render(<ToolProgress steps={[]} />);
    expect(container.innerHTML).toBe("");
  });

  it("đang chạy: câu tiếng Việt + spinner, KHÔNG lộ tên tool raw", () => {
    const steps: ToolStep[] = [
      { name: "find_free_slots", label: "Đang xem lịch trống…", done: false, ok: true },
    ];
    render(<ToolProgress steps={steps} />);

    expect(screen.getByText("Đang xem lịch trống…")).toBeInTheDocument();
    expect(screen.queryByText(/find_free_slots/)).not.toBeInTheDocument();
    expect(document.querySelector(".tool__spin")).not.toBeNull();
  });

  it("xong thành công: dấu tích, dòng GIỮ LẠI trên màn hình", () => {
    const steps: ToolStep[] = [
      { name: "find_free_slots", label: "Đã xem lịch trống", done: true, ok: true },
    ];
    render(<ToolProgress steps={steps} />);

    expect(screen.getByText("Đã xem lịch trống")).toBeInTheDocument();
    expect(screen.getByText("✓")).toBeInTheDocument();
    expect(document.querySelector(".tool__spin")).toBeNull();
  });

  it("xong nhưng lỗi: dấu ✕ chứ không phải dấu tích", () => {
    const steps: ToolStep[] = [
      { name: "find_free_slots", label: "Đã xem lịch trống", done: true, ok: false },
    ];
    render(<ToolProgress steps={steps} />);

    expect(screen.getByText("✕")).toBeInTheDocument();
    expect(screen.queryByText("✓")).not.toBeInTheDocument();
  });

  it("nhiều step: giữ TẤT CẢ các dòng, kể cả dòng đã xong", () => {
    const steps: ToolStep[] = [
      { name: "parse_time", label: "Đã xem lịch", done: true, ok: true },
      { name: "find_free_slots", label: "Đang xem lịch trống…", done: false, ok: true },
      { name: "propose_appointment", label: "Đang giữ chỗ cho cô…", done: false, ok: true },
    ];
    render(<ToolProgress steps={steps} />);

    expect(screen.getByText("Đã xem lịch")).toBeInTheDocument();
    expect(screen.getByText("Đang xem lịch trống…")).toBeInTheDocument();
    expect(screen.getByText("Đang giữ chỗ cho cô…")).toBeInTheDocument();
    expect(
      screen.queryByText(/parse_time|find_free_slots|propose_appointment/),
    ).not.toBeInTheDocument();
  });

  it("aria-live polite để trình đọc màn hình nghe được tiến trình", () => {
    const steps: ToolStep[] = [
      { name: "parse_time", label: "Đang xem lịch…", done: false, ok: true },
    ];
    render(<ToolProgress steps={steps} />);

    expect(screen.getByRole("list")).toHaveAttribute("aria-live", "polite");
  });

  it("dấu tích/spinner là trang trí, phải aria-hidden", () => {
    const steps: ToolStep[] = [
      { name: "parse_time", label: "Đang xem lịch…", done: false, ok: true },
    ];
    render(<ToolProgress steps={steps} />);

    const mark = document.querySelector(".tool__mark");
    expect(mark?.getAttribute("aria-hidden")).toBe("true");
  });
});
