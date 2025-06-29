"""Clean task service for recipient system - no legacy code."""

from typing import List, Optional, Tuple, Dict, Any
from datetime import datetime
from models.task import TaskCreate, TaskDB, PlatformTaskData
from models.recipient import Recipient
from core.recipient_interfaces import IRecipientService
from core.interfaces import ITaskRepository
from platforms import TaskPlatformFactory
from core.exceptions import TaskCreationError, ValidationError
from core.logging import get_logger

logger = get_logger(__name__)


class RecipientTaskService:
    """Clean task service using recipient system."""
    
    def __init__(
        self,
        task_repo: ITaskRepository,
        recipient_service: IRecipientService,
        unified_recipient_service=None
    ):
        self.task_repo = task_repo
        self.recipient_service = recipient_service
        self.unified_recipient_service = unified_recipient_service
    
    async def create_task(
        self,
        user_id: int,
        chat_id: int,
        message_id: int,
        task_data: TaskCreate,
        recipient_ids: Optional[List[str]] = None,
        initiator_link: Optional[str] = None,
        screenshot_data: Optional[Dict[str, Any]] = None
    ) -> Tuple[bool, Optional[str]]:
        """Create task for specified recipients."""
        
        try:
            # Validate task data
            self._validate_task_data(task_data)
            
            # Get recipients to use
            if recipient_ids:
                recipients = self.recipient_service.get_recipients_by_ids(user_id, recipient_ids)
                if not recipients:
                    raise TaskCreationError("No valid recipients found")
            else:
                # Use unified service for better defaults (personal accounts only)
                if self.unified_recipient_service:
                    recipients = self.unified_recipient_service.get_default_task_recipients(user_id)
                else:
                    recipients = self.recipient_service.get_default_recipients(user_id)
                    
                if not recipients:
                    raise TaskCreationError("No recipients configured")
            
            logger.info(f"Creating task for recipients: {[r.name for r in recipients]}")
            
            # Create task in local database
            task_db_id = self.task_repo.create(
                user_id=user_id,
                chat_id=chat_id,
                message_id=message_id,
                task_data=task_data,
                platform_type=recipients[0].platform_type  # Use first recipient's platform for DB
            )
            
            if not task_db_id:
                raise TaskCreationError("Failed to save task to database")
            
            logger.info(f"Task saved locally with ID {task_db_id}")
            
            # Create task on each recipient's platform
            created_tasks = []
            task_urls = []
            
            for recipient in recipients:
                try:
                    platform_task_id, error_message = await self._create_task_on_recipient(
                        task_data, recipient, user_id, initiator_link, screenshot_data
                    )
                    
                    if platform_task_id:
                        # Update DB with platform task ID for first recipient
                        if recipient == recipients[0]:
                            self.task_repo.update_platform_id(task_db_id, platform_task_id, recipient.platform_type)
                        
                        logger.info(f"Task created on {recipient.name} with ID {platform_task_id}")
                        
                        # Generate task URL
                        task_url = self._generate_task_url(platform_task_id, recipient)
                        if task_url:
                            task_urls.append(f"{recipient.name}: {task_url}")
                        
                        created_tasks.append(recipient.name)
                    else:
                        logger.warning(f"Failed to create task on {recipient.name}: {error_message}")
                        
                except Exception as e:
                    logger.error(f"Error creating task on {recipient.name}: {e}")
            
            if created_tasks:
                combined_urls = "\n".join(task_urls) if task_urls else None
                logger.info(f"Successfully created task on: {', '.join(created_tasks)}")
                
                # Add warning for partial failures
                if len(created_tasks) < len(recipients):
                    failed_recipients = [r.name for r in recipients if r.name not in created_tasks]
                    warning = f"\n\n⚠️ Failed to create on: {', '.join(failed_recipients)}"
                    combined_urls = (combined_urls or "") + warning
                
                # Generate enhanced feedback with post-task actions
                if self.unified_recipient_service:
                    feedback, actions = self._generate_enhanced_feedback(user_id, recipients, task_urls, task_db_id, failed_recipients if len(created_tasks) < len(recipients) else None)
                    return True, feedback, actions
                else:
                    return True, combined_urls, None
            else:
                logger.error("Failed to create task on all recipients")
                return False, None, None
                
        except Exception as e:
            logger.error(f"Task creation failed: {e}")
            raise TaskCreationError(f"Task creation failed: {e}")
    
    def _generate_enhanced_feedback(self, user_id: int, used_recipients: List[Recipient], task_urls: List[str], task_id: int, failed_recipients: Optional[List[str]] = None) -> Tuple[str, Optional[Dict[str, List[Dict[str, str]]]]]:
        """Generate enhanced task creation feedback with post-task actions."""
        logger.debug(f"Generating enhanced feedback for user {user_id}")
        
        # Basic success message with URLs  
        feedback = "✅ **Task Created Successfully!**\n\n"
        
        if task_urls:
            feedback += "🔗 **Created on (Personal Accounts):**\n"
            for url in task_urls:
                feedback += f"  • {url}\n"
        
        # Add warning for partial failures
        if failed_recipients:
            feedback += f"\n⚠️ **Failed to create on:** {', '.join(failed_recipients)}\n"
        
        # Generate post-task actions using unified service
        actions = None
        try:
            actions = self.unified_recipient_service.generate_post_task_actions(user_id, used_recipients)
            
            # Add task_id to callback data
            if actions["remove_actions"]:
                for action in actions["remove_actions"]:
                    action["callback_data"] = f"{action['callback_data']}_{task_id}"
            if actions["add_actions"]:
                for action in actions["add_actions"]:
                    action["callback_data"] = f"{action['callback_data']}_{task_id}"
            
            if actions["remove_actions"] or actions["add_actions"]:
                feedback += "\n📱 **Quick Actions:**\n"
                
                if actions["remove_actions"]:
                    feedback += "**Remove from:**\n"
                    for action in actions["remove_actions"]:
                        feedback += f"  • {action['recipient_name']}\n"
                
                if actions["add_actions"]:
                    feedback += "**Available to add:**\n"
                    for action in actions["add_actions"]:
                        feedback += f"  • {action['recipient_name']}\n"
                
                feedback += "\n💡 *Use the buttons below to quickly modify recipients*"
            
        except Exception as e:
            logger.warning(f"Failed to generate post-task actions: {e}")
            # Fall back to basic feedback without actions
        
        return feedback, actions
    
    async def _create_task_on_recipient(
        self,
        task_data: TaskCreate,
        recipient: Recipient,
        user_id: int,
        initiator_link: Optional[str] = None,
        screenshot_data: Optional[Dict[str, Any]] = None
    ) -> Tuple[Optional[str], Optional[str]]:
        """Create task on a specific recipient's platform."""
        
        try:
            # Get recipient's platform credentials
            credentials = self.recipient_service.get_recipient_credentials(user_id, recipient.id)
            if not credentials:
                return None, f"No credentials found for {recipient.name}"
            
            # Initialize platform
            platform = TaskPlatformFactory.get_platform(recipient.platform_type, credentials)
            if not platform:
                return None, f"Failed to initialize {recipient.platform_type} platform"
            
            # Prepare platform task data
            platform_task_data = PlatformTaskData(
                title=task_data.title,
                description=task_data.description,
                due_time=task_data.due_time,
                source_attachment=initiator_link
            )
            
            # Add platform-specific configuration
            config = self.recipient_service.get_recipient_config(user_id, recipient.id)
            if config:
                if recipient.platform_type == 'trello':
                    platform_task_data.board_id = config.get('board_id')
                    platform_task_data.list_id = config.get('list_id')
                    
                    if not platform_task_data.board_id or not platform_task_data.list_id:
                        return None, f"Incomplete Trello configuration for {recipient.name}"
            
            # Create task
            logger.debug(f"Creating task on {recipient.name} ({recipient.platform_type})")
            task_id = platform.create_task(platform_task_data.model_dump())
            
            if task_id:
                logger.info(f"Successfully created task {task_id} on {recipient.name}")
                return task_id, None
            else:
                return None, f"Platform returned no task ID for {recipient.name}"
                
        except Exception as e:
            logger.error(f"Failed to create task on {recipient.name}: {e}")
            return None, str(e)
    
    async def add_task_to_recipient(self, task_id: int, recipient_id: str, user_id: int) -> Tuple[bool, Optional[str]]:
        """Add an existing task to an additional recipient."""
        try:
            # Get the task from database
            all_tasks = self.task_repo.get_by_user(user_id)
            task_db = next((t for t in all_tasks if t.id == task_id), None)
            if not task_db:
                return False, "Task not found"
            
            # Get the recipient
            recipients = self.recipient_service.get_recipients_by_ids(user_id, [recipient_id])
            if not recipients:
                return False, "Recipient not found"
            
            recipient = recipients[0]
            
            # Create task data from DB
            task_data = TaskCreate(
                title=task_db.title,
                description=task_db.description,
                due_time=task_db.due_time
            )
            
            # Create task on recipient's platform
            platform_task_id, error = await self._create_task_on_recipient(
                task_data, recipient, user_id
            )
            
            if platform_task_id:
                task_url = self._generate_task_url(platform_task_id, recipient)
                return True, f"✅ Added to {recipient.name}" + (f": {task_url}" if task_url else "")
            else:
                return False, f"Failed to add to {recipient.name}: {error}"
                
        except Exception as e:
            logger.error(f"Error adding task to recipient: {e}")
            return False, str(e)
    
    def _generate_task_url(self, task_id: str, recipient: Recipient) -> Optional[str]:
        """Generate task URL for recipient."""
        try:
            if recipient.platform_type == 'todoist':
                return f"https://todoist.com/showTask?id={task_id}"
            elif recipient.platform_type == 'trello':
                return f"https://trello.com/c/{task_id}"
            
            return None
            
        except Exception as e:
            logger.error(f"Error generating task URL: {e}")
            return None
    
    def _validate_task_data(self, task_data: TaskCreate) -> None:
        """Validate task data."""
        if not task_data.title or not task_data.title.strip():
            raise ValidationError("Task title cannot be empty")
        
        if not task_data.due_time:
            raise ValidationError("Task due time is required")
        
        try:
            # Validate due time format
            if isinstance(task_data.due_time, str):
                datetime.fromisoformat(task_data.due_time.replace('Z', '+00:00'))
        except ValueError:
            raise ValidationError("Invalid due time format")
        
        logger.debug(f"Task data validation passed: {task_data.title}")