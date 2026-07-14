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
        if not isinstance(data, dict):
            return data
        found = data.get("schema_version")
        if found != SNAPSHOT_SCHEMA_VERSION:
            raise ValueError(
                f"unsupported machine-snapshot schema: expected "
                f"{SNAPSHOT_SCHEMA_VERSION!r}, got {found!r} — this engine reads only "
                f"current-line snapshots; inspect or resume older data with its matching "
                f"historical engine tag (the current line has no importer)"
            )

        # Recheck R1: the PERSISTED identity is sealed at the DECODE boundary — resume under
        # the exact captured identity or fail before anything executes. The seal applies when
        # identity arrives as RAW WIRE DATA (dicts from JSON — the only shape resume/deliver
        # ever decode). Live in-process capture passes typed WorkflowGoal/WorkflowRunContext
        # instances composed by the engine itself; a discarded CHILD-suspension envelope
        # legitimately pairs the child's goal with the parent's run lineage (B-post3) and is
        # never persisted or resumable, so instance-shaped identity is not wire data and is
        # not re-sealed here. Typed nested models alone cannot provide the wire seal:
        # WorkflowGoal mints goal_id for NEW live goals (correct for live construction, never
        # while decoding), run_context.goal_id is optional for live contexts, and the nested
        # models tolerate unknown keys.
        # Recheck RRR1: each raw side is validated INDEPENDENTLY — a wire-shaped identity gets
        # its decode rules (no minting, no unknown keys) regardless of how the OTHER side is
        # represented, so dict/dict and mixed inputs behave identically. Cross-field equality
        # lives exclusively in the unconditional after-validator.
        def _blank(value: Any) -> bool:
            return value is None or (isinstance(value, str) and not value.strip())

        problems: list[str] = []
        goal_raw = data.get("goal")
        rc_raw = data.get("run_context")
        if isinstance(goal_raw, dict):
            if _blank(goal_raw.get("goal_id")):
                problems.append("goal.goal_id is missing/blank (a snapshot may never mint identity)")
            unknown = set(goal_raw) - set(WorkflowGoal.model_fields)
            if unknown:
                problems.append(f"unknown goal fields: {sorted(unknown)}")
        if isinstance(rc_raw, dict):
            if _blank(rc_raw.get("workflow_id")):
                problems.append("run_context.workflow_id is missing/blank")
            if _blank(rc_raw.get("goal_id")):
                problems.append("run_context.goal_id is missing/blank")
            unknown = set(rc_raw) - set(WorkflowRunContext.model_fields)
            if unknown:
                problems.append(f"unknown run_context fields: {sorted(unknown)}")
        if problems:
            raise ValueError(
                "unsupported machine-snapshot identity: " + "; ".join(problems)
                + " — resume requires the exact captured identity"
            )
        return data

    @model_validator(mode="after")
    def _identity_is_consistent(self) -> "MachineSnapshot":
        """Recheck RR1: identity consistency is an UNCONDITIONAL post-parse invariant — dict,
        typed, and mixed inputs all land here, so no representation can smuggle a conflicting
        or hollow identity into a valid snapshot. (Raw-dict pre-validation above additionally
        blocks decode-time minting and unknown nested keys.)"""

        problems: list[str] = []
        if not (self.goal.goal_id or "").strip():
            problems.append("goal.goal_id is blank")
        if not (self.run_context.workflow_id or "").strip():
            problems.append("run_context.workflow_id is blank")
        rc_goal_id = self.run_context.goal_id
        if not (rc_goal_id or "").strip():
            problems.append("run_context.goal_id is missing/blank")
        elif rc_goal_id != self.goal.goal_id:
            problems.append(
                f"conflicting identities: goal.goal_id={self.goal.goal_id!r} != "
                f"run_context.goal_id={rc_goal_id!r}"
            )
        if self.run_context.workflow_type != self.goal.workflow_type:
            problems.append(
                f"conflicting workflow_type: goal={self.goal.workflow_type!r} != "
                f"run_context={self.run_context.workflow_type!r}"
            )
        if problems:
            raise ValueError(
                "unsupported machine-snapshot identity: " + "; ".join(problems)
                + " — resume requires the exact captured identity"
            )
        return self

    def to_json(self) -> str:
        """Serialize for cross-process resume. Raises loudly on non-serializable payloads."""

        return self.model_dump_json()
