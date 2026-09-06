"""
MeetingState — Expanded State definition for the AI Incident Commander LangGraph pipeline.

Represents the accumulated state for a meeting context, including transcript segments,
extracted facts, assumptions, decisions, action items, conflicts, timeline events, risks,
and evidence linkages.
"""

from typing import Annotated, Any, TypedDict
from typing_extensions import NotRequired


def add_items(left: list[Any], right: list[Any]) -> list[Any]:
    """Reducer helper to append new items to list state."""
    if not left:
        return list(right)
    if not right:
        return list(left)
    return left + right


class MeetingState(TypedDict):
    """
    LangGraph state schema for an active incident meeting session.
    """

    meeting_id: str
    latest_segment: NotRequired[dict[str, Any] | None]
    transcript_segments: Annotated[list[dict[str, Any]], add_items]
    facts: Annotated[list[dict[str, Any]], add_items]
    assumptions: Annotated[list[dict[str, Any]], add_items]
    decisions: Annotated[list[dict[str, Any]], add_items]
    action_items: Annotated[list[dict[str, Any]], add_items]
    conflicts: Annotated[list[dict[str, Any]], add_items]
    timeline_events: Annotated[list[dict[str, Any]], add_items]
    risks: Annotated[list[dict[str, Any]], add_items]
    evidence: Annotated[list[dict[str, Any]], add_items]

    # Sentinel mode and mute control
    mode: Annotated[str, add_items] = []  # "frequent" or "occasional"
    muted: Annotated[bool, add_items] = []
    # Tracking additional incident state
    pending_questions: Annotated[list[dict[str, Any]], add_items] = []
    approved_actions: Annotated[list[dict[str, Any]], add_items] = []
    completed_actions: Annotated[list[dict[str, Any]], add_items] = []
    unresolved_actions: Annotated[list[dict[str, Any]], add_items] = []
    timeline: Annotated[list[dict[str, Any]], add_items] = []
