"""Agent Đặt lịch (spec §3.1, §4.2, §5.1): `APPOINTMENT` cho
`dispatch.run_subagent`, và `extract_appointment` — CODE trích dữ kiện của
lượt và bộ nhớ phiên từ ToolMessage, không bao giờ từ chữ model.

`ProposalFact` chỉ khi đề xuất có thật trong DB (`ProposalService.get`), còn
`pending`, đúng loại và đúng giờ — ToolMessage nói gì cũng phải khớp DB.
"""
from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Optional

from langchain_core.messages import BaseMessage, ToolMessage

from app.agents.salon.shared.schemas import Moment, ProposalFact, TimeFact, TimeRole, TurnFact
from app.agents.salon.subagents.appointment.prompt import APPOINTMENT_PROMPT
from app.agents.salon.subagents.appointment.tools import make_appointment_tools
from app.agents.salon.subagents.dispatch import Extracted, RunContext, SubagentSpec
from app.core.clock import to_local
from app.core.vi_format import format_vi_datetime
from app.services.proposal import ProposalService


def _load(content: Any) -> Optional[dict]:
    if not isinstance(content, str):
        return None
    try:
        data = json.loads(content)
    except ValueError:
        return None
    return data if isinstance(data, dict) else None


def _moment_of(value: Any) -> Optional[Moment]:
    """Một `Moment` hợp lệ (iso đọc được, có múi giờ) hoặc None."""
    if not isinstance(value, dict):
        return None
    iso, spoken = value.get("iso"), value.get("spoken")
    if not isinstance(iso, str) or not isinstance(spoken, str):
        return None
    try:
        if datetime.fromisoformat(iso).tzinfo is None:
            return None
    except ValueError:
        return None
    return Moment(iso=iso, spoken=spoken)


def _instant(moment: Moment) -> datetime:
    return datetime.fromisoformat(moment.iso)


def _db_moment(dt: datetime) -> Moment:
    return Moment(iso=to_local(dt).isoformat(), spoken=format_vi_datetime(dt))


def _dedupe(moments: list[Moment]) -> list[Moment]:
    seen: set[datetime] = set()
    out = []
    for moment in moments:
        if _instant(moment) not in seen:
            seen.add(_instant(moment))
            out.append(moment)
    return out


async def _proposal_fact(proposals: ProposalService, ctx: RunContext, data: dict,
                         at: Optional[Moment]) -> Optional[ProposalFact]:
    proposal = await proposals.get(ctx.user, str(data.get("proposal_id")))
    if (proposal is None or at is None or proposal.status != "pending"
            or proposal.kind != data.get("kind") or proposal.start_at != _instant(at)):
        return None
    return ProposalFact(proposal_id=str(proposal.id), proposal_kind=proposal.kind,
                        at=_db_moment(proposal.start_at), service_ids=list(proposal.service_ids))


class _Collector:
    """Gom dữ kiện theo thứ tự xuất hiện, bỏ trùng."""

    def __init__(self) -> None:
        self.facts: list[TurnFact] = []
        self._keys: set[str] = set()

    def add(self, fact: TurnFact) -> None:
        key = fact.model_dump_json()
        if key not in self._keys:
            self._keys.add(key)
            self.facts.append(fact)

    def time(self, role: TimeRole, moment: Optional[Moment]) -> None:
        if moment is not None:
            self.add(TimeFact(role=role, moment=moment))


