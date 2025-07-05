"""Clean recipient task service - no ID prefixing, works with unified recipients."""

from typing import List, Tuple, Optional, Dict
from models.unified_recipient import UnifiedRecipient
from services.recipient_service import RecipientService
from core.interfaces import ITaskRepository
from core.logging import get_logger
from platforms.base import TaskPlatformFactory
from models.task import PlatformTaskData
from datetime import datetime, timedelta

logger = get_logger(__name__)


class RecipientTaskService:
    """Clean service for creating tasks with unified recipients."""
    
    def __init__(self, task_repo: ITaskRepository, recipient_service: RecipientService):
        self.task_repo = task_repo
        self.recipient_service = recipient_service
    
    def create_task_for_recipients(self, user_id: int, title: str, description: str = "", 
                                  due_time: Optional[str] = None, specific_recipients: Optional[List[int]] = None,
                                  screenshot_data: Optional[Dict] = None, chat_id: int = 0, message_id: int = 0) -> Tuple[bool, Optional[str], Optional[Dict]]:
        """Create task for specified recipients or defaults.
        
        Returns:
            Tuple of (success, feedback_message, action_buttons)
        """
        logger.info(f"Creating task '{title}' for user {user_id}")
        
        # Determine which recipients to use
        if specific_recipients:
            recipients = []
            for recipient_id in specific_recipients:
                recipient = self.recipient_service.get_recipient_by_id(user_id, recipient_id)
                if recipient and recipient.enabled:
                    recipients.append(recipient)
                else:
                    logger.warning(f"Recipient {recipient_id} not found or disabled for user {user_id}")
        else:
            # Use default recipients (personal + any marked as default)
            recipients = self.recipient_service.get_default_recipients(user_id)
        
        if not recipients:
            logger.warning(f"No recipients available for user {user_id}")
            return False, "❌ No recipients configured. Please add accounts first.", None
        
        # Create task in database using the existing task model
        from models.task import TaskCreate
        from datetime import datetime, timedelta
        
        # Use provided due_time or default to tomorrow at 9 AM UTC
        if due_time:
            due_time_str = due_time
        else:
            tomorrow_9am = (datetime.utcnow() + timedelta(days=1)).replace(hour=9, minute=0, second=0, microsecond=0)
            due_time_str = tomorrow_9am.strftime("%Y-%m-%dT%H:%M:%SZ")
        
        task_data = TaskCreate(
            title=title,
            description=description or "",
            due_time=due_time_str
        )
        
        # Extract screenshot file_id if available
        screenshot_file_id = screenshot_data.get('file_id') if screenshot_data else None
        
        # Create task with provided chat_id and message_id for proper notifications
        task_id = self.task_repo.create(
            user_id=user_id,
            chat_id=chat_id,
            message_id=message_id,
            task_data=task_data,
            screenshot_file_id=screenshot_file_id
        )
        if not task_id:
            logger.error(f"Failed to create task for user {user_id}")
            return False, "❌ Failed to create task in database.", None
        
        # Create tasks on platforms
        task_urls = []
        failed_recipients = []
        
        for recipient in recipients:
            success, url = self._create_platform_task(recipient, title, description, due_time_str, screenshot_data)
            if success and url:
                task_urls.append(url)
                logger.info(f"Created task on {recipient.platform_type} for recipient {recipient.id}")
            else:
                failed_recipients.append(recipient.name)
                logger.error(f"Failed to create task on {recipient.platform_type} for recipient {recipient.id}")
        
        # Generate feedback and action buttons
        if task_urls:
            feedback = self._generate_success_feedback(recipients, task_urls, failed_recipients, title, description, due_time_str, user_id)
            actions = self._generate_post_task_actions(user_id, recipients, task_id)
            return True, feedback, actions
        else:
            feedback = f"❌ Failed to create task on all platforms: {', '.join(failed_recipients)}"
            return False, feedback, None
    
    def add_task_to_recipient(self, user_id: int, task_id: int, recipient_id: int) -> Tuple[bool, str]:
        """Add existing task to specific recipient."""
        logger.info(f"Adding task {task_id} to recipient {recipient_id} for user {user_id}")
        
        recipient = self.recipient_service.get_recipient_by_id(user_id, recipient_id)
        if not recipient:
            return False, f"❌ Recipient {recipient_id} not found"
        
        if not recipient.enabled:
            return False, f"❌ {recipient.name} is disabled"
        
        # Get task details from database
        task = self.task_repo.get_by_id(task_id)
        if not task:
            logger.error(f"Task {task_id} not found in database")
            return False, f"❌ Task {task_id} not found"
        
        # Use the original task content
        task_title = task.title
        task_description = task.description
        due_time_str = task.due_time
        
        # Retrieve screenshot data from cache if available
        screenshot_data = None
        if task.screenshot_file_id:
            logger.info(f"Retrieving screenshot from cache for file_id: {task.screenshot_file_id}")
            from services.temporary_file_cache import get_screenshot_cache
            cache = get_screenshot_cache()
            cached_data = cache.get_screenshot(task.screenshot_file_id)
            if cached_data:
                screenshot_data = {
                    'file_id': task.screenshot_file_id,
                    'image_data': cached_data['image_data'],
                    'file_name': cached_data['file_name']
                }
                logger.info(f"Retrieved screenshot from cache: {len(cached_data['image_data'])} bytes")
            else:
                logger.warning(f"Screenshot not found in cache for file_id: {task.screenshot_file_id}")
        
        # Create task on platform
        success, url = self._create_platform_task(recipient, task_title, task_description, due_time_str, screenshot_data)
        if success and url:
            logger.info(f"Added task {task_id} to {recipient.name}")
            return True, f"✅ Added to {recipient.name}: `{url}`"
        else:
            logger.error(f"Failed to add task {task_id} to {recipient.name}")
            return False, f"❌ Failed to add to {recipient.name}"
    
    def _create_platform_task(self, recipient: UnifiedRecipient, title: str, description: str, due_time: str, screenshot_data: Optional[Dict] = None) -> Tuple[bool, Optional[str]]:
        """Create task on specific platform."""
        try:
            # Initialize platform
            platform = TaskPlatformFactory.get_platform(recipient.platform_type, recipient.credentials)
            if not platform:
                logger.error(f"Failed to initialize {recipient.platform_type} platform")
                return False, None
            
            # Prepare platform task data
            platform_task_data = PlatformTaskData(
                title=title,
                description=description,
                due_time=due_time
            )
            
            # Add platform-specific configuration
            if recipient.platform_config:
                if recipient.platform_type == 'trello':
                    platform_task_data.board_id = recipient.platform_config.get('board_id')
                    platform_task_data.list_id = recipient.platform_config.get('list_id')
                    
                    if not platform_task_data.board_id or not platform_task_data.list_id:
                        logger.error(f"Incomplete Trello configuration for {recipient.name}")
                        return False, None
            
            # Create task
            logger.debug(f"Creating task on {recipient.name} ({recipient.platform_type})")
            task_id = platform.create_task(platform_task_data.model_dump())
            
            if task_id:
                logger.info(f"Successfully created task {task_id} on {recipient.name}")
                
                # Add screenshot attachment if provided
                if screenshot_data:
                    logger.info(f"Attempting to attach screenshot to {recipient.platform_type} task {task_id} for recipient '{recipient.name}'")
                    
                    # Try to get image data from screenshot_data first
                    image_data = screenshot_data.get('image_data')
                    file_name = screenshot_data.get('file_name', 'screenshot.jpg')
                    file_id = screenshot_data.get('file_id')
                    
                    # If no direct image_data, try to get it from cache using file_id
                    if not image_data and file_id:
                        logger.info(f"No direct image_data, attempting to retrieve from cache for file_id: {file_id}")
                        from services.temporary_file_cache import get_screenshot_cache
                        cache = get_screenshot_cache()
                        cached_data = cache.get_screenshot(file_id)
                        if cached_data:
                            image_data = cached_data['image_data']
                            file_name = cached_data['file_name']
                            logger.info(f"Retrieved screenshot from cache: {len(image_data)} bytes")
                        else:
                            logger.warning(f"Screenshot not found in cache for file_id: {file_id}")
                    
                    # Attempt attachment if we have image data
                    if image_data:
                        try:
                            success = platform.attach_screenshot(task_id, image_data, file_name)
                            if success:
                                logger.info(f"Successfully attached screenshot to {recipient.platform_type} task {task_id}")
                            else:
                                logger.warning(f"Failed to attach screenshot to {recipient.platform_type} task {task_id}")
                        except Exception as e:
                            logger.error(f"Error attaching screenshot to {recipient.platform_type} task: {e}")
                    else:
                        logger.warning(f"No image data available for screenshot attachment to {recipient.platform_type}")
                
                # Generate platform-specific URL
                if recipient.platform_type == "todoist":
                    url = f"https://todoist.com/showTask?id={task_id}"
                elif recipient.platform_type == "trello":
                    url = f"https://trello.com/c/{task_id}"
                else:
                    url = str(task_id)
                return True, url
            else:
                logger.error(f"Platform returned no task ID for {recipient.name}")
                return False, None
                
        except Exception as e:
            logger.error(f"Error creating task on {recipient.platform_type}: {e}")
            return False, None
    
    def _generate_success_feedback(self, recipients: List[UnifiedRecipient], task_urls: List[str], 
                                 failed_recipients: List[str], title: str, description: str, due_time: str, user_id: int) -> str:
        """Generate success feedback message with full task details."""
        feedback_parts = ["✅ *Task Created Successfully!*\n"]
        
        # Add task details (escape special Markdown characters)
        escaped_title = title.replace('_', '\\_').replace('*', '\\*').replace('[', '\\[').replace(']', '\\]').replace('`', '\\`')
        feedback_parts.append(f"📝 *Title:* {escaped_title}")
        if description and description.strip():
            # Truncate long descriptions
            desc_preview = description[:200] + "..." if len(description) > 200 else description
            escaped_desc = desc_preview.replace('_', '\\_').replace('*', '\\*').replace('[', '\\[').replace(']', '\\]').replace('`', '\\`')
            feedback_parts.append(f"📄 *Description:* {escaped_desc}")
        
        # Format due time in user's local timezone
        try:
            from datetime import datetime
            
            # Get user's location from preferences
            user_prefs = self.recipient_service.get_user_preferences(user_id)
            location = user_prefs.location if user_prefs else None
            
            # Convert UTC time to user's local time
            if location:
                try:
                    # Simple timezone conversion using the same logic as parsing service
                    import zoneinfo
                    from dateutil import parser as date_parser
                    from datetime import datetime, timezone, timedelta
                    
                    # Parse UTC time
                    utc_time = date_parser.isoparse(due_time)
                    if utc_time.tzinfo is None:
                        utc_time = utc_time.replace(tzinfo=timezone.utc)
                    
                    # Get timezone offset using the same logic as parsing service
                    location_lower = location.lower().strip()
                    
                    # Use the existing parsing service timezone resolution
                    from services.parsing_service import ParsingService
                    temp_service = ParsingService.__new__(ParsingService)
                    
                    # Get timezone offset using the existing intelligent lookup
                    offset_hours = temp_service.get_timezone_offset(location)
                    timezone_name = temp_service._get_timezone_name(location)
                    
                    # Convert to local time
                    local_time = utc_time + timedelta(hours=offset_hours)
                    
                    local_time_display = f"{local_time.strftime('%B %d, %Y at %H:%M')} ({timezone_name})"
                    logger.info(f"Timezone conversion successful: UTC {due_time} -> Local {local_time_display}")
                    feedback_parts.append(f"⏰ *Due:* {local_time_display}")
                except Exception as e:
                    logger.warning(f"Failed to convert time to local timezone: {e}")
                    # Fallback to UTC display
                    due_dt = datetime.fromisoformat(due_time.replace('Z', '+00:00'))
                    due_formatted = due_dt.strftime("%Y-%m-%d at %H:%M UTC")
                    feedback_parts.append(f"⏰ *Due:* {due_formatted}")
            else:
                # No location set, display UTC
                due_dt = datetime.fromisoformat(due_time.replace('Z', '+00:00'))
                due_formatted = due_dt.strftime("%Y-%m-%d at %H:%M UTC")
                feedback_parts.append(f"⏰ *Due:* {due_formatted}")
        except Exception as e:
            logger.warning(f"Failed to format due time: {e}")
            feedback_parts.append(f"⏰ *Due:* {due_time}")
            
        feedback_parts.append("\n🔗 *Created on:*")
        
        for i, recipient in enumerate(recipients):
            if i < len(task_urls):
                # Escape special characters in URLs for Markdown
                url = task_urls[i]
                # Replace underscores and other special chars to prevent Markdown parsing issues
                escaped_url = url.replace('_', '\\_').replace('*', '\\*').replace('[', '\\[').replace(']', '\\]').replace('`', '\\`')
                feedback_parts.append(f"• {recipient.name}: `{escaped_url}`")
        
        if failed_recipients:
            feedback_parts.append(f"\n❌ *Failed:* {', '.join(failed_recipients)}")
        
        return "\n".join(feedback_parts)
    
    def _generate_post_task_actions(self, user_id: int, used_recipients: List[UnifiedRecipient], 
                                  task_id: int) -> Dict[str, List[Dict[str, str]]]:
        """Generate post-task action buttons."""
        logger.debug(f"Generating post-task actions for user {user_id}, task {task_id}")
        
        used_recipient_ids = {r.id for r in used_recipients}
        
        # Actions to remove from recipients that were used
        remove_actions = []
        for recipient in used_recipients:
            remove_actions.append({
                "text": f"❌ Remove from {recipient.name}",
                "callback_data": f"remove_task_from_{recipient.id}_{task_id}",
                "recipient_id": str(recipient.id),
                "recipient_name": recipient.name
            })
        
        # Actions to add to recipients that weren't used
        add_actions = []
        all_recipients = self.recipient_service.get_enabled_recipients(user_id)
        for recipient in all_recipients:
            if recipient.id not in used_recipient_ids:
                add_actions.append({
                    "text": f"➕ Add to {recipient.name}",
                    "callback_data": f"add_task_to_{recipient.id}_{task_id}",
                    "recipient_id": str(recipient.id),
                    "recipient_name": recipient.name
                })
        
        logger.debug(f"Generated {len(remove_actions)} remove actions, {len(add_actions)} add actions")
        return {
            "remove_actions": remove_actions,
            "add_actions": add_actions
        }