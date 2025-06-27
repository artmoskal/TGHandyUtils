"""Task management service."""

import json
from typing import Optional, Dict, Any, Tuple
from datetime import datetime, timezone, timedelta
from dateutil import parser

from core.interfaces import ITaskService, ITaskRepository, IUserRepository, IPartnerService, IUserPreferencesService
from models.task import TaskCreate, PlatformTaskData
from models.user import UserCreate
from models.partner import Partner
from platforms import TaskPlatformFactory
from core.exceptions import ValidationError, TaskCreationError, PlatformError
from core.logging import get_logger

logger = get_logger(__name__)

class TaskService(ITaskService):
    """Service for task management operations."""
    
    def __init__(self, task_repo: ITaskRepository, user_repo: IUserRepository, 
                 partner_service: IPartnerService = None, 
                 prefs_service: IUserPreferencesService = None):
        self.task_repo = task_repo
        self.user_repo = user_repo
        self.partner_service = partner_service
        self.prefs_service = prefs_service
    
    async def create_task(self, user_id: int, chat_id: int, message_id: int, 
                         task_data: TaskCreate, initiator_link: Optional[str] = None,
                         screenshot_data: Optional[Dict[str, Any]] = None) -> Tuple[bool, Optional[str]]:
        """Create a task both locally and on all configured platforms.
        
        Args:
            user_id: Telegram user ID
            chat_id: Telegram chat ID
            message_id: Telegram message ID
            task_data: Task creation data
            initiator_link: Optional link to original message
            screenshot_data: Optional screenshot data (image_data, file_name, etc.)
            
        Returns:
            Tuple of (success: bool, task_url: Optional[str])
            
        Raises:
            ValidationError: If task data is invalid
            TaskCreationError: If task creation fails
        """
        try:
            # Validate due time
            validated_due_time = self._validate_due_time(task_data.due_time)
            task_data.due_time = validated_due_time.isoformat()
            
            # Append initiator link to description if provided
            if initiator_link:
                task_data.description += f"\n\nOriginal message: {initiator_link}"
            
            # Use partner system if available, otherwise fallback to legacy
            if self.partner_service and self.prefs_service:
                return await self._create_task_with_partners(user_id, chat_id, message_id, task_data, initiator_link, screenshot_data)
            else:
                return await self._create_task_legacy(user_id, chat_id, message_id, task_data, initiator_link, screenshot_data)
            
                
        except Exception as e:
            logger.error(f"Failed to create task: {e}")
            raise TaskCreationError(f"Task creation failed: {e}")
    
    async def _create_task_with_partners(self, user_id: int, chat_id: int, message_id: int, 
                                        task_data: TaskCreate, initiator_link: Optional[str] = None,
                                        screenshot_data: Optional[Dict[str, Any]] = None) -> Tuple[bool, Optional[str]]:
        """Create task using the partner system."""
        try:
            # Get default partners for this user
            default_partners = self.partner_service.get_default_partners(user_id)
            if not default_partners:
                # Try to migrate user from legacy system
                user_info = self.user_repo.get_platform_info(user_id)
                if user_info:
                    platform_type = user_info.get('platform_type', 'todoist')
                    platform_token = user_info.get('platform_token')
                    platform_settings = user_info.get('platform_settings')
                    
                    if platform_token:
                        logger.info(f"Migrating user {user_id} from legacy system")
                        partner_id = self.partner_service.migrate_legacy_user(
                            user_id, platform_type, platform_token, platform_settings
                        )
                        default_partners = self.partner_service.get_default_partners(user_id)
                
                if not default_partners:
                    raise TaskCreationError("No partners configured")
            
            logger.info(f"Creating task for partners: {[p.name for p in default_partners]}")
            logger.debug(f"Task data: {task_data.dict()}")
            
            # Create ONE task in database using primary partner
            primary_partner = default_partners[0]
            logger.debug(f"Using {primary_partner.name} ({primary_partner.platform}) as primary partner")
            task_db_id = self.task_repo.create(
                user_id=user_id,
                chat_id=chat_id,
                message_id=message_id,
                task_data=task_data,
                platform_type=primary_partner.platform
            )
            
            if not task_db_id:
                raise TaskCreationError("Failed to save task to local database")
            
            logger.info(f"Task saved locally with ID {task_db_id} for user {user_id}")
            
            # Now sync this ONE task to all default partners
            created_tasks = []
            task_urls = []
            
            for partner in default_partners:
                try:
                    # Create task on partner's platform
                    platform_task_id, error_message = self._create_partner_task(
                        task_data, partner, initiator_link, screenshot_data
                    )
                    
                    if platform_task_id:
                        # Update local task with platform ID (only for primary partner in DB)
                        if partner == primary_partner:
                            self.task_repo.update_platform_id(task_db_id, platform_task_id, partner.platform)
                        
                        logger.info(f"Task {task_db_id} created for {partner.name} on {partner.platform} with ID {platform_task_id}")
                        
                        # Generate task URL
                        task_url = self._generate_partner_task_url(platform_task_id, partner)
                        if task_url:
                            task_urls.append(f"{partner.name} ({partner.platform.title()}): {task_url}")
                        
                        created_tasks.append(partner.name)
                    else:
                        logger.warning(f"Task {task_db_id} failed to create for {partner.name}: {error_message}")
                        
                except Exception as e:
                    logger.error(f"Failed to create task for partner {partner.name}: {e}")
            
            if created_tasks:
                # Return success with combined URLs
                combined_urls = "\n".join(task_urls) if task_urls else None
                logger.info(f"Successfully created task for partners: {', '.join(created_tasks)}")
                
                # If only some partners succeeded, add warning to URLs
                if len(created_tasks) < len(default_partners):
                    failed_partners = [p.name for p in default_partners if p.name not in created_tasks]
                    warning = f"\n\n⚠️ Failed to create for: {', '.join(failed_partners)}"
                    combined_urls = (combined_urls or "") + warning
                
                return True, combined_urls
            else:
                logger.error(f"Failed to create task for all partners: {[p.name for p in default_partners]}")
                return False, None
                
        except Exception as e:
            logger.error(f"Failed to create task with partners: {e}")
            raise TaskCreationError(f"Task creation failed: {e}")
    
    async def _create_task_legacy(self, user_id: int, chat_id: int, message_id: int, 
                                 task_data: TaskCreate, initiator_link: Optional[str] = None,
                                 screenshot_data: Optional[Dict[str, Any]] = None) -> Tuple[bool, Optional[str]]:
        """Legacy task creation using the old platform system."""
        try:
            # Get user platform info
            user_info = self.user_repo.get_platform_info(user_id)
            if not user_info:
                raise TaskCreationError("User not found")
            
            # Get configured platforms
            configured_platforms = self.user_repo.get_configured_platforms(user_id)
            if not configured_platforms:
                raise TaskCreationError("No platforms configured")
            
            logger.info(f"Creating task on configured platforms: {configured_platforms}")
            logger.debug(f"Task data: {task_data.dict()}")
            
            # Create ONE task in database, then sync to all platforms
            # First, save task to local database with primary platform
            primary_platform = configured_platforms[0]
            logger.debug(f"Using {primary_platform} as primary platform for database storage")
            task_db_id = self.task_repo.create(
                user_id=user_id,
                chat_id=chat_id,
                message_id=message_id,
                task_data=task_data,
                platform_type=primary_platform
            )
            
            if not task_db_id:
                raise TaskCreationError("Failed to save task to local database")
            
            logger.info(f"Task saved locally with ID {task_db_id} for user {user_id}")
            
            # Now sync this ONE task to all configured platforms
            created_tasks = []
            task_urls = []
            
            for platform_type in configured_platforms:
                try:
                    # Create task on platform
                    platform_task_id, error_message = self._create_platform_task(
                        task_data, user_info, initiator_link, screenshot_data, platform_type
                    )
                    
                    if platform_task_id:
                        # Update local task with platform ID (only for primary platform in DB)
                        if platform_type == primary_platform:
                            self.task_repo.update_platform_id(task_db_id, platform_task_id, platform_type)
                        
                        logger.info(f"Task {task_db_id} created on {platform_type} with ID {platform_task_id}")
                        
                        # Generate task URL
                        task_url = self._generate_task_url(platform_task_id, user_info, platform_type)
                        if task_url:
                            task_urls.append(f"{platform_type.title()}: {task_url}")
                        
                        created_tasks.append(platform_type)
                    else:
                        logger.warning(f"Task {task_db_id} failed to create on {platform_type}: {error_message}")
                        
                except Exception as e:
                    logger.error(f"Failed to create task on {platform_type}: {e}")
            
            if created_tasks:
                # Return success with combined URLs
                combined_urls = "\n".join(task_urls) if task_urls else None
                logger.info(f"Successfully created task on platforms: {', '.join(created_tasks)}")
                
                # If only some platforms succeeded, add warning to URLs
                if len(created_tasks) < len(configured_platforms):
                    failed_platforms = [p for p in configured_platforms if p not in created_tasks]
                    warning = f"\n\n⚠️ Failed to create on: {', '.join(failed_platforms)}"
                    combined_urls = (combined_urls or "") + warning
                
                return True, combined_urls
            else:
                logger.error(f"Failed to create task on all configured platforms: {configured_platforms}")
                return False, None
                
        except Exception as e:
            logger.error(f"Failed to create legacy task: {e}")
            raise TaskCreationError(f"Task creation failed: {e}")
    
    def _validate_due_time(self, due_time_str: str) -> datetime:
        """Validate and adjust due time if necessary.
        
        Args:
            due_time_str: Due time in ISO format
            
        Returns:
            Validated datetime object
            
        Raises:
            ValidationError: If due time format is invalid
        """
        try:
            due_time = parser.isoparse(due_time_str).astimezone(timezone.utc)
            now_utc = datetime.now(timezone.utc)
            
            logger.debug(f"Parsed due time: {due_time.isoformat()}")
            logger.debug(f"Current UTC time: {now_utc.isoformat()}")
            
            # Check if the due time is in the past
            if due_time <= now_utc:
                logger.warning("Due time is in the past, adjusting to one hour from now")
                due_time = now_utc.replace(microsecond=0) + timedelta(hours=1)
            
            return due_time
            
        except Exception as e:
            logger.error(f"Error parsing due time: {e}")
            # Return default time (tomorrow at 9 AM UTC) if parsing fails
            tomorrow = datetime.now(timezone.utc) + timedelta(days=1)
            default_time = tomorrow.replace(hour=9, minute=0, second=0, microsecond=0)
            logger.warning(f"Using default due time: {default_time.isoformat()}")
            return default_time
    
    def _create_platform_task(self, task_data: TaskCreate, user_info: Dict[str, Any], 
                              initiator_link: Optional[str] = None, 
                              screenshot_data: Optional[Dict[str, Any]] = None,
                              platform_type: Optional[str] = None) -> Tuple[Optional[str], Optional[str]]:
        """Create task on the specified platform.
        
        Args:
            task_data: Task creation data
            user_info: User platform information
            initiator_link: Optional link to original message
            screenshot_data: Optional screenshot data for attachment
            platform_type: Specific platform to create task on
            
        Returns:
            Tuple of (task_id, error_message)
        """
        try:
            if not platform_type:
                platform_type = user_info.get('platform_type', 'todoist')
            
            # Get platform token from settings
            platform_token = None
            if user_info.get('platform_settings'):
                settings = user_info['platform_settings']
                if isinstance(settings, str):
                    try:
                        settings = json.loads(settings)
                    except json.JSONDecodeError:
                        return None, f"Invalid platform settings format"
                
                # Use platform abstraction to get token
                platform_class = TaskPlatformFactory._get_registry().get(platform_type)
                if platform_class:
                    try:
                        # Create temporary instance to get token
                        temp_platform = platform_class("dummy")
                        platform_token = temp_platform.get_token_from_settings(settings)
                    except:
                        # Fallback to generic pattern
                        platform_token = settings.get(f'{platform_type}_token')
                else:
                    # Fallback for unregistered platforms
                    platform_token = settings.get(f'{platform_type}_token')
            
            # Fallback to legacy token
            if not platform_token:
                platform_token = user_info.get('platform_token')
            
            if not platform_token:
                return None, f"No {platform_type} token found"
            
            # Get platform instance
            platform = TaskPlatformFactory.get_platform(platform_type, platform_token)
            if not platform:
                return None, f"Failed to initialize {platform_type} platform"
            
            # Prepare platform-specific task data
            platform_task_data = PlatformTaskData(
                title=task_data.title,
                description=task_data.description,
                due_time=task_data.due_time,
                source_attachment=initiator_link if initiator_link else None
            )
            
            # Add platform-specific settings
            if user_info.get('platform_settings'):
                settings = user_info['platform_settings']
                if isinstance(settings, str):
                    try:
                        settings = json.loads(settings)
                    except json.JSONDecodeError:
                        return None, f"Invalid {platform_type} settings format"
                
                # Add settings to task data
                if platform_type == 'trello':
                    # Try both naming conventions (for backward compatibility)
                    platform_task_data.board_id = settings.get('trello_board_id') or settings.get('board_id')
                    platform_task_data.list_id = settings.get('trello_list_id') or settings.get('list_id')
                    
                    if not platform_task_data.board_id or not platform_task_data.list_id:
                        logger.error(f"Incomplete Trello configuration. Settings: {settings}")
                        return None, "Incomplete Trello configuration"
            
            # Create task on platform
            logger.debug(f"Attempting to create task on {platform_type} with data: {platform_task_data.dict()}")
            task_id = platform.create_task(platform_task_data.dict())
            
            if task_id:
                logger.info(f"Successfully created task with ID: {task_id} on platform {platform_type}")
                
                # Add screenshot attachment if provided
                if screenshot_data:
                    try:
                        success = platform.attach_screenshot(
                            task_id,
                            screenshot_data.get('image_data'),
                            screenshot_data.get('file_name', 'screenshot.jpg')
                        )
                        if success:
                            logger.info(f"Successfully attached screenshot to {platform_type} task {task_id}")
                        else:
                            logger.warning(f"Failed to attach screenshot to {platform_type} task {task_id}")
                    except Exception as e:
                        logger.error(f"Error attaching screenshot to {platform_type} task: {e}")
                
                return task_id, None
            else:
                error_msg = f"Platform {platform_type} returned no task ID - likely authentication failure"
                logger.error(error_msg)
                return None, error_msg
                
        except Exception as e:
            error_msg = f"Error creating task on {platform_type}: {e}"
            logger.error(error_msg)
            return None, error_msg
    
    def _generate_task_url(self, task_id: str, user_info: Dict[str, Any], platform_type: Optional[str] = None) -> Optional[str]:
        """Generate a direct URL to the created task.
        
        Args:
            task_id: Platform task ID
            user_info: User platform information
            platform_type: Specific platform type
            
        Returns:
            Direct URL to the task or None if generation fails
        """
        try:
            if not platform_type:
                platform_type = user_info.get('platform_type', 'todoist')
            
            # Get platform token from settings
            platform_token = None
            if user_info.get('platform_settings'):
                settings = user_info['platform_settings']
                if isinstance(settings, str):
                    try:
                        settings = json.loads(settings)
                    except json.JSONDecodeError:
                        return None
                
                # Use platform abstraction to get token
                platform_class = TaskPlatformFactory._get_registry().get(platform_type)
                if platform_class:
                    try:
                        # Create temporary instance to get token
                        temp_platform = platform_class("dummy")
                        platform_token = temp_platform.get_token_from_settings(settings)
                    except:
                        # Fallback to generic pattern
                        platform_token = settings.get(f'{platform_type}_token')
                else:
                    # Fallback for unregistered platforms
                    platform_token = settings.get(f'{platform_type}_token')
            
            # Fallback to legacy token
            if not platform_token:
                platform_token = user_info.get('platform_token')
            
            if not platform_token:
                return None
            
            # Get platform instance
            platform = TaskPlatformFactory.get_platform(platform_type, platform_token)
            if not platform:
                return None
            
            return platform.get_task_url(task_id)
            
        except Exception as e:
            logger.error(f"Error generating task URL: {e}")
            return None
    
    def _create_partner_task(self, task_data: TaskCreate, partner: Partner, 
                            initiator_link: Optional[str] = None, 
                            screenshot_data: Optional[Dict[str, Any]] = None) -> Tuple[Optional[str], Optional[str]]:
        """Create task for a specific partner.
        
        Args:
            task_data: Task creation data
            partner: Partner to create task for
            initiator_link: Optional link to original message
            screenshot_data: Optional screenshot data for attachment
            
        Returns:
            Tuple of (task_id, error_message)
        """
        try:
            # Get platform instance
            platform = TaskPlatformFactory.get_platform(partner.platform, partner.credentials)
            if not platform:
                return None, f"Failed to initialize {partner.platform} platform"
            
            # Prepare platform-specific task data
            platform_task_data = PlatformTaskData(
                title=task_data.title,
                description=task_data.description,
                due_time=task_data.due_time,
                source_attachment=initiator_link if initiator_link else None
            )
            
            # Add platform-specific configuration
            if partner.platform_config:
                if partner.platform == 'trello':
                    platform_task_data.board_id = partner.platform_config.get('board_id')
                    platform_task_data.list_id = partner.platform_config.get('list_id')
                    
                    if not platform_task_data.board_id or not platform_task_data.list_id:
                        logger.error(f"Incomplete Trello configuration for partner {partner.name}")
                        return None, "Incomplete Trello configuration"
            
            # Create task on platform
            logger.debug(f"Creating task for {partner.name} on {partner.platform} with data: {platform_task_data.dict()}")
            task_id = platform.create_task(platform_task_data.dict())
            
            if task_id:
                logger.info(f"Successfully created task with ID: {task_id} for {partner.name} on {partner.platform}")
                
                # Add screenshot attachment if provided
                if screenshot_data:
                    try:
                        success = platform.attach_screenshot(
                            task_id,
                            screenshot_data.get('image_data'),
                            screenshot_data.get('file_name', 'screenshot.jpg')
                        )
                        if success:
                            logger.info(f"Successfully attached screenshot to {partner.name}'s task {task_id}")
                        else:
                            logger.warning(f"Failed to attach screenshot to {partner.name}'s task {task_id}")
                    except Exception as e:
                        logger.error(f"Error attaching screenshot to {partner.name}'s task: {e}")
                
                return task_id, None
            else:
                error_msg = f"Platform {partner.platform} returned no task ID for {partner.name} - likely authentication failure"
                logger.error(error_msg)
                return None, error_msg
                
        except Exception as e:
            error_msg = f"Error creating task for {partner.name} on {partner.platform}: {e}"
            logger.error(error_msg)
            return None, error_msg
    
    def _generate_partner_task_url(self, task_id: str, partner: Partner) -> Optional[str]:
        """Generate a direct URL to the created task for a partner.
        
        Args:
            task_id: Platform task ID
            partner: Partner who owns the task
            
        Returns:
            Direct URL to the task or None if generation fails
        """
        try:
            # Get platform instance
            platform = TaskPlatformFactory.get_platform(partner.platform, partner.credentials)
            if not platform:
                return None
            
            return platform.get_task_url(task_id)
            
        except Exception as e:
            logger.error(f"Error generating task URL for {partner.name}: {e}")
            return None
    
    def save_user_platform(self, telegram_user_id: int, platform_token: str, 
                          platform_type: str, owner_name: str, 
                          location: Optional[str] = None, 
                          platform_settings: Optional[Dict[str, Any]] = None) -> bool:
        """Save or update user platform information.
        
        Args:
            telegram_user_id: Telegram user ID
            platform_token: Platform API token
            platform_type: Platform type (todoist, trello)
            owner_name: User's name
            location: User's location
            platform_settings: Platform-specific settings
            
        Returns:
            True if successful
            
        Raises:
            ValidationError: If validation fails
        """
        try:
            user_data = UserCreate(
                telegram_user_id=telegram_user_id,
                platform_token=platform_token,
                platform_type=platform_type,
                owner_name=owner_name,
                location=location,
                platform_settings=platform_settings
            )
            
            return self.user_repo.create_or_update(user_data)
            
        except Exception as e:
            logger.error(f"Failed to save user platform info: {e}")
            raise ValidationError(f"Failed to save user platform info: {e}")
    
    def get_user_platform_info(self, telegram_user_id: int) -> Optional[Dict[str, Any]]:
        """Get user's platform information.
        
        Args:
            telegram_user_id: Telegram user ID
            
        Returns:
            Dictionary with platform info or None
        """
        return self.user_repo.get_platform_info(telegram_user_id)
    
    def get_platform_token(self, telegram_user_id: int) -> Optional[str]:
        """Get user's platform token.
        
        Args:
            telegram_user_id: Telegram user ID
            
        Returns:
            Platform token or None
        """
        return self.user_repo.get_platform_token(telegram_user_id)
    
    def get_platform_type(self, telegram_user_id: int) -> str:
        """Get user's platform type.
        
        Args:
            telegram_user_id: Telegram user ID
            
        Returns:
            Platform type (defaults to 'todoist')
        """
        return self.user_repo.get_platform_type(telegram_user_id)
    
    def delete_user_data(self, telegram_user_id: int) -> bool:
        """Delete all user data.
        
        Args:
            telegram_user_id: Telegram user ID
            
        Returns:
            True if successful
        """
        return self.user_repo.delete(telegram_user_id)
    
    def update_platform_config(self, telegram_user_id: int, platform_type: str, token: str, additional_data: dict = None) -> bool:
        """Update platform configuration for multi-platform support."""
        return self.user_repo.update_platform_config(telegram_user_id, platform_type, token, additional_data)
    
    def get_configured_platforms(self, telegram_user_id: int) -> list:
        """Get list of configured platforms for a user."""
        return self.user_repo.get_configured_platforms(telegram_user_id)
    
    def update_notification_preference(self, telegram_user_id: int, enabled: bool) -> bool:
        """Update user's Telegram notification preference."""
        return self.user_repo.update_notification_preference(telegram_user_id, enabled)

# Remove global instance - use DI container instead