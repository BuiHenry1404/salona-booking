"""Test runner với cộng tác viên giả (không server, không mạng, không Mongo)."""

from __future__ import annotations

import json
import subprocess
from datetime import datetime
from pathlib import Path

import pytest

from bench import runner
from bench.adapters import ADAPTERS, TurnRecord
from bench.config import BenchSettings
from bench.dbread import ApptRow
from bench.runner import RefRun, RefSpec, parse_fault, run_bench, run_one
from bench.scenario import VN_TZ, load_scenarios
from bench.seed import Customer

SCRIPTED = """\
id: {id}
title: t
kind: scripted
tags: [edge]
turns:
{turns}
expect:
  booked: []
"""

SIMULATED = """\
id: sim1
title: s
kind: simulated
tags: [llm]
sim: {goal: g, persona: p}
expect: {}
"""


def _scenario_file(tmp_path: Path, sid: str, turns: str = '  - say: "chào em"') -> Path:
    p = tmp_path / f"{sid}.yaml"
    p.write_text(SCRIPTED.format(id=sid, turns=turns), encoding="utf-8")
    return p


def _settings(tmp_path: Path, **kw) -> BenchSettings:
    return BenchSettings(
        repo_root=tmp_path,
        results_dir=tmp_path / "results",
        worktrees_dir=tmp_path / "wt",
        **kw,
    )


# ---------------------------------------------------------------------------
# Workspace / proxy giả
# ---------------------------------------------------------------------------


class FakeWorkspace:
    instances: list["FakeWorkspace"] = []
    fail_start: set[str] = set()
    fail_prepare: set[str] = set()
    # ref -> số lần is_alive() trả True trước khi "chết"
    dies_after: dict[str, int] = {}

    def __init__(self, settings, ref, port, run_id, azure_endpoint):
        self.ref = ref
        self.port = port
        self.azure_endpoint = azure_endpoint
        self.sha = (ref.encode().hex() + "0" * 40)[:40]
        self.sha7 = self.sha[:7]
        self.path = settings.worktrees_dir / self.sha7
        self.db_name = f"bench_{self.sha7}_{run_id}"
        self.base_url = f"http://127.0.0.1:{port}"
        self.status = "pending"
        self.error = None
        self.stopped = False
        self.dropped = False
        self.alive_checks = 0
        FakeWorkspace.instances.append(self)

    def is_alive(self):
        self.alive_checks += 1
        limit = FakeWorkspace.dies_after.get(self.ref)
        return limit is None or self.alive_checks <= limit

    def log_tail(self, n=50):
        return "Traceback ...\nSegfault in uvicorn"

    async def prepare(self):
        if self.ref in FakeWorkspace.fail_prepare:
            raise subprocess.CalledProcessError(1, ["uv"], stderr="uv exploded")

    async def start(self, timeout_s: float = 90):
        if self.ref in FakeWorkspace.fail_start:
            self.status = "failed_to_start"
            self.error = "port busy\nlast log line"
            return False
        self.status = "ready"
        return True

    async def stop(self):
        self.stopped = True

    async def drop_db(self):
        self.dropped = True


class FakeProxy:
    instances: list["FakeProxy"] = []

    def __init__(self, upstream, port, hang_seconds=600.0):
        self.upstream = upstream
        self.port = port
        self.url = f"http://127.0.0.1:{port}"
        self.calls: list[tuple] = []
        self.stopped = False
        FakeProxy.instances.append(self)

    async def start(self):
        pass

    async def stop(self):
        self.stopped = True

    def set_fault(self, mode, count=1):
        self.calls.append((mode, count))


@pytest.fixture
def fakes(monkeypatch):
    FakeWorkspace.instances = []
    FakeWorkspace.fail_start = set()
    FakeWorkspace.fail_prepare = set()
    FakeWorkspace.dies_after = {}
    FakeProxy.instances = []
    monkeypatch.setattr(runner, "RefWorkspace", FakeWorkspace)
    monkeypatch.setattr(runner, "FaultProxy", FakeProxy)

    async def _noop(ws):
        return None

    monkeypatch.setattr(runner, "ensure_admin", _noop)
    monkeypatch.setattr(runner, "_LANGFUSE_SETTLE_S", 0)
    return monkeypatch