async def extract_appointment(messages: list[BaseMessage], ctx: RunContext) -> Extracted:
    collector = _Collector()
    proposals = ProposalService(ctx.db)
    prior = ctx.session.memory if ctx.session is not None else {}

    parsed: list[Moment] = []          # mọi giờ parse_time đọc ra
    checked: set[datetime] = set()     # giờ đã đem đi kiểm (việc đang làm)
    excluded: set[datetime] = {datetime.fromisoformat(a.at.iso) for a in ctx.snapshot.upcoming}
    proposed: set[datetime] = set()
    offered: list[Moment] = []
    taken: list[Moment] = []
    day: Optional[str] = None
    created = False

    for message in messages:
        if not isinstance(message, ToolMessage):
            continue
        data = _load(message.content)
        if data is None:
            continue
        name = message.name

        if name == "parse_time":
            moment = _moment_of(data)
            if moment is not None:
                collector.time("asked", moment)
                parsed.append(moment)
                day = to_local(_instant(moment)).date().isoformat()
            elif isinstance(data.get("day"), str):
                day = data["day"]

        elif name == "find_free_slots" and "error" not in data:
            day = data.get("day") if isinstance(data.get("day"), str) else day
            asked = _moment_of(data.get("asked"))
            if asked is not None:
                checked.add(_instant(asked))
                collector.time("asked", asked)
                if data.get("asked_status") == "taken":
                    collector.time("taken", asked)
                    taken.append(asked)
                elif data.get("asked_status") == "free":
                    collector.time("free", asked)
            slots = [m for m in (_moment_of(s) for s in data.get("slots") or []) if m]
            for slot in slots:
                collector.time("free", slot)
            offered = slots

        elif name == "propose_appointment":
            asked = _moment_of(data.get("asked") or data.get("held"))
            if asked is not None:
                checked.add(_instant(asked))
                collector.time("asked", asked)
                day = to_local(_instant(asked)).date().isoformat()
            if data.get("error") == "slot_unavailable" and asked is not None:
                collector.time("taken", asked)
                taken.append(asked)
                nearest = [m for m in (_moment_of(s) for s in data.get("nearest") or []) if m]
                for slot in nearest:
                    collector.time("free", slot)
                offered = nearest
            existing = data.get("existing")
            if isinstance(existing, dict):
                collector.time("appointment", _moment_of(existing.get("at")))
            replaces = data.get("replaces")
            if isinstance(replaces, dict):
                was = _moment_of(replaces.get("was"))
                collector.time("appointment", was)
                if was is not None:
                    excluded.add(_instant(was))
            if data.get("proposal_id"):
                fact = await _proposal_fact(proposals, ctx, data, _moment_of(data.get("held")))
                if fact is not None:
                    collector.add(fact)
                    proposed.add(_instant(fact.at))
                    created = True

        elif name == "propose_cancel":
            will_cancel = data.get("will_cancel")
            at = _moment_of(will_cancel.get("at")) if isinstance(will_cancel, dict) else None
            collector.time("appointment", at)
            if data.get("proposal_id"):
                fact = await _proposal_fact(proposals, ctx, data, at)
                if fact is not None:
                    collector.add(fact)
                    created = True

        elif name == "list_my_appointments":
            for item in data.get("appointments") or []:
                if isinstance(item, dict):
                    collector.time("appointment", _moment_of(item.get("at")))

    # Yêu cầu kế (spec §5.1): giờ khách xin THÊM mà lần gọi này không đem đi
    # kiểm/đề xuất. Chỉ tính giờ mới khi đã tạo đề xuất (lịch đầu); yêu cầu kế
    # của phiên trước được giữ tới khi được đề xuất.
    prior_next = [m for m in (_moment_of(x) for x in prior.get("next_requests") or []) if m]
    candidates = prior_next + (parsed if created else [])
    done = checked | excluded | proposed
    next_requests = [m for m in _dedupe(candidates) if _instant(m) not in done]

    prior_taken = [m for m in (_moment_of(x) for x in prior.get("taken") or []) if m]
    prior_offered = [m for m in (_moment_of(x) for x in prior.get("offered") or []) if m]
    memory = {
        "day": day or prior.get("day"),
        "service_ids": list(ctx.service_ids),
        "offered": [m.model_dump() for m in (offered or prior_offered)],
        "taken": [m.model_dump() for m in _dedupe(prior_taken + taken)],
        "next_requests": [m.model_dump() for m in next_requests],
    }
    return Extracted(facts=collector.facts, memory=memory, options=offered,
                     proposal_created=created)


APPOINTMENT = SubagentSpec(
    name="appointment",
    prompt=APPOINTMENT_PROMPT,
    build_tools=make_appointment_tools,
    extract=extract_appointment,
)
