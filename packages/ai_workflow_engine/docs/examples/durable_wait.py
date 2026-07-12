"""Durable wait contract using the deterministic in-memory reference coordinator."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory

from pydantic import BaseModel

from ai_workflow_engine import (
    DurableWaitPolicy,
    InMemoryWaitCoordinator,
    ObservationConfig,
    WaitEvent,
    WorkflowBuilder,
    WorkflowEngineBuilder,
)
from ai_workflow_viewer import FileEventSource


class ApprovalState(BaseModel):
    request: str
    status: str
    decision: str = ""


def approval(context, payload: dict | ApprovalState) -> ApprovalState:
    event = context.metadata.get("resume_event")
    request = payload.request if isinstance(payload, ApprovalState) else payload["request"]
    if event is None:
        return ApprovalState(request=request, status="pending")
    event_payload = event.get("payload") if isinstance(event, dict) else None
    decision = event_payload.get("decision", "unknown") if isinstance(event_payload, dict) else "unknown"
    return ApprovalState(request=request, status="answered", decision=decision)


def finish(_context, payload: ApprovalState | dict) -> dict:
    decision = payload.decision if isinstance(payload, ApprovalState) else payload.get("decision", "")
    return {"decision": decision, "finished": True}


async def main() -> None:
    now = datetime(2036, 1, 1, tzinfo=timezone.utc)
    clock = lambda: now  # noqa: E731 - explicit deterministic clock for the example
    coordinator = InMemoryWaitCoordinator(clock=clock, shared_state={})

    with TemporaryDirectory(prefix="engine-wait-") as directory:
        builder = (
            WorkflowEngineBuilder()
            .with_wait_coordinator(coordinator, clock=clock)
            .with_observation(ObservationConfig(enabled=True, bundle_dir=directory))
        )
        builder.register_capability("approval", approval, kind="deterministic")
        builder.register_capability("finish", finish, kind="deterministic")
        builder.register_workflow(
            WorkflowBuilder("approval_flow")
            .human(
                "approval",
                wait_policy=DurableWaitPolicy(timeout_s=3600),
                timeout_to="finish",
            )
            .step("finish")
            .build()
        )
        engine = builder.build()

        suspended = await engine.run("approval_flow", {"request": "deploy"})
        assert suspended.status == "requires_user_input"
        assert suspended.wait_handle is not None and suspended.snapshot is None

        delivered = await engine.deliver_wait_event(
            suspended.wait_handle.wait_id,
            WaitEvent(
                kind="signal",
                event_id="approval-message-42",
                payload={"decision": "approved"},
            ),
        )
        assert delivered.kind == "executed"
        assert delivered.run_result.status == "completed"

        run_id = Path(suspended.observation_bundle_path).name.split("--", 1)[0]
        group = FileEventSource(directory).read_group(run_id)
        assert group.status == "completed" and len(group.segments) == 2
        print(delivered.run_result.output)


if __name__ == "__main__":
    asyncio.run(main())
