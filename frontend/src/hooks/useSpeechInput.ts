import { useCallback, useEffect, useRef, useState } from "react";

/**
 * Copy type tối thiểu của Web Speech API. DOM lib của TypeScript không khai
 * báo sẵn SpeechRecognition/webkitSpeechRecognition — định nghĩa tại chỗ thay
 * vì sửa global typings: hook này là nơi duy nhất cần biết hình dạng đó.
 */
interface SpeechRecognitionAlternative {
  transcript: string;
}

interface SpeechRecognitionResultLike {
  readonly length: number;
  isFinal?: boolean;
  [index: number]: SpeechRecognitionAlternative;
}

interface SpeechRecognitionResultListLike {
  readonly length: number;
  [index: number]: SpeechRecognitionResultLike;
}

interface SpeechRecognitionEventLike {
  resultIndex?: number;
  results: SpeechRecognitionResultListLike;
}

interface SpeechRecognitionLike {
  lang: string;
  interimResults: boolean;
  continuous?: boolean;
  start(): void;
  stop(): void;
  abort?(): void;
  onstart: (() => void) | null;
  onresult: ((event: SpeechRecognitionEventLike) => void) | null;
  onend: (() => void) | null;
  onerror: ((event?: unknown) => void) | null;
}

type RecognitionCtor = new () => SpeechRecognitionLike;

/** Ưu tiên tên chuẩn, fallback tiền tố webkit của Safari cũ. */
function getRecognitionCtor(): RecognitionCtor | null {
  const w = window as unknown as Record<string, unknown>;
  const ctor = w.SpeechRecognition ?? w.webkitSpeechRecognition;
  return typeof ctor === "function" ? (ctor as RecognitionCtor) : null;
}

/**
 * Nhập bằng giọng nói tiếng Việt — gõ phím là rào cản lớn nhất với người
 * lớn tuổi. Trình duyệt không hỗ trợ thì `supported = false` và mọi hàm đều
 * no-op: màn hình chat dựa vào đó để ẩn nút micro, hiện nút chết còn tệ hơn
 * không có nút.
 *
 * Hook chỉ TRẢ text qua `onTranscript` — nối vào ô nhập hay gửi luôn là
 * quyết định của màn hình, không phải của hook. Một lượt nói đơn giản:
 * `continuous = false`, không transcript tạm (`interimResults = false`).
 */
export function useSpeechInput(onTranscript: (text: string) => void) {
  const [listening, setListening] = useState(false);
  const supported = getRecognitionCtor() !== null;

  const alive = useRef(true);
  // Phiên đang mở (kể cả giai đoạn start() đã gọi mà onstart chưa tới).
  // Non-null nghĩa là "đang bận" — chặn double-start đồng thời.
  const session = useRef<SpeechRecognitionLike | null>(null);
  // Consumer thường truyền inline callback (đổi identity mỗi render) — giữ
  // trong ref để start/stop không đổi identity theo.
  const transcriptCb = useRef(onTranscript);

  useEffect(() => {
    transcriptCb.current = onTranscript;
  });

  useEffect(() => {
    alive.current = true;
    return () => {
      alive.current = false;
      const recognition = session.current;
      session.current = null;
      if (!recognition) return;
      try {
        // Ưu tiên abort: đã unmount thì transcript dở cũng không ai nhận.
        if (typeof recognition.abort === "function") recognition.abort();
        else recognition.stop();
      } catch {
        /* engine có thể ném khi dừng phiên chưa kịp start — bỏ qua */
      }
    };
  }, []);

  const start = useCallback(() => {
    const Ctor = getRecognitionCtor();
    if (!Ctor) return;
    if (session.current) return; // đang start/nghe: không start chồng

    const recognition = new Ctor();
    recognition.lang = "vi-VN";
    recognition.interimResults = false;
    recognition.continuous = false;

    // Index của các entry ĐÃ THỰC SỰ phát final về consumer. Theo dõi từng
    // index thay vì đếm tổng: `results` dài ra append-only, nhưng một entry
    // interim CÓ THỂ thành final ngay tại index của nó — nếu coi
    // `results.length` là "đã xử lý hết" thì final đó bị mất tiêu. Index chỉ
    // vào Set khi callback thực sự đã phát (final + trim không rỗng), nên
    // interim không bị đánh dấu và event lặp cùng final index không phát kép.
    const deliveredFinals = new Set<number>();

    recognition.onstart = () => {
      if (session.current === recognition && alive.current) setListening(true);
    };

    recognition.onresult = (event) => {
      if (session.current !== recognition || !alive.current) return;
      const results = event.results;
      // resultIndex là index entry ĐẦU TIÊN thay đổi — mọi entry trước đó
      // không đổi, bỏ qua. Không dedupe theo text: người dùng hoàn toàn có
      // thể nói cùng một câu ở hai index khác nhau.
      const start = event.resultIndex ?? 0;
      for (let i = start; i < results.length; i += 1) {
        if (deliveredFinals.has(i)) continue;
        const result = results[i];
        if (!result || result.isFinal === false) continue;
        const transcript = result[0]?.transcript;
        const text = typeof transcript === "string" ? transcript.trim() : "";
        if (!text) continue;
        deliveredFinals.add(i);
        transcriptCb.current(text);
      }
    };

    // Kết thúc phiên (end bình thường hay error) là MỘT chỗ: bỏ session để
    // lượt bấm sau mở phiên mới, đưa listening về false. Event muộn của
    // phiên cũ bị guard `session.current !== recognition` chặn sẵn.
    const finish = () => {
      if (session.current !== recognition) return;
      session.current = null;
      if (alive.current) setListening(false);
    };
    recognition.onend = finish;
    recognition.onerror = finish; // không retry — hết quyền mic thì thôi

    session.current = recognition;
    try {
      recognition.start();
    } catch {
      // Ví dụ InvalidStateError khi engine cho rằng đã đang chạy — bỏ phiên
      // này để lượt bấm sau tạo phiên mới sạch sẽ.
      session.current = null;
      if (alive.current) setListening(false);
    }
  }, []);

  const stop = useCallback(() => {
    const recognition = session.current;
    if (!recognition) return;
    try {
      recognition.stop();
    } catch {
      /* phiên đã kết thúc từ trước thì stop có thể ném — bỏ qua */
    }
    // listening về false ở onend — theo đúng vòng đời của API.
  }, []);

  // Plan task-04 dùng `toggle` cho nút micro: idle → bắt đầu, đang nghe → dừng.
  const toggle = useCallback(() => {
    if (session.current) stop();
    else start();
  }, [start, stop]);

  return { supported, listening, start, stop, toggle };
}
