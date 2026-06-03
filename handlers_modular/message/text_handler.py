"""Routes assembled thread content to the right content processor (reminder / anki / ...)."""

import asyncio
from typing import List, Tuple, Optional
from aiogram.types import Message

from core.container import container
from core.interfaces import Intent, ProcessingContext
from core.logging import get_logger
from services.content.router import get_processor
from services.content.thread_assembly import assemble_thread

logger = get_logger(__name__)


async def process_thread_with_photos(message: Message, thread_content: List[Tuple],
                                     owner_name: str, location: str, owner_id: int,
                                     intent_override: Optional[Intent] = None):
    """Resolve the intent for an assembled thread and hand off to the matching processor.

    The reminder pipeline now lives in services/content/reminder_processor.py; anki in
    anki_processor.py. This function only decides *which* runs (and, for auto mode, runs the
    5s pre-commit window before committing).
    """
    resolver = container.intent_resolver()

    ctx = ProcessingContext(
        message=message,
        thread_content=thread_content,
        user_id=owner_id,
        owner_name=owner_name,
        location=location,
    )

    # Auto mode (no explicit command override): classify, then offer a 5s override window.
    if intent_override is None and resolver.get_mode(owner_id) == "auto":
        from services.content.auto_flow import start_auto_window
        content, _ = assemble_thread(thread_content)
        guess = await asyncio.to_thread(resolver.classify, content)
        logger.info(f"Auto mode for user {owner_id}: guessed '{guess.value}'")
        await start_auto_window(ctx, guess)
        return

    intent = resolver.resolve(owner_id, override=intent_override)
    processor = get_processor(intent)
    logger.info(f"Routing content for user {owner_id} to '{intent.value}' processor")
    return await processor.process(ctx)
