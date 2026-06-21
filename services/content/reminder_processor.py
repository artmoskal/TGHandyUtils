"""Reminder/task content processor.

The reminder ("todoist") pipeline now runs on the reusable workflow engine via
ReminderGenerationGraph (parse -> create, with engine-owned trace + usage/budget scope). This
processor owns only the Telegram delivery (success reply, no-default-recipient picker, error
replies) — mirroring how AnkiProcessor owns delivery after its graph runs.

Collaborators and delivery callbacks are supplied by the composition root. This module must not
import handler or container modules; those are outer-layer wiring details.
"""

from collections.abc import Awaitable, Callable
from typing import Any

from core.interfaces import IContentProcessor, ProcessingContext, ServiceResult
from core.logging import get_logger
from models.task import TaskCreate
from services.content.reminder_generation_graph import ReminderGenerationGraph
from services.content.thread_assembly import assemble_thread

logger = get_logger(__name__)

TaskCreationResponder = Callable[[Any, bool, str | None, dict[str, Any] | None], Awaitable[None]]
PlatformButtonFormatter = Callable[[str, str, str], str]
PostTaskKeyboardFactory = Callable[[dict[str, list[dict[str, str]]]], Any]


class ReminderProcessor(IContentProcessor):
    """Create a task/reminder from assembled content (the default behaviour)."""

    def __init__(
        self,
        *,
        parsing_service=None,
        task_service=None,
        recipient_service=None,
        task_repository=None,
        task_creation_responder: TaskCreationResponder | None = None,
        platform_button_formatter: PlatformButtonFormatter | None = None,
        post_task_keyboard_factory: PostTaskKeyboardFactory | None = None,
    ) -> None:
        self.parsing_service = parsing_service
        self.task_service = task_service
        self.recipient_service = recipient_service
        self.task_repository = task_repository
        self.task_creation_responder = task_creation_responder
        self.platform_button_formatter = platform_button_formatter
        self.post_task_keyboard_factory = post_task_keyboard_factory

    async def process(self, ctx: ProcessingContext) -> ServiceResult:
        message = ctx.message
        owner_name = ctx.owner_name
        owner_id = ctx.user_id
        try:
            if self.parsing_service is None or self.task_service is None:
                raise RuntimeError("ReminderProcessor requires parsing_service and task_service")
            if self.recipient_service is None:
                raise RuntimeError("ReminderProcessor requires recipient_service")
            if self.task_creation_responder is None:
                raise RuntimeError("ReminderProcessor requires task_creation_responder")

            concatenated_content, screenshot_data = assemble_thread(ctx.thread_content)

            logger.info(
                f"Processing reminder thread with {len(ctx.thread_content)} messages "
                f"and screenshot: {screenshot_data is not None}"
            )

            graph = ReminderGenerationGraph(self.parsing_service, self.task_service)
            graph_result = await graph.run(
                content=concatenated_content,
                owner_id=owner_id,
                owner_name=owner_name,
                location=ctx.location,
                screenshot_data=screenshot_data,
                chat_id=message.chat.id,
                message_id=message.message_id,
            )

            task_dict = graph_result.get("task_data")
            create_result = graph_result.get("create_result")
            if not task_dict or create_result is None:
                # Parse raised, or the create capability failed inside the engine: same
                # user-facing outcome as the old outer-exception path.
                logger.error("Reminder workflow produced no task/create result")
                await message.reply(
                    "❌ Error creating task from messages. Please try again.",
                    disable_web_page_preview=True,
                )
                return ServiceResult.failure("reminder workflow produced no result")

            task_data = TaskCreate(**task_dict)
            success = create_result.get("success")
            feedback = create_result.get("message")
            actions = create_result.get("data")

            if not success:
                if feedback == "NO_DEFAULT_RECIPIENTS":
                    ui_enabled = self.recipient_service.is_recipient_ui_enabled(owner_id)
                    if not ui_enabled:
                        from helpers.message_templates import format_ui_disabled_message
                        await message.reply(
                            format_ui_disabled_message(),
                            disable_web_page_preview=True
                        )
                        return ServiceResult.failure("UI disabled")
                elif feedback and feedback.startswith("🚫 **Cannot Create Task**"):
                    await message.reply(feedback, disable_web_page_preview=True)
                    return ServiceResult.failure("Cannot create task")

                # Create a temporary task in database first, then show recipient buttons
                if self.task_repository is None:
                    raise RuntimeError("ReminderProcessor requires task_repository for recipient picker")
                if self.platform_button_formatter is None or self.post_task_keyboard_factory is None:
                    raise RuntimeError("ReminderProcessor requires recipient picker UI callbacks")

                task_id = self.task_repository.create(
                    user_id=owner_id,
                    chat_id=message.chat.id,
                    message_id=message.message_id,
                    task_data=task_data,
                    screenshot_file_id=screenshot_data.get('file_id') if screenshot_data else None
                )

                if task_id:
                    recipients = self.recipient_service.get_enabled_recipients(owner_id)

                    add_actions = []
                    for recipient in recipients:
                        add_actions.append({
                            "text": self.platform_button_formatter(
                                recipient.platform_type,
                                recipient.name,
                                "Add to",
                            ),
                            "callback_data": f"add_task_to_{recipient.id}_{task_id}",
                            "recipient_id": str(recipient.id),
                            "recipient_name": recipient.name
                        })

                    actions = {"add_actions": add_actions, "remove_actions": []}
                    keyboard = self.post_task_keyboard_factory(actions)

                    await message.reply(
                        f"✅ **Task Created**\n\n"
                        f"**{task_data.title}**\n\n"
                        f"📅 **Due:** {task_data.due_time}\n\n"
                        f"No default recipients set. Choose where to add this task:",
                        reply_markup=keyboard,
                        parse_mode='Markdown',
                        disable_web_page_preview=True
                    )
                else:
                    await message.reply("❌ Error creating task. Please try again.", disable_web_page_preview=True)
                return ServiceResult.failure("No default recipients")

            # Use unified response handler
            await self.task_creation_responder(message, success, feedback, actions)
            return ServiceResult.success_with_data(feedback, actions)

        except Exception as e:
            logger.error(f"Error processing thread with photos: {e}")
            await message.reply("❌ Error creating task from messages. Please try again.", disable_web_page_preview=True)
            return ServiceResult.failure(str(e))
