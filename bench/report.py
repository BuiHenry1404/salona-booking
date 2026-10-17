"""Báo cáo so sánh HTML giữa nhiều ref (hộp đen — không `import app`).

Đọc thư mục kết quả do `runner.py` ghi (`meta.json` +
`runs/<sha7>/<scenario_id>/<attempt>.json`), gộp điểm theo ref (R10 — trung
bình qua các kịch bản xảy ra ở ĐÂY, `aggregate()` chỉ gộp một
scenario×ref), và tạo một trang HTML tự chứa (CSS nhúng, không tải gì từ
mạng) để người đọc thấy ngay bản nào tốt hơn.

Không bao giờ ném lỗi vì thiếu khoá trong JSON đã ghi — một lần chạy với
`score: null` (lỗi khởi tạo / exception, có thể thiếu cả `now_vn`,
`customer_id`, `turns`) được coi là một lần chạy TRƯỢT, không phải dữ liệu
thiếu, và báo cáo vẫn phải render được.
"""

from __future__ import annotations

import html as _html
import json
import statistics
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from bench.adapters.base import TurnRecord
from bench.dbread import ApptRow, diff
from bench.judge import JUDGE_AXES
from bench.scenario import Scenario, load_scenarios
from bench.score import RunScore, aggregate, behavior_match

_TEMPLATE_PATH = Path(__file__).resolve().parent / "templates" / "report.html"
_DEFAULT_SCENARIOS_DIR = Path(__file__).resolve().parent / "scenarios"


def e(value: Any) -> str:
    """Escape bất kỳ giá trị nào thành chuỗi an toàn để chèn vào HTML.

    Mọi câu trả lời của bot (LLM) và câu khách trong kịch bản đi qua đây
    trước khi vào trang — không có ngoại lệ."""
    if value is None:
        return ""
    return _html.escape(str(value), quote=True)


def _label_time(dt: datetime) -> str:
    return f"{dt.strftime('%H:%M')} {dt.day}/{dt.month}"


# ---------------------------------------------------------------------------
# Đọc JSON → cấu trúc dữ liệu nhẹ (không tin tưởng khoá nào tồn tại)
# ---------------------------------------------------------------------------


def _load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _load_meta(run_dir: Path) -> dict:
    meta = dict(_load_json(run_dir / "meta.json"))
    meta["_run_dir"] = run_dir
    return meta


def _attempt_num(path: Path) -> int:
    try:
        return int(path.stem)
    except ValueError:
        return 0


def _iter_run_payloads(run_dir: Path, sha7: str) -> dict[str, list[dict]]:
    """`scenario_id -> [payload lần chạy 0, 1, ...]` cho một ref (`sha7`) của
    MỘT thư mục run. Thiếu thư mục/file lỗi → bỏ qua, không crash."""
    base = run_dir / "runs" / sha7
    result: dict[str, list[dict]] = {}
    if not base.is_dir():
        return result
    for scenario_dir in sorted(p for p in base.iterdir() if p.is_dir()):
        payloads: list[dict] = []
        for f in sorted(scenario_dir.glob("*.json"), key=_attempt_num):
            try:
                payloads.append(_load_json(f))
            except (OSError, ValueError):
                continue
        if payloads:
            result[scenario_dir.name] = payloads
    return result


def _appt_row_from_dict(d: dict) -> ApptRow:
    start_at = d.get("start_at")
    if isinstance(start_at, str):
        try:
            start_at = datetime.fromisoformat(start_at)
        except ValueError:
            start_at = None
    return ApptRow(
        id=str(d.get("id") or ""),
        user_id=str(d.get("user_id") or ""),
        start_at=start_at,
        status=str(d.get("status") or ""),
        note=d.get("note"),
    )


def _rows_from_list(data: Any) -> list[ApptRow] | None:
    if not isinstance(data, list):
        return None
    return [_appt_row_from_dict(r) for r in data if isinstance(r, dict)]


