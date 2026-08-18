import { act, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { useSpeechInput } from "./useSpeechInput";

/** Copy type tối thiểu cho event giả — không cần dependency ngoài. */
interface FakeAlternative {
  transcript: string;
}

interface FakeResult extends Array<FakeAlternative> {
  isFinal?: boolean;
}

interface FakeEvent {
  resultIndex: number;
  results: FakeResult[];
}

function makeResults(entries: Array<{ transcript: string; isFinal?: boolean }>): FakeResult[] {
  return entries.map(({ transcript, isFinal = true }) => {
    const result = [{ transcript }] as FakeResult;
    result.isFinal = isFinal;
    return result;
  });
}

/** Recognition giả theo đúng semantics Web Speech: start/stop đếm lượt,
 * handler do hook gán vào, test tự bắn sự kiện. */
class FakeRecognition {
  static created: FakeRecognition[] = [];
  static throwOnStart = false;

  lang = "";
  interimResults = true; // giá trị sai có sẵn — hook phải ghi đè bằng false
  continuous = true;
  startCount = 0;
  stopCount = 0;
  abortCount = 0;
  onstart: (() => void) | null = null;
  onresult: ((event: FakeEvent) => void) | null = null;
  onend: (() => void) | null = null;
  onerror: ((event?: { error?: string }) => void) | null = null;

  constructor() {
    FakeRecognition.created.push(this);
  }

  start() {
    if (FakeRecognition.throwOnStart) {
      FakeRecognition.throwOnStart = false;
      throw new DOMException("already started", "InvalidStateError");
    }
    this.startCount += 1;
  }

  stop() {
    this.stopCount += 1;
  }

  abort() {
    this.abortCount += 1;
  }
}

/** Biến thể không có abort() — trình duyệt cũ chỉ có stop. */
class FakeNoAbort {
  static created: FakeNoAbort[] = [];
  lang = "";
  interimResults = true;
  continuous = true;
  startCount = 0;
  stopCount = 0;
  onstart: (() => void) | null = null;
  onresult: ((event: FakeEvent) => void) | null = null;
  onend: (() => void) | null = null;
  onerror: ((event?: { error?: string }) => void) | null = null;

  constructor() {
    FakeNoAbort.created.push(this);
  }

  start() {
    this.startCount += 1;
  }

  stop() {
    this.stopCount += 1;
  }
}

function install(ctor: unknown, webkit = false) {
  const w = window as unknown as Record<string, unknown>;
  if (webkit) {
    delete w.SpeechRecognition;
    w.webkitSpeechRecognition = ctor;
  } else {
    delete w.webkitSpeechRecognition;
    w.SpeechRecognition = ctor;
  }
}

let onTranscript: ReturnType<typeof vi.fn<(text: string) => void>>;
let onTranscript2: ReturnType<typeof vi.fn<(text: string) => void>>;

beforeEach(() => {
  FakeRecognition.created = [];
  FakeRecognition.throwOnStart = false;
  FakeNoAbort.created = [];
  onTranscript = vi.fn<(text: string) => void>();
  onTranscript2 = vi.fn<(text: string) => void>();
});

afterEach(() => {
  const w = window as unknown as Record<string, unknown>;
  delete w.SpeechRecognition;
  delete w.webkitSpeechRecognition;
});

describe("useSpeechInput", () => {
  it("A. không hỗ trợ: supported=false, listening=false, start/stop không throw", () => {
    const { result } = renderHook(() => useSpeechInput(onTranscript));
    expect(result.current.supported).toBe(false);
    expect(result.current.listening).toBe(false);
    expect(() => result.current.start()).not.toThrow();
    expect(() => result.current.stop()).not.toThrow();
  });

  it("B. có SpeechRecognition: supported, cấu hình vi-VN, interimResults=false, một lượt nói", () => {
    install(FakeRecognition);
    const { result } = renderHook(() => useSpeechInput(onTranscript));
    expect(result.current.supported).toBe(true);

    act(() => result.current.start());
    const rec = FakeRecognition.created[0];
    expect(rec.lang).toBe("vi-VN");
    expect(rec.interimResults).toBe(false);
    expect(rec.continuous).toBe(false);
  });

  it("C. fallback webkitSpeechRecognition khi thiếu SpeechRecognition", () => {
    install(FakeRecognition, true);
    const { result } = renderHook(() => useSpeechInput(onTranscript));
    expect(result.current.supported).toBe(true);

    act(() => result.current.start());
    expect(FakeRecognition.created).toHaveLength(1);
    expect(FakeRecognition.created[0].startCount).toBe(1);
  });

  it("D. start: recognition.start đúng MỘT lần; onstart mới là lúc listening=true", () => {
    install(FakeRecognition);
    const { result } = renderHook(() => useSpeechInput(onTranscript));
    act(() => result.current.start());
    const rec = FakeRecognition.created[0];

    expect(rec.startCount).toBe(1);
    expect(result.current.listening).toBe(false); // chưa có onstart

    act(() => rec.onstart?.());
    expect(result.current.listening).toBe(true);
  });

  it("E. đang listening mà start lần nữa: KHÔNG gọi start lần hai", () => {
    install(FakeRecognition);
    const { result } = renderHook(() => useSpeechInput(onTranscript));
    act(() => result.current.start());
    const rec = FakeRecognition.created[0];
    act(() => rec.onstart?.());

    act(() => result.current.start());
    expect(rec.startCount).toBe(1);
    expect(FakeRecognition.created).toHaveLength(1);
  });

  it("E2. start hai lần liên tiếp TRƯỚC onstart: vẫn chỉ MỘT phiên", () => {
    install(FakeRecognition);
    const { result } = renderHook(() => useSpeechInput(onTranscript));
    act(() => result.current.start());
    act(() => result.current.start());

    expect(FakeRecognition.created).toHaveLength(1);
    expect(FakeRecognition.created[0].startCount).toBe(1);
  });

  it("F. stop: gọi recognition.stop; onend mới đưa listening về false", () => {
    install(FakeRecognition);
    const { result } = renderHook(() => useSpeechInput(onTranscript));
    act(() => result.current.start());
    const rec = FakeRecognition.created[0];
    act(() => rec.onstart?.());

    act(() => result.current.stop());
    expect(rec.stopCount).toBe(1);

    act(() => rec.onend?.());
    expect(result.current.listening).toBe(false);
  });

  it("F2. stop lúc idle: no-op, không throw, không đụng recognition nào", () => {
    install(FakeRecognition);
    const { result } = renderHook(() => useSpeechInput(onTranscript));
    expect(() => act(() => result.current.stop())).not.toThrow();
    expect(FakeRecognition.created).toHaveLength(0);
  });

  it("G. transcript: trim khoảng trắng; rỗng thì KHÔNG callback", () => {
    install(FakeRecognition);
    const { result } = renderHook(() => useSpeechInput(onTranscript));
    act(() => result.current.start());
    const rec = FakeRecognition.created[0];

    act(() => {
      rec.onresult?.({ resultIndex: 0, results: makeResults([{ transcript: "  mai 3 giờ  " }]) });
    });
    expect(onTranscript).toHaveBeenCalledWith("mai 3 giờ");

    onTranscript.mockClear();
    act(() => {
      rec.onresult?.({ resultIndex: 0, results: makeResults([{ transcript: "   " }]) });
    });
    expect(onTranscript).not.toHaveBeenCalled();
  });

  it("G2. cùng final transcript phát tới hai lần: CHỈ callback một lần", () => {
    install(FakeRecognition);
    const { result } = renderHook(() => useSpeechInput(onTranscript));
    act(() => result.current.start());
    const rec = FakeRecognition.created[0];

    const event = { resultIndex: 0, results: makeResults([{ transcript: "mai 3 giờ" }]) };
    act(() => {
      rec.onresult?.(event);
    });
    act(() => {
      rec.onresult?.(event);
    });
    expect(onTranscript).toHaveBeenCalledTimes(1);
  });

  it("G3. results dài ra append-only: event sau chỉ phát entry MỚI", () => {
    install(FakeRecognition);
    const { result } = renderHook(() => useSpeechInput(onTranscript));
    act(() => result.current.start());
    const rec = FakeRecognition.created[0];

    const first = makeResults([{ transcript: "mai 3 giờ" }]);
    act(() => {
      rec.onresult?.({ resultIndex: 0, results: first });
    });

    // Event kế theo semantics thật: danh sách chứa cả entry cũ lẫn entry mới.
    const second = [...first, ...makeResults([{ transcript: "làm tóc" }])];
    act(() => {
      rec.onresult?.({ resultIndex: 1, results: second });
    });

    expect(onTranscript).toHaveBeenCalledTimes(2);
    expect(onTranscript).toHaveBeenNthCalledWith(1, "mai 3 giờ");
    expect(onTranscript).toHaveBeenNthCalledWith(2, "làm tóc");
  });

  it("G4. entry interim (isFinal=false) bị bỏ qua", () => {
    install(FakeRecognition);
    const { result } = renderHook(() => useSpeechInput(onTranscript));
    act(() => result.current.start());
    const rec = FakeRecognition.created[0];

    act(() => {
      rec.onresult?.({
        resultIndex: 0,
        results: makeResults([{ transcript: "tạm thời", isFinal: false }]),
      });
    });
    expect(onTranscript).not.toHaveBeenCalled();
  });

  it("G5. REGRESSION: interim cùng index sau thành final KHÔNG bị mất — không tin results.length", () => {
    install(FakeRecognition);
    const { result } = renderHook(() => useSpeechInput(onTranscript));
    act(() => result.current.start());
    const rec = FakeRecognition.created[0];

    // Event 1: entry 0 còn interim — không callback.
    act(() => {
      rec.onresult?.({
        resultIndex: 0,
        results: makeResults([{ transcript: "mai 3", isFinal: false }]),
      });
    });
    expect(onTranscript).not.toHaveBeenCalled();

    // Event 2: CHÍNH entry 0 đó giờ thành final — phải callback được.
    act(() => {
      rec.onresult?.({
        resultIndex: 0,
        results: makeResults([{ transcript: "mai 3 giờ" }]),
      });
    });
    expect(onTranscript).toHaveBeenCalledTimes(1);
    expect(onTranscript).toHaveBeenCalledWith("mai 3 giờ");

    // Bắn lại cùng final event/index: vẫn chỉ 1 lần.
    act(() => {
      rec.onresult?.({
        resultIndex: 0,
        results: makeResults([{ transcript: "mai 3 giờ" }]),
      });
    });
    expect(onTranscript).toHaveBeenCalledTimes(1);
  });

  it("G6. cùng CHỮ nhưng hai index khác nhau là hai lần nói — callback hai lần", () => {
    install(FakeRecognition);
    const { result } = renderHook(() => useSpeechInput(onTranscript));
    act(() => result.current.start());
    const rec = FakeRecognition.created[0];

    act(() => {
      rec.onresult?.({
        resultIndex: 0,
        results: makeResults([{ transcript: "mai 3 giờ" }]),
      });
    });
    // Entry thứ hai cùng nội dung — không được dedupe theo text toàn cục.
    act(() => {
      rec.onresult?.({
        resultIndex: 1,
        results: [
          ...makeResults([{ transcript: "mai 3 giờ" }]),
          ...makeResults([{ transcript: "mai 3 giờ" }]),
        ],
      });
    });

    expect(onTranscript).toHaveBeenCalledTimes(2);
    expect(onTranscript).toHaveBeenNthCalledWith(1, "mai 3 giờ");
    expect(onTranscript).toHaveBeenNthCalledWith(2, "mai 3 giờ");
  });

  it("H. onerror: listening=false, không throw, end đến sau cũng vô hại, start lại được", () => {
    install(FakeRecognition);
    const { result } = renderHook(() => useSpeechInput(onTranscript));
    act(() => result.current.start());
    const rec = FakeRecognition.created[0];
    act(() => rec.onstart?.());

    expect(() => act(() => rec.onerror?.({ error: "not-allowed" }))).not.toThrow();
    expect(result.current.listening).toBe(false);

    act(() => rec.onend?.()); // end sau error phải vô hại
    expect(result.current.listening).toBe(false);

    act(() => result.current.start()); // phiên mới sạch sẽ
    expect(FakeRecognition.created).toHaveLength(2);
  });

  it("I. recognition.start() ném InvalidStateError: không crash, state an toàn, thử lại được", () => {
    install(FakeRecognition);
    FakeRecognition.throwOnStart = true;
    const { result } = renderHook(() => useSpeechInput(onTranscript));

    expect(() => act(() => result.current.start())).not.toThrow();
    expect(result.current.listening).toBe(false);
    expect(FakeRecognition.created[0].startCount).toBe(0);

    act(() => result.current.start());
    expect(FakeRecognition.created).toHaveLength(2);
    expect(FakeRecognition.created[1].startCount).toBe(1);
  });

  it("J. unmount: abort an toàn phiên đang chạy; result muộn KHÔNG callback", () => {
    install(FakeRecognition);
    const { result, unmount } = renderHook(() => useSpeechInput(onTranscript));
    act(() => result.current.start());
    const rec = FakeRecognition.created[0];
    act(() => rec.onstart?.());

    unmount();
    expect(rec.abortCount).toBe(1);
    expect(rec.stopCount).toBe(0);

    act(() => {
      rec.onresult?.({ resultIndex: 0, results: makeResults([{ transcript: "muộn rồi" }]) });
    });
    expect(onTranscript).not.toHaveBeenCalled();
  });

  it("J2. unmount khi API không có abort: dùng stop", () => {
    install(FakeNoAbort);
    const { result, unmount } = renderHook(() => useSpeechInput(onTranscript));
    act(() => result.current.start());
    const rec = FakeNoAbort.created[0];
    act(() => rec.onstart?.());

    unmount();
    expect(rec.stopCount).toBe(1);
  });

  it("K. remount: instance cũ không rò transcript sang mount mới; mount mới chạy đúng MỘT lần", () => {
    install(FakeRecognition);
    const first = renderHook(() => useSpeechInput(onTranscript));
    act(() => first.result.current.start());
    const rec = FakeRecognition.created[0];
    act(() => rec.onstart?.());
    first.unmount();

    const second = renderHook(() => useSpeechInput(onTranscript2));
    act(() => {
      rec.onresult?.({ resultIndex: 0, results: makeResults([{ transcript: "cũ" }]) });
    });
    expect(onTranscript2).not.toHaveBeenCalled();
    expect(onTranscript).not.toHaveBeenCalled();

    act(() => second.result.current.start());
    const rec2 = FakeRecognition.created[1];
    act(() => rec2.onstart?.());
    act(() => {
      rec2.onresult?.({ resultIndex: 0, results: makeResults([{ transcript: "mới" }]) });
    });
    expect(onTranscript2).toHaveBeenCalledTimes(1);
    expect(onTranscript2).toHaveBeenCalledWith("mới");
    second.unmount();
  });

  it("T. toggle: idle thì bắt đầu nghe, đang nghe thì dừng (API theo plan task-04)", () => {
    install(FakeRecognition);
    const { result } = renderHook(() => useSpeechInput(onTranscript));

    act(() => result.current.toggle());
    const rec = FakeRecognition.created[0];
    act(() => rec.onstart?.());
    expect(result.current.listening).toBe(true);

    act(() => result.current.toggle());
    expect(rec.stopCount).toBe(1);
    act(() => rec.onend?.());
    expect(result.current.listening).toBe(false);
  });
});
