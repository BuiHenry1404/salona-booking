"""Test cho `bench rescore` — thư mục run dựng tay, không mạng, không Mongo."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from bench import rescore as rs
from bench.scenario import load_scenarios, scenario_fingerprint

SCENARIO = """\
id: s1
title: t
kind: scripted
tags: [edge]
setup:
  own:
  - day: tomorrow
    time: '09:00'
turns:
- say: "dời qua 2 giờ chiều"
expect:
  booked:
  - day: tomorrow
    time: '14:00'
"""

SEED = {"id": "a1", "user_id": "u1", "start_at": "2026-10-18T09:00:00+07:00",
        "status": "booked", "note": None}


def _payload(sid: str = "s1", **kw) -> dict:
    p = {
        "scenario_id": sid, "kind": "scripted", "title": "t", "ref": "main",
        "sha": "aaa1111222", "adapter": "a", "attempt": 0,
        "now_vn": "2026-10-17T10:00:00+07:00", "started_at": "2026-10-17T03:00:00+00:00",
        "customer_id": "u1", "stop_reason": None,
        "turns": [{"index": 0, "customer_text": "dời qua 2 giờ chiều", "events": [],
                   "answer": "dạ", "tools": ["parse_time"], "error": None,
                   "first_token_ms": 1, "complete_ms": 2, "db_after": [SEED]}],
        "before": [SEED], "after": [SEED],
        # Điểm cũ: lịch seed không ai đụng bị tính là ghi sai (C2).
        "score": {"passed": False, "valid": True, "invalid_reason": None,
                  "wrong_writes": ["extra:09:00 18/10"], "violations": [], "turns_used": 1},
        "judge": {"old": True}, "tokens": None,
    }
    p.update(kw)
    return p


@pytest.fixture
def run_dir(tmp_path):
    rd = tmp_path / "run"
    (rd / "runs" / "aaa1111" / "s1").mkdir(parents=True)
    (rd / "runs" / "aaa1111" / "s9").mkdir(parents=True)
    (rd / "meta.json").write_text(json.dumps({"run_id": "r", "k": 1, "refs": []}), "utf-8")
    (rd / "runs" / "aaa1111" / "s1" / "0.json").write_text(json.dumps(_payload()), "utf-8")
    (rd / "runs" / "aaa1111" / "s9" / "0.json").write_text(
        json.dumps(_payload("s9")), "utf-8"
    )
    return rd


@pytest.fixture
def scenarios(tmp_path):
    f = tmp_path / "s1.yaml"
    f.write_text(SCENARIO, encoding="utf-8")
    return {s.id: s for s in load_scenarios([f])}


def _read(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


async def test_rescore_updates_score_and_keeps_backup(run_dir, scenarios):
    summary = await rs.rescore_run(run_dir, scenarios, legacy_stale={})
    data = _read(run_dir / "runs" / "aaa1111" / "s1" / "0.json")
    assert data["score"]["wrong_writes"] == []
    assert "missing:14:00 18/10" in data["score"]["mismatches"]
    assert data["judge"] == {"old": True}  # không --rejudge → giữ judge cũ
    assert data["scenario_fingerprint"] == scenario_fingerprint(scenarios["s1"])
    orig = _read(run_dir / "_orig" / "runs" / "aaa1111" / "s1" / "0.json")
    assert orig["score"]["wrong_writes"] == ["extra:09:00 18/10"]
    assert (run_dir / "_orig" / "meta.json").exists()
    assert summary["rescored"] == 1
    assert summary["unknown_scenario"] == 1  # s9 không còn trong bộ kịch bản
    meta = _read(run_dir / "meta.json")
    assert meta["rescored"][0]["rejudge"] is False


async def test_rescore_twice_keeps_first_backup(run_dir, scenarios):
    await rs.rescore_run(run_dir, scenarios, legacy_stale={})
    await rs.rescore_run(run_dir, scenarios, legacy_stale={})
    orig = _read(run_dir / "_orig" / "runs" / "aaa1111" / "s1" / "0.json")
    assert orig["score"]["wrong_writes"] == ["extra:09:00 18/10"]


async def test_legacy_stale_scenario_is_marked_not_rescored(run_dir, scenarios):
    summary = await rs.rescore_run(run_dir, scenarios, legacy_stale={"s1": "persona đổi"})
    data = _read(run_dir / "runs" / "aaa1111" / "s1" / "0.json")
    assert data["stale_scenario"] == "persona đổi"
    assert data["score"]["wrong_writes"] == ["extra:09:00 18/10"]  # giữ nguyên
    assert summary["stale"] == 1


async def test_fingerprint_mismatch_is_stale(run_dir, scenarios):
    p = run_dir / "runs" / "aaa1111" / "s1" / "0.json"
    p.write_text(json.dumps(_payload(scenario_fingerprint="deadbeef")), "utf-8")
    await rs.rescore_run(run_dir, scenarios, legacy_stale={})
    assert _read(p)["stale_scenario"]


async def test_rejudge_uses_clean_transcript(run_dir, scenarios, monkeypatch):
    seen = []

    async def fake_judge(client, model, transcript):
        seen.append((model, transcript))
        return {"new": True}

    monkeypatch.setattr(rs, "judge", fake_judge)
    await rs.rescore_run(run_dir, scenarios, legacy_stale={}, rejudge=True,
                         client=object(), judge_model="claude-sonnet-4-6")
    data = _read(run_dir / "runs" / "aaa1111" / "s1" / "0.json")
    assert data["judge"] == {"new": True}
    assert seen[0][0] == "claude-sonnet-4-6"
    assert seen[0][1] == "Khách: dời qua 2 giờ chiều\nAI: dạ\n"  # không kèm tool (C1)


async def test_error_payload_is_left_alone(run_dir, scenarios):
    p = run_dir / "runs" / "aaa1111" / "s1" / "0.json"
    p.write_text(json.dumps({"scenario_id": "s1", "error": "boom", "score": None,
                             "turns": []}), "utf-8")
    summary = await rs.rescore_run(run_dir, scenarios, legacy_stale={})
    assert _read(p)["score"] is None
    assert summary["error_payloads"] == 1


def test_cli_rescore_calls_rescore_run(monkeypatch, run_dir):
    from bench import __main__ as cli

    seen = {}

    async def fake(rd, scenarios, **kw):
        seen.update(rd=rd, n=len(scenarios), **kw)
        return {"files": 0}

    monkeypatch.setattr(rs, "rescore_run", fake)
    assert cli.main(["rescore", str(run_dir)]) == 0
    assert seen["rd"] == run_dir and seen["rejudge"] is False and seen["n"] > 0


async def test_rejudge_skips_payloads_already_judged_by_same_model(run_dir, scenarios, monkeypatch):
    # Một lần rescore --rejudge bị ngắt giữa chừng: chạy lại không gọi judge
    # lần hai cho file đã chấm bằng đúng model đó.
    calls = []

    async def fake_judge(client, model, transcript):
        calls.append(model)
        return {"new": len(calls)}

    monkeypatch.setattr(rs, "judge", fake_judge)
    kw = dict(legacy_stale={}, rejudge=True, client=object(), judge_model="m1")
    await rs.rescore_run(run_dir, scenarios, **kw)
    await rs.rescore_run(run_dir, scenarios, **kw)
    assert calls == ["m1"]
    data = _read(run_dir / "runs" / "aaa1111" / "s1" / "0.json")
    assert data["judge"] == {"new": 1}
