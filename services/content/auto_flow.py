"""Auto-mode 5s pre-commit window.

When content_mode == auto we classify the message, show the guess with override buttons, and
create NOTHING for 5 seconds. If the user taps a button the timer is cancelled and the chosen
type is created; otherwise the guess is committed once. Because nothing is created until commit,
there are no inconsistent side effects.
"""

import asyncio
from typing import Dict

from ai_workflow_engine import (
    CapabilityRegistry,
    CapabilityRuntime,
    ClarificationOption,
    HumanClarificationCapability,
    HumanClarificationRequest,
    HumanClarificationResponse,
    InMemoryTraceSink,
    RuntimePlanCompiler,
    WorkflowGoal,
    WorkflowProfile,
    capability_context_for_goal,
)
from ai_workflow_engine.models import SafetyPolicy, WorkflowTraceEvent
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

from core.interfaces import Intent, ProcessingContext
from core.logging import get_logger
from services.content.router import get_processor

logger = get_logger(__name__)

AUTO_WINDOW_SECONDS = 5

# token -> {"ctx": ProcessingContext, "status_msg": Message, "task": asyncio.Task}
_pending: Dict[str, dict] = {}
_counter = 0

_GUESS_LABEL = {Intent.REMINDER: "📝 a reminder", Intent.ANKI: "🃏 flashcards"}


class _TelegramAutoClarificationChannel:
    """Telegram adapter for the generic human-clarification capability."""

    def __init__(self, ctx: ProcessingContext, token: str, guess: Intent) -> None:
        self.ctx = ctx
        self.token = token
        self.guess = guess

    async def request(
        self,
        _context,
        request: HumanClarificationRequest,
    ) -> HumanClarificationResponse:
        status_msg = await self.ctx.message.reply(
            f"🤔 Looks like {_GUESS_LABEL[self.guess]}. "
            f"Creating in {AUTO_WINDOW_SECONDS}s — tap to change.",
            reply_markup=_keyboard(self.token),
        )
        _pending[self.token] = {
            "ctx": self.ctx,
            "status_msg": status_msg,
            "task": None,
            "clarification_request": request,
        }
        return HumanClarificationResponse(
            clarification_id=request.clarification_id,
            status="pending",
            metadata={"waiting_for_user": True, "token": self.token},
        )


def _next_token() -> str:
    global _counter
    _counter += 1
    return str(_counter)


def _optional_int(value) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _keyboard(token: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="📝 Reminder", callback_data=f"autocommit:reminder:{token}"),
        InlineKeyboardButton(text="🃏 Card", callback_data=f"autocommit:anki:{token}"),
    ]])


async def start_auto_window(ctx: ProcessingContext, guess: Intent) -> None:
    """Show the guess + override buttons and schedule the delayed commit."""
    token = _next_token()
    registry = CapabilityRegistry()
    channel = _TelegramAutoClarificationChannel(ctx, token, guess)
    ask_user = HumanClarificationCapability(channel, name="auto_intent_correction")
    registry.register(ask_user.spec, ask_user)
    profile = WorkflowProfile(
        profile_id="telegram-auto-intent",
        workflow_type="auto_intent_correction",
        requested_capabilities=["auto_intent_correction"],
        safety=SafetyPolicy(allowed_side_effects=["notification"]),
    )
    plan = RuntimePlanCompiler().compile(profile, registry)
    goal = WorkflowGoal(
        workflow_type="auto_intent_correction",
        objective="Confirm whether auto-routed content should become a reminder or Anki cards.",
        constraints={"default_intent": guess.value, "timeout_s": AUTO_WINDOW_SECONDS},
        delivery_target="telegram",
        user_id=ctx.user_id,
        metadata={
            "telegram_chat_id": _optional_int(getattr(getattr(ctx.message, "chat", None), "id", None)),
            "telegram_message_id": _optional_int(getattr(ctx.message, "message_id", None)),
        },
    )
    trace_sink = InMemoryTraceSink()
    runtime = CapabilityRuntime(registry, trace_sink=trace_sink)
    request = HumanClarificationRequest(
        question="What should this message become?",
        clarification_id=f"auto-intent-{token}",
        options=[
            ClarificationOption(label="Reminder", value=Intent.REMINDER.value),
            ClarificationOption(label="Card", value=Intent.ANKI.value),
        ],
        allow_free_text=False,
        default_value=guess.value,
        continue_without_answer=True,
        timeout_s=AUTO_WINDOW_SECONDS,
        metadata={"token": token, "guess": guess.value},
    )
    result = await runtime.invoke(
        "auto_intent_correction",
        request,
        capability_context_for_goal(goal, plan=plan, workflow_id=f"auto-intent-{token}"),
    )
    if result.status != "partial":
        logger.error("Auto-mode clarification failed before creating pending window: %s", result.error)
        return

    entry = _pending.get(token)
    if not entry:
        logger.error("Auto-mode clarification returned partial without pending entry")
        return

    entry["clarification_result"] = result.output
    entry["trace_sink"] = trace_sink
    task = asyncio.create_task(_commit_after_delay(token, guess))
    entry["task"] = task


async def _commit_after_delay(token: str, guess: Intent) -> None:
    try:
        await asyncio.sleep(AUTO_WINDOW_SECONDS)
    except asyncio.CancelledError:
        return
    entry = _pending.pop(token, None)
    if entry:
        _record_human_resolution(entry, guess, auto=True)
        await _finalize(entry, guess, auto=True)


async def commit_choice(token: str, intent: Intent) -> bool:
    """Called by the button handler. Returns False if the window already closed."""
    entry = _pending.pop(token, None)
    if not entry:
        return False
    task = entry.get("task")
    if task and not task.done():
        task.cancel()
    _record_human_resolution(entry, intent, auto=False)
    await _finalize(entry, intent, auto=False)
    return True


def _record_human_resolution(entry: dict, intent: Intent, *, auto: bool) -> None:
    request = entry.get("clarification_request")
    clarification_id = (
        request.clarification_id
        if isinstance(request, HumanClarificationRequest)
        else ""
    )
    response = HumanClarificationResponse(
        clarification_id=clarification_id,
        status="provisional" if auto else "answered",
        value=intent.value,
        answer_text="timeout default" if auto else "button selection",
        provisional=auto,
        metadata={"auto_committed": auto},
    )
    entry["clarification_response"] = response
    trace_sink = entry.get("trace_sink")
    if trace_sink:
        trace_sink.record(
            WorkflowTraceEvent(
                node="auto_intent_correction",
                decision=response.status,
                metadata={
                    "clarification_id": clarification_id,
                    "value": intent.value,
                    "auto_committed": auto,
                },
            )
        )


async def _finalize(entry: dict, intent: Intent, auto: bool) -> None:
    ctx = entry["ctx"]
    status_msg = entry["status_msg"]
    label = "🃏 flashcards" if intent == Intent.ANKI else "📝 reminder"
    prefix = "Auto-detected" if auto else "You chose"
    try:
        await status_msg.edit_text(f"{prefix}: creating {label}…")
    except Exception:
        pass
    processor = get_processor(intent)
    await processor.process(ctx)
