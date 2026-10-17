"""CLI của benchmark.

    python -m bench run --ref A[:adapter] --ref B[:adapter] [--tags edge] [--k 4]
                        [--no-proxy] [--no-judge] [--keep-db] [--scenarios PATH ...]
    python -m bench run --resume <run_dir>      # chạy tiếp run dở, bỏ qua attempt đã có
    python -m bench compare <run_dir> [<run_dir> ...] [--base <ref>] -o report.html
    python -m bench rescore <run_dir> [<run_dir> ...] [--rejudge]
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
    run.add_argument("--ref", action="append", help="ref git[:adapter], lặp lại được")
    run.add_argument("--tags", action="append", help="lọc kịch bản theo tag (phẩy hoặc lặp lại)")
    run.add_argument("--k", type=int, default=None, help="số lần chạy mỗi kịch bản (pass^k, mặc định 4)")
    run.add_argument(
        "--resume",
        type=Path,
        default=None,
        help="chạy tiếp thư mục run dở: ref/k/tag lấy từ meta.json nếu không truyền, "
        "bỏ qua các <attempt>.json đã có",
    )
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

    rsc = sub.add_parser(
        "rescore",
        help="chấm lại các lần chạy đã lưu theo mã chấm + expect hiện tại (sao lưu vào _orig/)",
    )
    rsc.add_argument("run_dirs", nargs="+", type=Path)
    rsc.add_argument("--rejudge", action="store_true", help="gọi lại judge (judge model của bench/.env)")
    rsc.add_argument(
        "--scenarios",
        nargs="+",
        type=Path,
        default=[_DEFAULT_SCENARIOS],
        help="file hoặc thư mục kịch bản YAML (mặc định bench/scenarios)",
    )
    return parser


def _cmd_run(args: argparse.Namespace) -> int:
    import json

    from bench import runner
    from bench.config import load_settings
    from bench.runner import RefSpec

    meta: dict = {}
    if args.resume is not None:
        try:
            meta = json.loads((args.resume / "meta.json").read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            print(f"lỗi: không đọc được meta.json của {args.resume}: {exc}", file=sys.stderr)
            return 2

    try:
        if args.ref:
            refs = [RefSpec.parse(r) for r in args.ref]
        elif meta.get("refs"):
            refs = [RefSpec(r["ref"], r.get("adapter") or "a") for r in meta["refs"]]
        else:
            print("lỗi: cần --ref (hoặc --resume <run_dir>)", file=sys.stderr)
            return 2
        paths = _collect_scenarios(args.scenarios)
    except (ValueError, FileNotFoundError) as exc:
        print(f"lỗi: {exc}", file=sys.stderr)
        return 2
    if not paths:
        print("lỗi: không có file kịch bản nào", file=sys.stderr)
        return 2
    k = args.k if args.k is not None else int(meta.get("k") or 4)
    if k < 1:
        print("lỗi: --k phải >= 1", file=sys.stderr)
        return 2
    tags = _parse_tags(args.tags)
    if tags is None and meta.get("tags"):
        tags = set(meta["tags"])

    try:
        run_dir = asyncio.run(
            runner.run_bench(
                load_settings(),
                refs,
                paths,
                tags,
                k=k,
                use_proxy=not args.no_proxy,
                judge_on=not args.no_judge,
                keep_db=args.keep_db,
                resume_dir=args.resume,
            )
        )
    except ValueError as exc:
        print(f"lỗi: {exc}", file=sys.stderr)
        return 2
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


def _cmd_rescore(args: argparse.Namespace) -> int:
    from bench import rescore
    from bench.config import load_settings
    from bench.scenario import load_scenarios

    try:
        scenarios = {sc.id: sc for sc in load_scenarios(_collect_scenarios(args.scenarios))}
    except (ValueError, FileNotFoundError) as exc:
        print(f"lỗi: {exc}", file=sys.stderr)
        return 2
    for rd in args.run_dirs:
        if not (rd / "meta.json").exists():
            print(f"lỗi: {rd} không phải thư mục run (thiếu meta.json)", file=sys.stderr)
            return 2

    async def _go() -> None:
        client = None
        judge_model = None
        secrets_: list[str] = []
        if args.rejudge:
            from bench.llm import make_client
            from bench.runner import _llm_creds, secret_values

            settings = load_settings()
            if not _llm_creds(settings):
                raise ValueError("--rejudge cần BENCH_FOUNDRY_* trong bench/.env")
            client = make_client(settings)
            judge_model = settings.judge_model
            secrets_ = secret_values(settings.repo_root / ".env", settings)
        try:
            for rd in args.run_dirs:
                counts = await rescore.rescore_run(
                    rd,
                    scenarios,
                    rejudge=args.rejudge,
                    client=client,
                    judge_model=judge_model,
                    secrets_=secrets_,
                )
                print(f"{rd}: {counts}")
        finally:
            if client is not None:
                await client.close()

    try:
        asyncio.run(_go())
    except ValueError as exc:
        print(f"lỗi: {exc}", file=sys.stderr)
        return 2
    return 0


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    if args.command == "run":
        return _cmd_run(args)
    if args.command == "rescore":
        return _cmd_rescore(args)
    return _cmd_compare(args)


if __name__ == "__main__":
    sys.exit(main())
