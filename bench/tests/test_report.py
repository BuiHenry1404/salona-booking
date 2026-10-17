"""Test cho `bench/report.py` — thư mục run dựng tay (JSON tối thiểu), không
server, không mạng, không Mongo thật."""

from __future__ import annotations

import json
from pathlib import Path

from bench.report import build_report, warnings

CUSTOMER_TEXT = "cho em đặt 9h sáng mai"


def _write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


def _meta(
    run_id: str = "abc123",
    refs: list[dict] | None = None,
    scenarios: list[str] | None = None,
    started_at: str = "2026-10-17T09:00:00+07:00",
    vn_date_start: str = "2026-10-17",
    vn_date_end: str = "2026-10-17",
    azure_deployment: str = "gpt-4o",
    judge_model: str = "claude-sonnet-4-6",
    k: int = 1,
) -> dict:
    return {
        "run_id": run_id,
        "started_at": started_at,
        "finished_at": "2026-10-17T09:10:00+07:00",
        "vn_date_start": vn_date_start,
        "vn_date_end": vn_date_end,
        "azure_deployment": azure_deployment,
        "judge_model": judge_model,
        "judge_on": False,
        "use_proxy": True,
        "k": k,
        "tags": None,
        "scenarios": scenarios or ["g01"],
        "refs": refs
        or [
            {
                "ref": "main",
                "adapter": "a",
                "sha": "aaa1111222",
                "sha7": "aaa1111",
                "port": 8100,
                "status": "ready",
                "error": None,
                "azure_deployment": azure_deployment,
            },
            {
                "ref": "feature",
                "adapter": "a",
                "sha": "bbb2222333",
                "sha7": "bbb2222",
                "port": 8102,
                "status": "ready",
                "error": None,
                "azure_deployment": azure_deployment,
            },
        ],
        "skipped": [],
        "notes": [],
        "rulings": [],
    }


def _turn(
    index: int = 0,
    answer: str = "Dạ chị đặt giúp mình 9 giờ sáng mai nhé",
    tools: list[str] | None = None,
    error: str | None = None,
    db_after: list[dict] | None = None,
    customer_text: str = CUSTOMER_TEXT,
) -> dict:
    return {
        "index": index,
        "customer_text": customer_text,
        "events": [],
        "answer": answer,
        "tools": tools or ["propose_appointment"],
        "error": error,
        "first_token_ms": 500,
        "complete_ms": 1200,
        "db_after": db_after if db_after is not None else [],
    }


def _run_payload(
    ref: str,
    sha: str,
    scenario_id: str = "g01",
    attempt: int = 0,
    turns: list[dict] | None = None,
    score: dict | None = None,
    before: list[dict] | None = None,
    after: list[dict] | None = None,
) -> dict:
    return {
        "scenario_id": scenario_id,
        "ref": ref,
        "sha": sha,
        "adapter": "a",
        "attempt": attempt,
        "now_vn": "2026-10-17T09:00:00+07:00",
        "started_at": "2026-10-17T09:00:01+07:00",
        "customer_id": f"u-{ref}",
        "stop_reason": None,
        "turns": turns if turns is not None else [_turn()],
        "before": before or [],
        "after": after or [],
        "score": score
        if score is not None
        else {
            "passed": True,
            "valid": True,
            "invalid_reason": None,
            "wrong_writes": [],
            "violations": [],
            "turns_used": 1,
        },
        "judge": None,
        "tokens": None,
    }


def _build_basic_run_dir(tmp_path: Path, name: str = "run1", **overrides) -> Path:
    run_dir = tmp_path / name
    meta = _meta(**overrides)
    _write_json(run_dir / "meta.json", meta)
    for ref_meta in meta["refs"]:
        _write_json(
            run_dir / "runs" / ref_meta["sha7"] / "g01" / "0.json",
            _run_payload(ref_meta["ref"], ref_meta["sha"]),
        )
    return run_dir


