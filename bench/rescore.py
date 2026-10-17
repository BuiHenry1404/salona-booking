"""Chấm lại các lần chạy đã lưu: `python -m bench rescore <run_dir> [--rejudge]`.

Tính lại `score` (và, với `--rejudge`, gọi lại judge) cho MỌI file
`runs/<sha7>/<scenario>/<attempt>.json` từ chính các lượt/`before`/`after` đã
lưu, theo mã chấm điểm và `expect` HIỆN TẠI. Ghi đè tại chỗ, sau khi chép bản
gốc vào `<run_dir>/_orig/` (chỉ chép lần đầu — chấm lại lần hai không đè bản
gốc).

Chỉ hợp lệ khi kịch bản đổi phần KỲ VỌNG (cái chấm), không đổi phần "thế
giới" (setup, lượt khách, persona/goal của khách giả lập): đổi thế giới thì
khách lẽ ra đã nói khác, nên chấm lại lần chạy cũ như thể kịch bản mới đã
chạy là sai. Những lần chạy đó được đánh dấu `stale_scenario` (giữ nguyên
điểm cũ), và báo cáo loại chúng khỏi tổng hợp.

Phát hiện "thế giới đổi": so `scenario_fingerprint` lưu trong payload (runner
ghi từ sau commit này) với fingerprint kịch bản hiện tại. Payload cũ chưa có
fingerprint thì dùng danh sách `LEGACY_STALE` ghi tay.

Hộp đen — không `import app`.
"""

from __future__ import annotations

import asyncio
import json
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any

from bench.adapters.base import TurnRecord
from bench.judge import judge, render_transcript
from bench.report import _rows_from_list, _turn_record_from_dict
from bench.runner import _write_json
from bench.scenario import VN_TZ, Scenario, scenario_fingerprint
from bench.score import finalize_score, score_run

# Kịch bản đã đổi "thế giới" trong đợt sửa review cuối (2026-10-17), áp cho
# payload chạy TRƯỚC khi runner ghi `scenario_fingerprint`.
LEGACY_STALE: dict[str, str] = {
    "LLM-11": "persona đổi: chọn 11 giờ thay vì 10 giờ 30 (I4, review cuối)",
    "LLM-26": "persona đổi: 'chiều nay' → 'chiều mai' (T11, review cuối)",
    "LLM-27": "persona đổi: 'hôm nay' → 'ngày mai' (T11, review cuối)",
}

_ORIG_DIR = "_orig"


def _backup(run_dir: Path) -> None:
    orig = run_dir / _ORIG_DIR
    if orig.exists():
        return
    orig.mkdir()
    if (run_dir / "meta.json").exists():
        shutil.copy2(run_dir / "meta.json", orig / "meta.json")
    if (run_dir / "runs").is_dir():
        shutil.copytree(run_dir / "runs", orig / "runs")


def _payload_files(run_dir: Path) -> list[Path]:
    runs = run_dir / "runs"
    if not runs.is_dir():
        return []
    return sorted(runs.glob("*/*/*.json"))


def _stale_reason(
    payload: dict, scenario: Scenario, legacy_stale: dict[str, str]
) -> str | None:
    stored = payload.get("scenario_fingerprint")
    if stored:
        if stored != scenario_fingerprint(scenario):
            return "scenario_fingerprint khác kịch bản hiện tại (setup/lượt/persona đã đổi)"
        return None
    return legacy_stale.get(scenario.id)


def _parse_dt(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


async def rescore_run(
    run_dir: Path,
    scenarios: dict[str, Scenario],
    *,
    legacy_stale: dict[str, str] | None = None,
    rejudge: bool = False,
    client=None,
    judge_model: str | None = None,
    concurrency: int = 6,
    secrets_: list[str] | None = None,
) -> dict:
    """Chấm lại mọi payload trong `run_dir`. Trả số đếm theo loại."""
    run_dir = Path(run_dir)
    legacy = LEGACY_STALE if legacy_stale is None else legacy_stale
    if rejudge and (client is None or not judge_model):
        raise ValueError("--rejudge cần client LLM và judge_model")
    _backup(run_dir)

    counts = {
        "files": 0,
        "rescored": 0,
        "stale": 0,
        "error_payloads": 0,
        "unknown_scenario": 0,
        "rejudged": 0,
        "judge_failed": 0,
    }
    sem = asyncio.Semaphore(concurrency)

    async def handle(path: Path) -> None:
        counts["files"] += 1
        payload = json.loads(path.read_text(encoding="utf-8"))
        scenario = scenarios.get(str(payload.get("scenario_id")))
        if scenario is None:
            counts["unknown_scenario"] += 1
            return
        if not isinstance(payload.get("score"), dict):
            counts["error_payloads"] += 1
            return
        reason = _stale_reason(payload, scenario, legacy)
        if reason:
            payload["stale_scenario"] = reason
            counts["stale"] += 1
            _write_json(path, payload, secrets_)
            return
        payload.pop("stale_scenario", None)

        now_vn = _parse_dt(payload.get("now_vn")) or datetime.now(VN_TZ)
        records: list[TurnRecord] = [
            _turn_record_from_dict(t) for t in (payload.get("turns") or []) if isinstance(t, dict)
        ]
        before = _rows_from_list(payload.get("before")) or []
        after = _rows_from_list(payload.get("after")) or []
        score = score_run(
            scenario, records, before, after, str(payload.get("customer_id") or ""), now_vn
        )
        score = finalize_score(score, scenario.kind, len(records), payload.get("stop_reason"))
        payload["score"] = score
        payload["scenario_fingerprint"] = scenario_fingerprint(scenario)
        payload["rescored_at"] = datetime.now(VN_TZ)
        counts["rescored"] += 1

        if rejudge:
            result = None
            if score.valid and records:
                async with sem:
                    result = await judge(client, judge_model, render_transcript(records))
                counts["rejudged"] += 1
                if result is None:
                    counts["judge_failed"] += 1
            payload["judge"] = result
            payload["judge_model"] = judge_model
        _write_json(path, payload, secrets_)

    await asyncio.gather(*(handle(p) for p in _payload_files(run_dir)))

    meta_path = run_dir / "meta.json"
    if meta_path.exists():
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        meta.setdefault("rescored", []).append(
            {
                "at": datetime.now(VN_TZ),
                "rejudge": rejudge,
                "judge_model": judge_model if rejudge else None,
                "counts": counts,
            }
        )
        if rejudge and judge_model:
            meta["judge_model"] = judge_model
        _write_json(meta_path, meta, secrets_)
    return counts