def _recording_run_one(calls):
    async def fake_run_one(rr, scenario, attempt, *, settings, llm_client=None, judge_on=False):
        calls.append((attempt, scenario.id, rr.spec.ref))
        return {
            "scenario_id": scenario.id,
            "ref": rr.spec.ref,
            "attempt": attempt,
            "turns": [],
            "score": None,
        }

    return fake_run_one


# ---------------------------------------------------------------------------
# Thứ tự, ref hỏng, meta
# ---------------------------------------------------------------------------


async def test_interleaving_order(tmp_path, fakes):
    calls: list[tuple] = []
    fakes.setattr(runner, "run_one", _recording_run_one(calls))
    paths = [_scenario_file(tmp_path, "s1"), _scenario_file(tmp_path, "s2")]

    await run_bench(
        _settings(tmp_path),
        [RefSpec("A"), RefSpec("B")],
        paths,
        None,
        k=2,
        use_proxy=False,
        judge_on=False,
    )

    # attempt → scenario → ref; thứ tự ref xoay vòng theo attempt (M1).
    assert calls == [
        (0, "s1", "A"), (0, "s1", "B"), (0, "s2", "A"), (0, "s2", "B"),
        (1, "s1", "B"), (1, "s1", "A"), (1, "s2", "B"), (1, "s2", "A"),
    ]


async def test_ref_order_rotates_per_attempt(tmp_path, fakes):
    calls: list[tuple] = []
    fakes.setattr(runner, "run_one", _recording_run_one(calls))
    await run_bench(
        _settings(tmp_path), [RefSpec("A"), RefSpec("B"), RefSpec("C")],
        [_scenario_file(tmp_path, "s1")], None, k=4, use_proxy=False, judge_on=False,
    )
    assert [c[2] for c in calls] == list("ABC" "BCA" "CAB" "ABC")


async def test_crashed_ref_is_skipped_for_rest_of_bench(tmp_path, fakes):
    """I2: server của ref chết giữa chừng → status crashed + log, bỏ qua tiếp."""
    calls: list[tuple] = []
    fakes.setattr(runner, "run_one", _recording_run_one(calls))
    FakeWorkspace.dies_after = {"A": 1}

    run_dir = await run_bench(
        _settings(tmp_path), [RefSpec("A"), RefSpec("B")],
        [_scenario_file(tmp_path, "s1"), _scenario_file(tmp_path, "s2")], None,
        k=2, use_proxy=False, judge_on=False,
    )
    assert [c for c in calls if c[2] == "A"] == [(0, "s1", "A")]
    assert len([c for c in calls if c[2] == "B"]) == 4
    a = FakeWorkspace.instances[0]
    assert a.alive_checks == 2  # không kiểm lại sau khi đã đánh dấu crashed
    meta = json.loads((run_dir / "meta.json").read_text(encoding="utf-8"))
    by_ref = {r["ref"]: r for r in meta["refs"]}
    assert by_ref["A"]["status"] == "crashed"
    assert "Segfault" in by_ref["A"]["error"]
    assert by_ref["B"]["status"] == "ready"


async def test_failed_ref_is_skipped_and_recorded(tmp_path, fakes):
    calls: list[tuple] = []
    fakes.setattr(runner, "run_one", _recording_run_one(calls))
    FakeWorkspace.fail_start = {"BAD"}
    paths = [_scenario_file(tmp_path, "s1")]

    run_dir = await run_bench(
        _settings(tmp_path), [RefSpec("GOOD"), RefSpec("BAD")], paths, None,
        k=1, use_proxy=False, judge_on=False,
    )

    good, bad = FakeWorkspace.instances
    assert {c[2] for c in calls} == {"GOOD"}
    assert (run_dir / "runs" / good.sha7 / "s1" / "0.json").exists()
    assert not (run_dir / "runs" / bad.sha7).exists()

    meta = json.loads((run_dir / "meta.json").read_text(encoding="utf-8"))
    by_ref = {r["ref"]: r for r in meta["refs"]}
    assert by_ref["BAD"]["status"] == "failed_to_start"
    assert "port busy" in by_ref["BAD"]["error"]
    assert by_ref["GOOD"]["status"] == "ready"
    assert meta["k"] == 1 and meta["scenarios"] == ["s1"]
    assert meta["rulings"] == [] and meta["skipped"] == []
    for key in ("started_at", "finished_at", "vn_date_start", "vn_date_end",
                "azure_deployment", "judge_model"):
        assert key in meta
    # Dọn: mọi server dừng, DB bị xoá (không --keep-db).
    assert good.stopped and bad.stopped and good.dropped and bad.dropped


