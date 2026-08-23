import { act, fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ChatScreen } from "./ChatScreen";

/** Tất cả mock đều là object mutate được — test gán giá trị rồi rerender.
 * Factory chỉ trả closure đọc biến lúc GỌI nên không dính TDZ khi hoisted. */

const stream = {
  messages: [] as Array<{
    id: string;
    role: "user" | "bot";
    text: string;
    pending?: boolean;
  }>,
  steps: [] as Array<{ name: string; label: string; done: boolean; ok: boolean }>,
  phase: "idle" as "idle" | "thinking" | "tool" | "answering",
  connected: true,
  offline: false,
  failed: false,
  send: vi.fn<(text: string) => void>(),
};

const shop = {
  status: {
    is_busy: false,
    busy_until: null,
    minutes_left: null,
  } as { is_busy: boolean; busy_until: string | null; minutes_left: number | null },
  loading: false,
};

const speech = {
  supported: true,
  listening: false,
  start: vi.fn(),
  stop: vi.fn(),
  toggle: vi.fn(),
};

/** Hook mock捕获 callback để test bắn transcript vào — giống thật nhất có thể. */
let speechOnTranscript: ((text: string) => void) | null = null;
let authFullName: string | null = "Cô Lan";

vi.mock("../hooks/useAgentStream", () => ({ useAgentStream: () => stream }));
vi.mock("../hooks/useShopStatus", () => ({ useShopStatus: () => shop }));
vi.mock("../hooks/useSpeechInput", () => ({
  useSpeechInput: (onTranscript: (text: string) => void) => {
    speechOnTranscript = onTranscript;
    return speech;
  },
}));
vi.mock("../auth/AuthContext", () => ({
  useAuth: () => ({ role: "user" as const, fullName: authFullName, logout: vi.fn() }),
}));

function renderChat() {
  return render(
    <MemoryRouter>
      <ChatScreen />
    </MemoryRouter>,
  );
}

beforeEach(() => {
  stream.messages = [];
  stream.steps = [];
  stream.phase = "idle";
  stream.offline = false;
  stream.failed = false;
  stream.send = vi.fn<(text: string) => void>();
  shop.status = { is_busy: false, busy_until: null, minutes_left: null };
  shop.loading = false;
  speech.supported = true;
  speech.listening = false;
  speech.toggle = vi.fn();
  speechOnTranscript = null;
  authFullName = "Cô Lan";
  // jsdom không có scrollIntoView — ChatScreen tự gọi trong effect.
  HTMLElement.prototype.scrollIntoView = vi.fn();
});

afterEach(() => {
  vi.unstubAllEnvs();
});

