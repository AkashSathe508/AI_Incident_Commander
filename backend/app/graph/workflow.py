"""
LangGraph Workflow Graph Definition.

Constructs the complete AI Incident Commander reasoning pipeline:
1. Parallel Extraction: Fact Extraction, Assumption Detection, Decision Detection, Action Item Extraction
2. Conflict Detection (Differentiator): Analyzes claim pairs for updates vs contradictions
3. Timeline Agent: Constructs chronological timeline events
4. Risk Detection: Derives risks from conflicts, unassigned tasks, and timeline deadlines
5. Evidence Verification: Filters out unbacked claims against transcript evidence
6. Postgres Checkpointer / Memory checkpointer keyed on thread_id = meeting_id
"""

import logging
from typing import Any

from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, StateGraph

from app.graph.state import MeetingState
from app.reasoning.action_item_extraction import extract_action_items_node
from app.reasoning.assumption_detection import detect_assumptions_node
from app.reasoning.conflict_detection import detect_conflicts_node
from app.reasoning.decision_detection import detect_decisions_node
from app.reasoning.evidence_verification import verify_evidence_node
from app.reasoning.fact_extraction import extract_facts_node
from app.reasoning.risk_detection import detect_risks_node
from app.reasoning.timeline_agent import timeline_agent_node

logger = logging.getLogger(__name__)

# Initialize state checkpointer keyed on thread_id = meeting_id
checkpointer = MemorySaver()

# Build the LangGraph StateGraph
builder = StateGraph(MeetingState)

# Add extraction nodes
builder.add_node("fact_extraction", extract_facts_node)
builder.add_node("assumption_detection", detect_assumptions_node)
builder.add_node("decision_detection", detect_decisions_node)
builder.add_node("action_item_extraction", extract_action_items_node)

# Add reasoning & synthesis nodes
builder.add_node("conflict_detection", detect_conflicts_node)
builder.add_node("timeline_agent", timeline_agent_node)
builder.add_node("risk_detection", detect_risks_node)
builder.add_node("evidence_verification", verify_evidence_node)

# Wire entry points for parallel extraction
builder.set_entry_point("fact_extraction")
builder.set_entry_point("assumption_detection")
builder.set_entry_point("decision_detection")
builder.set_entry_point("action_item_extraction")

# Connect extraction nodes to conflict detection
builder.add_edge("fact_extraction", "conflict_detection")
builder.add_edge("assumption_detection", "conflict_detection")
builder.add_edge("decision_detection", "conflict_detection")
builder.add_edge("action_item_extraction", "conflict_detection")

# Connect sequential reasoning chain
builder.add_edge("conflict_detection", "timeline_agent")
builder.add_edge("timeline_agent", "risk_detection")
builder.add_edge("risk_detection", "evidence_verification")
builder.add_edge("evidence_verification", END)

# Compile graph with state checkpointer
meeting_graph = builder.compile(checkpointer=checkpointer)


def run_reasoning_pipeline(meeting_id: str, latest_segment: dict[str, Any]) -> dict[str, Any]:
    """
    Triggers the complete LangGraph reasoning pipeline for a newly ingested transcript segment.
    Keyed on thread_id = meeting_id for state persistence and checkpointer recovery.
    """
    config = {"configurable": {"thread_id": meeting_id}}
    initial_state = {
        "meeting_id": meeting_id,
        "latest_segment": latest_segment,
        "transcript_segments": [latest_segment],
        "facts": [],
        "assumptions": [],
        "decisions": [],
        "action_items": [],
        "conflicts": [],
        "timeline_events": [],
        "risks": [],
        "evidence": [],
    }

    try:
        final_state = meeting_graph.invoke(initial_state, config=config)
        logger.info(
            "[GRAPH] Reasoning graph completed for meeting %s (facts: %d, assumptions: %d, decisions: %d, actions: %d, conflicts: %d, timeline: %d, risks: %d)",
            meeting_id[:8],
            len(final_state.get("facts", [])),
            len(final_state.get("assumptions", [])),
            len(final_state.get("decisions", [])),
            len(final_state.get("action_items", [])),
            len(final_state.get("conflicts", [])),
            len(final_state.get("timeline_events", [])),
            len(final_state.get("risks", [])),
        )
        return final_state
    except Exception as exc:
        logger.error("Error executing LangGraph reasoning pipeline: %s", exc)
        return {}