def _turn_record_from_dict(d: dict) -> TurnRecord:
    return TurnRecord(
        index=d.get("index", 0) or 0,
        customer_text=d.get("customer_text") or "",
        events=[],
        answer=d.get("answer") or "",
        tools=list(d.get("tools") or []),
        error=d.get("error"),
        first_token_ms=d.get("first_token_ms"),
        complete_ms=d.get("complete_ms"),
        db_after=_rows_from_list(d.get("db_after")),
    )


def _score_from_payload(payload: dict) -> RunScore:
    """`score: null` (lỗi khởi tạo/exception) → một `RunScore` TRƯỢT nhưng
    hợp lệ (tính vào mẫu số `pass_rate`, không bị loại như lần chạy không
    hợp lệ)."""
    raw = payload.get("score")
    if not isinstance(raw, dict):
        reason = payload.get("error") or "failed_run"
        return RunScore(
            passed=False,
            valid=True,
            invalid_reason=str(reason),
            wrong_writes=[],
            violations=[],
            turns_used=0,
        )
    return RunScore(
        passed=bool(raw.get("passed", False)),
        valid=bool(raw.get("valid", True)),
        invalid_reason=raw.get("invalid_reason"),
        wrong_writes=list(raw.get("wrong_writes") or []),
        violations=list(raw.get("violations") or []),
        turns_used=int(raw.get("turns_used") or 0),
    )


def _first_with_turns(payloads: list[dict]) -> dict | None:
    for p in payloads:
        if p.get("turns"):
            return p
    return payloads[0] if payloads else None


def _error_excerpt(error: str | None, max_lines: int = 12, max_chars: int = 1200) -> str:
    if not error:
        return ""
    lines = str(error).splitlines()
    excerpt = "\n".join(lines[-max_lines:])
    if len(excerpt) > max_chars:
        excerpt = excerpt[-max_chars:]
    return excerpt


# ---------------------------------------------------------------------------
# Cảnh báo (Review Focus #4)
# ---------------------------------------------------------------------------


def _time_bucket(hour: int) -> str:
    if hour < 12:
        return "sáng"
    if hour < 18:
        return "chiều"
    return "tối"


def warnings(metas: list[dict]) -> list[str]:
    """Cảnh báo khi so các run không cùng điều kiện: khác ngày VN (hoặc một
    run chạy qua nửa đêm), khác khung buổi trong ngày, khác `azure_deployment`
    hoặc `judge_model`."""
    out: list[str] = []
    dates: set[str] = set()
    crossed: list[str] = []
    buckets: set[str] = set()
    deployments: set[str] = set()
    judge_models: set[str] = set()

    for meta in metas:
        run_id = str(meta.get("run_id") or "?")
        d_start = meta.get("vn_date_start")
        d_end = meta.get("vn_date_end")
        if d_start:
            dates.add(str(d_start))
        if d_end:
            dates.add(str(d_end))
        if d_start and d_end and d_start != d_end:
            crossed.append(run_id)

        started = meta.get("started_at")
        if isinstance(started, str):
            try:
                buckets.add(_time_bucket(datetime.fromisoformat(started).hour))
            except ValueError:
                pass

        dep = meta.get("azure_deployment")
        if dep:
            deployments.add(str(dep))
        jm = meta.get("judge_model")
        if jm:
            judge_models.add(str(jm))

    if len(dates) > 1:
        out.append(f"Các run thuộc các ngày VN khác nhau: {', '.join(sorted(dates))}.")
    if crossed:
        out.append(f"Run chạy qua nửa đêm giờ VN (ngày bắt đầu khác ngày kết thúc): {', '.join(crossed)}.")
    if len(buckets) > 1:
        out.append(f"Các run chạy ở khung buổi khác nhau trong ngày: {', '.join(sorted(buckets))}.")
    if len(deployments) > 1:
        out.append(f"Các run dùng azure_deployment khác nhau: {', '.join(sorted(deployments))}.")
    if len(judge_models) > 1:
        out.append(f"Các run dùng judge_model khác nhau: {', '.join(sorted(judge_models))}.")
    return out


# ---------------------------------------------------------------------------
# Kịch bản: scripted hay simulated
# ---------------------------------------------------------------------------