async def test_prepare_exception_marks_failed_to_start(tmp_path, fakes):
    """R6: prepare() raise → failed_to_start kèm text lỗi, ref khác vẫn chạy."""
    calls: list[tuple] = []
    fakes.setattr(runner, "run_one", _recording_run_one(calls))
    FakeWorkspace.fail_prepare = {"BROKEN"}

    run_dir = await run_bench(
        _settings(tmp_path), [RefSpec("BROKEN"), RefSpec("OK")],
        [_scenario_file(tmp_path, "s1")], None, k=1, use_proxy=False, judge_on=False,
    )
    meta = json.loads((run_dir / "meta.json").read_text(encoding="utf-8"))
    by_ref = {r["ref"]: r for r in meta["refs"]}
    assert by_ref["BROKEN"]["status"] == "failed_to_start"
    assert "uv exploded" in by_ref["BROKEN"]["error"]
    assert calls == [(0, "s1", "OK")]


async def test_keep_db_and_proxy_ports(tmp_path, fakes):
    calls: list[tuple] = []
    fakes.setattr(runner, "run_one", _recording_run_one(calls))
    (tmp_path / ".env").write_text(
        "AZURE_OPENAI_ENDPOINT=https://real.example\nAZURE_OPENAI_DEPLOYMENT=dep1\n",
        encoding="utf-8",
    )
    run_dir = await run_bench(
        _settings(tmp_path, base_port=9100), [RefSpec("A"), RefSpec("B")],
        [_scenario_file(tmp_path, "s1")], None, k=1, use_proxy=True, judge_on=False,
        keep_db=True,
    )
    ws_a, ws_b = FakeWorkspace.instances
    assert (ws_a.port, ws_b.port) == (9100, 9102)
    assert [p.port for p in FakeProxy.instances] == [9101, 9103]
    assert all(p.upstream == "https://real.example" for p in FakeProxy.instances)
    assert ws_a.azure_endpoint == "http://127.0.0.1:9101"
    assert all(p.stopped for p in FakeProxy.instances)
    assert not ws_a.dropped and not ws_b.dropped
    meta = json.loads((run_dir / "meta.json").read_text(encoding="utf-8"))
    assert meta["azure_deployment"] == "dep1"


async def test_simulated_skipped_without_credentials(tmp_path, fakes):
    calls: list[tuple] = []
    fakes.setattr(runner, "run_one", _recording_run_one(calls))
    sim = tmp_path / "sim1.yaml"
    sim.write_text(SIMULATED, encoding="utf-8")

    run_dir = await run_bench(
        _settings(tmp_path), [RefSpec("A")], [_scenario_file(tmp_path, "s1"), sim], None,
        k=1, use_proxy=False, judge_on=True,
    )
    meta = json.loads((run_dir / "meta.json").read_text(encoding="utf-8"))
    assert meta["skipped"] == [{"scenario_id": "sim1", "reason": "no_llm_credentials"}]
    assert calls == [(0, "s1", "A")]


