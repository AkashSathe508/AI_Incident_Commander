"""
LangGraph Workflow Graph Definition.

Constructs the reasoning pipeline graph for the AI Incident Commander.
Includes the Fact Extraction node and uses a state checkpointer keyed on thread_id = meeting_id.
"""

import logging
from typing import Any

from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, StateGraph

from app.graph.state import MeetingState
from app.reasoning.fact_extraction import extract_facts_node

logger = logging.getLogger(__name__)

# Initialize checkpointer keyed on thread_id = meeting_id
checkpointer = MemorySaver()

# Build the LangGraph StateGraph
builder = StateGraph(MeetingState)

# Add single fact extraction node
builder.add_node("extract_facts", extract_facts_node)

# Wire single-node execution path
builder.set_entry_point("extract_facts")
builder.add_edge("extract_facts", END)

# Compile graph with state checkpointer
meeting_graph = builder.compile(checkpointer=checkpointer)


def run_fact_extraction(meeting_id: str, latest_segment: dict[str, Any]) -> dict[str, Any]:
    """
    Triggers the LangGraph fact extraction pipeline for a newly ingested segment.
    Keyed on thread_id = meeting_id for state persistence across runs.
    """
    config = {"configurable": {"thread_id": meeting_id}}
    initial_state = {
        "meeting_id": meeting_id,
        "latest_segment": latest_segment,
        "transcript_segments": [latest_segment],
        "facts": [],
        "evidence": [],
    }

    try:
        final_state = meeting_graph.invoke(initial_state, config=config)
        logger.info(
            "[GRAPH] Graph execution completed for meeting %s (facts: %d)",
            meeting_id[:8],
            len(final_state.get("facts", [])),
        )
        return final_state
    except Exception as exc:
        logger.error("Error executing LangGraph fact extraction workflow: %s", exc)
        return {}
