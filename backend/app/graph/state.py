"""
MeetingState — State definition for the AI Incident Commander LangGraph pipeline.

Represents the accumulated state for a meeting context, including transcript segments,
extracted facts, evidence linkages, decisions, and risks.
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
    evidence: Annotated[list[dict[str, Any]], add_items]