async def test_run_one_exception_is_recorded_not_raised(tmp_path, fakes):
    async def boom(*a, **kw):
        raise RuntimeError("seed failed")

    fakes.setattr(runner, "run_one", boom)
    run_dir = await run_bench(
        _settings(tmp_path), [RefSpec("A")], [_scenario_file(tmp_path, "s1")], None,
        k=1, use_proxy=False, judge_on=False,
    )
    ws = FakeWorkspace.instances[0]
    data = json.loads((run_dir / "runs" / ws.sha7 / "s1" / "0.json").read_text("utf-8"))
    assert data["score"] is None and "seed failed" in data["error"]
    # Report (Task 10) phải biết scripted/simulated mà không cần đoán —
    # payload lỗi vẫn phải mang kind/title của kịch bản đã biết.
    assert data["kind"] == "scripted" and data["title"] == "t"


# ---------------------------------------------------------------------------
# Tiện ích
# ---------------------------------------------------------------------------


def test_parse_fault():
    assert parse_fault("http500") == ("http500", 1)
    assert parse_fault("tool_loop:3") == ("tool_loop", 3)
    for bad in ("nope", "http500:x", "hang:0", ""):
        with pytest.raises(ValueError):
            parse_fault(bad)


def test_refspec_parse():
    assert RefSpec.parse("HEAD") == RefSpec("HEAD", "a")
    assert RefSpec.parse("docs/arch-security-review:a") == RefSpec("docs/arch-security-review", "a")
    with pytest.raises(ValueError):
        RefSpec.parse("main:zzz")


# ---------------------------------------------------------------------------
# run_one với seeder/adapter/Mongo giả
# ---------------------------------------------------------------------------


class FakeSeeder:
    def __init__(self, base_url, phone, password):
        self.n = 0

    async def set_shop(self, *a):
        pass

    async def set_busy(self, minutes):
        pass

    async def new_customer(self, name):
        self.n += 1
        return Customer(id=f"cust{self.n}", phone="0987000000", name=name, token="tok")

    async def book_for(self, customer_id, at, note):
        return "x"


class ScriptedAdapter:
    """Trả lời theo danh sách hành vi: str = answer, "TIMEOUT", "RAISE", "ERR"."""

    behaviours: list[str] = []
    sent: list[int] = []
    open_raises = False

    def __init__(self):
        self.closed = False

    async def open(self, base_url, customer):
        if ScriptedAdapter.open_raises:
            raise ConnectionRefusedError("server gone")

    async def send(self, turn, index, timeout_s):
        ScriptedAdapter.sent.append(index)
        b = ScriptedAdapter.behaviours[index]
        if b == "RAISE":
            raise ConnectionError("socket died")
        rec = TurnRecord(index=index, customer_text=turn.say or turn.intent)
        if b == "TIMEOUT":
            rec.error = "timeout"
        elif b == "ERR":
            rec.error = "Bạn gửi quá nhiều tin"
        else:
            rec.answer = b
        return rec

    async def close(self):
        self.closed = True


@pytest.fixture
def run_one_env(tmp_path, monkeypatch):
    monkeypatch.setattr(runner, "Seeder", FakeSeeder)
    monkeypatch.setattr(runner, "snapshot", lambda uri, db: [])
    monkeypatch.setitem(ADAPTERS, "fake", ScriptedAdapter)
    ScriptedAdapter.sent = []
    ScriptedAdapter.open_raises = False
    settings = _settings(tmp_path)
    ws = FakeWorkspace(settings, "HEAD", 9100, "r1", None)
    proxy = FakeProxy("https://up", 9101)
    rr = RefRun(spec=RefSpec("HEAD", "fake"), index=0, port=9100, proxy_port=9101,
                ws=ws, proxy=proxy, status="ready")
    return settings, rr, proxy


THREE_TURNS = '  - say: "một"\n  - say: "hai"\n  - say: "ba"'


async def test_run_one_stops_on_timeout_without_fault(tmp_path, run_one_env):
    settings, rr, _ = run_one_env
    ScriptedAdapter.behaviours = ["ok", "TIMEOUT", "ok"]
    [sc] = load_scenarios([_scenario_file(tmp_path, "t", THREE_TURNS)])
    payload = await run_one(rr, sc, 0, settings=settings)
    assert ScriptedAdapter.sent == [0, 1]
    assert payload["stop_reason"] == "timeout@1"
    assert payload["score"].passed is False
    assert all(t.db_after == [] for t in payload["turns"])
    assert payload["kind"] == sc.kind and payload["title"] == sc.title


