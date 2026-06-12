"""Durable machine position: capture at suspension, resume anywhere.

A :class:`MachineSnapshot` is the serializable position of a suspended state machine — node
outputs/status, recorded routes, loop counters, plan and usage — captured automatically when a
run returns ``requires_user_input``. ``engine.resume(snapshot, event)`` fast-forwards through
completed nodes (zero re-execution: recorded routes steer the compiled graph) and continues live
from the suspended node with the resume event injected.

In-process resume accepts any payload; CROSS-process resume requires JSON-serializable payloads —
:meth:`MachineSnapshot.to_json` raises loudly when they are not (never a silent downgrade).
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field


class MachineSnapshot(BaseModel):
    """Complete, restorable position of a suspended workflow run."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    workflow_id: str
    suspended_node: str
    reason: str = "requires_user_input"
    payload: Any = None
    node_outputs: Dict[str, Any] = Field(default_factory=dict)
    node_inputs: Dict[str, Any] = Field(default_factory=dict)
    node_status: Dict[str, str] = Field(default_factory=dict)
    routes: Dict[str, str] = Field(default_factory=dict)
    branch_decisions: Dict[str, str] = Field(default_factory=dict)
    eval_counters: Dict[str, Dict[str, int]] = Field(default_factory=dict)
    transition_counts: Dict[str, int] = Field(default_factory=dict)
    attempts: Dict[str, int] = Field(default_factory=dict)
    node_results: List[Dict[str, Any]] = Field(default_factory=list)
    artifacts: List[Any] = Field(default_factory=list)
    plan_artifact: Optional[Any] = None
    usage: Dict[str, Any] = Field(default_factory=dict)
    fallback_reason: Optional[str] = None

    def to_json(self) -> str:
        """Serialize for cross-process resume. Raises loudly on non-serializable payloads."""

        return self.model_dump_json()
