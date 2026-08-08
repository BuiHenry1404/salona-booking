# Task 4 · Màn hình chat

> Thuộc plan [Frontend React](README.md). **Đọc [Ràng buộc toàn cục](README.md#ràng-buộc-toàn-cục) trước khi bắt đầu** — chúng áp cho mọi task, kể cả khi không nhắc lại ở đây.

**Files:**
- Create: `frontend/src/components/ShopStatusCard.tsx`, `frontend/src/components/ShopStatusCard.css`, `frontend/src/components/ToolProgress.tsx`, `frontend/src/components/ToolProgress.css`, `frontend/src/components/BottomNav.tsx`, `frontend/src/components/BottomNav.css`, `frontend/src/hooks/useSpeechInput.ts`, `frontend/src/screens/ChatScreen.tsx`, `frontend/src/screens/ChatScreen.css`, `frontend/src/screens/ChatScreen.test.tsx`
- Modify: `frontend/src/App.tsx`

**Interfaces:**
- Consumes: `useAgentStream`, `useShopStatus` (task 3), `Button` (task 1)
- Produces: `<ChatScreen />` tại route `/`

Đối chiếu với [`ui-mockup-streaming.html`](../../specs/2026-08-06-booking-nail-toc/ui-mockup-streaming.html) — bốn trạng thái phải nhìn giống mockup đã duyệt.

- [ ] **Step 1: Viết test (sẽ fail)**

Tạo `frontend/src/screens/ChatScreen.test.tsx`:

```tsx
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ChatScreen } from "./ChatScreen";

const stream = {
  messages: [] as Array<{
    id: string;
    role: "user" | "bot";
    text: string;
    pending?: boolean;
  }>,
  steps: [] as Array<{ name: string; label: string; done: boolean; ok: boolean }>,
  phase: "idle" as string,
  connected: true,
  offline: false,
  failed: false,
  send: vi.fn(),
};
const shop = { status: { is_busy: false, busy_until: null, minutes_left: null }, loading: false };

vi.mock("../hooks/useAgentStream", () => ({ useAgentStream: () => stream }));
vi.mock("../hooks/useShopStatus", () => ({ useShopStatus: () => shop }));
vi.mock("../auth/AuthContext", () => ({
  useAuth: () => ({ role: "user", fullName: "Cô Lan", logout: vi.fn() }),
}));

function renderChat() {
  return render(
    <MemoryRouter>
      <ChatScreen />
    </MemoryRouter>,
  );
}

beforeEach(() => {
  vi.stubEnv("VITE_SHOP_PHONE", "0287654321");
  stream.messages = [];
  stream.steps = [];
  stream.phase = "idle";
  stream.offline = false;
  stream.failed = false;
  stream.send = vi.fn();
  shop.status = { is_busy: false, busy_until: null, minutes_left: null };
});
afterEach(() => vi.unstubAllEnvs());

describe("ChatScreen", () => {
  it("thẻ trạng thái nói RÕ BẰNG CHỮ là tiệm đang rảnh", () => {
    renderChat();
    expect(screen.getByText(/đang rảnh/i)).toBeInTheDocument();
  });

  it("đang bận thì hiện luôn còn bao nhiêu phút", () => {
    shop.status = { is_busy: true, busy_until: "2026-08-07T08:30:00Z", minutes_left: 30 };
    renderChat();
    expect(screen.getByText(/đang bận/i)).toBeInTheDocument();
    expect(screen.getByText(/30 phút/)).toBeInTheDocument();
  });

  it("gửi tin nhắn rồi xóa sạch ô nhập", async () => {
    renderChat();
    const box = screen.getByLabelText(/nhắn cho tiệm/i);
    await userEvent.type(box, "mai 3h làm tóc được không con");
    await userEvent.click(screen.getByRole("button", { name: /gửi/i }));

    expect(stream.send).toHaveBeenCalledWith("mai 3h làm tóc được không con");
    expect(box).toHaveValue("");
  });

  it("đang đọc thì hiện ba chấm kèm chữ cho người đọc màn hình", () => {
    stream.phase = "thinking";
    renderChat();
    expect(screen.getByRole("status")).toHaveTextContent(/đang đọc/i);
  });

  it("hiện câu tiếng Việt của tool, tuyệt đối không hiện tên tool", () => {
    stream.phase = "tool";
    stream.steps = [
      { name: "find_free_slots", label: "Đang xem lịch trống…", done: false, ok: true },
    ];
    renderChat();

    expect(screen.getByText("Đang xem lịch trống…")).toBeInTheDocument();
    expect(screen.queryByText(/find_free_slots/)).not.toBeInTheDocument();
  });

  it("tool đã xong vẫn còn trên màn hình", () => {
    stream.steps = [
      { name: "find_free_slots", label: "Đã xem lịch trống", done: true, ok: true },
    ];
    renderChat();
    expect(screen.getByText("Đã xem lịch trống")).toBeInTheDocument();
  });

  it("mất mạng thì giữ nguyên chữ đã hiện và báo thêm một dòng", () => {
    stream.offline = true;
    stream.messages = [{ id: "1", role: "bot", text: "Dạ mai 3 giờ" }];
    renderChat();

    expect(screen.getByText("Dạ mai 3 giờ")).toBeInTheDocument();
    expect(screen.getByText(/mất mạng, đang thử lại/i)).toBeInTheDocument();
  });

  it("câu chưa gửi được thì vẫn nằm đó kèm chữ 'Đang gửi lại…'", () => {
    stream.offline = true;
    stream.messages = [
      { id: "1", role: "user", text: "hủy giùm cô lịch mai", pending: true },
    ];
    renderChat();

    expect(screen.getByText("hủy giùm cô lịch mai")).toBeInTheDocument();
    expect(screen.getByText(/đang gửi lại/i)).toBeInTheDocument();
  });

  it("AI lỗi thì hiện nút gọi thẳng cho tiệm", () => {
    // Không có nút này, khách gặp lỗi là hết đường — mà tiệm thì luôn có
    // người thật nghe máy.
    stream.failed = true;
    stream.messages = [
      { id: "1", role: "bot", text: "Máy đang bận chút xíu, cô chú nhắn lại giúp con nhé." },
    ];
    renderChat();

    const call = screen.getByRole("link", { name: /gọi cho tiệm/i });
    expect(call).toHaveAttribute("href", "tel:0287654321");
  });

  it("lượt bình thường thì KHÔNG hiện nút gọi", () => {
    stream.messages = [{ id: "1", role: "bot", text: "Dạ mai 3 giờ được ạ" }];
    renderChat();
    expect(screen.queryByRole("link", { name: /gọi cho tiệm/i })).not.toBeInTheDocument();
  });

  it("KHÔNG có nút dừng — thêm nút là thêm thứ bấm nhầm", () => {
    stream.phase = "answering";
    renderChat();
    expect(screen.queryByRole("button", { name: /dừng/i })).not.toBeInTheDocument();
  });

  it("ô nhập rỗng thì nút gửi bị vô hiệu hóa", () => {
    renderChat();
    expect(screen.getByRole("button", { name: /gửi/i })).toBeDisabled();
  });

  it("có đường sang Lịch của tôi", () => {
    renderChat();
    expect(screen.getByRole("link", { name: /lịch của tôi/i })).toHaveAttribute(
      "href",
      "/lich-cua-toi",
    );
  });
});
```

- [ ] **Step 2: Chạy test để xác nhận fail**

Run: `cd frontend && npm test`
Expected: FAIL — không tìm thấy `./ChatScreen`

- [ ] **Step 3: Viết `frontend/src/components/ShopStatusCard.tsx`**

```tsx
import type { ShopStatus } from "../lib/api";
import "./ShopStatusCard.css";

/**
 * Luôn hiển thị trên đầu màn hình chat. Nhờ thẻ này phần lớn khách biết tiệm
 * bận hay rảnh mà không cần hỏi AI — hỏi AI là đường dự phòng, không phải
 * đường duy nhất.
 */
export function ShopStatusCard({ status, loading }: { status: ShopStatus | null; loading: boolean }) {
  if (loading || !status) {
    return <div className="shopcard shopcard--loading">Đang xem tiệm…</div>;
  }

  const busy = status.is_busy;
  return (
    <div className={`shopcard ${busy ? "shopcard--busy" : "shopcard--free"}`}>
      <span className="shopcard__dot" aria-hidden="true" />
      <div>
        {/* Chữ trước, màu sau. Chỉ đổi màu thì người mù màu nhẹ không phân biệt
            được, mà người lớn tuổi rất hay bị. */}
        <strong className="shopcard__title">
          {busy ? "Chủ tiệm đang bận" : "Chủ tiệm đang rảnh"}
        </strong>
        {busy && status.minutes_left != null && (
          <p className="shopcard__sub">Khoảng {status.minutes_left} phút nữa xong ạ</p>
        )}
      </div>
    </div>
  );
}
```

Tạo `frontend/src/components/ShopStatusCard.css`:

```css
.shopcard {
  display: flex;
  align-items: center;
  gap: var(--s2);
  padding: var(--s3);
  border-radius: var(--r);
  border: 2px solid var(--color-border);
  background: var(--color-surface);
  margin-bottom: var(--s3);
}
.shopcard__dot { width: 16px; height: 16px; border-radius: 50%; flex: none; }
.shopcard--free  { background: var(--color-accent-weak); border-color: var(--color-accent); }
.shopcard--free  .shopcard__dot { background: var(--color-accent); }
.shopcard--busy  { background: var(--color-warn-weak); border-color: #b45309; }
.shopcard--busy  .shopcard__dot { background: #b45309; }
.shopcard__title { font-size: 21px; }
.shopcard__sub { font-size: 18px; color: var(--color-fg-muted); }
.shopcard--loading { color: var(--color-fg-muted); }
```

- [ ] **Step 4: Viết `frontend/src/components/ToolProgress.tsx`**

```tsx
import type { ToolStep } from "../hooks/useAgentStream";
import "./ToolProgress.css";

export function ToolProgress({ steps }: { steps: ToolStep[] }) {
  if (steps.length === 0) return null;
  return (
    <ul className="tools" aria-live="polite">
      {steps.map((step, index) => (
        <li key={`${step.name}-${index}`} className={step.done ? "tool tool--done" : "tool"}>
          <span className="tool__mark" aria-hidden="true">
            {step.done ? (step.ok ? "✓" : "✕") : <span className="tool__spin" />}
          </span>
          {/* `step.label` đã là câu tiếng Việt do `toolLabel()` dịch.
              Không bao giờ render `step.name`. */}
          <span>{step.label}</span>
        </li>
      ))}
    </ul>
  );
}
```

Tạo `frontend/src/components/ToolProgress.css`:

```css
.tools { list-style: none; margin: var(--s2) 0; }
.tool {
  display: flex;
  align-items: center;
  gap: 10px;
  font-size: 18px;
  color: var(--color-fg-muted);
  padding: 6px 0;
}
.tool--done { color: var(--color-accent); }
.tool__mark { width: 22px; text-align: center; font-weight: 700; }
.tool__spin {
  display: inline-block;
  width: 16px; height: 16px;
  border: 3px solid var(--color-border);
  border-top-color: var(--color-primary);
  border-radius: 50%;
  animation: tool-spin 0.9s linear infinite;
}
@keyframes tool-spin { to { transform: rotate(360deg); } }

/* prefers-reduced-motion đã được xử lý toàn cục trong tokens.css: vòng xoay
   đứng yên nhưng dòng chữ vẫn nói rõ đang chạy hay đã xong. */
```

- [ ] **Step 5: Viết `frontend/src/hooks/useSpeechInput.ts`**

```ts
import { useCallback, useRef, useState } from "react";

type Recognition = { start: () => void; stop: () => void; [key: string]: unknown };

function getRecognitionCtor(): (new () => Recognition) | null {
  const w = window as unknown as Record<string, unknown>;
  return (w.SpeechRecognition ?? w.webkitSpeechRecognition ?? null) as
    | (new () => Recognition)
    | null;
}

/**
 * Gõ phím là rào cản lớn nhất với người lớn tuổi. Trình duyệt không hỗ trợ thì
 * `supported = false` và màn hình ẩn hẳn nút micro — hiện nút chết còn tệ hơn
 * không có nút.
 */
export function useSpeechInput(onResult: (text: string) => void) {
  const [listening, setListening] = useState(false);
  const ref = useRef<Recognition | null>(null);
  const supported = getRecognitionCtor() !== null;

  const toggle = useCallback(() => {
    const Ctor = getRecognitionCtor();
    if (!Ctor) return;

    if (listening) {
      ref.current?.stop();
      setListening(false);
      return;
    }

    const recognition = new Ctor();
    recognition.lang = "vi-VN";
    recognition.interimResults = false;
    recognition.onresult = (event: { results: Array<Array<{ transcript: string }>> }) => {
      onResult(event.results[0][0].transcript);
    };
    recognition.onend = () => setListening(false);
    recognition.onerror = () => setListening(false);
    ref.current = recognition;
    recognition.start();
    setListening(true);
  }, [listening, onResult]);

  return { supported, listening, toggle };
}
```

- [ ] **Step 6: Viết `frontend/src/components/BottomNav.tsx`**

```tsx
import { NavLink } from "react-router-dom";
import "./BottomNav.css";

/**
 * Chỉ hai mục, luôn hiện, chữ to. Không hamburger, không menu ẩn: thứ gì phải
 * bấm mới thấy thì với người lớn tuổi coi như không tồn tại.
 */
export function BottomNav() {
  return (
    <nav className="bottomnav">
      <NavLink to="/" end className={({ isActive }) => (isActive ? "bn bn--on" : "bn")}>
        Nhắn tin
      </NavLink>
      <NavLink
        to="/lich-cua-toi"
        className={({ isActive }) => (isActive ? "bn bn--on" : "bn")}
      >
        Lịch của tôi
      </NavLink>
    </nav>
  );
}
```

Tạo `frontend/src/components/BottomNav.css`:

```css
.bottomnav {
  position: fixed;
  left: 0; right: 0; bottom: 0;
  display: flex;
  background: var(--color-surface);
  border-top: 2px solid var(--color-border);
  padding-bottom: env(safe-area-inset-bottom);
}
.bn {
  flex: 1;
  min-height: var(--tap);
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 19px;
  font-weight: 600;
  text-decoration: none;
  color: var(--color-fg-muted);
}
.bn--on { color: var(--color-primary); box-shadow: inset 0 -4px 0 var(--color-primary); }
```

- [ ] **Step 7: Viết `frontend/src/screens/ChatScreen.tsx`**

```tsx
import { useEffect, useRef, useState } from "react";
import { BottomNav } from "../components/BottomNav";
import { ShopStatusCard } from "../components/ShopStatusCard";
import { ToolProgress } from "../components/ToolProgress";
import { useAgentStream } from "../hooks/useAgentStream";
import { useShopStatus } from "../hooks/useShopStatus";
import { useSpeechInput } from "../hooks/useSpeechInput";
import { useAuth } from "../auth/AuthContext";
import "./ChatScreen.css";

export function ChatScreen() {
  // Đọc TRONG component chứ không phải hằng số ở đầu file: hằng số bị đóng băng
  // lúc import, `vi.stubEnv` trong test sẽ không tác dụng.
  const SHOP_PHONE = import.meta.env.VITE_SHOP_PHONE ?? "";
  const { messages, steps, phase, offline, failed, send } = useAgentStream();
  const { status, loading } = useShopStatus();
  const { fullName } = useAuth();
  const [draft, setDraft] = useState("");
  const bottom = useRef<HTMLDivElement>(null);
  const speech = useSpeechInput((text) => setDraft((prev) => (prev ? `${prev} ${text}` : text)));

  useEffect(() => {
    bottom.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages, steps, phase]);

  function submit() {
    if (!draft.trim()) return;
    send(draft);
    setDraft("");
  }

  return (
    <div className="chat">
      <header className="chat__head">
        <ShopStatusCard status={status} loading={loading} />
      </header>

      <main className="chat__log">
        {messages.length === 0 && (
          <p className="chat__hello">
            Dạ chào {fullName ?? "cô chú"}. Cô chú muốn đặt lịch giờ nào thì nhắn cho con ạ.
          </p>
        )}

        {messages.map((m) => (
          <div key={m.id} className="bubble__wrap">
            <p className={m.role === "user" ? "bubble bubble--me" : "bubble bubble--bot"}>
              {m.text}
              {/* Con trỏ nhấp nháy ở cuối bong bóng đang stream. */}
              {m.role === "bot" &&
                phase === "answering" &&
                m.id === messages[messages.length - 1].id && <span className="caret" />}
            </p>
            {/* Câu gõ lúc mất mạng: giữ nguyên trên màn hình, nói rõ là chưa
                gửi được và máy đang tự lo. Khách không phải gõ lại. */}
            {m.pending && <p className="bubble__pending">Đang gửi lại…</p>}
          </div>
        ))}

        <ToolProgress steps={steps} />

        {phase === "thinking" && (
          // role="status" + chữ ẩn: người dùng trình đọc màn hình cũng biết máy
          // đang chạy, ba chấm nhấp nháy không nói được điều gì với họ.
          <div className="thinking" role="status">
            <span className="sr-only">Con đang đọc tin nhắn ạ</span>
            <span className="dot" /><span className="dot" /><span className="dot" />
          </div>
        )}

        {offline && <p className="alert alert--warn">Mất mạng, đang thử lại…</p>}

        {/* AI hỏng thì vẫn phải còn một đường đi tiếp. Người lớn tuổi gọi điện
            thoải mái hơn gõ lại câu hỏi, nên nút này to bằng nút gửi. */}
        {failed && SHOP_PHONE && (
          <a className="callbtn" href={`tel:${SHOP_PHONE}`}>
            Gọi cho tiệm: {SHOP_PHONE}
          </a>
        )}
        <div ref={bottom} />
      </main>

      <form
        className="chat__bar"
        onSubmit={(e) => {
          e.preventDefault();
          submit();
        }}
      >
        <label className="sr-only" htmlFor="draft">
          Nhắn cho tiệm
        </label>
        <textarea
          id="draft"
          className="chat__input"
          rows={1}
          value={draft}
          placeholder="Cô chú nhắn ở đây…"
          onChange={(e) => setDraft(e.target.value)}
        />
        {speech.supported && (
          <button
            type="button"
            className={speech.listening ? "iconbtn iconbtn--on" : "iconbtn"}
            onClick={speech.toggle}
            aria-label={speech.listening ? "Dừng nói" : "Nói thay vì gõ"}
          >
            <svg width="26" height="26" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true">
              <path d="M12 15a3 3 0 0 0 3-3V6a3 3 0 1 0-6 0v6a3 3 0 0 0 3 3z" />
              <path d="M19 11a1 1 0 1 0-2 0 5 5 0 0 1-10 0 1 1 0 1 0-2 0 7 7 0 0 0 6 6.92V21h2v-3.08A7 7 0 0 0 19 11z" />
            </svg>
          </button>
        )}
        <button type="submit" className="iconbtn iconbtn--send" disabled={!draft.trim()}>
          Gửi
        </button>
      </form>

      <BottomNav />
    </div>
  );
}
```

- [ ] **Step 8: Viết `frontend/src/screens/ChatScreen.css`**

```css
.chat { display: flex; flex-direction: column; min-height: 100dvh; }
.chat__head { padding: var(--s3) var(--s3) 0; }
.chat__log { flex: 1; padding: var(--s3); padding-bottom: 180px; }
.chat__hello { font-size: 20px; color: var(--color-fg-muted); }

.bubble {
  max-width: 88%;
  padding: var(--s2) var(--s3);
  border-radius: var(--r);
  font-size: 20px;
  margin-bottom: var(--s2);
  white-space: pre-wrap;
}
.bubble--me  { margin-left: auto; background: var(--color-primary); color: #fff; }
.bubble--bot { background: var(--color-surface); border: 2px solid var(--color-border); }

.bubble__pending {
  text-align: right;
  font-size: 17px;
  color: var(--color-fg-muted);
  margin: -6px 0 var(--s2);
}

/* To ngang nút gửi: lúc AI hỏng thì đây mới là đường đi thật của khách. */
.callbtn {
  display: block;
  min-height: var(--tap);
  line-height: var(--tap);
  margin: var(--s3) 0;
  text-align: center;
  font-size: 21px;
  font-weight: 700;
  border-radius: var(--r);
  background: var(--color-accent);
  color: #fff;
  text-decoration: none;
}

.caret {
  display: inline-block;
  width: 3px; height: 1em;
  margin-left: 2px;
  background: var(--color-fg);
  vertical-align: -2px;
  animation: caret-blink 1s steps(2) infinite;
}
@keyframes caret-blink { 50% { opacity: 0; } }

.thinking { display: flex; gap: 6px; padding: var(--s2) 0; }
.dot {
  width: 10px; height: 10px; border-radius: 50%;
  background: var(--color-fg-muted);
  animation: dot-bounce 1.2s infinite;
}
.dot:nth-child(3) { animation-delay: 0.2s; }
.dot:nth-child(4) { animation-delay: 0.4s; }
@keyframes dot-bounce { 0%, 60%, 100% { opacity: 0.35; } 30% { opacity: 1; } }

.chat__bar {
  position: fixed;
  left: 0; right: 0; bottom: 56px;
  display: flex;
  gap: var(--s1);
  padding: var(--s2) var(--s3);
  background: var(--color-surface);
  border-top: 2px solid var(--color-border);
}
.chat__input {
  flex: 1;
  min-height: var(--tap);
  padding: var(--s2) var(--s3);
  font-family: inherit;
  font-size: 20px;
  border: 2px solid var(--color-border);
  border-radius: var(--r);
  resize: none;
}
.iconbtn {
  min-width: var(--tap);
  min-height: var(--tap);
  border: none;
  border-radius: var(--r);
  background: var(--color-surface);
  border: 2px solid var(--color-primary);
  color: var(--color-primary);
  font-size: 19px;
  font-weight: 600;
  font-family: inherit;
  cursor: pointer;
}
.iconbtn--on   { background: var(--color-danger); border-color: var(--color-danger); color: #fff; }
.iconbtn--send { background: var(--color-primary); color: #fff; padding: 0 var(--s3); }
.iconbtn:disabled { opacity: 0.5; cursor: not-allowed; }

.sr-only {
  position: absolute; width: 1px; height: 1px;
  padding: 0; margin: -1px; overflow: hidden;
  clip: rect(0 0 0 0); white-space: nowrap; border: 0;
}
```

- [ ] **Step 9: Nối route**

Trong `frontend/src/App.tsx`, thay phần giữ chỗ của route `/`:

```tsx
import { ChatScreen } from "./screens/ChatScreen";
```

```tsx
          <Route
            path="/"
            element={
              <RequireAuth>
                <ChatScreen />
              </RequireAuth>
            }
          />
```

- [ ] **Step 10: Chạy test để xác nhận pass**

Run: `cd frontend && npm test`
Expected: PASS (30 passed)

- [ ] **Step 11: Kiểm tay bốn trạng thái**

Chạy backend + `npm run dev`, gõ "mai 3h chiều làm tóc được không con". Phải thấy đúng thứ tự: ba chấm → "Đang xem lịch trống…" có vòng xoay → dòng đó thu thành dấu tích **và ở lại** → chữ hiện dần kèm con trỏ. Tắt wifi giữa lúc chữ đang chạy: chữ đã hiện **phải còn nguyên**, thêm dòng "Mất mạng, đang thử lại…".

- [ ] **Step 12: Commit**

```bash
git add frontend
git commit -m "feat(fe): chat screen with streaming text and tool progress"
```