def _load_scenario_defs() -> dict[str, Scenario]:
    """Nạp định nghĩa kịch bản thật từ `bench/scenarios/` nếu có — dự phòng
    hạng hai khi payload lần chạy chưa có `kind`/`title` (thư mục run cũ).
    Không có thư mục, hoặc lỗi nạp bất kỳ → trả `{}`."""
    try:
        if not _DEFAULT_SCENARIOS_DIR.is_dir():
            return {}
        paths = sorted(_DEFAULT_SCENARIOS_DIR.rglob("*.yaml")) + sorted(
            _DEFAULT_SCENARIOS_DIR.rglob("*.yml")
        )
        if not paths:
            return {}
        return {sc.id: sc for sc in load_scenarios(paths)}
    except Exception:  # noqa: BLE001 — báo cáo không được vỡ vì thiếu/lỗi kịch bản
        return {}


def _scenario_kind_and_title(
    scenario_id: str, defs: dict[str, Scenario], payloads_by_ref: dict[str, list[dict]]
) -> tuple[str, str]:
    """Nguồn sự thật theo thứ tự ưu tiên: (1) `kind`/`title` runner đã ghi
    thẳng vào payload (Task 10 fix round 1 — không đoán nữa khi có sẵn);
    (2) định nghĩa kịch bản thật nạp từ `bench/scenarios/`; (3) suy luận từ
    dữ liệu, chỉ để tương thích ngược với thư mục run cũ chưa có `kind`."""
    for payloads in payloads_by_ref.values():
        for p in payloads:
            kind = p.get("kind")
            if kind in ("scripted", "simulated"):
                return kind, (p.get("title") or scenario_id)

    if scenario_id in defs:
        return defs[scenario_id].kind, defs[scenario_id].title

    return _infer_scenario_kind(payloads_by_ref), scenario_id


def _infer_scenario_kind(payloads_by_ref: dict[str, list[dict]]) -> str:
    # Suy luận (chỉ dùng cho thư mục run cũ, từ trước khi runner ghi `kind`):
    # kịch bản scripted phát lại đúng một kịch bản chữ cố định, nên mọi lần
    # chạy (mọi ref, mọi attempt) có câu khách là tiền tố của cùng một chuỗi
    # dài nhất (ngắn hơn khi dừng sớm vì lỗi/fault). Simulated do model sinh
    # câu khách nên chuỗi khác nhau thật sự giữa các lần chạy.
    sequences: list[tuple[str, ...]] = []
    for payloads in payloads_by_ref.values():
        for p in payloads:
            texts = tuple(t.get("customer_text") or "" for t in (p.get("turns") or []))
            if texts:
                sequences.append(texts)
    if not sequences:
        return "scripted"
    longest = max(sequences, key=len)
    if all(longest[: len(seq)] == seq for seq in sequences):
        return "scripted"
    return "simulated"


# ---------------------------------------------------------------------------
# Tổng hợp theo ref
# ---------------------------------------------------------------------------


@dataclass
class RefSummary:
    ref: str
    status: str
    error: str | None
    pass_rate_avg: float | None
    pass_rate_n: int
    pass_hat_k_avg: float | None
    pass_hat_k_n: int
    wrong_writes_total: int
    violations_by_rule: dict[str, int]
    first_token_median: float | None
    first_token_p90: float | None
    complete_median: float | None
    complete_p90: float | None
    tokens_mean: dict[str, float] | None
    judge_mean: dict[str, float] = field(default_factory=dict)
    scenario_aggregates: dict[str, dict] = field(default_factory=dict)


