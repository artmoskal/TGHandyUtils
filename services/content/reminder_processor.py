"""Reminder/task content processor.

This is the ORIGINAL reminder pipeline, moved verbatim out of
handlers_modular/message/text_handler.process_thread_with_photos. Behaviour is unchanged;
it still resolves collaborators via the global container + service locator so existing tests
(which patch those points) keep working.
"""

import asyncio

from core.interfaces import IContentProcessor, ProcessingContext, ServiceResult
from core.logging import get_logger
from models.task import TaskCreate
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

            # Parse using recipient parsing service
            from core.initialization import services
            parsing_service = services.get_parsing_service()

            parsed_task_dict = await asyncio.to_thread(
                parsing_service.parse_content_to_task,
                concatenated_content,
                owner_name=owner_name,
                location=ctx.location,
                user_id=owner_id
            )

            if parsed_task_dict:
                logger.info(f"LLM parsed task title: {parsed_task_dict['title']}")
                task_data = TaskCreate(
                    title=parsed_task_dict['title'],
                    description=concatenated_content,  # Use original content
                    due_time=parsed_task_dict['due_time']
                )
            else:
                logger.info("LLM parsing failed, using fallback title")
                from datetime import datetime, timezone, timedelta
                tomorrow = datetime.now(timezone.utc) + timedelta(days=1)
                due_time = tomorrow.replace(hour=9, minute=0, second=0, microsecond=0).isoformat()
                task_data = TaskCreate(
                    title=concatenated_content[:100],
                    description=concatenated_content,
                    due_time=due_time
                )

            # Create task using recipient task service WITH screenshot data
            task_service = container.recipient_task_service()
            recipient_service = container.recipient_service()

            result = await asyncio.to_thread(
                task_service.create_task_for_recipients,
                user_id=owner_id,
                title=task_data.title,
                description=task_data.description,
                due_time=task_data.due_time,
                specific_recipients=None,
                screenshot_data=screenshot_data,
                chat_id=message.chat.id,
                message_id=message.message_id
            )

            success = result.success
            feedback = result.message
            actions = result.data

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
