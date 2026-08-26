import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { BusySwitch } from "./BusySwitch";
import type { ShopStatus } from "../hooks/useShopStatus";

const freeStatus: ShopStatus = {
  is_busy: false,
  busy_until: null,
  minutes_left: null,
};

const busyStatus: ShopStatus = {
  is_busy: true,
  busy_until: "2026-08-21T08:00:00Z",
  minutes_left: 60,
};

const DURATION_LABELS = ["15 phút", "30 phút", "1 tiếng", "2 tiếng"];

function mockStatusResponse(body: ShopStatus) {
  const fn = vi.fn().mockResolvedValue({
    ok: true,
    status: 200,
    json: async () => body,
  });
  vi.stubGlobal("fetch", fn);
  return fn;
}

const renderSwitch = (status: ShopStatus | null = freeStatus) => {
  const onApplied = vi.fn();
  return {
    onApplied,
    ...render(<BusySwitch status={status} onApplied={onApplied} />),
  };
};

describe("BusySwitch", () => {
  beforeEach(() => {
    vi.unstubAllGlobals();
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("1. trạng thái rảnh: nút kích hoạt là button, ban đầu KHÔNG tự động focus", () => {
    renderSwitch();
    const trigger = screen.getByRole("button", { name: /tôi đang bận/i });
    expect(trigger).toBeInTheDocument();
    expect(document.activeElement).not.toBe(trigger);
    trigger.focus();
    expect(document.activeElement).toBe(trigger);
  });

  it("2. sau khi bấm 'Tôi đang bận', picker hiện, aria-expanded đúng, aria-controls trỏ tới picker", async () => {
    renderSwitch();
    const trigger = screen.getByRole("button", { name: /tôi đang bận/i });

    await userEvent.click(trigger);

    expect(trigger).toHaveAttribute("aria-expanded", "true");
    const pickerId = trigger.getAttribute("aria-controls");
    expect(pickerId).toBeTruthy();
    const picker = document.getElementById(pickerId!);
    expect(picker).toBeInTheDocument();
    expect(picker).toHaveAttribute("role", "group");
    expect(picker).toHaveAttribute("aria-labelledby");
  });

  it("3. mở picker chuyển focus sang nút thời lượng đầu tiên", async () => {
    renderSwitch();
    const trigger = screen.getByRole("button", { name: /tôi đang bận/i });

    await userEvent.click(trigger);

    const firstButton = screen.getByRole("button", { name: "15 phút" });
    await waitFor(() => expect(document.activeElement).toBe(firstButton));
  });

  it("4. các nút thời lượng có tên truy cập đọc được", async () => {
    renderSwitch();
    await userEvent.click(screen.getByRole("button", { name: /tôi đang bận/i }));

    for (const label of DURATION_LABELS) {
      expect(screen.getByRole("button", { name: label })).toBeInTheDocument();
    }
  });

  it("5. picker có nhãn truy cập 'Bận khoảng bao lâu ?'", async () => {
    renderSwitch();
    await userEvent.click(screen.getByRole("button", { name: /tôi đang bận/i }));

    const picker = document.getElementById("busy-duration-picker");
    expect(picker).toBeInTheDocument();
    const labelId = picker!.getAttribute("aria-labelledby");
    expect(document.getElementById(labelId!)).toHaveTextContent("Bận khoảng bao lâu ạ?");
  });

  it("6. bấm 'Thôi, để sau' đóng picker và trả focus về nút kích hoạt", async () => {
    renderSwitch();
    const trigger = screen.getByRole("button", { name: /tôi đang bận/i });

    await userEvent.click(trigger);
    await waitFor(() => expect(document.activeElement).toBe(screen.getByRole("button", { name: "15 phút" })));

    await userEvent.click(screen.getByRole("button", { name: /thôi, để sau/i }));

    await waitFor(() => expect(document.activeElement).toBe(trigger));
    expect(screen.queryByRole("button", { name: "15 phút" })).not.toBeInTheDocument();
  });

  it("7. bấm Escape đóng picker và trả focus về nút kích hoạt", async () => {
    renderSwitch();
    const trigger = screen.getByRole("button", { name: /tôi đang bận/i });

    await userEvent.click(trigger);
    await waitFor(() => expect(document.activeElement).toBe(screen.getByRole("button", { name: "15 phút" })));

    await userEvent.keyboard("{Escape}");

    await waitFor(() => expect(document.activeElement).toBe(trigger));
    expect(screen.queryByRole("button", { name: "15 phút" })).not.toBeInTheDocument();
  });

  it("8. chọn thời lượng gọi API đúng và áp dụng trạng thái", async () => {
    const fetchMock = mockStatusResponse(busyStatus);
    const { onApplied } = renderSwitch();

    await userEvent.click(screen.getByRole("button", { name: /tôi đang bận/i }));
    await userEvent.click(screen.getByRole("button", { name: "1 tiếng" }));

    await waitFor(() => expect(onApplied).toHaveBeenCalledWith(busyStatus));
    const call = fetchMock.mock.calls.at(-1)!;
    expect(call[0]).toContain("/api/v1/shop/busy");
    expect(JSON.parse(call[1].body)).toEqual({ minutes: 60 });
  });

  it("9. trạng thái bận hiển thị đúng và cho phép trở lại rảnh", async () => {
    mockStatusResponse(freeStatus);
    const { onApplied } = renderSwitch(busyStatus);

    expect(screen.getByText(/^đang bận$/i)).toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: /tôi rảnh rồi/i }));

    await waitFor(() => expect(onApplied).toHaveBeenCalledWith(freeStatus));
  });
});
