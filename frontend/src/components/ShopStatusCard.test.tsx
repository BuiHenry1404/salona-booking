import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { ShopStatusCard } from "./ShopStatusCard";
import type { ShopStatus } from "../hooks/useShopStatus";

const FREE: ShopStatus = { is_busy: false, busy_until: null, minutes_left: null };
const BUSY_Z: ShopStatus = {
  is_busy: true,
  busy_until: "2026-08-07T08:30:00Z",
  minutes_left: 30,
};
const BUSY_NAIVE: ShopStatus = {
  is_busy: true,
  busy_until: "2026-08-07T08:30:00",
  minutes_left: 30,
};
const BUSY_OFFSET: ShopStatus = {
  is_busy: true,
  busy_until: "2026-08-07T15:30:00+07:00",
  minutes_left: 30,
};

describe("ShopStatusCard", () => {
  it("đang tải thì nói bằng chữ, không để trống", () => {
    render(<ShopStatusCard status={null} loading={true} />);
    expect(screen.getByText(/đang xem tiệm/i)).toBeInTheDocument();
  });

  it("status null (tải lỗi) cũng nói bằng chữ", () => {
    render(<ShopStatusCard status={null} loading={false} />);
    expect(screen.getByText(/đang xem tiệm/i)).toBeInTheDocument();
  });

  it("rảnh: nói rõ bằng CHỮ là chủ tiệm đang rảnh", () => {
    render(<ShopStatusCard status={FREE} loading={false} />);
    expect(screen.getByText(/chủ tiệm đang rảnh/i)).toBeInTheDocument();
  });

  it("bận: nói rõ bằng chữ kèm giờ xong — chuỗi đã có Z", () => {
    render(<ShopStatusCard status={BUSY_Z} loading={false} />);
    expect(screen.getByText(/chủ tiệm đang bận/i)).toBeInTheDocument();
    expect(screen.getByText(/xong lúc/i)).toBeInTheDocument();
    // 08:30 UTC = 3:30 chiều giờ Việt Nam.
    expect(screen.getByText("3:30 chiều")).toBeInTheDocument();
  });

  it("bận: busy_until UTC không suffix KHÔNG bị hiểu theo giờ máy — không lệch 7 tiếng", () => {
    render(<ShopStatusCard status={BUSY_NAIVE} loading={false} />);
    expect(screen.getByText("3:30 chiều")).toBeInTheDocument();
    expect(screen.queryByText(/8:30/)).not.toBeInTheDocument();
  });

  it("busy_until đã kèm offset thì giữ nguyên, không thêm Z", () => {
    // +07:00 chính là giờ Việt Nam: 15:30.
    render(<ShopStatusCard status={BUSY_OFFSET} loading={false} />);
    expect(screen.getByText("3:30 chiều")).toBeInTheDocument();
  });

  it("bận mà thiếu busy_until thì vẫn hiện tiêu đề, không vỡ", () => {
    render(
      <ShopStatusCard
        status={{ is_busy: true, busy_until: null, minutes_left: null }}
        loading={false}
      />,
    );
    expect(screen.getByText(/chủ tiệm đang bận/i)).toBeInTheDocument();
    expect(screen.queryByText(/xong lúc/i)).not.toBeInTheDocument();
  });

  it("TUYỆT ĐỐI KHÔNG đếm ngược trên màn hình", () => {
    render(<ShopStatusCard status={BUSY_Z} loading={false} />);
    expect(screen.queryByText(/30 phút/)).not.toBeInTheDocument();
    expect(screen.queryByText(/còn .* phút/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/nữa xong/i)).not.toBeInTheDocument();
  });

  it("dot trang trí phải aria-hidden", () => {
    render(<ShopStatusCard status={FREE} loading={false} />);
    const dot = document.querySelector(".shopcard__dot");
    expect(dot).not.toBeNull();
    expect(dot?.getAttribute("aria-hidden")).toBe("true");
  });
});
