"""Executable-engine reminder/task generation workflow.

The reminder ("todoist") path now runs on the reusable workflow engine, mirroring the Anki
migration: the engine owns orchestration (node dispatch, trace, usage/budget scope), while this
module contributes only the two domain capabilities and the declarative shape. The leaf
capabilities REUSE the existing services (ParsingService.parse_content_to_task and
RecipientTaskService.create_task_for_recipients) verbatim, so behaviour is preserved by
construction — this is orchestration migration, not a reimplementation.

Telegram delivery (success reply, no-default-recipient picker, error replies) stays in
ReminderProcessor, exactly as Anki's processor owns package delivery after its graph runs.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional, TypedDict

from core.logging import get_logger
from models.task import TaskCreate
from ai_workflow_engine import WorkflowBuilder, WorkflowEngine
from ai_workflow_engine.engine import (
    CapabilityRegistry,
    CapabilityRuntime,
    InMemoryTraceSink,
    WorkflowRunner,
)
from ai_workflow_engine.models import (
    CapabilityContext,
    CapabilitySpec,
    RuntimeLimits,
    SafetyPolicy,
    WorkflowGoal,
    WorkflowProfile,
)

logger = get_logger(__name__)

# Fallback when the LLM (and its own static-pattern fallback) cannot parse a due time at all:
# schedule for tomorrow 09:00 UTC, title = first 100 chars. Moved here verbatim from the old
# inline ReminderProcessor fallback so the single parse capability owns the whole parse contract.
_FALLBACK_HOUR_UTC = 9
_FALLBACK_TITLE_LEN = 100


class ReminderGraphState(TypedDict, total=False):
    content: str
    screenshot_data: Optional[dict]
    owner_name: Optional[str]
    owner_id: int
    location: Optional[str]
    chat_id: Optional[int]
    message_id: Optional[int]
    task_data: Dict[str, Any]
    create_result: Dict[str, Any]


class ReminderGenerationGraph:
    """Build and run the reminder workflow on the executable engine."""

    def __init__(self, parsing_service: Any, task_service: Any, runner: Optional[WorkflowRunner] = None):
        self.parsing_service = parsing_service
        self.task_service = task_service
        # Config comes from ParsingService (which carries it); RecipientTaskService does NOT have a
        # .config, so reading it from there would silently hand the runner None and disable budget.
        self.runner = runner or WorkflowRunner(config=getattr(parsing_service, "config", None))
        self.capability_trace_sink = InMemoryTraceSink()
        self.capability_registry = CapabilityRegistry()
        self.capability_runtime = CapabilityRuntime(self.capability_registry, self.capability_trace_sink)
        self._workflow_engine: Optional[WorkflowEngine] = None
        self._capabilities_registered = False
        self.last_run_state: Optional[ReminderGraphState] = None

    async def run(
        self,
        *,
        content: str,
        owner_id: int,
        owner_name: Optional[str] = None,
        location: Optional[str] = None,
        screenshot_data: Optional[dict] = None,
        chat_id: Optional[int] = None,
        message_id: Optional[int] = None,
    ) -> Dict[str, Any]:
        """Run parse -> create on the engine; return {task_data, create_result, usage}."""

        engine = self._engine()
        goal = WorkflowGoal(
            workflow_type="reminder_generation",
            objective="Create a task/reminder from Telegram content",
            delivery_target="telegram_task",
            user_id=owner_id,
            metadata={"telegram_chat_id": chat_id, "telegram_message_id": message_id},
        )
        initial_state: ReminderGraphState = {
            "content": content,
            "screenshot_data": screenshot_data,
            "owner_name": owner_name,
            "owner_id": owner_id,
            "location": location,
            "chat_id": chat_id,
            "message_id": message_id,
        }
        result = await engine.run("reminder_generation", initial_state, goal=goal)
        final_state = result.output if isinstance(result.output, dict) else {}
        self.last_run_state = final_state
        return {
            "task_data": final_state.get("task_data"),
            "create_result": final_state.get("create_result"),
            "usage": result.usage,
        }

    # ---------------------------------------------------------------- engine wiring
    def _engine(self) -> WorkflowEngine:
        if self._workflow_engine is not None:
            return self._workflow_engine
        self._register_workflow_capabilities()
        engine = WorkflowEngine(
            registry=self.capability_registry,
            trace_sink=self.capability_trace_sink,
        )
        engine.executor.runner = self.runner
        engine.register_workflow(self._workflow_definition(), profile=self._workflow_profile())
        self._workflow_engine = engine
        return engine

    def _workflow_definition(self):
        return (
            WorkflowBuilder("reminder_generation", description="Parse content into a task, then create it")
            .step("parse_reminder")
            .step("create_reminder_task")
            .build()
        )

    def _workflow_profile(self) -> WorkflowProfile:
        # R1 (v0.9): typed limits are the ONLY budget source — the engine no longer duck-reads
        # WORKFLOW_MAX_* off the app config, so the ceilings MUST cross here explicitly via the
        # one application boundary (config.engine_runtime_limits, app 0-means-no-cap → None).
        from config import engine_runtime_limits

        app_limits = engine_runtime_limits(getattr(self.parsing_service, "config", None))
        return WorkflowProfile(
            workflow_type="reminder_generation",
            requested_capabilities=self.capability_registry.names(),
            limits=app_limits.model_copy(
                update={"max_steps": 8, "max_retries": 0, "max_parallel_children": 1}
            ),
            safety=SafetyPolicy(
                allowed_side_effects=["read_only", "local_write", "external_call", "notification"]
            ),
        )

    def _register_workflow_capabilities(self) -> None:
        if self._capabilities_registered:
            return
        self.capability_registry.register(
            CapabilitySpec(
                name="parse_reminder",
                kind="llm",
                description="Parse assembled content into a task (title, due_time) with fallback",
                side_effects=["read_only"],
            ),
            self._parse_reminder,
        )
        self.capability_registry.register(
            CapabilitySpec(
                name="create_reminder_task",
                kind="tool",
                description="Create the task locally and on each recipient platform",
                side_effects=["external_call", "local_write", "notification"],
            ),
            self._create_reminder_task,
        )
        self._capabilities_registered = True

    # ---------------------------------------------------------------- capabilities
    async def _parse_reminder(self, _context: CapabilityContext, state: ReminderGraphState) -> Dict[str, Any]:
        content = state.get("content") or ""
        parsed = await asyncio.to_thread(
            self.parsing_service.parse_content_to_task,
            content,
            owner_name=state.get("owner_name"),
            location=state.get("location"),
            user_id=state.get("owner_id"),
        )
        if parsed:
            logger.info(f"LLM parsed task title: {parsed['title']}")
            task_data = TaskCreate(
                title=parsed["title"],
                description=content,  # preserve original content as description (parity)
                due_time=parsed["due_time"],
            )
        else:
            logger.info("LLM parsing failed, using fallback title")
            tomorrow = datetime.now(timezone.utc) + timedelta(days=1)
            due_time = tomorrow.replace(
                hour=_FALLBACK_HOUR_UTC, minute=0, second=0, microsecond=0
            ).isoformat()
            task_data = TaskCreate(
                title=content[:_FALLBACK_TITLE_LEN],
                description=content,
                due_time=due_time,
            )
        return {**state, "task_data": task_data.model_dump()}

    async def _create_reminder_task(self, _context: CapabilityContext, state: ReminderGraphState) -> Dict[str, Any]:
        task_data = state.get("task_data") or {}
        result = await asyncio.to_thread(
            self.task_service.create_task_for_recipients,
            user_id=state.get("owner_id"),
            title=task_data.get("title"),
            description=task_data.get("description"),
            due_time=task_data.get("due_time"),
            specific_recipients=None,
            screenshot_data=state.get("screenshot_data"),
            chat_id=state.get("chat_id"),
            message_id=state.get("message_id"),
        )
        return {
            **state,
            "create_result": {
                "success": result.success,
                "message": result.message,
                "data": result.data,
            },
        }
