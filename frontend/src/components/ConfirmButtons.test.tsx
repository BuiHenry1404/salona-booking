import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { ConfirmButtons } from "./ConfirmButtons";
import type { Proposal } from "../hooks/useAgentStream";

const proposal: Proposal = {
  id: "p1",
  kind: "book",
  expiresAt: "2030-01-01T00:00:00+07:00",
  whenSpoken: "Đặt lịch 3 giờ chiều mai",
};

describe("ConfirmButtons", () => {
  it("1. render đúng hai nút Đồng ý / Không", () => {
    render(<ConfirmButtons proposal={proposal} onConfirm={vi.fn()} expired={false} />);
    expect(screen.getByRole("button", { name: "Đồng ý" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Không" })).toBeInTheDocument();
  });

  it("2. bấm Đồng ý gọi onConfirm('yes')", async () => {
    const onConfirm = vi.fn();
    render(<ConfirmButtons proposal={proposal} onConfirm={onConfirm} expired={false} />);
    await userEvent.setup().click(screen.getByRole("button", { name: "Đồng ý" }));
    expect(onConfirm).toHaveBeenCalledWith("yes");
  });

  it("3. bấm Không gọi onConfirm('no')", async () => {
    const onConfirm = vi.fn();
    render(<ConfirmButtons proposal={proposal} onConfirm={onConfirm} expired={false} />);
    await userEvent.setup().click(screen.getByRole("button", { name: "Không" }));
    expect(onConfirm).toHaveBeenCalledWith("no");
  });

  it("4. expired: cả hai nút disabled, hiện chữ hết hạn", () => {
    render(<ConfirmButtons proposal={proposal} onConfirm={vi.fn()} expired={true} />);
    expect(screen.getByRole("button", { name: "Đồng ý" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Không" })).toBeDisabled();
    expect(screen.getByText("Đề xuất đã hết hạn, anh chị nhắn lại giúp em")).toBeInTheDocument();
  });
});
