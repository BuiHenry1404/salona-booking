import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ShopHoursScreen } from "./ShopHoursScreen";

/**
 * Màn giờ mở cửa — route `/chu-tiem/gio-mo-cua`.
 *
 * API thật (backend đã có sẵn, shop.py):
 *   GET /api/v1/shop/hours → ShopHours { open_time: "08:00", close_time: "19:00",
 *                                    closed_days: number[] }  (0 = Thứ Hai ... 6 = Chủ Nhật,
 *                                    theo Python date.weekday())
 *   PUT /api/v1/shop/hours → ShopHours (trả lại đúng cái vừa lưu)
 *
 * Stub `fetch` toàn cục theo pattern các screen test khác.
 */

const hours = { open_time: "08:00", close_time: "19:00", closed_days: [6] };

function mockApi(onPut?: (body: unknown) => unknown) {
  const fn = vi.fn().mockImplementation(async (_url: string, init?: { method?: string; body?: string }) => {
    if (init?.method === "PUT") {
      const body = JSON.parse(init.body ?? "{}");
      onPut?.(body);
      return { ok: true, status: 200, json: async () => body };
    }
    return { ok: true, status: 200, json: async () => hours };
  });
  vi.stubGlobal("fetch", fn);
  return fn;
}

afterEach(() => vi.unstubAllGlobals());

const renderScreen = () =>
  render(
    <MemoryRouter initialEntries={["/chu-tiem/gio-mo-cua"]}>
      <ShopHoursScreen />
    </MemoryRouter>,
  );

describe("ShopHoursScreen", () => {
  it("1. mount: GET /api/v1/shop/hours, nạp giờ thật vào ô mở cửa/đóng cửa", async () => {
    const fetchMock = mockApi();
    renderScreen();

    expect(await screen.findByLabelText(/mở cửa/i)).toHaveValue("08:00");
    expect(screen.getByLabelText(/đóng cửa/i)).toHaveValue("19:00");
    expect(String(fetchMock.mock.calls[0]?.[0])).toContain("/api/v1/shop/hours");
  });

  it("2. đổi giờ rồi Lưu: PUT /api/v1/shop/hours với body đúng contract", async () => {
    const seen: unknown[] = [];
    mockApi((body) => seen.push(body));
    renderScreen();

    const close = await screen.findByLabelText(/đóng cửa/i);
    await userEvent.clear(close);
    await userEvent.type(close, "18:00");
    await userEvent.click(screen.getByRole("button", { name: /^lưu/i }));

    await waitFor(() => expect(seen).toHaveLength(1));
    expect(seen[0]).toEqual({
      open_time: "08:00",
      close_time: "18:00",
      closed_days: [6],
    });
  });

  it("3. ngày nghỉ: tick Thứ Hai (weekday 0) cập nhật closed_days đúng [0, 6]", async () => {
    // Thứ Hai là 0 và Chủ Nhật là 6 theo Python date.weekday() — đảo nhầm
    // chỗ này thì tiệm nghỉ sai ngày và khách đến gặp cửa đóng.
    const seen: unknown[] = [];
    mockApi((body) => seen.push(body));
    renderScreen();

    // closed_days [6] → Chủ Nhật đang được tick.
    expect(await screen.findByRole("checkbox", { name: /chủ nhật/i })).toBeChecked();

    await userEvent.click(screen.getByRole("checkbox", { name: /thứ hai/i }));
    await userEvent.click(screen.getByRole("button", { name: /^lưu/i }));

    await waitFor(() => expect(seen).toHaveLength(1));
    expect((seen[0] as { closed_days: number[] }).closed_days.slice().sort()).toEqual([0, 6]);
  });

  it("4. sau PUT thành công: trạng thái ĐÃ LƯU bằng chữ, không chỉ đổi màu", async () => {
    mockApi();
    renderScreen();

    await screen.findByLabelText(/mở cửa/i);
    await userEvent.click(screen.getByRole("button", { name: /^lưu/i }));

    expect(await screen.findByText(/đã lưu/i)).toBeInTheDocument();
  });

  it("5. sửa tiếp SAU khi lưu: 'Đã lưu' phải biến mất — chưa lưu lại thì không được lừa là đã lưu", async () => {
    // Regression: chữ "Đã lưu" treo lơ lửng sau khi chủ tiệm đổi giờ mà chưa
    // bấm Lưu lại thì họ đóng màn hình đi và tin rằng đã lưu.
    mockApi();
    renderScreen();

    await screen.findByLabelText(/mở cửa/i);
    await userEvent.click(screen.getByRole("button", { name: /^lưu/i }));
    expect(await screen.findByText(/đã lưu/i)).toBeInTheDocument();

    // Đổi giờ đóng cửa nhưng CHƯA bấm Lưu lần nữa.
    const close = screen.getByLabelText(/đóng cửa/i);
    await userEvent.clear(close);
    await userEvent.type(close, "18:00");

    expect(screen.queryByText(/đã lưu/i)).not.toBeInTheDocument();
  });
});
