"""Đếm token LLM của một lần chạy kịch bản từ Langfuse (tuỳ chọn).

Hộp đen — không `import app`. App gắn `langfuse_session_id = user_id` cho mọi
trace của một khách (xem spec mục "Chi phí"), và mỗi lần chạy dùng một khách
mới, nên `session_id` = id khách của lần chạy đó.

Dùng Langfuse public API (basic auth public/secret key):
- `GET /api/public/traces?sessionId=…&fromTimestamp=…` lấy các trace,
- `GET /api/public/observations?traceId=…&type=GENERATION` lấy từng lần gọi
  LLM để cộng `usage` input/output.

Không cấu hình Langfuse, hoặc bất kỳ lỗi nào (mạng, auth, JSON lạ) → `None`
— chỉ số chi phí để trống, không làm hỏng lần chạy.
"""

from __future__ import annotations

from datetime import datetime, timezone

import httpx

from bench.config import BenchSettings

_PAGE_LIMIT = 100
_MAX_PAGES = 50


def langfuse_configured(settings: BenchSettings) -> bool:
    return bool(
        settings.langfuse_host
        and settings.langfuse_public_key
        and settings.langfuse_secret_key
    )


def _iso_utc(dt: datetime) -> str:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _usage_pair(obs: dict) -> tuple[int, int]:
    """(input, output) của một generation — chấp nhận cả `usageDetails` (v3)
    lẫn `usage` (v2, khoá `input`/`output` hoặc `promptTokens`/`completionTokens`)."""
    details = obs.get("usageDetails")
    if isinstance(details, dict) and details:
        return int(details.get("input") or 0), int(details.get("output") or 0)
    usage = obs.get("usage")
    if isinstance(usage, dict):
        inp = usage.get("input", usage.get("promptTokens")) or 0
        out = usage.get("output", usage.get("completionTokens")) or 0
        return int(inp), int(out)
    return 0, 0


async def _get_pages(client: httpx.AsyncClient, path: str, params: dict) -> list[dict]:
    items: list[dict] = []
    page = 1
    while page <= _MAX_PAGES:
        resp = await client.get(path, params={**params, "page": page, "limit": _PAGE_LIMIT})
        resp.raise_for_status()
        body = resp.json()
        items.extend(body.get("data") or [])
        total_pages = (body.get("meta") or {}).get("totalPages") or 1
        if page >= total_pages:
            break
        page += 1
    return items


async def fetch_tokens(
    settings: BenchSettings, session_id: str, since: datetime
) -> dict | None:
    """Tổng token của mọi generation trong session `session_id` từ `since`.

    Trả `{"input", "output", "total", "generations", "traces"}` hoặc `None`
    nếu Langfuse không cấu hình / lỗi / chưa có trace nào.
    """
    if not langfuse_configured(settings):
        return None

    auth = (settings.langfuse_public_key or "", settings.langfuse_secret_key or "")
    try:
        async with httpx.AsyncClient(
            base_url=(settings.langfuse_host or "").rstrip("/"), auth=auth, timeout=30.0
        ) as client:
            traces = await _get_pages(
                client,
                "/api/public/traces",
                {"sessionId": session_id, "fromTimestamp": _iso_utc(since)},
            )
            total_in = total_out = generations = 0
            for trace in traces:
                trace_id = trace.get("id")
                if not trace_id:
                    continue
                observations = await _get_pages(
                    client,
                    "/api/public/observations",
                    {"traceId": trace_id, "type": "GENERATION"},
                )
                for obs in observations:
                    if obs.get("type", "GENERATION") != "GENERATION":
                        continue
                    inp, out = _usage_pair(obs)
                    total_in += inp
                    total_out += out
                    generations += 1
    except (httpx.HTTPError, ValueError, TypeError, AttributeError):
        return None

    if not traces:
        # Chưa có trace nào (Langfuse chưa kịp ghi, hoặc tracing tắt) — không
        # trả 0 giả; runner thử lại sau.
        return None

    return {
        "input": total_in,
        "output": total_out,
        "total": total_in + total_out,
        "generations": generations,
        "traces": len(traces),
    }