def test_report_contains_summary_and_side_by_side(tmp_path):
    run_dir = _build_basic_run_dir(tmp_path)

    out = build_report([run_dir], base_ref="main")

    assert "main" in out
    assert "feature" in out
    assert "pass^k" in out
    assert out.count(CUSTOMER_TEXT) >= 2  # cả hai cột ref


def test_wrong_writes_are_highlighted(tmp_path):
    run_dir = tmp_path / "run1"
    meta = _meta()
    _write_json(run_dir / "meta.json", meta)

    extra_row = {
        "id": "r-extra",
        "user_id": "u-main",
        "start_at": "2026-10-18T10:00:00+07:00",
        "status": "booked",
        "note": None,
    }
    bad_turn = _turn(db_after=[extra_row])
    bad_score = {
        "passed": False,
        "valid": True,
        "invalid_reason": None,
        "wrong_writes": ["extra:10:00 18/10"],
        "violations": [],
        "turns_used": 1,
    }
    _write_json(
        run_dir / "runs" / "aaa1111" / "g01" / "0.json",
        _run_payload("main", "aaa1111222", turns=[bad_turn], score=bad_score, after=[extra_row]),
    )
    _write_json(
        run_dir / "runs" / "bbb2222" / "g01" / "0.json",
        _run_payload("feature", "bbb2222333"),
    )

    out = build_report([run_dir], base_ref="main")

    assert "extra:10:00 18/10" in out
    assert 'class="bad"' in out


def test_compare_warns_on_different_days_or_models(tmp_path):
    meta_a = _meta(run_id="runA", vn_date_start="2026-10-17", vn_date_end="2026-10-17")
    meta_b = _meta(run_id="runB", vn_date_start="2026-10-18", vn_date_end="2026-10-18")
    msgs = warnings([meta_a, meta_b])
    assert any("ngày" in m for m in msgs)

    meta_c = _meta(run_id="runC", judge_model="claude-sonnet-4-6")
    meta_d = _meta(run_id="runD", judge_model="claude-opus-9")
    msgs2 = warnings([meta_c, meta_d])
    assert any("judge_model" in m for m in msgs2)

    run_dir_a = _build_basic_run_dir(tmp_path, name="run_a", run_id="runA")
    run_dir_b = _build_basic_run_dir(tmp_path, name="run_b", run_id="runB", vn_date_start="2026-10-18", vn_date_end="2026-10-18")
    out = build_report([run_dir_a, run_dir_b])
    assert any(w in out for w in msgs)


def test_report_is_self_contained(tmp_path):
    run_dir = _build_basic_run_dir(tmp_path)
    out = build_report([run_dir], base_ref="main")
    assert 'src="http' not in out
    assert 'href="http' not in out


def test_failed_to_start_ref_is_shown(tmp_path):
    run_dir = tmp_path / "run1"
    meta = _meta(
        refs=[
            {
                "ref": "main",
                "adapter": "a",
                "sha": None,
                "sha7": None,
                "port": 8100,
                "status": "failed_to_start",
                "error": "RuntimeError: Mongo không có ở :27017",
                "azure_deployment": None,
            }
        ]
    )
    _write_json(run_dir / "meta.json", meta)

    out = build_report([run_dir], base_ref=None)

    assert "failed_to_start" in out
    assert "Mongo không có" in out


def test_null_score_counts_as_failed_run(tmp_path):
    run_dir = tmp_path / "run1"
    meta = _meta()
    _write_json(run_dir / "meta.json", meta)
    _write_json(
        run_dir / "runs" / "aaa1111" / "g01" / "0.json",
        {
            "scenario_id": "g01",
            "ref": "main",
            "sha": "aaa1111222",
            "adapter": "a",
            "attempt": 0,
            "error": "setup_exception: boom",
            "turns": [],
            "score": None,
            "judge": None,
            "tokens": None,
        },
    )
    _write_json(
        run_dir / "runs" / "bbb2222" / "g01" / "0.json",
        _run_payload("feature", "bbb2222333"),
    )

    out = build_report([run_dir], base_ref="main")
    assert out  # không crash trên score=None / thiếu now_vn, customer_id, turns
