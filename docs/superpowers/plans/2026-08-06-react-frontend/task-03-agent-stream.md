# Task 3 · Hook streaming và ánh xạ tên tool

> Thuộc plan [Frontend React](README.md). **Đọc [Ràng buộc toàn cục](README.md#ràng-buộc-toàn-cục) trước khi bắt đầu** — chúng áp cho mọi task, kể cả khi không nhắc lại ở đây.

**Files:**
- Create: `frontend/src/lib/viDate.ts`, `frontend/src/lib/viDate.test.ts`, `frontend/src/hooks/useShopStatus.test.ts`, `frontend/src/lib/toolLabels.ts`, `frontend/src/lib/toolLabels.test.ts`, `frontend/src/lib/socket.ts`, `frontend/src/hooks/useAgentStream.ts`, `frontend/src/hooks/useAgentStream.test.ts`, `frontend/src/hooks/useShopStatus.ts`

**Interfaces:**
- Consumes: sự kiện Socket.IO của Plan 2 task 10 — `turn_started`, `tool_started`, `tool_finished`, `token`, `complete`, `error`, `shop_status_changed`, `appointment_created`
- Produces:
  - `formatViDateTime(iso) -> "Thứ Năm, 7/8 — 3:00 chiều"`, `formatViTime(iso) -> "3:00 chiều"`
  - `toolLabel(name) -> { running: string; done: string }`
  - `acquireSocket(token) -> Socket` / `releaseSocket()` — một kết nối dùng chung, đếm tham chiếu
  - `useAgentStream() -> { messages, steps, phase, connected, offline, failed, send }` — tin nhắn gõ lúc mất mạng được xếp hàng và tự gửi lại khi nối lại
  - `useShopStatus() -> { status, loading, apply }`

- [ ] **Step 1: Viết test định dạng ngày giờ (sẽ fail)**

Tạo `frontend/src/lib/viDate.test.ts`:

```ts
import { describe, expect, it } from "vitest";
import { formatViDateTime, formatViTime } from "./viDate";

/* Giờ Việt Nam là UTC+7 và không có DST, nên tính ngược từ UTC là an toàn. */

describe("formatViDateTime", () => {
  it("3 giờ chiều thứ Sáu 7/8/2026", () => {
    expect(formatViDateTime("2026-08-07T08:00:00Z")).toBe("Thứ Sáu, 7/8 — 3:00 chiều");
  });

  it("buổi sáng gọi là sáng", () => {
    expect(formatViDateTime("2026-08-07T02:30:00Z")).toBe("Thứ Sáu, 7/8 — 9:30 sáng");
  });

  it("sau 6 giờ chiều gọi là tối", () => {
    expect(formatViDateTime("2026-08-07T12:00:00Z")).toBe("Thứ Sáu, 7/8 — 7:00 tối");
  });

  it("12 giờ trưa KHÔNG được thành 0 giờ", () => {
    expect(formatViDateTime("2026-08-07T05:00:00Z")).toContain("12:00 chiều");
  });

  it("Chủ Nhật không gọi là Thứ Tám", () => {
    expect(formatViDateTime("2026-08-09T08:00:00Z")).toContain("Chủ Nhật");
  });

  it("formatViTime chỉ trả phần giờ", () => {
    expect(formatViTime("2026-08-07T08:00:00Z")).toBe("3:00 chiều");
  });
});
```

- [ ] **Step 2: Chạy test để xác nhận fail**

Run: `cd frontend && npm test`
Expected: FAIL — không tìm thấy `./viDate`

- [ ] **Step 3: Viết `frontend/src/lib/viDate.ts`**

```ts
const WEEKDAYS = ["Chủ Nhật", "Thứ Hai", "Thứ Ba", "Thứ Tư", "Thứ Năm", "Thứ Sáu", "Thứ Bảy"];

/**
 * Ép về giờ Việt Nam thay vì dùng giờ máy: khách có thể đang ở nước ngoài gọi
 * về đặt lịch cho hôm sau, mà lịch thì luôn theo giờ tiệm.
 */
function vnParts(iso: string) {
  const date = new Date(iso);
  const parts = new Intl.DateTimeFormat("en-US", {
    timeZone: "Asia/Ho_Chi_Minh",
    weekday: "short",
    day: "numeric",
    month: "numeric",
    hour: "numeric",
    minute: "2-digit",
    hour12: false,
  }).formatToParts(date);
  const get = (type: string) => parts.find((p) => p.type === type)?.value ?? "";
  return {
    weekday: ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"].indexOf(get("weekday")),
    day: Number(get("day")),
    month: Number(get("month")),
    hour: Number(get("hour")) % 24, // Intl trả "24" cho nửa đêm ở một số runtime
    minute: get("minute"),
  };
}

/** "3:00 chiều" — cách người Việt lớn tuổi thực sự nói giờ, không phải "15:00". */
export function formatViTime(iso: string): string {
  const { hour, minute } = vnParts(iso);
  let period: string;
  let display: number;
  if (hour < 12) {
    period = "sáng";
    display = hour === 0 ? 12 : hour;
  } else if (hour < 18) {
    period = "chiều";
    display = hour === 12 ? 12 : hour - 12;
  } else {
    period = "tối";
    display = hour - 12;
  }
  return `${display}:${minute} ${period}`;
}

export function formatViDateTime(iso: string): string {
  const { weekday, day, month } = vnParts(iso);
  return `${WEEKDAYS[weekday]}, ${day}/${month} — ${formatViTime(iso)}`;
}
```

- [ ] **Step 4: Viết test cho bảng ánh xạ (sẽ fail)**

Tạo `frontend/src/lib/toolLabels.test.ts`:

```ts
import { describe, expect, it } from "vitest";
import { TOOL_LABELS, toolLabel } from "./toolLabels";

describe("toolLabel", () => {
  it("dịch đủ sáu tool trong spec", () => {
    expect(Object.keys(TOOL_LABELS).sort()).toEqual([
      "cancel_appointment",
      "find_free_slots",
      "get_shop_status",
      "list_my_appointments",
      "parse_time",
      "propose_appointment",
    ]);
  });

  it("parse_time nói theo việc khách quan tâm, không nói theo việc máy làm", () => {
    // "Đang phân tích thời gian" là câu vô nghĩa với một cụ 70 tuổi.
    expect(toolLabel("parse_time").running).toBe("Đang xem lịch…");
    expect(toolLabel("parse_time").running).not.toMatch(/phân tích|xử lý dữ liệu/i);
  });

  it("dịch find_free_slots sang câu người thường đọc được", () => {
    expect(toolLabel("find_free_slots")).toEqual({
      running: "Đang xem lịch trống…",
      done: "Đã xem lịch trống",
    });
  });

  it("tool lạ thì hiện câu chung, KHÔNG BAO GIỜ hiện tên thô", () => {
    const label = toolLabel("some_new_internal_tool_v2");
    expect(label.running).toBe("Đang xử lý…");
    expect(label.running + label.done).not.toContain("some_new_internal_tool_v2");
    expect(label.running + label.done).not.toMatch(/_/);
  });

  it("không câu nào lọt dấu gạch dưới ra ngoài", () => {
    for (const label of Object.values(TOOL_LABELS)) {
      expect(label.running).not.toMatch(/_/);
      expect(label.done).not.toMatch(/_/);
    }
  });
});
```

- [ ] **Step 5: Chạy test để xác nhận fail**

Run: `cd frontend && npm test`
Expected: FAIL — không tìm thấy `./toolLabels`

- [ ] **Step 6: Viết `frontend/src/lib/toolLabels.ts`**

```ts
export interface ToolLabel {
  running: string;
  done: string;
}

/**
 * Bảng ánh xạ nằm ở FRONTEND, không ở backend: đổi câu chữ cho dễ hiểu hơn là
 * việc sẽ làm nhiều lần sau khi tiệm dùng thật, và không đáng phải deploy lại API.
 */
export const TOOL_LABELS: Record<string, ToolLabel> = {
  get_shop_status: {
    running: "Đang xem chủ tiệm có rảnh không…",
    done: "Đã xem trạng thái tiệm",
  },
  // Khách không cần biết máy đang "phân tích thời gian" — họ chỉ cần biết
  // máy đang làm việc với cái lịch.
  parse_time: { running: "Đang xem lịch…", done: "Đã xem lịch" },
  find_free_slots: { running: "Đang xem lịch trống…", done: "Đã xem lịch trống" },
  // Agent không ghi lịch trực tiếp — nó giữ chỗ rồi hỏi khách xác nhận.
  propose_appointment: { running: "Đang giữ chỗ cho cô…", done: "Đã giữ chỗ" },
  list_my_appointments: { running: "Đang xem lịch của cô…", done: "Đã xem lịch của cô" },
  cancel_appointment: { running: "Đang hủy lịch…", done: "Đã hủy lịch" },
};

const GENERIC: ToolLabel = { running: "Đang xử lý…", done: "Đã xong" };

/** Tool lạ (backend thêm tool mới trước khi frontend kịp cập nhật) rơi vào câu
 *  chung. Hiện `find_free_slots` cho một cụ 70 tuổi là hỏng cả màn hình. */
export function toolLabel(name: string): ToolLabel {
  return TOOL_LABELS[name] ?? GENERIC;
}
```

- [ ] **Step 7: Viết `frontend/src/lib/socket.ts`**

```ts
import { io } from "socket.io-client";
import type { Socket } from "socket.io-client";

const BASE = import.meta.env.VITE_API_BASE ?? "";

let shared: Socket | null = null;
let refCount = 0;

/**
 * MỘT kết nối duy nhất cho cả app, đếm tham chiếu.
 *
 * Vì sao không để mỗi hook tự `io(BASE)`: socket.io-client gộp theo URL, nên
 * hai lần gọi trả về CÙNG một socket. Hook nào unmount trước sẽ `disconnect()`
 * cái socket mà hook kia vẫn đang dùng — màn hình chat và thẻ trạng thái tiệm
 * chết ngẫu nhiên tùy thứ tự unmount, cực khó lần ra.
 *
 * Đổi lại, mỗi hook PHẢI tự `socket.off(event, handler)` handler của mình khi
 * dọn dẹp, nếu không handler chồng lên nhau sau vài lần chuyển màn hình.
 */
export function acquireSocket(token: string): Socket {
  if (!shared) {
    shared = io(BASE, {
      auth: { token },
      transports: ["websocket", "polling"],
      reconnection: true,
      reconnectionDelay: 1000,
      reconnectionDelayMax: 5000,
    });
  }
  refCount += 1;
  return shared;
}

export function releaseSocket(): void {
  refCount -= 1;
  if (refCount <= 0) {
    shared?.disconnect();
    shared = null;
    refCount = 0;
  }
}

export type { Socket };
```

- [ ] **Step 8: Viết test cho hook (sẽ fail)**

Tạo `frontend/src/hooks/useAgentStream.test.ts`:

```ts
import { act, renderHook } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { useAgentStream } from "./useAgentStream";

/** Socket giả: giữ handler trong Map để test tự bắn sự kiện. */
class FakeSocket {
  handlers = new Map<string, (data: unknown) => void>();
  emitted: Array<{ event: string; data: unknown }> = [];
  // `useAgentStream.send` đọc cờ này để biết nên gửi luôn hay xếp hàng đợi.
  connected = true;

  on(event: string, handler: (data: unknown) => void) {
    this.handlers.set(event, handler);
  }
  off(event: string) {
    this.handlers.delete(event);
  }
  emit(event: string, data: unknown) {
    this.emitted.push({ event, data });
  }
  fire(event: string, data?: unknown) {
    this.handlers.get(event)?.(data);
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

vi.mock("../auth/AuthContext", () => ({
  useAuth: () => ({ token: "tok", role: "user", fullName: "Cô Lan" }),
}));

beforeEach(() => {
  socket = new FakeSocket();
  released = 0;
});

describe("useAgentStream", () => {
  it("gửi tin nhắn thì thêm ngay bong bóng của khách", () => {
    const { result } = renderHook(() => useAgentStream());
    act(() => result.current.send("mai 3h được không con"));

    expect(result.current.messages.at(-1)).toMatchObject({
      role: "user",
      text: "mai 3h được không con",
    });
    expect(socket.emitted[0]).toEqual({
      event: "send_message",
      data: { message: "mai 3h được không con" },
    });
  });

  it("mất mạng thì GIỮ câu đã gõ và đánh dấu đang chờ gửi", () => {
    const { result } = renderHook(() => useAgentStream());
    socket.connected = false;
    act(() => result.current.send("hủy giùm cô lịch mai"));

    expect(result.current.messages.at(-1)).toMatchObject({
      role: "user",
      text: "hủy giùm cô lịch mai",
      pending: true,
    });
    // Chưa gửi đi đâu cả — nhưng cũng KHÔNG mất.
    expect(socket.emitted).toEqual([]);
  });

  it("có mạng lại thì tự gửi nốt hàng đợi theo đúng thứ tự gõ", () => {
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
  });

  it("turn_started chuyển sang trạng thái đang đọc", () => {
    const { result } = renderHook(() => useAgentStream());
    act(() => socket.fire("turn_started"));
    expect(result.current.phase).toBe("thinking");
  });

  it("tool_started hiện câu tiếng Việt, không hiện tên tool", () => {
    const { result } = renderHook(() => useAgentStream());
    act(() => socket.fire("tool_started", { name: "find_free_slots" }));

    expect(result.current.phase).toBe("tool");
    expect(result.current.steps).toEqual([
      { name: "find_free_slots", label: "Đang xem lịch trống…", done: false, ok: true },
    ]);
  });

  it("tool_finished GIỮ LẠI dòng đã xong chứ không xóa", () => {
    const { result } = renderHook(() => useAgentStream());
    act(() => socket.fire("tool_started", { name: "find_free_slots" }));
    act(() => socket.fire("tool_finished", { name: "find_free_slots", ok: true }));

    expect(result.current.steps).toEqual([
      { name: "find_free_slots", label: "Đã xem lịch trống", done: true, ok: true },
    ]);
  });

  it("token nối dần vào một bong bóng duy nhất", () => {
    const { result } = renderHook(() => useAgentStream());
    act(() => socket.fire("turn_started"));
    act(() => socket.fire("token", { text: "Dạ " }));
    act(() => socket.fire("token", { text: "được ạ" }));

    const bot = result.current.messages.filter((m) => m.role === "bot");
    expect(bot).toHaveLength(1);
    expect(bot[0].text).toBe("Dạ được ạ");
    expect(result.current.phase).toBe("answering");
  });

  it("complete dọn tiến trình tool và về trạng thái nghỉ", () => {
    const { result } = renderHook(() => useAgentStream());
    act(() => socket.fire("tool_started", { name: "propose_appointment" }));
    act(() => socket.fire("token", { text: "Xong ạ" }));
    act(() => socket.fire("complete", { answer: "Xong ạ" }));

    expect(result.current.phase).toBe("idle");
    expect(result.current.steps).toEqual([]);
    expect(result.current.messages.at(-1)).toMatchObject({ role: "bot", text: "Xong ạ" });
  });

  it("complete không có token nào trước đó vẫn hiện câu trả lời", () => {
    const { result } = renderHook(() => useAgentStream());
    act(() => socket.fire("turn_started"));
    act(() => socket.fire("complete", { answer: "Dạ chủ tiệm đang rảnh ạ" }));

    expect(result.current.messages.at(-1)).toMatchObject({
      role: "bot",
      text: "Dạ chủ tiệm đang rảnh ạ",
    });
  });

  it("MẤT MẠNG GIỮA CHỪNG KHÔNG ĐƯỢC XÓA CHỮ ĐÃ HIỆN", () => {
    const { result } = renderHook(() => useAgentStream());
    act(() => socket.fire("token", { text: "Dạ mai 3 giờ" }));
    act(() => socket.fire("disconnect"));

    expect(result.current.offline).toBe(true);
    expect(result.current.messages.at(-1)?.text).toBe("Dạ mai 3 giờ");
  });

  it("kết nối lại thì tắt cờ mất mạng", () => {
    const { result } = renderHook(() => useAgentStream());
    act(() => socket.fire("disconnect"));
    act(() => socket.fire("connect"));
    expect(result.current.offline).toBe(false);
    expect(result.current.connected).toBe(true);
  });

  it("lỗi phía server hiện câu xin lỗi và dừng tiến trình", () => {
    const { result } = renderHook(() => useAgentStream());
    act(() => socket.fire("turn_started"));
    act(() => socket.fire("error", { message: "Máy đang bận chút xíu ạ" }));

    expect(result.current.phase).toBe("idle");
    expect(result.current.messages.at(-1)).toMatchObject({
      role: "bot",
      text: "Máy đang bận chút xíu ạ",
    });
  });

  it("tin nhắn rỗng thì không gửi gì", () => {
    const { result } = renderHook(() => useAgentStream());
    act(() => result.current.send("   "));
    expect(socket.emitted).toEqual([]);
  });

  it("gỡ handler và trả socket khi rời màn hình", () => {
    const { unmount } = renderHook(() => useAgentStream());
    unmount();

    // Không gỡ thì mỗi lần vào lại màn hình chat sẽ có thêm một bộ handler nữa,
    // và một token sẽ được nối vào bong bóng nhiều lần.
    expect(socket.handlers.size).toBe(0);
    expect(released).toBe(1);
  });
});
```

- [ ] **Step 9: Chạy test để xác nhận fail**

Run: `cd frontend && npm test`
Expected: FAIL — không tìm thấy `./useAgentStream`

- [ ] **Step 10: Viết `frontend/src/hooks/useAgentStream.ts`**

```ts
import { useCallback, useEffect, useRef, useState } from "react";
import { acquireSocket, releaseSocket } from "../lib/socket";
import type { Socket } from "../lib/socket";
import { toolLabel } from "../lib/toolLabels";
import { useAuth } from "../auth/AuthContext";

export type Phase = "idle" | "thinking" | "tool" | "answering";

export interface ChatMessage {
  id: string;
  role: "user" | "bot";
  text: string;
  /** Câu của khách gõ lúc mất mạng, còn nằm trong hàng đợi chưa gửi được. */
  pending?: boolean;
}

export interface ToolStep {
  name: string;
  label: string;
  done: boolean;
  ok: boolean;
}

let counter = 0;
const nextId = () => `m${++counter}`;

/**
 * Một máy trạng thái duy nhất cho cả khung chat. Màn hình chat chỉ việc vẽ
 * `messages` + `steps` + `phase`, không tự ghép sự kiện — đó là chỗ dễ sinh ra
 * hai bong bóng cho cùng một câu trả lời.
 */
export function useAgentStream() {
  const { token } = useAuth();
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [steps, setSteps] = useState<ToolStep[]>([]);
  const [phase, setPhase] = useState<Phase>("idle");
  const [connected, setConnected] = useState(false);
  const [offline, setOffline] = useState(false);
  // Lượt vừa rồi AI trả lời không được. Màn hình dựa vào cờ này để hiện nút
  // gọi điện cho tiệm — khách vẫn đặt được lịch bằng cách nói chuyện với người.
  const [failed, setFailed] = useState(false);
  const socketRef = useRef<Socket | null>(null);
  // id của bong bóng bot đang được stream. Dùng ref chứ không dùng state vì
  // handler socket đóng gói giá trị cũ nếu đọc từ state.
  const streamingId = useRef<string | null>(null);
  // Hàng đợi câu gõ lúc mất mạng. Ref chứ không state: `onConnect` đọc nó và
  // sẽ thấy mảng cũ nếu để trong state.
  const queue = useRef<Array<{ id: string; message: string }>>([]);

  useEffect(() => {
    if (!token) return;
    const socket = acquireSocket(token);
    socketRef.current = socket;

    const onConnect = () => {
      setConnected(true);
      setOffline(false);
      // Nối lại mạng thì gửi nốt những câu đang chờ, theo đúng thứ tự gõ.
      // Người lớn tuổi gõ xong là coi như đã nhắn — bắt họ gõ lại là mất câu.
      const waiting = queue.current;
      queue.current = [];
      if (waiting.length === 0) return;
      waiting.forEach(({ message }) => socket.emit("send_message", { message }));
      setMessages((prev) => prev.map((m) => (m.pending ? { ...m, pending: false } : m)));
      setPhase("thinking");
    };

    const onDisconnect = () => {
      setConnected(false);
      // KHÔNG xóa messages. Người lớn tuổi thấy chữ biến mất sẽ tưởng mình
      // vừa làm hỏng máy. Chỉ thêm một dòng báo bên dưới.
      setOffline(true);
    };

    const onTurnStarted = () => {
      streamingId.current = null;
      setSteps([]);
      setFailed(false);
      setPhase("thinking");
    };

    const onToolStarted = (data: { name: string }) => {
      setPhase("tool");
      setSteps((prev) => [
        ...prev,
        { name: data.name, label: toolLabel(data.name).running, done: false, ok: true },
      ]);
    };

    const onToolFinished = (data: { name: string; ok: boolean }) => {
      // Thu thành dòng có dấu tích và GIỮ LẠI trên màn hình: khách phải thấy
      // máy đã làm gì, nếu không họ nghi câu trả lời là bịa.
      setSteps((prev) =>
        prev.map((step) =>
          step.name === data.name && !step.done
            ? { ...step, done: true, ok: data.ok, label: toolLabel(data.name).done }
            : step,
        ),
      );
    };

    const onToken = (data: { text: string }) => {
      setPhase("answering");
      setMessages((prev) => {
        if (streamingId.current) {
          return prev.map((m) =>
            m.id === streamingId.current ? { ...m, text: m.text + data.text } : m,
          );
        }
        const id = nextId();
        streamingId.current = id;
        return [...prev, { id, role: "bot", text: data.text }];
      });
    };

    const onComplete = (data: { answer?: string }) => {
      const answer = data?.answer ?? "";
      setMessages((prev) => {
        if (streamingId.current) {
          // Câu chốt của backend là nguồn đúng nhất: nếu vài token rơi mất
          // giữa đường thì đây là lúc chữa lại.
          return prev.map((m) =>
            m.id === streamingId.current && answer ? { ...m, text: answer } : m,
          );
        }
        return answer ? [...prev, { id: nextId(), role: "bot", text: answer }] : prev;
      });
      streamingId.current = null;
      setSteps([]);
      setPhase("idle");
    };

    const onError = (data: { message?: string }) => {
      streamingId.current = null;
      setSteps([]);
      setPhase("idle");
      setFailed(true);
      setMessages((prev) => [
        ...prev,
        {
          id: nextId(),
          role: "bot",
          text: data?.message ?? "Máy đang bận chút xíu, cô chú nhắn lại giúp con nhé.",
        },
      ]);
    };

    const handlers: Array<[string, (data: never) => void]> = [
      ["connect", onConnect],
      ["disconnect", onDisconnect],
      ["turn_started", onTurnStarted],
      ["tool_started", onToolStarted as (data: never) => void],
      ["tool_finished", onToolFinished as (data: never) => void],
      ["token", onToken as (data: never) => void],
      ["complete", onComplete as (data: never) => void],
      ["error", onError as (data: never) => void],
    ];
    handlers.forEach(([event, handler]) => socket.on(event, handler));

    return () => {
      // Gỡ đúng handler của mình rồi mới trả socket: socket dùng chung với
      // `useShopStatus` và `useAdminFeed`.
      handlers.forEach(([event, handler]) => socket.off(event, handler));
      socketRef.current = null;
      releaseSocket();
    };
  }, [token]);

  const send = useCallback((text: string) => {
    const message = text.trim();
    if (!message) return;
    const id = nextId();
    const socket = socketRef.current;
    const live = socket?.connected ?? false;

    // Bong bóng của khách hiện NGAY dù có mạng hay không. Chữ vừa gõ mà biến
    // mất là cảm giác "máy nuốt mất câu của mình".
    setMessages((prev) => [...prev, { id, role: "user", text: message, pending: !live }]);

    if (!live) {
      queue.current.push({ id, message });
      return;
    }
    setFailed(false);
    setPhase("thinking");
    socket!.emit("send_message", { message });
  }, []);

  return { messages, steps, phase, connected, offline, failed, send };
}
```

- [ ] **Step 11: Viết `frontend/src/hooks/useShopStatus.ts`**

```ts
import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "../lib/api";
import type { ShopStatus } from "../lib/api";
import { acquireSocket, releaseSocket } from "../lib/socket";
import { useAuth } from "../auth/AuthContext";

/**
 * Thẻ trạng thái tiệm. Nạp một lần bằng REST rồi để `shop_status_changed`
 * (broadcast tới MỌI user) cập nhật — nhờ vậy phần lớn khách biết tiệm bận hay
 * rảnh mà không cần hỏi AI.
 *
 * `minutes_left` KHÔNG bao giờ được hiển thị. Màn hình chỉ nói giờ xong cụ thể
 * (`busy_until`). Trường này chỉ dùng để hẹn giờ lật thẻ, vì nó là một khoảng
 * nên không dính lệch đồng hồ máy khách.
 *
 * Dùng chung socket với `useAgentStream`: màn hình chủ tiệm cũng dùng hook này
 * mà không có khung chat, còn màn hình chat thì dùng cả hai.
 */
export function useShopStatus() {
  const { token } = useAuth();
  const [status, setStatus] = useState<ShopStatus | null>(null);
  const [loading, setLoading] = useState(true);

  const receive = useCallback((next: ShopStatus) => setStatus(next), []);

  useEffect(() => {
    if (!token) return;
    let cancelled = false;

    api
      .get<ShopStatus>("/api/v1/shop/status")
      .then((s) => !cancelled && receive(s))
      .catch(() => undefined)
      .finally(() => !cancelled && setLoading(false));

    const socket = acquireSocket(token);
    socket.on("shop_status_changed", receive);

    return () => {
      cancelled = true;
      socket.off("shop_status_changed", receive);
      releaseSocket();
    };
  }, [token, receive]);

  // Backend KHÔNG có timer nào phát sự kiện lúc `busy_until` hết hạn — nó chỉ
  // tính lại khi có ai đó gọi `get_status()`. Thiếu chỗ này thì hết giờ bận,
  // thẻ vẫn hiện "đang bận" cho tới khi khách tải lại trang.
  //
  // MỘT `setTimeout` duy nhất, không phải đồng hồ đếm từng phút: màn hình chỉ
  // hiện giờ xong, nên giữa hai mốc chẳng có gì để vẽ lại.
  //
  // Hẹn theo `minutes_left` (một KHOẢNG) chứ không theo `busy_until` trừ đi
  // `Date.now()` (hai MỐC): điện thoại của người lớn tuổi lệch giờ là chuyện
  // thường, mà khoảng thì miễn nhiễm với lệch đồng hồ.
  useEffect(() => {
    if (!status?.is_busy || status.minutes_left == null) return;

    const id = setTimeout(() => {
      // Đổi thẻ ngay cho khách thấy, rồi hỏi lại server một lần để xác nhận —
      // phòng khi có sự kiện rơi mất lúc mạng chập chờn.
      setStatus((prev) => (prev ? { ...prev, is_busy: false, busy_until: null } : prev));
      api.get<ShopStatus>("/api/v1/shop/status").then(receive).catch(() => undefined);
    }, status.minutes_left * 60_000);

    // Dọn khi trạng thái đổi — chủ tiệm bấm bận thêm lần nữa thì hẹn giờ cũ
    // phải bị hủy, nếu không nó lật thẻ sang rảnh giữa chừng.
    return () => clearTimeout(id);
  }, [status?.is_busy, status?.busy_until, status?.minutes_left, receive]);

  // `apply` cho chỗ nào vừa gọi API đổi trạng thái và đã cầm sẵn kết quả thật
  // từ server (`POST /shop/busy` trả về `ShopStatusResponse`). Không phải đoán
  // trước — chỉ là dùng câu trả lời có sẵn thay vì ngồi chờ broadcast vọng về.
  // Đi qua `receive` để đồng hồ đếm ngược được gieo lại từ đầu.
  return { status, loading, apply: receive };
}
```

**Vì sao cần `apply` chứ không chỉ nghe broadcast.** Việc phát `shop_status_changed`
nằm ở `SocketNotifier` của **Plan 3**. Nếu Plan 3 chưa làm, hoặc bot Telegram đang tắt,
broadcast không bao giờ tới — chủ tiệm bấm "Tôi đang bận" mà màn hình đứng im, tưởng
máy hỏng và bấm lại mấy lần. Có `apply`, máy vừa bấm cập nhật ngay bằng phản hồi HTTP;
broadcast vẫn giữ nguyên vai trò đồng bộ sang các máy khác.

- [ ] **Step 12: Viết test cho đồng hồ đếm ngược**

Tạo `frontend/src/hooks/useShopStatus.test.ts`:

```ts
import { act, renderHook, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { useShopStatus } from "./useShopStatus";

const socket = { on: vi.fn(), off: vi.fn() };

vi.mock("../lib/socket", () => ({
  acquireSocket: () => socket,
  releaseSocket: () => undefined,
}));
vi.mock("../auth/AuthContext", () => ({ useAuth: () => ({ token: "tok" }) }));

function mockStatus(body: unknown) {
  vi.stubGlobal(
    "fetch",
    vi.fn().mockResolvedValue({ ok: true, status: 200, json: async () => body }),
  );
}

beforeEach(() => vi.useFakeTimers({ shouldAdvanceTime: true }));
afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

describe("useShopStatus", () => {
  it("chưa tới giờ thì giữ nguyên đang bận", async () => {
    mockStatus({ is_busy: true, busy_until: "2026-08-07T08:30:00Z", minutes_left: 30 });
    const { result } = renderHook(() => useShopStatus());
    await waitFor(() => expect(result.current.status?.is_busy).toBe(true));

    await act(async () => {
      vi.advanceTimersByTime(29 * 60_000);
    });
    expect(result.current.status?.is_busy).toBe(true);
    expect(result.current.status?.busy_until).toBe("2026-08-07T08:30:00Z");
  });

  it("TỚI GIỜ THÌ TỰ ĐỔI SANG RẢNH — backend không có timer nào báo", async () => {
    mockStatus({ is_busy: true, busy_until: "2026-08-07T08:30:00Z", minutes_left: 30 });
    const { result } = renderHook(() => useShopStatus());
    await waitFor(() => expect(result.current.status?.is_busy).toBe(true));

    await act(async () => {
      vi.advanceTimersByTime(31 * 60_000);
    });
    expect(result.current.status?.is_busy).toBe(false);
  });

  it("đồng hồ máy sai giờ không ảnh hưởng — hẹn theo khoảng, không theo mốc", async () => {
    vi.setSystemTime(new Date("2019-01-01T00:00:00Z"));  // máy lệch 7 năm
    mockStatus({ is_busy: true, busy_until: "2026-08-07T08:30:00Z", minutes_left: 30 });
    const { result } = renderHook(() => useShopStatus());
    await waitFor(() => expect(result.current.status?.is_busy).toBe(true));

    await act(async () => {
      vi.advanceTimersByTime(29 * 60_000);
    });
    expect(result.current.status?.is_busy).toBe(true);    // chưa tới giờ

    await act(async () => {
      vi.advanceTimersByTime(2 * 60_000);
    });
    expect(result.current.status?.is_busy).toBe(false);   // đúng 30 phút sau
  });

  it("bấm bận thêm lần nữa thì HỦY hẹn giờ cũ", async () => {
    mockStatus({ is_busy: true, busy_until: "2026-08-07T08:30:00Z", minutes_left: 30 });
    const { result } = renderHook(() => useShopStatus());
    await waitFor(() => expect(result.current.status?.is_busy).toBe(true));

    await act(async () => {
      vi.advanceTimersByTime(25 * 60_000);
    });

    // Chủ tiệm bấm bận thêm 60 phút nữa.
    const onChanged = socket.on.mock.calls.find(([e]) => e === "shop_status_changed")[1];
    await act(async () => {
      onChanged({ is_busy: true, busy_until: "2026-08-07T09:30:00Z", minutes_left: 60 });
      vi.advanceTimersByTime(10 * 60_000);
    });

    // Hẹn giờ cũ đáng lẽ đã nổ ở phút thứ 30. Không hủy thì thẻ lật sang rảnh
    // giữa lúc chủ tiệm vẫn đang bận.
    expect(result.current.status?.is_busy).toBe(true);
    expect(result.current.status?.busy_until).toBe("2026-08-07T09:30:00Z");
  });
});
```

- [ ] **Step 13: Chạy test để xác nhận pass**

Run: `cd frontend && npm test`
Expected: PASS (44 passed) — quan trọng nhất là `HẾT GIỜ THÌ TỰ ĐỔI SANG RẢNH`, `MẤT MẠNG GIỮA CHỪNG KHÔNG ĐƯỢC XÓA CHỮ ĐÃ HIỆN`, `có mạng lại thì tự gửi nốt hàng đợi` và `tool lạ thì hiện câu chung`

- [ ] **Step 14: Commit**

```bash
git add frontend/src/lib frontend/src/hooks
git commit -m "feat(fe): agent stream state machine with Vietnamese tool labels"
```
