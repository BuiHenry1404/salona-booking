import { useCallback, useEffect, useRef, useState } from "react";
import type { FormEvent, KeyboardEvent } from "react";
import { BottomNav } from "../components/BottomNav";
import { MessageBubble } from "../components/MessageBubble";
import { ShopStatusCard } from "../components/ShopStatusCard";
import { ToolProgress } from "../components/ToolProgress";
import { useAuth } from "../auth/AuthContext";
import { useAgentStream } from "../hooks/useAgentStream";
import { useShopStatus } from "../hooks/useShopStatus";
import { useSpeechInput } from "../hooks/useSpeechInput";
import "./ChatScreen.css";

/**
 * Màn hình chính: thẻ trạng thái tiệm, khung chat streaming, ô nhập có mic.
 * Chỉ VẼ — mọi logic dòng chở (queue offline, stream token, canonical
 * complete, tool state) đã nằm trong `useAgentStream`.
 */
export function ChatScreen() {
  // Đọc TRONG component chứ không phải hằng số module: hằng số bị đóng băng
  // lúc import, `vi.stubEnv` trong test sẽ không tác dụng.
  const shopPhone = import.meta.env.VITE_SHOP_PHONE ?? "";
  // `connected` không destruct — offline đã đủ cho UI, destruct mà không dùng
  // thì noUnusedLocals của tsc bắt.
  const { messages, steps, phase, offline, failed, send } = useAgentStream();
  const { status, loading } = useShopStatus();
  const { fullName } = useAuth();
  const [draft, setDraft] = useState("");
  const bottomRef = useRef<HTMLDivElement>(null);
  // Transcript NỐI THÊM vào ô nhập (không gửi ngay) — khách muốn xem lại và
  // sửa trước khi gửi. Nối tại ranh giới bằng đúng MỘT khoảng trắng: bỏ
  // whitespace thừa ở đuôi draft, trim transcript phòng thủ, draft toàn
  // whitespace thì transcript thành nội dung duy nhất. Nội dung/newline ở
  // GIỮA draft không đụng.
  const speech = useSpeechInput((text) =>
    setDraft((prev) => {
      const piece = text.trim();
      if (!piece) return prev;
      const base = prev.replace(/\s+$/, "");
      return base ? `${base} ${piece}` : piece;
    }),
  );

  useEffect(() => {
    // CSS reduced-motion không kiểm soát được behavior JS — hỏi trực tiếp.
    const reduce = window.matchMedia?.("(prefers-reduced-motion: reduce)")?.matches;
    bottomRef.current?.scrollIntoView({ behavior: reduce ? "auto" : "smooth" });
  }, [messages, steps, phase]);

  const sendDraft = useCallback(() => {
    const text = draft.trim();
    if (!text) return;
    send(text); // offline thì hook tự xếp hàng đợi — UI không chặn
    setDraft("");
  }, [draft, send]);

  // Enter = gửi, Shift+Enter = xuống dòng, đang gõ IME = không gửi (Enter
  // lúc này là chọn chữ gợi ý). Form submit và Enter dùng CHUNG sendDraft.
  function onKeyDown(event: KeyboardEvent<HTMLTextAreaElement>) {
    if (event.key === "Enter" && !event.shiftKey && !event.nativeEvent.isComposing) {
      event.preventDefault();
      sendDraft();
    }
  }

  function onSubmit(event: FormEvent) {
    event.preventDefault();
    sendDraft();
  }

  // Caret chỉ thuộc về tin CUỐI CÙNG của toàn timeline khi nó là bot đang
  // stream — không phải "bot cuối bất kể phía sau còn user". Khách nhắn thêm
  // giữa chừng thì bot cũ phải ngừng nhấp nháy, mắt họ đang ở tin mới nhất.
  const lastMessageId = messages[messages.length - 1]?.id;

  return (
    <div className="chat">
      <header className="chat__head">
        <ShopStatusCard status={status} loading={loading} />
      </header>

      <main className="chat__log">
        {messages.length === 0 && (
          // Empty state thuần UI — không push vào useAgentStream để thành
          // một "message": nó sẽ bị trộn lẫn logic dòng chở.
          <p className="chat__hello">
            Dạ chào {fullName || "cô chú"}. Cô chú muốn đặt lịch giờ nào thì nhắn cho con ạ.
          </p>
        )}

        {messages.map((m) => (
          <MessageBubble
            key={m.id}
            role={m.role}
            text={m.text}
            pending={m.pending}
            streaming={phase === "answering" && m.role === "bot" && m.id === lastMessageId}
          />
        ))}

        <ToolProgress steps={steps} />

        {phase === "thinking" && (
          // role="status" + chữ ẩn: người dùng trình đọc màn hình cũng biết
          // máy đang chạy — ba chấm nhấp nháy không nói được điều gì với họ.
          <div className="thinking" role="status">
            <span className="sr-only">Con đang đọc tin nhắn ạ</span>
            <span className="dot" aria-hidden="true" />
            <span className="dot" aria-hidden="true" />
            <span className="dot" aria-hidden="true" />
          </div>
        )}

        {offline && <p className="alert alert--warn">Mất mạng, đang thử lại…</p>}

        {/* AI hỏng thì vẫn phải còn một đường đi tiếp. Người lớn tuổi gọi
            điện thoải mái hơn gõ lại câu hỏi, nên nút này to bằng nút gửi.
            Không có số điện thoại thì không render link chết. */}
        {failed && shopPhone.trim() !== "" && (
          <a className="callbtn" href={`tel:${shopPhone}`}>
            Gọi cho tiệm: {shopPhone}
          </a>
        )}
        <div ref={bottomRef} />
      </main>

      <form className="chat__bar" onSubmit={onSubmit}>
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
          onKeyDown={onKeyDown}
        />
        {speech.supported && (
          // type="button" để bấm mic không submit form.
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
