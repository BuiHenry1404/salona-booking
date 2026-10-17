"""Runner nhiều ref: dựng mọi ref song song, chạy bộ kịch bản XEN KẼ giữa các
ref trong cùng khung giờ, chấm điểm, (tuỳ chọn) judge + đếm token, rồi ghi
JSON kết quả.

Hộp đen — không `import app`. Bố cục kết quả:

    results/<timestamp>-<runid>/
        meta.json
        runs/<sha7>/<scenario_id>/<attempt>.json

Thứ tự chạy: `for attempt: for scenario: for ref:` — xen kẽ để mọi ref chịu
cùng điều kiện (giờ trong ngày, tải Azure) ở mỗi vòng.
"""

from __future__ import annotations

import asyncio
import dataclasses
import json
import secrets
import subprocess
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

from pymongo import MongoClient

from bench.adapters import ADAPTERS, TurnRecord
from bench.config import BenchSettings, _parse_env_file
from bench.dbread import snapshot
from bench.judge import judge, render_transcript
from bench.llm import make_client
from bench.proxy import _FAULT_MODES, FaultProxy
from bench.scenario import VN_TZ, Scenario, Turn, load_scenarios, resolve_at
from bench.score import RunScore, check_precondition, score_run
from bench.seed import Seeder, ensure_admin
from bench.simulate import SimCustomer
from bench.tokens import fetch_tokens, langfuse_configured
from bench.workspace import RefWorkspace

# Langfuse ghi trace bất đồng bộ — chờ một lúc trước khi đọc token, rồi thử
# lại các lần chạy chưa có trace (test monkeypatch các hằng này về 0).
_LANGFUSE_SETTLE_S = 15.0
_TOKEN_RETRIES = 3
_TOKEN_RETRY_BACKOFF_S = 15.0

# I2: server của một ref chết giữa run → khởi động lại tối đa chừng này lần
# (cùng DB, cùng cổng) rồi mới đánh dấu crashed.
_MAX_RESTARTS = 1

# Khoá .env không phải bí mật — được ghi vào meta có chủ đích.
_NON_SECRET_KEYS = {"AZURE_OPENAI_DEPLOYMENT", "AZURE_OPENAI_MODEL", "AZURE_OPENAI_API_VERSION"}
_SECRET_MARKERS = ("KEY", "SECRET", "TOKEN", "PASSWORD")
_MIN_SECRET_LEN = 6


@dataclass(frozen=True)
class RefSpec:
    ref: str
    adapter: str = "a"

    @classmethod
    def parse(cls, text: str) -> "RefSpec":
        """`"docs/x:a"` → RefSpec("docs/x", "a"); không có `:` → adapter `a`.
        Tên ref git không được chứa `:`, nên tách ở dấu `:` cuối là an toàn."""
        ref, sep, adapter = text.rpartition(":")
        if not sep:
            return cls(ref=text, adapter="a")
        if not ref or not adapter:
            raise ValueError(f"--ref không hợp lệ: {text!r}")
        if adapter not in ADAPTERS:
            raise ValueError(
                f"adapter không tồn tại: {adapter!r} (có: {', '.join(sorted(ADAPTERS))})"
            )
        return cls(ref=ref, adapter=adapter)


@dataclass
class RefRun:
    """Trạng thái một ref trong một lần chạy benchmark."""

    spec: RefSpec
    index: int
    port: int
    proxy_port: int
    ws: RefWorkspace | None = None
    proxy: FaultProxy | None = None
    status: str = "pending"
    error: str | None = None
    azure_deployment: str | None = None
    restarts: int = 0
    crash_logs: list[str] = dataclasses.field(default_factory=list)


# ---------------------------------------------------------------------------
# Tiện ích
# ---------------------------------------------------------------------------


def parse_fault(fault: str) -> tuple[str, int]:
    """R1: `"mode"` hoặc `"mode:count"` → (mode, count)."""
    mode, sep, count_text = fault.partition(":")
    mode = mode.strip()
    count = 1
    if sep:
        try:
            count = int(count_text)
        except ValueError as exc:
            raise ValueError(f"fault không hợp lệ: {fault!r}") from exc
        if count < 1:
            raise ValueError(f"fault không hợp lệ (count < 1): {fault!r}")
    if not mode or mode not in _FAULT_MODES:
        raise ValueError(f"fault không hợp lệ: {fault!r}")
    return mode, count


