import { act, renderHook } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { useAgentStream } from "./useAgentStream";

/** Socket giả: giữ handler trong Map để test tự bắn sự kiện. */
class FakeSocket {
  handlers = new Map<string, Set<(data?: unknown) => void>>();
  emitted: Array<{ event: string; data: unknown }> = [];
  connected = true;

  on(event: string, handler: (data?: unknown) => void) {
    const set = this.handlers.get(event) ?? new Set();
    set.add(handler);
    this.handlers.set(event, set);
  }
  /** Semantics socket.io-client: có handler → chỉ gỡ đúng handler đó. */
  off(event: string, handler?: (data?: unknown) => void) {
    if (!handler) {
      this.handlers.delete(event);
      return;
    }
    this.handlers.get(event)?.delete(handler);
  }
  emit(event: string, data: unknown) {
    this.emitted.push({ event, data });
  }
  fire(event: string, data?: unknown) {
    for (const h of this.handlers.get(event) ?? []) h(data);
  }
}

let socket: FakeSocket;
let released = 0;

vi.mock("../lib/socket", () => ({
  acquireSocket: () => socket,
  releaseSocket: () => {
    released += 1;
  },
}));

beforeEach(() => {
  socket = new FakeSocket();
  released = 0;
});

describe("useAgentStream", () => {
  it("A. send online: thêm bong bóng khách, emit đúng payload, phase thinking", () => {
    const { result } = renderHook(() => useAgentStream());
    act(() => result.current.send("mai 3h được không con"));

    expect(result.current.messages.at(-1)).toMatchObject({
      role: "user",
      text: "mai 3h được không con",
    });
    expect(socket.emitted).toEqual([
      { event: "send_message", data: { message: "mai 3h được không con" } },
    ]);
    expect(result.current.phase).toBe("thinking");
  });

  it("B. chuỗi rỗng/whitespace: không bubble, không emit", () => {
    const { result } = renderHook(() => useAgentStream());
    act(() => result.current.send("   "));
    expect(result.current.messages).toEqual([]);
    expect(socket.emitted).toEqual([]);
  });

  it("C. gửi lúc mất mạng: bubble pending, chưa emit, không mất câu", () => {
    const { result } = renderHook(() => useAgentStream());
    socket.connected = false;
    act(() => result.current.send("hủy giùm cô lịch mai"));

    expect(result.current.messages.at(-1)).toMatchObject({
      role: "user",
      text: "hủy giùm cô lịch mai",
      pending: true,
    });
    expect(socket.emitted).toEqual([]);
  });

  it("D. nhiều câu offline: reconnect flush đúng thứ tự, mỗi câu emit 1 lần, không bubble đôi", () => {
    const { result } = renderHook(() => useAgentStream());
    socket.connected = false;
    act(() => result.current.send("câu một"));
    act(() => result.current.send("câu hai"));

    socket.connected = true;
    act(() => socket.fire("connect"));

    expect(socket.emitted).toEqual([
      { event: "send_message", data: { message: "câu một" } },
      { event: "send_message", data: { message: "câu hai" } },
    ]);
    expect(result.current.messages.every((m) => !m.pending)).toBe(true);
    expect(result.current.messages.filter((m) => m.role === "user")).toHaveLength(2);
  });

  it("E. MẤT MẠNG GIỮA CHỪNG: chữ đã stream giữ nguyên, offline=true", () => {
    const { result } = renderHook(() => useAgentStream());
    act(() => socket.fire("token", { text: "Dạ mai 3 giờ" }));
    act(() => socket.fire("disconnect"));

    expect(result.current.offline).toBe(true);
    expect(result.current.messages.at(-1)?.text).toBe("Dạ mai 3 giờ");
    expect(result.current.messages.filter((m) => m.role === "bot")).toHaveLength(1);
  });

  it("F. reconnect: connected=true, offline=false", () => {
    const { result } = renderHook(() => useAgentStream());
    act(() => socket.fire("disconnect"));
    act(() => socket.fire("connect"));
    expect(result.current.offline).toBe(false);
    expect(result.current.connected).toBe(true);
  });

  it("G. turn_started: phase thinking, failed=false", () => {
    const { result } = renderHook(() => useAgentStream());
    act(() => result.current.send("gì đó"));
    act(() => socket.fire("error", { message: "lỗi cũ" }));
    act(() => socket.fire("turn_started"));
    expect(result.current.phase).toBe("thinking");
    expect(result.current.failed).toBe(false);
  });

  it("H. tool_started known: phase tool, label tiếng Việt đúng", () => {
    const { result } = renderHook(() => useAgentStream());
    act(() => socket.fire("tool_started", { name: "find_free_slots" }));

    expect(result.current.phase).toBe("tool");
    expect(result.current.steps).toEqual([
      { name: "find_free_slots", label: "Đang xem lịch trống…", done: false, ok: true },
    ]);
  });

  it("I. tool lạ: fallback, không lộ tên thô/dấu gạch dưới", () => {
    const { result } = renderHook(() => useAgentStream());
    act(() => socket.fire("tool_started", { name: "some_new_internal_tool_v2" }));

    expect(result.current.steps[0].label).toBe("Đang xử lý…");
    expect(result.current.steps[0].label).not.toMatch(/_/);
  });

  it("J. tool_finished: GIỮ LẠI dòng đã xong chứ không xoá", () => {
    const { result } = renderHook(() => useAgentStream());
    act(() => socket.fire("tool_started", { name: "find_free_slots" }));
    act(() => socket.fire("tool_finished", { name: "find_free_slots", ok: true }));

    expect(result.current.steps).toEqual([
      { name: "find_free_slots", label: "Đã xem lịch trống", done: true, ok: true },
    ]);
  });

  it("K. token đầu tiên: tạo đúng MỘT assistant bubble", () => {
    const { result } = renderHook(() => useAgentStream());
    act(() => socket.fire("token", { text: "Dạ" }));

    const bot = result.current.messages.filter((m) => m.role === "bot");
    expect(bot).toHaveLength(1);
    expect(bot[0].text).toBe("Dạ");
    expect(result.current.phase).toBe("answering");
  });

  it("L. nhiều token: append vào CÙNG bubble", () => {
    const { result } = renderHook(() => useAgentStream());
    act(() => socket.fire("token", { text: "Xin " }));
    act(() => socket.fire("token", { text: "chào" }));

    const bot = result.current.messages.filter((m) => m.role === "bot");
    expect(bot).toHaveLength(1);
    expect(bot[0].text).toBe("Xin chào");
  });

  it("M. complete sau streaming: REPLACE bằng answer, clear steps, phase idle", () => {
    const { result } = renderHook(() => useAgentStream());
    act(() => socket.fire("token", { text: "Dạ mai 3" }));
    act(() => socket.fire("complete", { answer: "Dạ mai 3 giờ chiều còn trống ạ" }));

    const bot = result.current.messages.filter((m) => m.role === "bot");
    expect(bot).toHaveLength(1);
    expect(bot[0].text).toBe("Dạ mai 3 giờ chiều còn trống ạ");
    expect(result.current.steps).toEqual([]);
    expect(result.current.phase).toBe("idle");
  });

  it("N. complete khi chưa có token: vẫn tạo assistant bubble final", () => {
    const { result } = renderHook(() => useAgentStream());
    act(() => socket.fire("turn_started"));
    act(() => socket.fire("complete", { answer: "Dạ chủ tiệm đang rảnh ạ" }));

    expect(result.current.messages.at(-1)).toMatchObject({
      role: "bot",
      text: "Dạ chủ tiệm đang rảnh ạ",
    });
  });

  it("O. DUPLICATE complete cùng turn: chỉ finalize MỘT lần, không bubble thứ hai", () => {
    const { result } = renderHook(() => useAgentStream());
    act(() => socket.fire("token", { text: "Dạ được" }));
    act(() => socket.fire("complete", { answer: "Dạ được ạ" }));
    act(() => socket.fire("complete", { answer: "Dạ được ạ" }));

    const bot = result.current.messages.filter((m) => m.role === "bot");
    expect(bot).toHaveLength(1);
    expect(bot[0].text).toBe("Dạ được ạ");
  });

  it("P. turn mới sau complete: guard reset, complete của turn mới hoạt động", () => {
    const { result } = renderHook(() => useAgentStream());
    act(() => socket.fire("complete", { answer: "câu trả lời một" }));

    act(() => result.current.send("câu hỏi hai"));
    act(() => socket.fire("turn_started"));
    act(() => socket.fire("token", { text: "câu trả lời " }));
    act(() => socket.fire("complete", { answer: "câu trả lời hai" }));

    const bot = result.current.messages.filter((m) => m.role === "bot");
    expect(bot.map((m) => m.text)).toEqual(["câu trả lời một", "câu trả lời hai"]);
  });

  it("Q. error: failed=true, phase idle, clear steps, bot bubble xin lỗi, không kẹt", () => {
    const { result } = renderHook(() => useAgentStream());
    act(() => result.current.send("hỏi gì đó"));
    act(() => socket.fire("tool_started", { name: "parse_time" }));
    act(() => socket.fire("token", { text: "đang nói dở" }));
    act(() => socket.fire("error", { message: "Máy đang bận chút xíu ạ" }));

    expect(result.current.phase).toBe("idle");
    expect(result.current.failed).toBe(true);
    expect(result.current.steps).toEqual([]);
    expect(result.current.messages.at(-1)).toMatchObject({
      role: "bot",
      text: "Máy đang bận chút xíu ạ",
    });
    // Câu của khách vẫn còn.
    expect(result.current.messages[0]).toMatchObject({ role: "user", text: "hỏi gì đó" });
  });

  it("R. unmount: off ĐÚNG từng exact handler, release đúng 1 lần", () => {
    const { unmount } = renderHook(() => useAgentStream());
    unmount();

    // Không gỡ thì mỗi lần vào lại màn hình chat sẽ có thêm một bộ handler.
    const owned = ["connect", "disconnect", "turn_started", "tool_started",
      "tool_finished", "token", "complete", "error"];
    for (const event of owned) expect(socket.handlers.get(event)?.size ?? 0).toBe(0);
    expect(released).toBe(1);
  });

  it("S. StrictMode mount→unmount→mount: token chỉ xử lý MỘT lần", () => {
    const first = renderHook(() => useAgentStream());
    first.unmount();
    const second = renderHook(() => useAgentStream());

    act(() => socket.fire("token", { text: "Xin chào" }));

    const bot = second.result.current.messages.filter((m) => m.role === "bot");
    expect(bot).toHaveLength(1);
    expect(bot[0].text).toBe("Xin chào");
    expect(released).toBe(1); // đúng một release cho mỗi chu kỳ mount
  });

  it("T. disconnect KHÔNG resend: reconnect không gửi lại message đã emit", () => {
    const { result } = renderHook(() => useAgentStream());
    act(() => result.current.send("câu đã gửi rồi"));
    expect(socket.emitted).toHaveLength(1);

    act(() => socket.fire("disconnect"));
    act(() => socket.fire("connect"));

    expect(socket.emitted).toHaveLength(1);
    expect(result.current.messages.filter((m) => m.role === "user")).toHaveLength(1);
  });

  it("U. REGRESSION: complete phải tự đưa failed về false (không nhờ turn_started)", () => {
    const { result } = renderHook(() => useAgentStream());

    // Lượt trước hỏng → failed=true.
    act(() => socket.fire("error", { message: "Máy đang bận chút xíu ạ" }));
    expect(result.current.failed).toBe(true);

    // Lượt mới: KHÔNG qua turn_started (đường gửi lại lỗi mạng vẫn có thể
    // thiếu) — complete đến trực tiếp. Chính onComplete phải reset failed.
    act(() => socket.fire("token", { text: "Đã" }));
    act(() => socket.fire("complete", { answer: "Đã ổn ạ" }));

    expect(result.current.failed).toBe(false);
    expect(result.current.phase).toBe("idle");
    const bot = result.current.messages.filter((m) => m.role === "bot");
    expect(bot.map((m) => m.text)).toEqual([
      "Máy đang bận chút xíu ạ",
      "Đã ổn ạ",
    ]);
  });

  it("V. REGRESSION: cùng tool chạy 2 lần — tool_finished chỉ đích MỘT invocation gần nhất", () => {
    const { result } = renderHook(() => useAgentStream());
    act(() => socket.fire("tool_started", { name: "find_free_slots" }));
    act(() => socket.fire("tool_started", { name: "find_free_slots" }));

    expect(result.current.steps).toHaveLength(2);
    expect(result.current.steps.every((s) => !s.done)).toBe(true);

    act(() => socket.fire("tool_finished", { name: "find_free_slots", ok: true }));

    expect(result.current.steps).toHaveLength(2); // không xóa step
    const done = result.current.steps.filter((s) => s.done);
    const pending = result.current.steps.filter((s) => !s.done);
    expect(done).toHaveLength(1); // chỉ MỘT invocation được đánh dấu xong
    expect(pending).toHaveLength(1);
    expect(done[0].label).toBe("Đã xem lịch trống");
    expect(pending[0].label).toBe("Đang xem lịch trống…"); // invocation còn lại
    expect(result.current.steps.every((s) => !s.label.includes("find_free_slots"))).toBe(true);

    act(() => socket.fire("tool_finished", { name: "find_free_slots", ok: true }));
    expect(result.current.steps.every((s) => s.done)).toBe(true); // cả hai mới xong
  });
});
