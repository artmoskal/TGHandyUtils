"""Small in-memory scheduling policy runtime."""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

from ai_workflow_engine.models import SchedulingPolicy


@dataclass(frozen=True)
class SchedulingDecision:
    action: str
    run_id: str
    reason: str = ""
    payload: Any = None
    previous_run_id: str | None = None


@dataclass(frozen=True)
class _QueuedRun:
    run_id: str
    payload: Any
    created_at: float
    priority: int = 0


class WorkflowScheduler:
    """Apply generic in-memory scheduling policies before workflow execution."""

    def __init__(self, clock=time.monotonic):
        self.clock = clock
        self._active: dict[str, str] = {}
        self._active_backend: dict[str, str] = {}
        self._queued: dict[str, list[_QueuedRun]] = {}
        self._backend_active: dict[str, set[str]] = {}

    def submit(
        self,
        *,
        key: str,
        run_id: str,
        payload: Any,
        policy: SchedulingPolicy,
        created_at: float | None = None,
    ) -> SchedulingDecision:
        now = self.clock()
        created = now if created_at is None else created_at
        if policy.stale_after_s is not None and now - created > policy.stale_after_s:
            return SchedulingDecision("drop", run_id, "stale", payload)

        backend_denial = self._backend_denial(run_id, payload, policy)
        if backend_denial:
            return backend_denial

        if policy.mode in {"run_immediately", "fan_out_gather"}:
            self._mark_active(key, run_id, policy)
            return SchedulingDecision("accept", run_id, "run immediately", payload)

        if policy.mode == "drop_not_queue":
            if key in self._active:
                return SchedulingDecision("drop", run_id, f"active run {self._active[key]} in progress", payload)
            self._mark_active(key, run_id, policy)
            return SchedulingDecision("accept", run_id, "first run", payload)

        if policy.mode == "drop_stale":
            previous = self._active.get(key)
            self._mark_active(key, run_id, policy)
            if previous:
                return SchedulingDecision(
                    "accept_latest", run_id, f"replaced stale run {previous}", payload, previous
                )
            return SchedulingDecision("accept", run_id, "first run", payload)

        if policy.mode in {"run_latest", "live_latest_only"}:
            self._queued[key] = [_QueuedRun(run_id, payload, created, policy.priority)]
            return SchedulingDecision("queue_latest", run_id, "queued as latest", payload)

        if policy.mode == "single_flight_cancel":
            previous = self._active.get(key)
            if previous:
                self._queued[key] = [_QueuedRun(run_id, payload, created, policy.priority)]
                return SchedulingDecision(
                    "cancel_previous", run_id, f"cancel previous run {previous}", payload, previous
                )
            self._mark_active(key, run_id, policy)
            return SchedulingDecision("accept", run_id, "first run", payload)

        if policy.mode in {"queue", "replay_process_all"}:
            queue = self._queued.setdefault(key, [])
            if len(queue) >= policy.max_queue_size:
                return SchedulingDecision("drop", run_id, "queue full", payload)
            queue.append(_QueuedRun(run_id, payload, created, policy.priority))
            queue.sort(key=lambda item: (-item.priority, item.created_at))
            return SchedulingDecision("queue", run_id, "queued", payload)

        if policy.mode == "coalesce":
            queue = self._queued.setdefault(key, [])
            if queue:
                queue[0] = _QueuedRun(run_id, payload, created, policy.priority)
                return SchedulingDecision("coalesce", run_id, "coalesced queued work", payload)
            queue.append(_QueuedRun(run_id, payload, created, policy.priority))
            return SchedulingDecision("queue", run_id, "queued for coalescing", payload)

        self._mark_active(key, run_id, policy)
        return SchedulingDecision("accept", run_id, "run immediately", payload)

    def complete(self, *, key: str, run_id: str) -> SchedulingDecision | None:
        """Mark an active run complete and promote the next queued run for the same key."""

        if self._active.get(key) != run_id:
            return None
        self._active.pop(key, None)
        backend_key = self._active_backend.pop(key, None)
        if backend_key:
            active_set = self._backend_active.get(backend_key)
            if active_set is not None:
                active_set.discard(run_id)
                if not active_set:
                    self._backend_active.pop(backend_key, None)

        queue = self._queued.get(key) or []
        if not queue:
            self._queued.pop(key, None)
            return None
        next_run = queue.pop(0)
        if not queue:
            self._queued.pop(key, None)
        self._active[key] = next_run.run_id
        return SchedulingDecision("promote", next_run.run_id, f"promoted after {run_id}", next_run.payload)

    def queued_run_ids(self, key: str) -> list[str]:
        return [item.run_id for item in self._queued.get(key, [])]

    def active_run_id(self, key: str) -> str | None:
        return self._active.get(key)

    def _backend_denial(
        self,
        run_id: str,
        payload: Any,
        policy: SchedulingPolicy,
    ) -> SchedulingDecision | None:
        if not policy.backend_key or policy.max_backend_concurrency is None:
            return None
        active_count = len(self._backend_active.get(policy.backend_key, set()))
        if active_count < policy.max_backend_concurrency:
            return None
        if policy.mode in {
            "queue",
            "replay_process_all",
            "run_latest",
            "live_latest_only",
            "coalesce",
            "single_flight_cancel",
        }:
            return None
        return SchedulingDecision(
            "drop",
            run_id,
            f"backend {policy.backend_key} concurrency limit reached",
            payload,
        )

    def _mark_active(self, key: str, run_id: str, policy: SchedulingPolicy) -> None:
        self._active[key] = run_id
        if policy.backend_key:
            self._active_backend[key] = policy.backend_key
            self._backend_active.setdefault(policy.backend_key, set()).add(run_id)
