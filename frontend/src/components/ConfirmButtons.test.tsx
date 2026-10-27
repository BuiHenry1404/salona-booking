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

  it("5. CONTEXT.md: cả hai nút rộng HẾT CHIỀU NGANG, không chia đôi hàng ngang", () => {
    render(<ConfirmButtons proposal={proposal} onConfirm={vi.fn()} expired={false} />);
    const yes = screen.getByRole("button", { name: "Đồng ý" });
    const no = screen.getByRole("button", { name: "Không" });
    // Button.tsx gắn "btn--full" khi fullWidth (mặc định) — "btn--full" vắng
    // mặt nghĩa là ai đó lại truyền fullWidth={false}, phá quy tắc 56px cao /
    // rộng hết chiều ngang của CONTEXT.md.
    expect(yes.className).toMatch(/\bbtn--full\b/);
    expect(no.className).toMatch(/\bbtn--full\b/);
  });

  it("6. disabled (mất mạng): cả hai nút disabled, hiện chữ chờ mạng, KHÔNG phải chữ hết hạn", () => {
    render(
      <ConfirmButtons proposal={proposal} onConfirm={vi.fn()} expired={false} disabled={true} />,
    );
    expect(screen.getByRole("button", { name: "Đồng ý" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Không" })).toBeDisabled();
    expect(
      screen.getByText("Đang mất mạng, anh chị chờ chút rồi bấm lại nhé"),
    ).toBeInTheDocument();
    expect(screen.queryByText(/hết hạn/)).not.toBeInTheDocument();
  });
});