def _has_fault(scenario: Scenario) -> bool:
    return any(t.fault for t in scenario.turns)


def _llm_creds(settings: BenchSettings) -> bool:
    return bool(settings.foundry_api_key and settings.foundry_resource)


def _read_env_value(path: Path, key: str) -> str | None:
    """Đọc đúng một khoá từ file `.env` — không log, không trả gì khác."""
    try:
        return _parse_env_file(path).get(key) or None
    except OSError:
        return None


def _to_jsonable(obj: Any) -> Any:
    if dataclasses.is_dataclass(obj) and not isinstance(obj, type):
        return _to_jsonable(dataclasses.asdict(obj))
    if isinstance(obj, dict):
        return {str(k): _to_jsonable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple, set)):
        return [_to_jsonable(v) for v in obj]
    if isinstance(obj, (datetime, date)):
        return obj.isoformat()
    if isinstance(obj, Path):
        return str(obj)
    return obj


def secret_values(env_path: Path, settings: BenchSettings | None = None) -> list[str]:
    """Giá trị bí mật cần che khi ghi JSON: mọi `AZURE_OPENAI_*` (trừ tên
    deployment/model/version) và mọi khoá có KEY/SECRET/TOKEN/PASSWORD trong
    `.env` gốc, cộng khoá Foundry/Langfuse của benchmark."""
    values: list[str] = []
    try:
        env = _parse_env_file(env_path)
    except OSError:
        env = {}
    for key, value in env.items():
        upper = key.upper()
        if upper in _NON_SECRET_KEYS:
            continue
        if upper.startswith("AZURE_OPENAI_") or any(m in upper for m in _SECRET_MARKERS):
            values.append(value)
    if settings is not None:
        values += [settings.foundry_api_key or "", settings.langfuse_secret_key or ""]
    # Dài trước để giá trị chứa giá trị khác được che trọn.
    return sorted({v for v in values if len(v) >= _MIN_SECRET_LEN}, key=len, reverse=True)


def redact(obj: Any, secrets_: list[str]) -> Any:
    """Thay mọi chuỗi bí mật bằng `***` trong cây JSON (sau `_to_jsonable`)."""
    if not secrets_:
        return obj
    if isinstance(obj, str):
        for value in secrets_:
            if value in obj:
                obj = obj.replace(value, "***")
        return obj
    if isinstance(obj, dict):
        return {k: redact(v, secrets_) for k, v in obj.items()}
    if isinstance(obj, list):
        return [redact(v, secrets_) for v in obj]
    return obj


