"""Reminder/task content processor.

The reminder ("todoist") pipeline now runs on the reusable workflow engine via
ReminderGenerationGraph (parse -> create, with engine-owned trace + usage/budget scope). This
processor owns only the Telegram delivery (success reply, no-default-recipient picker, error
replies) — mirroring how AnkiProcessor owns delivery after its graph runs.

Collaborators are still resolved via the global container + service locator at call time, so
existing tests (which patch those points) keep working; the resolved services are handed straight
into the graph's leaf capabilities, which call them verbatim.
"""

from core.interfaces import IContentProcessor, ProcessingContext, ServiceResult
from core.logging import get_logger
from models.task import TaskCreate
from services.content.reminder_generation_graph import ReminderGenerationGraph
from services.content.thread_assembly import assemble_thread

# NOTE: handlers_modular.base / helpers.ui_helpers / core.container are imported lazily inside
# process() on purpose. This module is imported by core.container, and those modules import
# core.container back (via the handlers package) - importing them at module top creates a cycle.

logger = get_logger(__name__)


class ReminderProcessor(IContentProcessor):
    """Create a task/reminder from assembled content (the default behaviour)."""

    async def process(self, ctx: ProcessingContext) -> ServiceResult:
        # Imported lazily to avoid a core.container <-> reminder_processor import cycle.
        # Tests patch core.container.container, which this picks up at call time.
        from core.container import container
        from handlers_modular.base import handle_task_creation_response
        from helpers.ui_helpers import format_platform_button

        message = ctx.message
        owner_name = ctx.owner_name
        owner_id = ctx.user_id
        try:
            concatenated_content, screenshot_data = assemble_thread(ctx.thread_content)

            logger.info(
                f"Processing reminder thread with {len(ctx.thread_content)} messages "
                f"and screenshot: {screenshot_data is not None}"
            )

            # Resolve collaborators at call time (preserves test patch-points), then run the
            # parse -> create workflow on the engine.
            from core.initialization import services
            parsing_service = services.get_parsing_service()
            task_service = container.recipient_task_service()
            recipient_service = container.recipient_service()

            graph = ReminderGenerationGraph(parsing_service, task_service)
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
                    ui_enabled = recipient_service.is_recipient_ui_enabled(owner_id)
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
                task_repo = container.task_repository()
                task_id = task_repo.create(
                    user_id=owner_id,
                    chat_id=message.chat.id,
                    message_id=message.message_id,
                    task_data=task_data,
                    screenshot_file_id=screenshot_data.get('file_id') if screenshot_data else None
                )

                if task_id:
                    recipients = recipient_service.get_enabled_recipients(owner_id)
                    from keyboards.recipient import get_post_task_actions_keyboard

                    add_actions = []
                    for recipient in recipients:
                        add_actions.append({
                            "text": format_platform_button(recipient.platform_type, recipient.name, "Add to"),
                            "callback_data": f"add_task_to_{recipient.id}_{task_id}",
                            "recipient_id": str(recipient.id),
                            "recipient_name": recipient.name
                        })

                    actions = {"add_actions": add_actions, "remove_actions": []}
                    keyboard = get_post_task_actions_keyboard(actions)

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
            await handle_task_creation_response(message, success, feedback, actions)
            return ServiceResult.success_with_data(feedback, actions)

        except Exception as e:
            logger.error(f"Error processing thread with photos: {e}")
            await message.reply("❌ Error creating task from messages. Please try again.", disable_web_page_preview=True)
            return ServiceResult.failure(str(e))
