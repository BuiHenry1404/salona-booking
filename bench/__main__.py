"""CLI của benchmark.

    python -m bench run --ref A[:adapter] --ref B[:adapter] [--tags edge] [--k 4]
                        [--no-proxy] [--no-judge] [--keep-db] [--scenarios PATH ...]
    python -m bench compare <run_dir> [<run_dir> ...] [--base <ref>] -o report.html
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

_DEFAULT_SCENARIOS = Path(__file__).resolve().parent / "scenarios"


def _collect_scenarios(paths: list[Path]) -> list[Path]:
    files: list[Path] = []
    for p in paths:
        if p.is_dir():
            files.extend(sorted(p.rglob("*.yaml")) + sorted(p.rglob("*.yml")))
        elif p.is_file():
            files.append(p)
        else:
            raise FileNotFoundError(f"không thấy kịch bản: {p}")
    return files


def _parse_tags(values: list[str] | None) -> set[str] | None:
    if not values:
        return None
    tags = {t.strip() for v in values for t in v.split(",") if t.strip()}
    return tags or None


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m bench")
    sub = parser.add_subparsers(dest="command", required=True)

    run = sub.add_parser("run", help="chạy bộ kịch bản trên nhiều ref")
    run.add_argument("--ref", action="append", required=True, help="ref git[:adapter], lặp lại được")
    run.add_argument("--tags", action="append", help="lọc kịch bản theo tag (phẩy hoặc lặp lại)")
    run.add_argument("--k", type=int, default=4, help="số lần chạy mỗi kịch bản (pass^k)")
    run.add_argument("--no-proxy", action="store_true", help="không chèn proxy lỗi trước Azure")
    run.add_argument("--no-judge", action="store_true", help="không gọi judge LLM")
    run.add_argument("--keep-db", action="store_true", help="giữ database Mongo của từng ref")
    run.add_argument(
        "--scenarios",
        nargs="+",
        type=Path,
        default=[_DEFAULT_SCENARIOS],
        help="file hoặc thư mục kịch bản YAML (mặc định bench/scenarios)",
    )

    cmp_ = sub.add_parser("compare", help="xuất báo cáo HTML so sánh các run")
    cmp_.add_argument("run_dirs", nargs="+", type=Path)
    cmp_.add_argument("--base", default=None, help="ref làm mốc so sánh")
    cmp_.add_argument("-o", "--output", type=Path, default=Path("report.html"))
    return parser


def _cmd_run(args: argparse.Namespace) -> int:
    from bench.config import load_settings
    from bench.runner import RefSpec, run_bench

    try:
        refs = [RefSpec.parse(r) for r in args.ref]
        paths = _collect_scenarios(args.scenarios)
    except (ValueError, FileNotFoundError) as exc:
        print(f"lỗi: {exc}", file=sys.stderr)
        return 2
    if not paths:
        print("lỗi: không có file kịch bản nào", file=sys.stderr)
        return 2
    if args.k < 1:
        print("lỗi: --k phải >= 1", file=sys.stderr)
        return 2

    run_dir = asyncio.run(
        run_bench(
            load_settings(),
            refs,
            paths,
            _parse_tags(args.tags),
            k=args.k,
            use_proxy=not args.no_proxy,
            judge_on=not args.no_judge,
            keep_db=args.keep_db,
        )
    )
    print(run_dir)
    return 0


def _cmd_compare(args: argparse.Namespace) -> int:
    try:
        from bench.report import build_report  # Task 10 — import trễ
    except ImportError:
        print("lỗi: chưa có bench/report.py (build_report) — chưa thể so sánh", file=sys.stderr)
        return 2
    html = build_report(list(args.run_dirs), args.base)
    args.output.write_text(html, encoding="utf-8")
    print(args.output)
    return 0


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    if args.command == "run":
        return _cmd_run(args)
    return _cmd_compare(args)


if __name__ == "__main__":
    sys.exit(main())
