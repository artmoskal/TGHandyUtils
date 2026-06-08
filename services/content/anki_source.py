"""Build typed Anki workflow input from the existing Telegram thread representation."""

from typing import Any, List, Optional

from models.anki_workflow import ContentImage, ContentSource
from services.content.thread_assembly import assemble_thread, collect_screenshots


def build_content_source(
    thread_content: List[Any],
    user_id: int,
    owner_name: str,
    location: Optional[str] = None,
) -> ContentSource:
    """Normalize the current thread tuple format into graph input.

    Image OCR/summary has already happened upstream. This function preserves those markers in
    content and keeps the raw image bytes available for media/reference decisions.
    """
    content, _ = assemble_thread(thread_content)
    images = []
    for index, screenshot in enumerate(collect_screenshots(thread_content)):
        if not isinstance(screenshot, dict):
            continue
        images.append(
            ContentImage(
                index=index,
                file_id=screenshot.get("file_id"),
                file_name=screenshot.get("file_name"),
                image_data=screenshot.get("image_data"),
            )
        )
    return ContentSource(
        content=content,
        user_id=user_id,
        owner_name=owner_name or "User",
        location=location,
        images=images,
    )
