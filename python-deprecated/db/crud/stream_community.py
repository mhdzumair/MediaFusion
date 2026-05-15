"""Aggregated community signals for streams (issue reports + thumbs)."""

from __future__ import annotations

from collections.abc import Sequence

from sqlalchemy import func, or_
from sqlmodel import col, select
from sqlmodel.ext.asyncio.session import AsyncSession

from db.models import StreamSuggestion, StreamVote

ISSUE_SUGGESTION_TYPES: tuple[str, ...] = ("report_broken", "other")
STATUS_REJECTED = "rejected"
ISSUE_TRIAGE_DISMISSED = "dismissed"


def empty_stream_signals_dict() -> dict[str, int]:
    return {
        "issue_report_count": 0,
        "rating_up": 0,
        "rating_down": 0,
        "rating_score": 0,
        "rating_total": 0,
    }


async def fetch_stream_community_signals_batch(
    session: AsyncSession,
    stream_ids: Sequence[int],
) -> dict[int, dict[str, int]]:
    """Return per-stream issue report counts and thumb vote aggregates."""
    ids = list(dict.fromkeys(int(s) for s in stream_ids if s))
    if not ids:
        return {}

    out: dict[int, dict[str, int]] = {sid: empty_stream_signals_dict() for sid in ids}

    issue_q = (
        select(StreamSuggestion.stream_id, func.count(StreamSuggestion.id))
        .where(
            col(StreamSuggestion.stream_id).in_(ids),
            col(StreamSuggestion.suggestion_type).in_(ISSUE_SUGGESTION_TYPES),
            StreamSuggestion.status != STATUS_REJECTED,
            or_(
                col(StreamSuggestion.issue_triage_status).is_(None),
                StreamSuggestion.issue_triage_status != ISSUE_TRIAGE_DISMISSED,
            ),
        )
        .group_by(StreamSuggestion.stream_id)
    )
    issue_res = await session.exec(issue_q)
    for row in issue_res.all():
        sid, cnt = row[0], int(row[1])
        if sid in out:
            out[sid]["issue_report_count"] = cnt

    vote_q = (
        select(StreamVote.stream_id, StreamVote.vote_type, func.count(StreamVote.id))
        .where(col(StreamVote.stream_id).in_(ids))
        .group_by(StreamVote.stream_id, StreamVote.vote_type)
    )
    vote_res = await session.exec(vote_q)
    for row in vote_res.all():
        sid, vtype, cnt = row[0], row[1], int(row[2])
        if sid not in out:
            continue
        if vtype == "up":
            out[sid]["rating_up"] = cnt
        elif vtype == "down":
            out[sid]["rating_down"] = cnt

    for sid in out:
        up = out[sid]["rating_up"]
        down = out[sid]["rating_down"]
        out[sid]["rating_score"] = up - down
        out[sid]["rating_total"] = up + down

    return out


async def fetch_recent_issue_reasons_for_stream(
    session: AsyncSession,
    stream_id: int,
    *,
    limit: int = 5,
    max_len: int = 200,
) -> list[str]:
    """Sanitized recent issue report reasons for display."""
    q = (
        select(StreamSuggestion.reason)
        .where(
            StreamSuggestion.stream_id == stream_id,
            col(StreamSuggestion.suggestion_type).in_(ISSUE_SUGGESTION_TYPES),
            StreamSuggestion.status != STATUS_REJECTED,
            or_(
                col(StreamSuggestion.issue_triage_status).is_(None),
                StreamSuggestion.issue_triage_status != ISSUE_TRIAGE_DISMISSED,
            ),
            col(StreamSuggestion.reason).is_not(None),
            StreamSuggestion.reason != "",
        )
        .order_by(StreamSuggestion.created_at.desc())
        .limit(limit)
    )
    res = await session.exec(q)
    reasons: list[str] = []
    for row in res.all():
        reason = row[0] if row is not None else None
        if not reason:
            continue
        text = " ".join(str(reason).split())
        if len(text) > max_len:
            text = text[: max_len - 1] + "…"
        reasons.append(text)
    return reasons


def vote_type_to_int(vote_type: str | None) -> int | None:
    if vote_type == "up":
        return 1
    if vote_type == "down":
        return -1
    return None


def int_vote_to_type(vote: int) -> str:
    return "up" if vote > 0 else "down"