def _write_json(path: Path, data: Any, secrets_: list[str] | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    jsonable = redact(_to_jsonable(data), secrets_ or [])
    path.write_text(json.dumps(jsonable, ensure_ascii=False, indent=2), encoding="utf-8")


def _exc_text(exc: BaseException) -> str:
    text = f"{type(exc).__name__}: {exc}"
    if isinstance(exc, subprocess.CalledProcessError):
        extra = exc.stderr or exc.output
        if isinstance(extra, bytes):
            extra = extra.decode(errors="replace")
        if extra:
            text += "\n" + "\n".join(str(extra).splitlines()[-50:])
    return text


def clear_appointments(mongo_uri: str, db_name: str) -> None:
    """Xoá mọi document trong `appointments` của DB ref — chỉ collection này
    (khách luôn mới mỗi lần chạy, các collection khác để nguyên)."""
    client: MongoClient = MongoClient(mongo_uri)
    try:
        client[db_name]["appointments"].delete_many({})
    finally:
        client.close()


def _customer_text(turn: Turn) -> str:
    return turn.say if turn.say is not None else (turn.intent or "")


# ---------------------------------------------------------------------------
# Một lần chạy kịch bản
# ---------------------------------------------------------------------------


async def _seed(rr: RefRun, scenario: Scenario, settings: BenchSettings, now_vn: datetime):
    assert rr.ws is not None
    seeder = Seeder(rr.ws.base_url, settings.admin_phone, settings.admin_password)
    setup = scenario.setup
    await seeder.set_shop("08:00", "19:00", ())
    await seeder.set_busy(setup.owner_busy_minutes)
    customer = await seeder.new_customer(setup.customer_name)
    for spec in setup.own:
        await seeder.book_for(customer.id, resolve_at(spec, now_vn), spec.note)
    for i, spec in enumerate(setup.others):
        other = await seeder.new_customer(f"Khách khác {i + 1}")
        await seeder.book_for(other.id, resolve_at(spec, now_vn), spec.note)
    return customer


async def _play_turn(
    rr: RefRun,
    adapter,
    turn: Turn,
    index: int,
    settings: BenchSettings,
) -> tuple[TurnRecord, str | None]:
    """Gửi một lượt (bật/tắt fault trên proxy quanh lượt đó), chụp DB sau
    lượt. Trả (record, lý do dừng kịch bản hoặc None)."""
    assert rr.ws is not None
    stop: str | None = None
    fault_on = turn.fault is not None and rr.proxy is not None
    if fault_on:
        mode, count = parse_fault(turn.fault)  # type: ignore[arg-type]
        rr.proxy.set_fault(mode, count)  # type: ignore[union-attr]
    try:
        record = await adapter.send(turn, index, settings.turn_timeout_s)
    except Exception as exc:  # noqa: BLE001 — R9: lỗi adapter = lỗi lượt, dừng kịch bản
        record = TurnRecord(
            index=index,
            customer_text=_customer_text(turn),
            error=f"adapter_exception: {_exc_text(exc)}",
        )
        stop = f"adapter_exception@{index}"
    finally:
        if fault_on:
            rr.proxy.set_fault(None)  # type: ignore[union-attr]

    record.db_after = await asyncio.to_thread(snapshot, settings.mongo_uri, rr.ws.db_name)

    # R8 + R11: lỗi (timeout, error, rate_limited) ở lượt không khai báo fault
    # → dừng kịch bản ngay, lần chạy vẫn hợp lệ và score_run chấm trượt. Không
    # để lượt sau "biến" lỗi này thành tiền đề sai (không hợp lệ).
    if stop is None and record.error is not None and turn.fault is None:
        kind = "timeout" if record.error.startswith("timeout") else "error"
        stop = f"{kind}@{index}"
    return record, stop


async def run_one(
    rr: RefRun,
    scenario: Scenario,
    attempt: int,
    *,
    settings: BenchSettings,
    llm_client=None,
    judge_on: bool = False,
) -> dict:
    """Chạy MỘT kịch bản trên MỘT ref, trả payload JSON của lần chạy (chưa ghi
    file). `tokens` để `None` — runner điền sau khi Langfuse kịp ghi trace."""
    assert rr.ws is not None
    ws = rr.ws
    now_vn = datetime.now(VN_TZ)
    started_utc = datetime.now(timezone.utc)

    # Mọi lần chạy của một ref dùng chung một DB: xoá sạch lịch của lần chạy
    # trước (kể cả lịch ghi sai) để chúng không chiếm slot của lần này.
    await asyncio.to_thread(clear_appointments, settings.mongo_uri, ws.db_name)
    customer = await _seed(rr, scenario, settings, now_vn)
    before = await asyncio.to_thread(snapshot, settings.mongo_uri, ws.db_name)

    records: list[TurnRecord] = []
    stop_reason: str | None = None
    adapter = ADAPTERS[rr.spec.adapter]()
    try:
        try:
            await adapter.open(ws.base_url, customer)
        except Exception as exc:  # noqa: BLE001 — R9
            stop_reason = f"adapter_open_failed: {_exc_text(exc)}"

        if stop_reason is None and scenario.kind == "scripted":
            for i, turn in enumerate(scenario.turns):
                if turn.wait_s > 0:
                    await asyncio.sleep(turn.wait_s)
                prev = records[-1] if records else None
                if not check_precondition(turn, prev, now_vn):
                    stop_reason = f"precondition@{i}"
                    break
                record, stop = await _play_turn(rr, adapter, turn, i, settings)
                records.append(record)
                if stop:
                    stop_reason = stop
                    break
        elif stop_reason is None and scenario.kind == "simulated":
            assert scenario.sim is not None and llm_client is not None
            sim = SimCustomer(llm_client, settings.judge_model, scenario.sim)
            i = 0
            while True:
                msg = await sim.next_message(records)
                if msg is None:
                    break
                record, stop = await _play_turn(rr, adapter, Turn(say=msg), i, settings)
                records.append(record)
                i += 1
                if stop:
                    stop_reason = stop
                    break
    finally:
        try:
            await adapter.close()
        except Exception:  # noqa: BLE001
            pass

    after = await asyncio.to_thread(snapshot, settings.mongo_uri, ws.db_name)
    score: RunScore = score_run(scenario, records, before, after, customer.id, now_vn)
    # score_run chỉ thấy các lượt đã gửi; lượt có tiền đề sai không được gửi
    # nên tự đánh dấu không hợp lệ ở đây (cùng định dạng `precondition@<i>`).
    if stop_reason and stop_reason.startswith("precondition@") and score.valid:
        score = dataclasses.replace(
            score, valid=False, passed=False, invalid_reason=stop_reason
        )
    elif stop_reason and stop_reason.startswith("adapter_open_failed"):
        # Không mở được kết nối = không lượt nào chạy; kịch bản kiểu "từ chối"
        # (booked: []) sẽ khớp kỳ vọng một cách giả — ép trượt (vẫn hợp lệ).
        score = dataclasses.replace(score, passed=False)
    elif scenario.kind == "simulated" and not records:
        # Khách giả lập không nói câu nào (lỗi model / DONE ngay) — không đo được gì.
        score = dataclasses.replace(
            score, valid=False, passed=False, invalid_reason="sim_no_turns"
        )

    judge_result = None
    if judge_on and llm_client is not None and score.valid and records:
        judge_result = await judge(llm_client, settings.judge_model, render_transcript(records))

    return {
        "scenario_id": scenario.id,
        "kind": scenario.kind,
        "title": scenario.title,
        "ref": rr.spec.ref,
        "sha": ws.sha,
        "adapter": rr.spec.adapter,
        "attempt": attempt,
        "now_vn": now_vn,
        "started_at": started_utc,
        "customer_id": customer.id,
        "stop_reason": stop_reason,
        "turns": records,
        "before": before,
        "after": after,
        "score": score,
        "judge": judge_result,
        "tokens": None,
    }


# ---------------------------------------------------------------------------
# Dựng / dọn ref
# ---------------------------------------------------------------------------


async def _bring_up(rr: RefRun, settings: BenchSettings, upstream: str | None) -> None:
    """Proxy → prepare → ensure_admin → start. Mọi lỗi → failed_to_start (R6)."""
    assert rr.ws is not None
    try:
        if upstream is not None:
            rr.proxy = FaultProxy(upstream, rr.proxy_port)
            await rr.proxy.start()
            rr.ws.azure_endpoint = rr.proxy.url
        await rr.ws.prepare()
        rr.azure_deployment = _read_env_value(rr.ws.path / ".env", "AZURE_OPENAI_DEPLOYMENT")
        await ensure_admin(rr.ws)
    except Exception as exc:  # noqa: BLE001
        rr.status = "failed_to_start"
        rr.error = _exc_text(exc)
        return

    ok = await rr.ws.start()
    rr.status = "ready" if ok else "failed_to_start"
    rr.error = None if ok else (rr.ws.error or "start() trả False")


async def _tear_down(rr: RefRun, keep_db: bool) -> None:
    if rr.ws is not None:
        try:
            await rr.ws.stop()
        except Exception:  # noqa: BLE001
            pass
    if rr.proxy is not None:
        try:
            await rr.proxy.stop()
        except Exception:  # noqa: BLE001
            pass
    if rr.ws is not None and not keep_db:
        try:
            await rr.ws.drop_db()
        except Exception:  # noqa: BLE001
            pass
    if rr.ws is not None:
        try:
            rr.ws.release_lock()
        except Exception:  # noqa: BLE001
            pass


def _ref_meta(rr: RefRun) -> dict:
    return {
        "ref": rr.spec.ref,
        "adapter": rr.spec.adapter,
        "sha": rr.ws.sha if rr.ws else None,
        "sha7": rr.ws.sha7 if rr.ws else None,
        "port": rr.port,
        "status": rr.status,
        "error": rr.error,
        "azure_deployment": rr.azure_deployment,
        "restarts": rr.restarts,
        "crash_logs": rr.crash_logs,
    }


# ---------------------------------------------------------------------------
# Toàn bộ benchmark
# ---------------------------------------------------------------------------


async def run_bench(
    settings: BenchSettings,
    refs: list[RefSpec],
    scenario_paths: list[Path],
    tags: set[str] | None,
    k: int = 4,
    use_proxy: bool = True,
    judge_on: bool = True,
    keep_db: bool = False,
    resume_dir: Path | None = None,
) -> Path:
    """Chạy bộ kịch bản trên nhiều ref, trả thư mục run `results/<ts>-<runid>/`.

    `resume_dir`: chạy tiếp một run dở (I2) — dùng lại run_id/thư mục/meta cũ,
    bỏ qua mọi `(ref, kịch bản, attempt)` đã có file JSON. `k` phải trùng meta;
    nếu meta có danh sách kịch bản thì chỉ chạy các kịch bản trong đó."""
    old_meta: dict | None = None
    if resume_dir is not None:
        old_meta = json.loads((Path(resume_dir) / "meta.json").read_text(encoding="utf-8"))
        if int(old_meta.get("k") or 0) != k:
            raise ValueError(
                f"--resume: k={k} khác k={old_meta.get('k')} của run cũ — pass^k sẽ sai"
            )
    scenarios = load_scenarios(scenario_paths, tags)
    if old_meta is not None and old_meta.get("scenarios"):
        wanted = set(old_meta["scenarios"])
        scenarios = [sc for sc in scenarios if sc.id in wanted]
    for sc in scenarios:  # fault sai định dạng → báo ngay, trước khi dựng server
        for t in sc.turns:
            if t.fault:
                parse_fault(t.fault)

    now = datetime.now(VN_TZ)
    if old_meta is not None:
        run_id = str(old_meta["run_id"])
        run_dir = Path(resume_dir)  # type: ignore[arg-type]
        try:
            started = datetime.fromisoformat(str(old_meta["started_at"]))
        except (KeyError, ValueError):
            started = now
    else:
        run_id = secrets.token_hex(3)
        started = now
        run_dir = settings.results_dir / f"{started.strftime('%Y%m%d-%H%M%S')}-{run_id}"
    run_dir.mkdir(parents=True, exist_ok=True)

    root_env = settings.repo_root / ".env"
    endpoint = _read_env_value(root_env, "AZURE_OPENAI_ENDPOINT")
    notes: list[str] = []
    if use_proxy and endpoint is None:
        notes.append("proxy tắt: .env gốc không có AZURE_OPENAI_ENDPOINT")
        use_proxy = False
    upstream = endpoint if use_proxy else None

    creds = _llm_creds(settings)
    skipped: list[dict] = []
    runnable: list[Scenario] = []
    for sc in scenarios:
        if sc.kind == "simulated" and not creds:
            skipped.append({"scenario_id": sc.id, "reason": "no_llm_credentials"})
        elif _has_fault(sc) and not use_proxy:
            skipped.append({"scenario_id": sc.id, "reason": "no_proxy"})
        else:
            runnable.append(sc)

    ref_runs: list[RefRun] = []
    seen_sha: dict[str, str] = {}
    for i, spec in enumerate(refs):
        rr = RefRun(
            spec=spec,
            index=i,
            port=settings.base_port + 2 * i,
            proxy_port=settings.base_port + 2 * i + 1,
        )
        try:
            rr.ws = RefWorkspace(settings, spec.ref, rr.port, run_id, azure_endpoint=None)
        except Exception as exc:  # noqa: BLE001 — ref không resolve được
            rr.status = "failed_to_start"
            rr.error = _exc_text(exc)
        else:
            if rr.ws.sha in seen_sha:
                rr.status = "failed_to_start"
                rr.error = f"trùng commit với ref {seen_sha[rr.ws.sha]!r}"
                rr.ws = None
            else:
                seen_sha[rr.ws.sha] = spec.ref
        ref_runs.append(rr)

    meta: dict[str, Any] = {
        "run_id": run_id,
        "started_at": started,
        "finished_at": None,
        "vn_date_start": started.date(),
        "vn_date_end": None,
        "azure_deployment": _read_env_value(root_env, "AZURE_OPENAI_DEPLOYMENT"),
        "judge_model": settings.judge_model,
        "judge_on": judge_on and creds,
        "use_proxy": use_proxy,
        "k": k,
        "tags": sorted(tags) if tags else None,
        "scenarios": [sc.id for sc in scenarios],
        "refs": [],
        "skipped": skipped,
        "notes": notes,
        "rulings": [],
        "resumed_at": [],
    }
    if old_meta is not None:
        meta["vn_date_start"] = old_meta.get("vn_date_start", meta["vn_date_start"])
        meta["resumed_at"] = list(old_meta.get("resumed_at") or []) + [now]

    secrets_ = secret_values(root_env, settings)
    llm_client = make_client(settings) if creds else None
    pending_tokens: list[tuple[Path, str, datetime]] = []
    try:
        await asyncio.gather(
            *(_bring_up(rr, settings, upstream) for rr in ref_runs if rr.ws is not None)
        )
        meta["refs"] = [_ref_meta(rr) for rr in ref_runs]
        _write_json(run_dir / "meta.json", meta, secrets_)

        ready = [rr for rr in ref_runs if rr.status == "ready"]
        for attempt in range(k):
            # Xoay thứ tự ref mỗi attempt để không ref nào luôn chạy trước.
            shift = attempt % len(ready) if ready else 0
            order = ready[shift:] + ready[:shift]
            for sc in runnable:
                for rr in order:
                    if rr.status != "ready":
                        continue
                    assert rr.ws is not None
                    out = run_dir / "runs" / rr.ws.sha7 / sc.id / f"{attempt}.json"
                    if out.exists():  # --resume: đã chạy xong ở lần trước
                        continue
                    if not rr.ws.is_alive():
                        tail = rr.ws.log_tail()
                        rr.crash_logs.append(tail)
                        revived = False
                        if rr.restarts < _MAX_RESTARTS:
                            rr.restarts += 1
                            try:
                                await rr.ws.stop()
                                revived = await rr.ws.start()
                            except Exception as exc:  # noqa: BLE001
                                rr.crash_logs.append(_exc_text(exc))
                            if not revived and getattr(rr.ws, "error", None):
                                rr.crash_logs.append(str(rr.ws.error))
                        if not revived:
                            rr.status = "crashed"
                            rr.error = tail
                        meta["refs"] = [_ref_meta(r) for r in ref_runs]
                        _write_json(run_dir / "meta.json", meta, secrets_)
                        if not revived:
                            continue
                    try:
                        payload = await run_one(
                            rr,
                            sc,
                            attempt,
                            settings=settings,
                            llm_client=llm_client,
                            judge_on=judge_on,
                        )
                    except Exception as exc:  # noqa: BLE001 — một lần chạy hỏng không dừng cả bench
                        payload = {
                            "scenario_id": sc.id,
                            "kind": sc.kind,
                            "title": sc.title,
                            "ref": rr.spec.ref,
                            "sha": rr.ws.sha if rr.ws else None,
                            "adapter": rr.spec.adapter,
                            "attempt": attempt,
                            "error": _exc_text(exc),
                            "turns": [],
                            "score": None,
                            "judge": None,
                            "tokens": None,
                        }
                    _write_json(out, payload, secrets_)
                    if payload.get("customer_id") and payload.get("started_at"):
                        pending_tokens.append(
                            (out, payload["customer_id"], payload["started_at"])
                        )

        if langfuse_configured(settings) and pending_tokens:
            await _fill_tokens(settings, pending_tokens, secrets_)
    finally:
        await asyncio.gather(*(_tear_down(rr, keep_db) for rr in ref_runs))
        if llm_client is not None:
            try:
                await llm_client.close()
            except Exception:  # noqa: BLE001
                pass
        finished = datetime.now(VN_TZ)
        meta["finished_at"] = finished
        meta["vn_date_end"] = finished.date()
        meta["refs"] = [_ref_meta(rr) for rr in ref_runs]
        _write_json(run_dir / "meta.json", meta, secrets_)

    return run_dir


async def _fill_tokens(
    settings: BenchSettings,
    pending: list[tuple[Path, str, datetime]],
    secrets_: list[str],
) -> None:
    """Điền `tokens` vào file của từng lần chạy. Lần chạy chưa có trace
    (`fetch_tokens` → None) được thử lại tối đa `_TOKEN_RETRIES` lần, cách
    nhau `_TOKEN_RETRY_BACKOFF_S` giây."""
    await asyncio.sleep(_LANGFUSE_SETTLE_S)
    remaining = list(pending)
    for round_ in range(1 + _TOKEN_RETRIES):
        if round_:
            await asyncio.sleep(_TOKEN_RETRY_BACKOFF_S)
        still: list[tuple[Path, str, datetime]] = []
        for item in remaining:
            out, session_id, since = item
            tokens = await fetch_tokens(settings, session_id, since)
            if tokens is None:
                still.append(item)
                continue
            data = json.loads(out.read_text(encoding="utf-8"))
            data["tokens"] = tokens
            _write_json(out, data, secrets_)
        remaining = still
        if not remaining:
            break