def _percentile(values: list[float], pct: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    pos = (len(ordered) - 1) * pct
    lo = int(pos)
    hi = min(lo + 1, len(ordered) - 1)
    if lo == hi:
        return ordered[lo]
    return ordered[lo] + (ordered[hi] - ordered[lo]) * (pos - lo)


def _build_ref_summary(
    ref: str,
    status: str,
    error: str | None,
    scenario_payloads: dict[str, list[dict]],
    k: int,
) -> RefSummary:
    pass_rates: list[float] = []
    hat_ks: list[float] = []
    wrong_writes_total = 0
    violations_by_rule: dict[str, int] = {}
    scenario_aggs: dict[str, dict] = {}
    first_tokens: list[float] = []
    completes: list[float] = []
    tokens_list: list[dict] = []
    judge_axis_values: dict[str, list[float]] = {axis: [] for axis in JUDGE_AXES}

    for scenario_id, payloads in scenario_payloads.items():
        scores = [_score_from_payload(p) for p in payloads]
        agg = aggregate(scores, k)
        agg["wrong_writes_list"] = [w for s in scores for w in s.wrong_writes]
        scenario_aggs[scenario_id] = agg
        if agg["pass_rate"] is not None:
            pass_rates.append(agg["pass_rate"])
        if agg["pass_hat_k_rate"] is not None:
            hat_ks.append(agg["pass_hat_k_rate"])
        wrong_writes_total += agg["wrong_writes_total"]
        for rule, count in agg["violations_by_rule"].items():
            violations_by_rule[rule] = violations_by_rule.get(rule, 0) + count

        for p in payloads:
            for t in p.get("turns") or []:
                if isinstance(t, dict):
                    if isinstance(t.get("first_token_ms"), (int, float)):
                        first_tokens.append(t["first_token_ms"])
                    if isinstance(t.get("complete_ms"), (int, float)):
                        completes.append(t["complete_ms"])
            tok = p.get("tokens")
            if isinstance(tok, dict):
                tokens_list.append(tok)
            jg = p.get("judge")
            if isinstance(jg, dict):
                for axis in JUDGE_AXES:
                    item = jg.get(axis)
                    if isinstance(item, dict) and isinstance(item.get("score"), (int, float)):
                        judge_axis_values[axis].append(item["score"])

    tokens_mean = None
    if tokens_list:
        tokens_mean = {
            key: sum((t.get(key) or 0) for t in tokens_list) / len(tokens_list)
            for key in ("input", "output", "total")
        }
    judge_mean = {axis: sum(vals) / len(vals) for axis, vals in judge_axis_values.items() if vals}

    return RefSummary(
        ref=ref,
        status=status,
        error=error,
        pass_rate_avg=(sum(pass_rates) / len(pass_rates)) if pass_rates else None,
        pass_rate_n=len(pass_rates),
        pass_hat_k_avg=(sum(hat_ks) / len(hat_ks)) if hat_ks else None,
        pass_hat_k_n=len(hat_ks),
        wrong_writes_total=wrong_writes_total,
        violations_by_rule=violations_by_rule,
        first_token_median=(statistics.median(first_tokens) if first_tokens else None),
        first_token_p90=_percentile(first_tokens, 0.9),
        complete_median=(statistics.median(completes) if completes else None),
        complete_p90=_percentile(completes, 0.9),
        tokens_mean=tokens_mean,
        judge_mean=judge_mean,
        scenario_aggregates=scenario_aggs,
    )


def _worse_flags(summaries: dict[str, RefSummary], base_ref: str | None) -> dict[str, dict[str, bool]]:
    """`ref -> {metric: True nếu tệ hơn base}` — chỉ so các ref khác `base_ref`,
    chỉ đánh dấu khi cả hai giá trị đều có (không None)."""
    flags: dict[str, dict[str, bool]] = {}
    if not base_ref or base_ref not in summaries:
        return flags
    base = summaries[base_ref]
    for ref, s in summaries.items():
        if ref == base_ref:
            continue
        f: dict[str, bool] = {}
        if s.pass_rate_avg is not None and base.pass_rate_avg is not None:
            f["pass_rate"] = s.pass_rate_avg < base.pass_rate_avg
        if s.pass_hat_k_avg is not None and base.pass_hat_k_avg is not None:
            f["pass_hat_k"] = s.pass_hat_k_avg < base.pass_hat_k_avg
        f["wrong_writes"] = s.wrong_writes_total > base.wrong_writes_total
        f["violations_total"] = sum(s.violations_by_rule.values()) > sum(
            base.violations_by_rule.values()
        )
        if s.first_token_median is not None and base.first_token_median is not None:
            f["first_token_median"] = s.first_token_median > base.first_token_median
        if s.first_token_p90 is not None and base.first_token_p90 is not None:
            f["first_token_p90"] = s.first_token_p90 > base.first_token_p90
        if s.complete_median is not None and base.complete_median is not None:
            f["complete_median"] = s.complete_median > base.complete_median
        if s.complete_p90 is not None and base.complete_p90 is not None:
            f["complete_p90"] = s.complete_p90 > base.complete_p90
        for axis in JUDGE_AXES:
            if axis in s.judge_mean and axis in base.judge_mean:
                f[f"judge_{axis}"] = s.judge_mean[axis] < base.judge_mean[axis]
        flags[ref] = f
    return flags


# ---------------------------------------------------------------------------
# Rendering HTML (chuỗi — không phụ thuộc Jinja, repo không cài)
# ---------------------------------------------------------------------------


def _fmt_num(value: float | None, digits: int = 0) -> str:
    if value is None:
        return "—"
    return f"{value:.{digits}f}"


def _fmt_pct(value: float | None) -> str:
    if value is None:
        return "—"
    return f"{value * 100:.0f}%"


def _render_warnings_html(items: list[str]) -> str:
    if not items:
        return ""
    lis = "".join(f"<li>{e(w)}</li>" for w in items)
    return f'<div class="warnings"><strong>Cảnh báo</strong><ul>{lis}</ul></div>'


def _render_summary_table(
    ref_order: list[str],
    ref_meta: dict[str, dict],
    summaries: dict[str, RefSummary],
    worse: dict[str, dict[str, bool]],
    base_ref: str | None,
) -> str:
    def cell(ref: str, metric: str, text: str) -> str:
        cls = "worse" if worse.get(ref, {}).get(metric) else ""
        cls_attr = f' class="{cls}"' if cls else ""
        return f"<td{cls_attr}>{text}</td>"

    rows: list[str] = []

    def row(label: str, metric: str, render) -> str:
        cells = "".join(cell(ref, metric, render(summaries[ref])) for ref in ref_order)
        return f"<tr><th>{e(label)}</th>{cells}</tr>"

    rows.append(row("pass rate (TB qua kịch bản, n)", "pass_rate", lambda s: f"{_fmt_pct(s.pass_rate_avg)} (n={s.pass_rate_n})"))
    rows.append(row("pass^k (TB qua kịch bản, n)", "pass_hat_k", lambda s: f"{_fmt_pct(s.pass_hat_k_avg)} (n={s.pass_hat_k_n})"))

    def wrong_writes_cell(s: RefSummary) -> str:
        if s.wrong_writes_total > 0:
            return f'<span class="bad">{s.wrong_writes_total}</span>'
        return "0"

    ww_cells = "".join(cell(ref, "wrong_writes", wrong_writes_cell(summaries[ref])) for ref in ref_order)
    rows.append(f"<tr><th>Ghi sai (tổng)</th>{ww_cells}</tr>")

    def violations_cell(s: RefSummary) -> str:
        total = sum(s.violations_by_rule.values())
        if not s.violations_by_rule:
            return "0"
        by_rule = "<br/>".join(f"{e(rule)}: {count}" for rule, count in sorted(s.violations_by_rule.items()))
        return f"{total}<br/>{by_rule}"

    v_cells = "".join(
        cell(ref, "violations_total", violations_cell(summaries[ref])) for ref in ref_order
    )
    rows.append(f"<tr><th>Vi phạm theo luật (tổng + chi tiết)</th>{v_cells}</tr>")

    rows.append(row("first_token_ms trung vị", "first_token_median", lambda s: _fmt_num(s.first_token_median)))
    rows.append(row("first_token_ms p90", "first_token_p90", lambda s: _fmt_num(s.first_token_p90)))
    rows.append(row("complete_ms trung vị", "complete_median", lambda s: _fmt_num(s.complete_median)))
    rows.append(row("complete_ms p90", "complete_p90", lambda s: _fmt_num(s.complete_p90)))

    def tokens_cell(s: RefSummary) -> str:
        if not s.tokens_mean:
            return ""
        return (
            f"in {_fmt_num(s.tokens_mean.get('input'))} / "
            f"out {_fmt_num(s.tokens_mean.get('output'))} / "
            f"tổng {_fmt_num(s.tokens_mean.get('total'))}"
        )

    tok_cells = "".join(f"<td>{tokens_cell(summaries[ref])}</td>" for ref in ref_order)
    rows.append(f"<tr><th>Token trung bình</th>{tok_cells}</tr>")

    for axis in JUDGE_AXES:
        metric = f"judge_{axis}"

        def judge_render(s: RefSummary, axis=axis) -> str:
            v = s.judge_mean.get(axis)
            return "—" if v is None else f"{v:.2f}"

        rows.append(row(f"Judge — {axis}", metric, judge_render))

    header_cells = "".join(f"<th>{e(ref)}{' (base)' if ref == base_ref else ''}</th>" for ref in ref_order)
    status_cells = "".join(
        f'<td class="{"ref-status-ready" if ref_meta[ref]["status"] == "ready" else "ref-status-bad"}">'
        f'{e(ref_meta[ref]["status"])}</td>'
        for ref in ref_order
    )
    error_rows = ""
    for ref in ref_order:
        err = ref_meta[ref].get("error")
        if err:
            error_rows += f'<tr><th>Lỗi — {e(ref)}</th><td colspan="{len(ref_order)}"><pre class="err">{e(_error_excerpt(err))}</pre></td></tr>'

    return (
        "<h2>Tổng hợp theo ref</h2>"
        f'<table><tr><th>Chỉ số</th>{header_cells}</tr>'
        f"<tr><th>Trạng thái</th>{status_cells}</tr>"
        + "".join(rows)
        + error_rows
        + "</table>"
    )


def _render_scenario_table(
    ref_order: list[str], scenario_ids: list[str], summaries: dict[str, RefSummary]
) -> str:
    header_cells = "".join(f"<th>{e(ref)}</th>" for ref in ref_order)
    rows = []
    for sid in scenario_ids:
        cells = []
        for ref in ref_order:
            agg = summaries[ref].scenario_aggregates.get(sid)
            if agg is None:
                cells.append("<td>—</td>")
                continue
            hat_k = agg.get("pass_hat_k")
            hat_k_text = "—" if hat_k is None else ("đạt k/k" if hat_k else "không đạt k/k")
            runs = agg.get("runs", 0)
            valid_runs = agg.get("valid_runs", 0)
            invalid = runs - valid_runs
            ww = agg.get("wrong_writes_total", 0)
            ww_html = f'<span class="bad">{ww}</span>' if ww > 0 else "0"
            wrong_list = agg.get("wrong_writes_list") or []
            ww_detail = (
                f'<br/><span class="tag">{"; ".join(e(w) for w in wrong_list)}</span>'
                if wrong_list
                else ""
            )
            cells.append(
                f"<td>{hat_k_text}<br/>không hợp lệ: {invalid}<br/>ghi sai: {ww_html}{ww_detail}</td>"
            )
        rows.append(f"<tr><th>{e(sid)}</th>{''.join(cells)}</tr>")
    return (
        "<h2>Theo kịch bản</h2>"
        f'<table><tr><th>Kịch bản</th>{header_cells}</tr>' + "".join(rows) + "</table>"
    )


def _turn_db_change(prev_db: list[ApptRow] | None, record: TurnRecord) -> str:
    if record.db_after is None:
        return "—"
    changes = diff(prev_db or [], record.db_after)
    parts: list[str] = []
    for row in changes.created:
        parts.append(f"tạo: {_label_time(row.start_at)}" if row.start_at else "tạo: ?")
    for row in changes.cancelled:
        parts.append(f"huỷ: {_label_time(row.start_at)}" if row.start_at else "huỷ: ?")
    for before_row, after_row in changes.other:
        b = _label_time(before_row.start_at) if before_row.start_at else "?"
        a = _label_time(after_row.start_at) if after_row.start_at else "?"
        parts.append(f"dời: {b} → {a}")
    return "; ".join(parts) if parts else "—"


def _render_scripted_scenario(
    sid: str,
    title: str,
    ref_order: list[str],
    payloads_by_ref: dict[str, list[dict]],
    base_ref: str | None,
) -> str:
    chosen: dict[str, dict | None] = {ref: _first_with_turns(payloads_by_ref.get(ref, [])) for ref in ref_order}
    active_refs = [ref for ref in ref_order if chosen.get(ref)]
    if not active_refs:
        return ""

    turns_by_ref: dict[str, list[dict]] = {ref: (chosen[ref].get("turns") or []) for ref in active_refs}
    before_by_ref: dict[str, list[ApptRow]] = {
        ref: (_rows_from_list(chosen[ref].get("before")) or []) for ref in active_refs
    }
    max_turns = max((len(t) for t in turns_by_ref.values()), default=0)

    match_by_ref: dict[str, list[bool]] = {}
    if base_ref and base_ref in turns_by_ref:
        base_records = [_turn_record_from_dict(t) for t in turns_by_ref[base_ref]]
        for ref in active_refs:
            if ref == base_ref:
                continue
            cand_records = [_turn_record_from_dict(t) for t in turns_by_ref[ref]]
            match_by_ref[ref] = behavior_match(base_records, cand_records, before=before_by_ref[base_ref])

    header_cells = "".join(f"<th>{e(ref)}</th>" for ref in active_refs)
    rows: list[str] = []
    for i in range(max_turns):
        mismatch = any(
            ref != base_ref and i < len(match_by_ref.get(ref, [])) and not match_by_ref[ref][i]
            for ref in active_refs
        )
        cells = []
        for ref in active_refs:
            turns = turns_by_ref[ref]
            if i >= len(turns):
                cells.append("<td class=\"muted\">—</td>")
                continue
            t = turns[i]
            record = _turn_record_from_dict(t)
            prev_db = before_by_ref[ref] if i == 0 else _rows_from_list(turns[i - 1].get("db_after"))
            db_change = _turn_db_change(prev_db, record)
            tools = ", ".join(record.tools) if record.tools else "—"
            err = f'<br/><span class="bad">lỗi: {e(record.error)}</span>' if record.error else ""
            cells.append(
                "<td>"
                f"<div><strong>Khách:</strong> {e(record.customer_text)}</div>"
                f"<div><strong>Bot:</strong> {e(record.answer)}</div>"
                f'<div class="tag">tools: {e(tools)}</div>'
                f'<div class="tag">DB: {e(db_change)}</div>'
                f"{err}"
                "</td>"
            )
        row_cls = ' class="mismatch"' if mismatch else ""
        rows.append(f"<tr{row_cls}><th>Lượt {i}</th>{''.join(cells)}</tr>")

    return (
        f'<div class="scenario-block"><h3>{e(sid)} — {e(title)} (scripted)</h3>'
        f'<table><tr><th>#</th>{header_cells}</tr>' + "".join(rows) + "</table></div>"
    )


def _render_simulated_scenario(
    sid: str, title: str, ref_order: list[str], payloads_by_ref: dict[str, list[dict]]
) -> str:
    cols = []
    any_data = False
    for ref in ref_order:
        payloads = payloads_by_ref.get(ref, [])
        payload = payloads[0] if payloads else None
        turns = (payload or {}).get("turns") or []
        if turns:
            any_data = True
        turn_html = []
        for t in turns:
            record = _turn_record_from_dict(t)
            tools = ", ".join(record.tools) if record.tools else "—"
            err = f'<div class="bad">lỗi: {e(record.error)}</div>' if record.error else ""
            turn_html.append(
                '<div class="sim-turn">'
                f"<div><strong>Khách:</strong> {e(record.customer_text)}</div>"
                f"<div><strong>Bot:</strong> {e(record.answer)}</div>"
                f'<div class="tag">tools: {e(tools)}</div>'
                f"{err}"
                "</div>"
            )
        cols.append(f'<div class="sim-col"><h4>{e(ref)}</h4>' + "".join(turn_html) + "</div>")

    if not any_data:
        return ""
    return (
        f'<div class="scenario-block"><h3>{e(sid)} — {e(title)} (simulated)</h3>'
        f'<div class="sim-columns">{"".join(cols)}</div></div>'
    )


# ---------------------------------------------------------------------------
# Lắp ráp
# ---------------------------------------------------------------------------


def build_report(run_dirs: list[Path], base_ref: str | None = None) -> str:
    metas = [_load_meta(Path(rd)) for rd in run_dirs]
    warn_list = warnings(metas)
    defs = _load_scenario_defs()

    ref_order: list[str] = []
    ref_meta: dict[str, dict] = {}
    ref_scenario_payloads: dict[str, dict[str, list[dict]]] = {}

    for meta in metas:
        k = int(meta.get("k") or 4)
        run_dir: Path = meta["_run_dir"]
        for rm in meta.get("refs") or []:
            ref = rm.get("ref")
            if not ref:
                continue
            if ref not in ref_meta:
                ref_order.append(ref)
                ref_meta[ref] = {
                    "status": rm.get("status") or "unknown",
                    "error": rm.get("error"),
                    "k": k,
                }
                ref_scenario_payloads[ref] = {}
            elif ref_meta[ref]["status"] == "ready" and rm.get("status") != "ready":
                ref_meta[ref]["status"] = rm.get("status") or ref_meta[ref]["status"]
                ref_meta[ref]["error"] = rm.get("error")

            sha7 = rm.get("sha7")
            if rm.get("status") == "ready" and sha7:
                for sid, payloads in _iter_run_payloads(run_dir, sha7).items():
                    ref_scenario_payloads[ref].setdefault(sid, []).extend(payloads)

    if base_ref and base_ref in ref_order:
        ref_order = [base_ref] + [r for r in ref_order if r != base_ref]

    scenario_ids: list[str] = []
    seen_sids: set[str] = set()
    for meta in metas:
        for sid in meta.get("scenarios") or []:
            if sid not in seen_sids:
                seen_sids.add(sid)
                scenario_ids.append(sid)
    for ref in ref_order:
        for sid in ref_scenario_payloads.get(ref, {}):
            if sid not in seen_sids:
                seen_sids.add(sid)
                scenario_ids.append(sid)

    kinds: dict[str, str] = {}
    titles: dict[str, str] = {}
    for sid in scenario_ids:
        kind, title = _scenario_kind_and_title(
            sid, defs, {ref: ref_scenario_payloads[ref].get(sid, []) for ref in ref_order}
        )
        kinds[sid] = kind
        titles[sid] = title

    summaries = {
        ref: _build_ref_summary(
            ref,
            ref_meta[ref]["status"],
            ref_meta[ref]["error"],
            ref_scenario_payloads[ref],
            ref_meta[ref]["k"],
        )
        for ref in ref_order
    }

    worse = _worse_flags(summaries, base_ref)

    body: list[str] = [_render_warnings_html(warn_list)]
    body.append(_render_summary_table(ref_order, ref_meta, summaries, worse, base_ref))
    body.append(_render_scenario_table(ref_order, scenario_ids, summaries))

    for sid in scenario_ids:
        title = titles[sid]
        payloads_by_ref = {ref: ref_scenario_payloads[ref].get(sid, []) for ref in ref_order}
        if not any(payloads_by_ref.values()):
            continue
        if kinds[sid] == "scripted":
            block = _render_scripted_scenario(sid, title, ref_order, payloads_by_ref, base_ref)
        else:
            block = _render_simulated_scenario(sid, title, ref_order, payloads_by_ref)
        if block:
            body.append(block)

    template = _TEMPLATE_PATH.read_text(encoding="utf-8")
    generated_at = datetime.now().isoformat(timespec="seconds")
    return (
        template.replace("{{TITLE}}", e("Báo cáo so sánh benchmark"))
        .replace("{{GENERATED_AT}}", e(generated_at))
        .replace("{{BODY}}", "\n".join(body))
    )