async def test_run_one_continues_after_timeout_on_fault_turn(tmp_path, run_one_env):
    settings, rr, proxy = run_one_env
    ScriptedAdapter.behaviours = ["ok", "TIMEOUT", "ok"]
    turns = '  - say: "một"\n  - say: "hai"\n    fault: "hang:2"\n  - say: "ba"'
    [sc] = load_scenarios([_scenario_file(tmp_path, "t", turns)])
    payload = await run_one(rr, sc, 0, settings=settings)
    assert ScriptedAdapter.sent == [0, 1, 2]
    assert proxy.calls == [("hang", 2), (None, 1)]
    assert payload["stop_reason"] is None
    assert payload["score"].passed is True  # R11


async def test_run_one_adapter_exception_stops_run(tmp_path, run_one_env):
    settings, rr, _ = run_one_env
    ScriptedAdapter.behaviours = ["RAISE", "ok", "ok"]
    [sc] = load_scenarios([_scenario_file(tmp_path, "t", THREE_TURNS)])
    payload = await run_one(rr, sc, 0, settings=settings)
    assert ScriptedAdapter.sent == [0]
    assert payload["stop_reason"] == "adapter_exception@0"
    assert "socket died" in payload["turns"][0].error
    assert payload["score"].passed is False


async def test_run_one_precondition_failure_marks_invalid(tmp_path, run_one_env):
    settings, rr, _ = run_one_env
    ScriptedAdapter.behaviours = ["chị muốn làm gì ạ", "ok"]
    turns = (
        '  - say: "mai 3 giờ chiều"\n'
        '  - intent: decline\n'
        '    requires_asked: {day: tomorrow, time: "15:00"}'
    )
    [sc] = load_scenarios([_scenario_file(tmp_path, "t", turns)])
    payload = await run_one(rr, sc, 0, settings=settings)
    assert ScriptedAdapter.sent == [0]
    score = payload["score"]
    assert score.valid is False and score.invalid_reason == "precondition@1"
    assert score.passed is False


async def test_run_one_open_failure_is_not_passed(tmp_path, run_one_env):
    """C1: không mở được kết nối → không lượt nào; kịch bản booked: [] không
    được tính là đạt."""
    settings, rr, _ = run_one_env
    ScriptedAdapter.open_raises = True
    turns = '  - say: "mai 3 giờ chiều"\n  - intent: decline'
    [sc] = load_scenarios([_scenario_file(tmp_path, "t", turns)])
    payload = await run_one(rr, sc, 0, settings=settings)
    assert ScriptedAdapter.sent == []
    assert payload["stop_reason"].startswith("adapter_open_failed")
    assert payload["score"].passed is False and payload["score"].valid is True


async def test_simulated_with_no_turns_is_invalid(tmp_path, run_one_env, monkeypatch):
    settings, rr, _ = run_one_env

    class SilentSim:
        def __init__(self, client, model, goal):
            pass

        async def next_message(self, turns):
            return None

    monkeypatch.setattr(runner, "SimCustomer", SilentSim)
    sim = tmp_path / "sim1.yaml"
    sim.write_text(SIMULATED, encoding="utf-8")
    [sc] = load_scenarios([sim])
    payload = await run_one(rr, sc, 0, settings=settings, llm_client=object())
    score = payload["score"]
    assert score.valid is False and score.invalid_reason == "sim_no_turns"
    assert score.passed is False


async def test_error_turn_stops_run_as_failure_not_invalid(tmp_path, run_one_env):
    """I1: lỗi ở lượt không có fault → dừng ngay, lần chạy hợp lệ nhưng trượt
    (không để tiền đề của lượt sau biến nó thành không hợp lệ)."""
    settings, rr, _ = run_one_env
    ScriptedAdapter.behaviours = ["ERR", "ok"]
    turns = (
        '  - say: "mai 3 giờ chiều"\n'
        '  - intent: decline\n'
        '    requires_asked: {day: tomorrow, time: "15:00"}'
    )
    [sc] = load_scenarios([_scenario_file(tmp_path, "t", turns)])
    payload = await run_one(rr, sc, 0, settings=settings)
    assert ScriptedAdapter.sent == [0]
    assert payload["stop_reason"] == "error@0"
    assert payload["score"].valid is True and payload["score"].passed is False