describe("ChatScreen", () => {
  it("1. trống tin nhắn: lời chào kèm tên từ AuthContext", () => {
    renderChat();
    expect(screen.getByText(/dạ chào cô lan\./i)).toBeInTheDocument();
    expect(
      screen.getByText(/anh chị muốn đặt lịch giờ nào thì nhắn cho em ạ\./i),
    ).toBeInTheDocument();
  });

  it("2. không có tên: chào chung 'anh chị'", () => {
    authFullName = null;
    renderChat();
    expect(screen.getByText(/dạ chào anh chị\./i)).toBeInTheDocument();
    expect(screen.queryByText(/cô lan/i)).not.toBeInTheDocument();
  });

  it("3. thẻ trạng thái rảnh hiện đúng chữ", () => {
    renderChat();
    expect(screen.getByText(/chủ tiệm đang rảnh/i)).toBeInTheDocument();
  });

  it("4. bận: hiện chữ bận + giờ xong đúng (08:30Z = 3:30 chiều)", () => {
    shop.status = { is_busy: true, busy_until: "2026-08-07T08:30:00Z", minutes_left: 30 };
    renderChat();
    expect(screen.getByText(/chủ tiệm đang bận/i)).toBeInTheDocument();
    expect(screen.getByText("3:30 chiều")).toBeInTheDocument();
  });

  it("5. TUYỆT ĐỐI KHÔNG hiện minutes_left/countdown", () => {
    shop.status = { is_busy: true, busy_until: "2026-08-07T08:30:00Z", minutes_left: 30 };
    renderChat();
    expect(screen.queryByText(/30 phút/)).not.toBeInTheDocument();
    expect(screen.queryByText(/còn .* phút/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/nữa xong/i)).not.toBeInTheDocument();
  });

  it("6. hiện đúng text bubble user và bot", () => {
    stream.messages = [
      { id: "1", role: "user", text: "mai 3h làm tóc được không con" },
      { id: "2", role: "bot", text: "Dạ mai 3 giờ chiều còn trống ạ" },
    ];
    renderChat();
    expect(screen.getByText("mai 3h làm tóc được không con")).toBeInTheDocument();
    expect(screen.getByText("Dạ mai 3 giờ chiều còn trống ạ")).toBeInTheDocument();
  });

  it("7. answering: CHỈ bot cuối có caret, bot cũ không có, đúng số bubble", () => {
    stream.messages = [
      { id: "1", role: "bot", text: "câu bot cũ" },
      { id: "2", role: "user", text: "hỏi tiếp" },
      { id: "3", role: "bot", text: "đang stream" },
    ];
    stream.phase = "answering";
    renderChat();

    const bubbles = document.querySelectorAll(".bubble");
    expect(bubbles.length).toBe(3);
    const carets = document.querySelectorAll(".caret");
    expect(carets.length).toBe(1);
    // Caret nằm trong bubble "đang stream".
    expect(bubbles[2].querySelector(".caret")).not.toBeNull();
    expect(bubbles[0].querySelector(".caret")).toBeNull();
  });

  it("8. thinking: role=status, chữ cho trình đọc màn hình", () => {
    stream.phase = "thinking";
    renderChat();
    const status = screen.getByRole("status");
    expect(status).toHaveTextContent(/con đang đọc tin nhắn ạ/i);
  });

  it("9. tool: label tiếng Việt xuất hiện, tên raw KHÔNG xuất hiện", () => {
    stream.steps = [
      { name: "find_free_slots", label: "Đang xem lịch trống…", done: false, ok: true },
    ];
    renderChat();
    expect(screen.getByText("Đang xem lịch trống…")).toBeInTheDocument();
    expect(screen.queryByText(/find_free_slots/)).not.toBeInTheDocument();
  });

  it("10. tool đã xong vẫn còn trên màn hình", () => {
    stream.steps = [
      { name: "find_free_slots", label: "Đã xem lịch trống", done: true, ok: true },
    ];
    renderChat();
    expect(screen.getByText("Đã xem lịch trống")).toBeInTheDocument();
  });

  it("11. offline: dòng 'Mất mạng, đang thử lại…', messages cũ còn nguyên", () => {
    stream.messages = [{ id: "1", role: "bot", text: "Dạ mai 3 giờ" }];
    stream.offline = true;
    renderChat();
    expect(screen.getByText(/mất mạng, đang thử lại/i)).toBeInTheDocument();
    expect(screen.getByText("Dạ mai 3 giờ")).toBeInTheDocument();
  });

  it("12. câu chưa gửi được: 'Đang gửi lại…'", () => {
    stream.messages = [{ id: "1", role: "user", text: "hủy giùm cô lịch mai", pending: true }];
    renderChat();
    expect(screen.getByText("hủy giùm cô lịch mai")).toBeInTheDocument();
    expect(screen.getByText(/đang gửi lại/i)).toBeInTheDocument();
  });

  it("13. AI lỗi: link gọi thẳng cho tiệm với đúng số", () => {
    vi.stubEnv("VITE_SHOP_PHONE", "0287654321");
    stream.failed = true;
    renderChat();
    const call = screen.getByRole("link", { name: /gọi cho tiệm/i });
    expect(call).toHaveAttribute("href", "tel:0287654321");
  });

  it("14. lượt bình thường: KHÔNG có nút gọi", () => {
    vi.stubEnv("VITE_SHOP_PHONE", "0287654321");
    renderChat();
    expect(screen.queryByRole("link", { name: /gọi cho tiệm/i })).not.toBeInTheDocument();
  });

  it("15. lỗi mà không có số điện thoại: không render link chết", () => {
    vi.stubEnv("VITE_SHOP_PHONE", "");
    stream.failed = true;
    renderChat();
    expect(screen.queryByRole("link", { name: /gọi cho tiệm/i })).not.toBeInTheDocument();
  });

  it("16. KHÔNG có nút dừng AI", () => {
    stream.phase = "answering";
    renderChat();
    expect(screen.queryByRole("button", { name: /dừng/i })).not.toBeInTheDocument();
  });

  it("17. ô nhập rỗng: nút Gửi bị vô hiệu hóa", () => {
    renderChat();
    expect(screen.getByRole("button", { name: "Gửi" })).toBeDisabled();
  });

  it("18. submit form: send nhận text đã trim, ô nhập sạch", async () => {
    renderChat();
    const user = userEvent.setup();
    const box = screen.getByLabelText(/nhắn cho tiệm/i);
    await user.type(box, "  mai 3 giờ  ");
    await user.click(screen.getByRole("button", { name: "Gửi" }));

    expect(stream.send).toHaveBeenCalledWith("mai 3 giờ");
    expect(stream.send).toHaveBeenCalledTimes(1);
    expect(box).toHaveValue("");
  });

  it("19. offline vẫn submit được — UI không chặn, hook tự xếp hàng đợi", async () => {
    stream.offline = true;
    renderChat();
    const user = userEvent.setup();
    await user.type(screen.getByLabelText(/nhắn cho tiệm/i), "hủy giùm cô lịch mai");
    await user.click(screen.getByRole("button", { name: "Gửi" }));
    expect(stream.send).toHaveBeenCalledWith("hủy giùm cô lịch mai");
  });

  it("20. Enter: gửi luôn", async () => {
    renderChat();
    const user = userEvent.setup();
    await user.type(screen.getByLabelText(/nhắn cho tiệm/i), "mai 3 giờ{Enter}");
    expect(stream.send).toHaveBeenCalledWith("mai 3 giờ");
  });

  it("21. Shift+Enter: xuống dòng, KHÔNG gửi", async () => {
    renderChat();
    const user = userEvent.setup();
    const box = screen.getByLabelText(/nhắn cho tiệm/i);
    await user.type(box, "dòng một{Shift>}{Enter}{/Shift}dòng hai");
    expect(stream.send).not.toHaveBeenCalled();
    expect(box).toHaveValue("dòng một\ndòng hai");
  });

  it("22. IME composing + Enter: KHÔNG gửi", async () => {
    renderChat();
    const user = userEvent.setup();
    const box = screen.getByLabelText(/nhắn cho tiệm/i);
    await user.type(box, "đang gõ tiếng việt");
    fireEvent.keyDown(box, { key: "Enter", isComposing: true });
    expect(stream.send).not.toHaveBeenCalled();
    expect(box).toHaveValue("đang gõ tiếng việt");
  });

  it("23. trình duyệt không có mic: KHÔNG render nút mic", () => {
    speech.supported = false;
    renderChat();
    expect(
      screen.queryByRole("button", { name: /nói thay vì gõ|dừng nói/i }),
    ).not.toBeInTheDocument();
  });

  it("24. có mic, đang idle: aria-label 'Nói thay vì gõ'", () => {
    renderChat();
    expect(screen.getByRole("button", { name: "Nói thay vì gõ" })).toBeInTheDocument();
  });

  it("25. đang nghe: aria-label đổi thành 'Dừng nói'", () => {
    speech.listening = true;
    renderChat();
    expect(screen.getByRole("button", { name: "Dừng nói" })).toBeInTheDocument();
  });

  it("26. bấm mic: gọi toggle, KHÔNG submit send", async () => {
    renderChat();
    const user = userEvent.setup();
    await user.type(screen.getByLabelText(/nhắn cho tiệm/i), "chưa gửi đâu");
    await user.click(screen.getByRole("button", { name: "Nói thay vì gõ" }));
    expect(speech.toggle).toHaveBeenCalledTimes(1);
    expect(stream.send).not.toHaveBeenCalled();
  });

  it("27. transcript khi draft rỗng: thành chính nó, không dư khoảng trắng", () => {
    renderChat();
    act(() => speechOnTranscript?.("làm tóc"));
    expect(screen.getByLabelText(/nhắn cho tiệm/i)).toHaveValue("làm tóc");
  });

  it("28. transcript append vào draft đang có, nối bằng MỘT khoảng trắng", async () => {
    renderChat();
    const user = userEvent.setup();
    await user.type(screen.getByLabelText(/nhắn cho tiệm/i), "mai 3 giờ");
    act(() => speechOnTranscript?.("làm tóc"));
    expect(screen.getByLabelText(/nhắn cho tiệm/i)).toHaveValue("mai 3 giờ làm tóc");
  });

  it("29. auto-scroll: mount + mỗi lần messages/steps/phase đổi", () => {
    const view = renderChat();
    const scrollIntoView = HTMLElement.prototype.scrollIntoView as ReturnType<typeof vi.fn>;
    expect(scrollIntoView).toHaveBeenCalledTimes(1); // mount

    stream.messages = [{ id: "1", role: "user", text: "chào" }];
    view.rerender(
      <MemoryRouter>
        <ChatScreen />
      </MemoryRouter>,
    );
    expect(scrollIntoView).toHaveBeenCalledTimes(2);

    stream.phase = "thinking";
    view.rerender(
      <MemoryRouter>
        <ChatScreen />
      </MemoryRouter>,
    );
    expect(scrollIntoView).toHaveBeenCalledTimes(3);
  });

  it("30. BottomNav: có Nhắn tin và Lịch của tôi trỏ đúng chỗ", () => {
    renderChat();
    expect(screen.getByRole("link", { name: /nhắn tin/i })).toHaveAttribute("href", "/");
    expect(screen.getByRole("link", { name: /lịch của tôi/i })).toHaveAttribute(
      "href",
      "/lich-cua-toi",
    );
  });

  it("31. answering nhưng tin CUỐI timeline là user: KHÔNG bubble nào có caret", () => {
    // Bot đang stream giữa chừng thì khách nhắn thêm — caret trên bot cũ
    // là sai vì mắt người dùng đang ở tin user dưới cùng.
    stream.messages = [
      { id: "bot1", role: "bot", text: "đang trả lời" },
      { id: "user2", role: "user", text: "hỏi thêm" },
    ];
    stream.phase = "answering";
    renderChat();

    const bubbles = document.querySelectorAll(".bubble");
    expect(bubbles.length).toBe(2);
    expect(document.querySelectorAll(".caret").length).toBe(0);
  });

  it("32. append transcript: draft dư whitespace đuôi + transcript thừa khoảng trắng → nối đúng MỘT khoảng trắng", async () => {
    renderChat();
    fireEvent.change(screen.getByLabelText(/nhắn cho tiệm/i), {
      target: { value: "mai 3 giờ " },
    });
    act(() => speechOnTranscript?.(" làm tóc "));
    expect(screen.getByLabelText(/nhắn cho tiệm/i)).toHaveValue("mai 3 giờ làm tóc");
  });

  it("33. append transcript: draft chỉ toàn whitespace → transcript là nội dung duy nhất", () => {
    renderChat();
    fireEvent.change(screen.getByLabelText(/nhắn cho tiệm/i), {
      target: { value: "   " },
    });
    act(() => speechOnTranscript?.("làm tóc"));
    expect(screen.getByLabelText(/nhắn cho tiệm/i)).toHaveValue("làm tóc");
  });

  it("34. append transcript: newline ở GIỮA draft không bị phá", () => {
    renderChat();
    fireEvent.change(screen.getByLabelText(/nhắn cho tiệm/i), {
      target: { value: "dòng một\ndòng hai" },
    });
    act(() => speechOnTranscript?.("nữa nè"));
    expect(screen.getByLabelText(/nhắn cho tiệm/i)).toHaveValue("dòng một\ndòng hai nữa nè");
  });

  it("35. có đường vào lịch sử: link 'Xem các cuộc trò chuyện trước' trỏ /lich-su", () => {
    renderChat();
    const link = screen.getByRole("link", { name: /xem các cuộc trò chuyện trước/i });
    expect(link).toHaveAttribute("href", "/lich-su");
  });
});
