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

from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator

from ai_workflow_engine.models import WorkflowGoal, WorkflowRunContext


SNAPSHOT_SCHEMA_VERSION = "v0.11"


class MachineSnapshot(BaseModel):
    """Complete, restorable position of a suspended workflow run (current line only).

    v0.11 clean contract (manifest row M6): the schema is versioned and CLOSED — unknown keys are
    rejected, the capturing run's identity (goal/run_context) is REQUIRED, and pre-v0.11 persisted
    snapshots fail with an actionable unsupported-version error instead of being half-read.
    Historical data is inspected with its matching historical tag; the current engine has no
    importer."""

    model_config = ConfigDict(arbitrary_types_allowed=True, extra="forbid")

    schema_version: Literal["v0.11"]
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
    # B-post3: the run's identity travels with the machine position, so resume continues under
    # the SAME goal/constraints/user/delivery/run-id lineage unless the caller overrides them.
    # REQUIRED and TYPED (v0.11, review finding 1): an empty/malformed identity fails at the
    # model — resume can never see a hollow identity and mint a replacement.
    goal: WorkflowGoal
    run_context: WorkflowRunContext
    # W4/R1: the LOGICAL observation position of the run-half that captured this snapshot
    # (segment index within the logical run). Deliberately NOT a physical directory key —
    # physical attempt identity varies per delivery retry, and the machine position must
    # stay byte-identical across retries of the same delivery (W2B reuse). None = the run
    # half executed without observation. Resume derives the continuation's logical index
    # from this field, never from a directory scan.
    segment_index: Optional[int] = Field(default=None, ge=0)
    # W3R.1: a DURABLE suspension's snapshot is sealed to its registered wait — the public
    # resume door rejects it; only the engine's claimed delivery path (which validates an
    # unforgeable in-flight claim) may execute it. None = local wait, public resume as ever.
    durable_wait_id: Optional[str] = None
    # v0.10: cumulative ACTIVE run-budget already consumed when this snapshot was captured,
    # as a DURATION in seconds (never a monotonic timestamp — those are process-local and
    # unsafe to persist). Resume rebuilds the run deadline from the remaining active budget,
    # so time spent suspended at the gate does not count against the run timeout.
    active_elapsed_s: float = Field(default=0.0, ge=0, allow_inf_nan=False, strict=True)

    @model_validator(mode="before")
    @classmethod
    def _current_schema_only(cls, data: Any) -> Any:
        if isinstance(data, dict):
            found = data.get("schema_version")
            if found != SNAPSHOT_SCHEMA_VERSION:
                raise ValueError(
                    f"unsupported machine-snapshot schema: expected "
                    f"{SNAPSHOT_SCHEMA_VERSION!r}, got {found!r} — this engine reads only "
                    f"current-line snapshots; inspect or resume older data with its matching "
                    f"historical engine tag (the current line has no importer)"
                )
        return data

    def to_json(self) -> str:
        """Serialize for cross-process resume. Raises loudly on non-serializable payloads."""

        return self.model_dump_json()