async def test_error_on_fault_turn_does_not_stop(tmp_path, run_one_env):
    settings, rr, _ = run_one_env
    ScriptedAdapter.behaviours = ["ERR", "ok"]
    turns = '  - say: "một"\n    fault: http500\n  - say: "hai"'
    [sc] = load_scenarios([_scenario_file(tmp_path, "t", turns)])
    payload = await run_one(rr, sc, 0, settings=settings)
    assert ScriptedAdapter.sent == [0, 1]
    assert payload["stop_reason"] is None and payload["score"].passed is True


async def test_secrets_are_redacted_from_meta_and_run_json(tmp_path, fakes):
    key = "sk-supersecret-azure-key-123"
    endpoint = "https://secret-res.openai.azure.com"
    (tmp_path / ".env").write_text(
        f"AZURE_OPENAI_API_KEY={key}\nAZURE_OPENAI_ENDPOINT={endpoint}\n"
        "AZURE_OPENAI_DEPLOYMENT=dep1\nLANGFUSE_SECRET_KEY=lf-secret-999\n",
        encoding="utf-8",
    )

    async def boom(rr, scenario, attempt, **kw):
        raise RuntimeError(f"401 from {endpoint} with api-key {key}")

    fakes.setattr(runner, "run_one", boom)
    FakeWorkspace.fail_start = {"BAD"}
    orig_start = FakeWorkspace.start

    async def leaky_start(self, timeout_s: float = 90):
        ok = await orig_start(self, timeout_s)
        if not ok:
            self.error = f"crash: key={key} secret=lf-secret-999"
        return ok

    fakes.setattr(FakeWorkspace, "start", leaky_start)
    run_dir = await run_bench(
        _settings(tmp_path), [RefSpec("A"), RefSpec("BAD")],
        [_scenario_file(tmp_path, "s1")], None, k=1, use_proxy=False, judge_on=False,
    )
    texts = [p.read_text(encoding="utf-8") for p in run_dir.rglob("*.json")]
    assert len(texts) == 2
    for text in texts:
        assert key not in text and endpoint not in text and "lf-secret-999" not in text
    meta = json.loads((run_dir / "meta.json").read_text(encoding="utf-8"))
    assert "***" in {r["ref"]: r for r in meta["refs"]}["BAD"]["error"]
    assert meta["azure_deployment"] == "dep1"  # không phải bí mật, giữ nguyên


async def test_tokens_retried_until_traces_appear(tmp_path, fakes):
    fakes.setattr(runner, "_TOKEN_RETRY_BACKOFF_S", 0)
    calls: list[str] = []
    answers = [None, None, {"input": 1, "output": 2, "total": 3, "generations": 1, "traces": 1}]

    async def fake_fetch(settings, session_id, since):
        calls.append(session_id)
        return answers[len(calls) - 1] if len(calls) <= len(answers) else None

    async def fake_run_one(rr, scenario, attempt, **kw):
        return {"scenario_id": scenario.id, "customer_id": "cust9",
                "started_at": datetime.now(VN_TZ), "tokens": None, "score": None}

    fakes.setattr(runner, "fetch_tokens", fake_fetch)
    fakes.setattr(runner, "run_one", fake_run_one)
    settings = _settings(tmp_path, langfuse_host="http://lf", langfuse_public_key="pk",
                         langfuse_secret_key="sk-langfuse")
    run_dir = await run_bench(settings, [RefSpec("A")], [_scenario_file(tmp_path, "s1")],
                              None, k=1, use_proxy=False, judge_on=False)
    ws = FakeWorkspace.instances[0]
    data = json.loads((run_dir / "runs" / ws.sha7 / "s1" / "0.json").read_text("utf-8"))
    assert calls == ["cust9"] * 3
    assert data["tokens"]["total"] == 3


