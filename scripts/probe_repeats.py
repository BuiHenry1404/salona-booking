"""Chạy tầng gác `repeats()` trên các transcript đã có để hiệu chỉnh ngưỡng.

    PYTHONPATH=. .venv/bin/python scripts/probe_repeats.py [--ratio 0.85] [--min-words 6] FILE...

In từng cặp (câu đáp trước, câu đáp bị cờ) để người đọc quyết: cờ đúng hay cờ
oan. Không gọi LLM. Định dạng transcript: dòng "BOT   : ..." của
scripts/chat_e2e_transcript.py; mỗi "##### KỊCH BẢN" là một cuộc riêng.
"""
import argparse
import glob

from app.agents.booking_graph.guard import repeats


def bot_lines(path):
    convos, current = [], []
    for line in open(path, encoding="utf-8"):
        if line.startswith("##### KỊCH BẢN"):
            if current:
                convos.append(current)
            current = []
        elif line.startswith("BOT   :"):
            current.append(line.split(":", 1)[1].strip())
    if current:
        convos.append(current)
    return convos


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("files", nargs="*", default=sorted(glob.glob("*.txt")))
    ap.add_argument("--ratio", type=float, default=0.85)
    ap.add_argument("--min-words", type=int, default=6)
    args = ap.parse_args()

    total = flagged = 0
    for path in args.files:
        for convo in bot_lines(path):
            for i, reply in enumerate(convo):
                total += 1
                if repeats(reply, convo[:i], ratio=args.ratio, min_words=args.min_words):
                    flagged += 1
                    print(f"\n[{path}] lượt {i + 1}")
                    for prev in convo[max(0, i - 3):i]:
                        print(f"  trước: {prev}")
                    print(f"  CỜ   : {reply}")
    print(f"\n{flagged}/{total} câu đáp bị cờ (ratio={args.ratio}, min_words={args.min_words})")


if __name__ == "__main__":
    main()
