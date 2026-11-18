import { act, renderHook } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { createElement } from "react";
import { StrictMode } from "react";
import type { ReactNode } from "react";
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
      {
        event: "send_message",
        data: { message: "mai 3h được không con", message_id: expect.any(String) },
      },
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
      { event: "send_message", data: { message: "câu một", message_id: expect.any(String) } },
      { event: "send_message", data: { message: "câu hai", message_id: expect.any(String) } },
    ]);
    expect(result.current.messages.every((m) => !m.pending)).toBe(true);
    expect(result.current.messages.filter((m) => m.role === "user")).toHaveLength(2);
  });

  it("E. MẤT MẠNG GIỮA CHỪNG: câu đã hiện giữ nguyên, offline=true", () => {
    const { result } = renderHook(() => useAgentStream());
    act(() => socket.fire("complete", { answer: "Dạ mai 3 giờ còn trống ạ" }));
    act(() => socket.fire("disconnect"));

    expect(result.current.offline).toBe(true);
    expect(result.current.messages.at(-1)?.text).toBe("Dạ mai 3 giờ còn trống ạ");
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

  it("tool_started hiện label, không hiện tên tool", () => {
    const { result } = renderHook(() => useAgentStream());
    // Nhãn do server gửi (`TOOL_LABELS` của gói salon) — kể cả tool frontend
    // chưa từng biết tên.
    act(() =>
      socket.fire("tool_started", { name: "appointment_task", label: "Đang xem lịch hẹn…" }),
    );

    expect(result.current.phase).toBe("tool");
    expect(result.current.steps).toEqual([
      { name: "appointment_task", label: "Đang xem lịch hẹn…", done: false, ok: true },
    ]);
    expect(result.current.steps[0].label).not.toContain("appointment_task");
  });

  it("I. tool_started thiếu label: câu chung, không lộ tên thô/dấu gạch dưới", () => {
    const { result } = renderHook(() => useAgentStream());
    act(() => socket.fire("tool_started", { name: "some_new_internal_tool_v2" }));
    act(() => socket.fire("tool_started", { name: "other_tool", label: "" }));

    expect(result.current.steps.map((s) => s.label)).toEqual(["Đang xử lý…", "Đang xử lý…"]);
    for (const step of result.current.steps) expect(step.label).not.toMatch(/_/);
  });

  it("J. tool_finished: GIỮ LẠI dòng đã xong chứ không xoá, vẫn là label của server", () => {
    const { result } = renderHook(() => useAgentStream());
    act(() =>
      socket.fire("tool_started", { name: "find_free_slots", label: "Đang xem lịch trống…" }),
    );
    act(() =>
      socket.fire("tool_finished", {
        name: "find_free_slots",
        label: "Đang xem lịch trống…",
        ok: true,
      }),
    );

    expect(result.current.steps).toEqual([
      { name: "find_free_slots", label: "Đang xem lịch trống…", done: true, ok: true },
    ]);
  });

  it("không có token: turn_started → thinking, complete → một bubble", () => {
    const { result } = renderHook(() => useAgentStream());
    act(() => result.current.send("mai 3h được không em"));
    act(() => socket.fire("turn_started"));
    expect(result.current.phase).toBe("thinking");

    // Server không còn phát `token`: hook không nghe sự kiện đó nữa — lỡ có
    // (server cũ) cũng không sinh bong bóng dở.
    expect(socket.handlers.get("token")?.size ?? 0).toBe(0);
    act(() => socket.fire("token", { text: "Dạ" }));
    expect(result.current.messages.filter((m) => m.role === "bot")).toHaveLength(0);
    expect(result.current.phase).toBe("thinking");

    act(() => socket.fire("complete", { answer: "Dạ mai 3 giờ chiều còn trống ạ", proposal: null }));

    const bot = result.current.messages.filter((m) => m.role === "bot");
    expect(bot).toHaveLength(1);
    expect(bot[0].text).toBe("Dạ mai 3 giờ chiều còn trống ạ");
    expect(result.current.phase).toBe("idle");
  });

  it("M. complete sau tool: clear steps, phase idle", () => {
    const { result } = renderHook(() => useAgentStream());
    act(() => socket.fire("turn_started"));
    act(() => socket.fire("tool_started", { name: "parse_time", label: "Đang xem lịch…" }));
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
    act(() => socket.fire("turn_started"));
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
    act(() => socket.fire("complete", { answer: "câu trả lời hai" }));

    const bot = result.current.messages.filter((m) => m.role === "bot");
    expect(bot.map((m) => m.text)).toEqual(["câu trả lời một", "câu trả lời hai"]);
  });

  it("Q. error: failed=true, phase idle, clear steps, bot bubble xin lỗi, không kẹt", () => {
    const { result } = renderHook(() => useAgentStream());
    act(() => result.current.send("hỏi gì đó"));
    act(() => socket.fire("tool_started", { name: "parse_time", label: "Đang xem lịch…" }));
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

  it("Q2. sau error, lượt sau vẫn ra bubble MỚI bằng complete", () => {
    const { result } = renderHook(() => useAgentStream());
    act(() => result.current.send("hỏi gì đó"));
    act(() => socket.fire("error", { message: "Máy đang bận chút xíu ạ" }));
    act(() => socket.fire("turn_started"));
    act(() => socket.fire("complete", { answer: "Dạ chị cần gì ạ?" }));

    expect(result.current.messages.filter((m) => m.role === "bot").map((m) => m.text))
      .toEqual(["Máy đang bận chút xíu ạ", "Dạ chị cần gì ạ?"]);
  });

  it("R. unmount: off ĐÚNG từng exact handler, release đúng 1 lần", () => {
    const { unmount } = renderHook(() => useAgentStream());
    unmount();

    // Không gỡ thì mỗi lần vào lại màn hình chat sẽ có thêm một bộ handler.
    const owned = ["connect", "disconnect", "turn_started", "tool_started",
      "tool_finished", "complete", "error"];
    for (const event of owned) expect(socket.handlers.get(event)?.size ?? 0).toBe(0);
    expect(released).toBe(1);
  });

  it("S. StrictMode mount→unmount→mount: complete chỉ xử lý MỘT lần", () => {
    const first = renderHook(() => useAgentStream());
    first.unmount();
    const second = renderHook(() => useAgentStream());

    act(() => socket.fire("complete", { answer: "Xin chào" }));

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
    const label = "Đang xem lịch trống…";
    act(() => socket.fire("tool_started", { name: "find_free_slots", label }));
    act(() => socket.fire("tool_started", { name: "find_free_slots", label }));

    expect(result.current.steps).toHaveLength(2);
    expect(result.current.steps.every((s) => !s.done)).toBe(true);

    act(() => socket.fire("tool_finished", { name: "find_free_slots", label, ok: true }));

    expect(result.current.steps).toHaveLength(2); // không xóa step
    const done = result.current.steps.filter((s) => s.done);
    const pending = result.current.steps.filter((s) => !s.done);
    expect(done).toHaveLength(1); // chỉ MỘT invocation được đánh dấu xong
    expect(pending).toHaveLength(1);
    expect(result.current.steps[1].done).toBe(true); // invocation GẦN NHẤT
    expect(result.current.steps.every((s) => !s.label.includes("find_free_slots"))).toBe(true);

    act(() => socket.fire("tool_finished", { name: "find_free_slots", label, ok: true }));
    expect(result.current.steps.every((s) => s.done)).toBe(true); // cả hai mới xong
  });

  /** TRÌNH DUYỆT THẬT chạy trong <StrictMode> (main.tsx) — StrictMode gọi updater
   * của setState HAI LẦN cùng một `prev` để lộ updater không thuần. Chuỗi sự
   * kiện của một lượt thật (không còn `token`) phải ra đúng MỘT bong bóng. */
  it("W. REGRESSION live sequence dưới StrictMode: câu trả lời phải hiện sau complete", () => {
    const wrapper = ({ children }: { children: ReactNode }) =>
      createElement(StrictMode, null, children);
    const { result } = renderHook(() => useAgentStream(), { wrapper });

    act(() => result.current.send("Xin chào, hôm nay tiệm còn giờ trống không?"));
    act(() => socket.fire("turn_started"));
    // Thinking xuất hiện ngay khi lượt bắt đầu.
    expect(result.current.phase).toBe("thinking");

    const label = "Đang xem lịch trống…";
    act(() => socket.fire("tool_started", { name: "find_free_slots", label }));
    // Trạng thái tool ĐANG CHẠY phải hiện.
    expect(result.current.phase).toBe("tool");
    expect(result.current.steps).toEqual([
      { name: "find_free_slots", label, done: false, ok: true },
    ]);

    act(() => socket.fire("tool_finished", { name: "find_free_slots", label, ok: true }));
    // Trạng thái tool ĐÃ XONG phải hiện (và giữ lại trên màn hình).
    expect(result.current.steps).toEqual([
      { name: "find_free_slots", label, done: true, ok: true },
    ]);

    act(() => socket.fire("complete", { answer: "Dạ chào cô, hôm nay tiệm còn trống buổi sáng ạ." }));

    // Câu của khách phải còn.
    expect(result.current.messages[0]).toMatchObject({ role: "user" });
    const bot = result.current.messages.filter((m) => m.role === "bot");
    expect(bot).toHaveLength(1);
    expect(bot[0].text).toBe("Dạ chào cô, hôm nay tiệm còn trống buổi sáng ạ.");
    expect(result.current.steps).toEqual([]);
    expect(result.current.phase).toBe("idle");
    expect(result.current.failed).toBe(false);
  });

  it("Y. complete kèm proposal: proposal được điền từ data (snake_case → camelCase)", () => {
    const { result } = renderHook(() => useAgentStream());
    act(() =>
      socket.fire("complete", {
        answer: "Dạ đặt lịch 3 giờ chiều mai giúp cô nhé ạ?",
        proposal: {
          id: "p1",
          kind: "book",
          expires_at: "2030-01-01T00:00:00+07:00",
          when_spoken: "Đặt lịch 3 giờ chiều mai",
        },
      }),
    );

    expect(result.current.proposal).toEqual({
      id: "p1",
      kind: "book",
      expiresAt: "2030-01-01T00:00:00+07:00",
      whenSpoken: "Đặt lịch 3 giờ chiều mai",
    });
  });

  it("Z. complete không proposal: proposal là null", () => {
    const { result } = renderHook(() => useAgentStream());
    act(() => socket.fire("complete", { answer: "Dạ vâng ạ" }));
    expect(result.current.proposal).toBeNull();
  });

  it("AA. complete mới THAY proposal cũ bằng giá trị mới (hoặc null)", () => {
    const { result } = renderHook(() => useAgentStream());
    act(() =>
      socket.fire("complete", {
        answer: "câu 1",
        proposal: {
          id: "p1",
          kind: "book",
          expires_at: "2030-01-01T00:00:00+07:00",
          when_spoken: "Đặt lịch 3 giờ chiều mai",
        },
      }),
    );
    expect(result.current.proposal?.id).toBe("p1");

    act(() => socket.fire("turn_started"));
    act(() => socket.fire("complete", { answer: "câu 2" }));
    expect(result.current.proposal).toBeNull();
  });

  it("BB. confirm('yes'): emit confirm_proposal đúng payload, thêm bong bóng 'Đồng ý', xoá proposal, phase thinking", () => {
    const { result } = renderHook(() => useAgentStream());
    act(() =>
      socket.fire("complete", {
        answer: "Dạ đặt lịch 3 giờ chiều mai giúp cô nhé ạ?",
        proposal: {
          id: "p1",
          kind: "book",
          expires_at: "2030-01-01T00:00:00+07:00",
          when_spoken: "Đặt lịch 3 giờ chiều mai",
        },
      }),
    );

    act(() => result.current.confirm("yes"));

    expect(socket.emitted).toEqual([
      {
        event: "confirm_proposal",
        data: { proposal_id: "p1", decision: "yes", message_id: expect.any(String) },
      },
    ]);
    expect(result.current.messages.at(-1)).toMatchObject({ role: "user", text: "Đồng ý" });
    expect(result.current.proposal).toBeNull();
    expect(result.current.phase).toBe("thinking");
  });

  it("CC. confirm('no'): bong bóng 'Không'", () => {
    const { result } = renderHook(() => useAgentStream());
    act(() =>
      socket.fire("complete", {
        answer: "Dạ huỷ lịch 3 giờ chiều mai giúp cô nhé ạ?",
        proposal: {
          id: "p2",
          kind: "cancel",
          expires_at: "2030-01-01T00:00:00+07:00",
          when_spoken: "Huỷ lịch 3 giờ chiều mai",
        },
      }),
    );

    act(() => result.current.confirm("no"));

    expect(result.current.messages.at(-1)).toMatchObject({ role: "user", text: "Không" });
    expect(socket.emitted[0]).toMatchObject({ data: { decision: "no" } });
  });

  it("DD. send(): câu gõ mới xoá proposal đang treo", () => {
    const { result } = renderHook(() => useAgentStream());
    act(() =>
      socket.fire("complete", {
        answer: "Dạ đặt lịch 3 giờ chiều mai giúp cô nhé ạ?",
        proposal: {
          id: "p1",
          kind: "book",
          expires_at: "2030-01-01T00:00:00+07:00",
          when_spoken: "Đặt lịch 3 giờ chiều mai",
        },
      }),
    );
    act(() => result.current.send("thôi để con nhắn lại"));
    expect(result.current.proposal).toBeNull();
  });

  it("EE. confirm() lúc MẤT MẠNG: không emit, không bong bóng, proposal GIỮ NGUYÊN", () => {
    const { result } = renderHook(() => useAgentStream());
    act(() =>
      socket.fire("complete", {
        answer: "Dạ đặt lịch 3 giờ chiều mai giúp cô nhé ạ?",
        proposal: {
          id: "p1",
          kind: "book",
          expires_at: "2030-01-01T00:00:00+07:00",
          when_spoken: "Đặt lịch 3 giờ chiều mai",
        },
      }),
    );

    socket.connected = false;
    act(() => result.current.confirm("yes"));

    expect(socket.emitted).toEqual([]);
    expect(result.current.messages.some((m) => m.text === "Đồng ý")).toBe(false);
    expect(result.current.proposal?.id).toBe("p1");
    // Khách có mạng lại rồi bấm lại thì vẫn đi đúng đường bình thường.
    socket.connected = true;
    act(() => result.current.confirm("yes"));
    expect(socket.emitted).toEqual([
      {
        event: "confirm_proposal",
        data: { proposal_id: "p1", decision: "yes", message_id: expect.any(String) },
      },
    ]);
    expect(result.current.proposal).toBeNull();
  });
});