async def test_tokens_give_up_after_retries(tmp_path, fakes):
    fakes.setattr(runner, "_TOKEN_RETRY_BACKOFF_S", 0)
    calls: list[str] = []

    async def fake_fetch(settings, session_id, since):
        calls.append(session_id)
        return None

    async def fake_run_one(rr, scenario, attempt, **kw):
        return {"scenario_id": scenario.id, "customer_id": "c1",
                "started_at": datetime.now(VN_TZ), "tokens": None, "score": None}

    fakes.setattr(runner, "fetch_tokens", fake_fetch)
    fakes.setattr(runner, "run_one", fake_run_one)
    settings = _settings(tmp_path, langfuse_host="http://lf", langfuse_public_key="pk",
                         langfuse_secret_key="sk-langfuse")
    await run_bench(settings, [RefSpec("A")], [_scenario_file(tmp_path, "s1")],
                    None, k=1, use_proxy=False, judge_on=False)
    assert len(calls) == 1 + runner._TOKEN_RETRIES


async def test_run_one_payload_serialises(tmp_path, run_one_env):
    settings, rr, _ = run_one_env
    ScriptedAdapter.behaviours = ["ok"]
    [sc] = load_scenarios([_scenario_file(tmp_path, "t")])
    payload = await run_one(rr, sc, 2, settings=settings)
    payload["before"] = [ApptRow("a1", "cust1", datetime(2026, 10, 18, 15, 0, tzinfo=VN_TZ), "booked")]
    out = tmp_path / "x.json"
    runner._write_json(out, payload)
    data = json.loads(out.read_text(encoding="utf-8"))
    for key in ("scenario_id", "ref", "sha", "attempt", "now_vn", "customer_id",
                "turns", "before", "after", "score", "judge", "tokens"):
        assert key in data
    assert data["attempt"] == 2 and data["customer_id"] == "cust1"
    assert data["before"][0]["start_at"] == "2026-10-18T15:00:00+07:00"
    assert data["now_vn"].endswith("+07:00")
    assert data["score"]["passed"] is True and data["turns"][0]["answer"] == "ok"


def test_compare_without_report_module_exits_2(monkeypatch, tmp_path, capsys):
    import builtins

    from bench.__main__ import main

    real_import = builtins.__import__

    def fake_import(name, *a, **kw):
        if name == "bench.report":
            raise ImportError("no report")
        return real_import(name, *a, **kw)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    assert main(["compare", str(tmp_path)]) == 2
    assert "report" in capsys.readouterr().err


# ---------------------------------------------------------------------------
# Live: server thật trên HEAD (cần Mongo; Azure không bắt buộc — lỗi lượt OK)
# ---------------------------------------------------------------------------


@pytest.mark.live
async def test_run_one_scenario_on_head(tmp_path):
    import dataclasses
    import socket

    from bench.config import load_settings

    settings = dataclasses.replace(load_settings(), results_dir=tmp_path / "results")
    scenario = _scenario_file(
        tmp_path, "live-g01", '  - say: "mai 3 giờ chiều chị làm móng"\n  - intent: decline'
    )

    run_dir = await run_bench(
        settings, [RefSpec("HEAD")], [scenario], None, k=1, use_proxy=True, judge_on=False
    )

    meta = json.loads((run_dir / "meta.json").read_text(encoding="utf-8"))
    [ref] = meta["refs"]
    assert ref["status"] == "ready", ref["error"]
    data = json.loads(
        (run_dir / "runs" / ref["sha7"] / "live-g01" / "0.json").read_text(encoding="utf-8")
    )
    assert data["score"] is not None
    assert {"passed", "valid", "wrong_writes", "violations", "turns_used"} <= set(data["score"])

    # Dọn sạch: không còn server/proxy nghe ở cổng của ref.
    for port in (settings.base_port, settings.base_port + 1):
        with socket.socket() as s:
            assert s.connect_ex(("127.0.0.1", port)) != 0
