"""Abstract interfaces for dependency injection."""

from abc import ABC, abstractmethod
from typing import Optional, Dict, Any, List, Tuple, BinaryIO, Union
from models.task import TaskCreate, TaskDB
from models.user import UserCreate, UserDB
from models.partner import Partner, PartnerCreate, PartnerUpdate, UserPreferences, UserPreferencesCreate, UserPreferencesUpdate


class ITaskRepository(ABC):
    """Abstract interface for task repository."""
    
    @abstractmethod
    def create(self, user_id: int, chat_id: int, message_id: int, 
               task_data: TaskCreate, platform_task_id: Optional[str] = None, 
               platform_type: str = 'todoist') -> Optional[int]:
        """Create a new task."""
        pass
    
    @abstractmethod
    def get_all(self) -> List[TaskDB]:
        """Get all tasks."""
        pass
    
    @abstractmethod
    def get_by_user(self, user_id: int) -> List[TaskDB]:
        """Get tasks by user."""
        pass
    
    @abstractmethod
    def delete(self, task_id: int) -> bool:
        """Delete a task."""
        pass
    
    @abstractmethod
    def update_platform_id(self, task_id: int, platform_task_id: str, platform_type: str) -> bool:
        """Update task with platform ID."""
        pass


class IUserRepository(ABC):
    """Abstract interface for user repository."""
    
    @abstractmethod
    def create_or_update(self, user_data: UserCreate) -> bool:
        """Create or update user."""
        pass
    
    @abstractmethod
    def get_by_telegram_id(self, telegram_user_id: int) -> Optional[UserDB]:
        """Get user by Telegram ID."""
        pass
    
    @abstractmethod
    def get_platform_info(self, telegram_user_id: int) -> Optional[Dict[str, Any]]:
        """Get user platform info."""
        pass
    
    @abstractmethod
    def get_platform_token(self, telegram_user_id: int) -> Optional[str]:
        """Get user platform token."""
        pass
    
    @abstractmethod
    def get_platform_type(self, telegram_user_id: int) -> str:
        """Get user platform type."""
        pass
    
    @abstractmethod
    def delete(self, telegram_user_id: int) -> bool:
        """Delete user."""
        pass


class IParsingService(ABC):
    """Abstract interface for parsing service."""
    
    @abstractmethod
    def parse_content_to_task(self, content_message: str, owner_name: Optional[str] = None, 
                             location: Optional[str] = None) -> Optional[Dict[str, Any]]:
        """Parse content message into structured task data."""
        pass
    
    @abstractmethod
    def get_timezone_offset(self, location: Optional[str]) -> int:
        """Get timezone offset for location."""
        pass
    
    @abstractmethod
    def convert_utc_to_local_display(self, utc_time_str: str, location: Optional[str]) -> str:
        """Convert UTC time to local display."""
        pass


class ITaskService(ABC):
    """Abstract interface for task service."""
    
    @abstractmethod
    async def create_task(self, user_id: int, chat_id: int, message_id: int, 
                         task_data: TaskCreate, initiator_link: Optional[str] = None) -> bool:
        """Create a task."""
        pass
    
    @abstractmethod
    def save_user_platform(self, telegram_user_id: int, platform_token: str, 
                          platform_type: str, owner_name: str, 
                          location: Optional[str] = None, 
                          platform_settings: Optional[Dict[str, Any]] = None) -> bool:
        """Save user platform info."""
        pass
    
    @abstractmethod
    def get_user_platform_info(self, telegram_user_id: int) -> Optional[Dict[str, Any]]:
        """Get user platform info."""
        pass
    
    @abstractmethod
    def get_platform_token(self, telegram_user_id: int) -> Optional[str]:
        """Get platform token."""
        pass
    
    @abstractmethod
    def get_platform_type(self, telegram_user_id: int) -> str:
        """Get platform type."""
        pass
    
    @abstractmethod
    def delete_user_data(self, telegram_user_id: int) -> bool:
        """Delete user data."""
        pass


class IConfig(ABC):
    """Abstract interface for configuration."""
    
    @property
    @abstractmethod
    def TELEGRAM_BOT_TOKEN(self) -> str:
        pass
    
    @property
    @abstractmethod
    def OPENAI_API_KEY(self) -> str:
        pass
    
    @property
    @abstractmethod
    def DATABASE_PATH(self) -> str:
        pass
    
    @property
    @abstractmethod
    def DATABASE_TIMEOUT(self) -> int:
        pass
    
    @property
    @abstractmethod
    def DEFAULT_TASK_PLATFORM(self) -> str:
        pass
    
    @property
    @abstractmethod
    def SUPPORTED_PLATFORMS(self) -> List[str]:
        pass


class IOpenAIService(ABC):
    """Abstract interface for OpenAI service."""
    
    @abstractmethod
    async def transcribe_audio(self, audio_data: BinaryIO) -> str:
        """Transcribe audio data using OpenAI Whisper."""
        pass
    
    @abstractmethod
    async def analyze_image(self, image_data: bytes, prompt: str = None) -> str:
        """Analyze image and extract text/content using OpenAI Vision."""
        pass


class IVoiceProcessingService(ABC):
    """Abstract interface for voice processing service."""
    
    @abstractmethod
    async def process_voice_message(self, voice, bot) -> str:
        """Process a voice message and return transcribed text."""
        pass


class IImageProcessingService(ABC):
    """Abstract interface for image processing service."""
    
    @abstractmethod
    async def process_image_message(self, media: Union[List, Any], bot) -> Dict[str, Any]:
        """Process an image message (photo list or document) and return analyzed content."""
        pass


class IPartnerRepository(ABC):
    """Abstract interface for partner repository."""
    
    @abstractmethod
    def get_user_partners(self, user_id: int) -> List[Partner]:
        """Get all partners for a user."""
        pass
    
    @abstractmethod
    def get_enabled_partners(self, user_id: int) -> List[Partner]:
        """Get only enabled partners for a user."""
        pass
    
    @abstractmethod
    def get_self_partner(self, user_id: int) -> Optional[Partner]:
        """Get the user's self partner."""
        pass
    
    @abstractmethod
    def get_partner_by_id(self, user_id: int, partner_id: str) -> Optional[Partner]:
        """Get a specific partner by ID."""
        pass
    
    @abstractmethod
    def add_partner(self, user_id: int, partner: PartnerCreate) -> str:
        """Add a new partner for a user. Returns the generated partner_id."""
        pass
    
    @abstractmethod
    def update_partner(self, user_id: int, partner_id: str, updates: PartnerUpdate) -> bool:
        """Update a partner's information."""
        pass
    
    @abstractmethod
    def delete_partner(self, user_id: int, partner_id: str) -> bool:
        """Delete a partner."""
        pass
    
    @abstractmethod
    def get_partners_by_ids(self, user_id: int, partner_ids: List[str]) -> List[Partner]:
        """Get multiple partners by their IDs."""
        pass


class IUserPreferencesRepository(ABC):
    """Abstract interface for user preferences repository."""
    
    @abstractmethod
    def get_preferences(self, user_id: int) -> Optional[UserPreferences]:
        """Get user preferences."""
        pass
    
    @abstractmethod
    def create_or_update_preferences(self, user_id: int, prefs: UserPreferencesCreate) -> bool:
        """Create or update user preferences."""
        pass
    
    @abstractmethod
    def update_preferences(self, user_id: int, updates: UserPreferencesUpdate) -> bool:
        """Update specific user preferences."""
        pass


class IPartnerService(ABC):
    """Abstract interface for partner service."""
    
    @abstractmethod
    def get_user_partners(self, user_id: int) -> List[Partner]:
        """Get all partners for a user."""
        pass
    
    @abstractmethod
    def get_enabled_partners(self, user_id: int) -> List[Partner]:
        """Get only enabled partners for a user."""
        pass
    
    @abstractmethod
    def get_default_partners(self, user_id: int) -> List[Partner]:
        """Get the user's default partners for task creation."""
        pass
    
    @abstractmethod
    def add_partner(self, user_id: int, partner: PartnerCreate) -> str:
        """Add a new partner for a user."""
        pass
    
    @abstractmethod
    def update_partner(self, user_id: int, partner_id: str, updates: PartnerUpdate) -> bool:
        """Update a partner's information."""
        pass
    
    @abstractmethod
    def delete_partner(self, user_id: int, partner_id: str) -> bool:
        """Delete a partner."""
        pass
    
    @abstractmethod
    def migrate_legacy_user(self, user_id: int, platform_type: str, credentials: str, platform_config: Optional[Dict[str, Any]] = None) -> str:
        """Migrate a legacy user to the partner system."""
        pass


class IUserPreferencesService(ABC):
    """Abstract interface for user preferences service."""
    
    @abstractmethod
    def get_preferences(self, user_id: int) -> Optional[UserPreferences]:
        """Get user preferences."""
        pass
    
    @abstractmethod
    def get_preferences_or_default(self, user_id: int) -> UserPreferences:
        """Get user preferences or return defaults."""
        pass
    
    @abstractmethod
    def update_sharing_ui_enabled(self, user_id: int, enabled: bool) -> bool:
        """Update the sharing UI visibility setting."""
        pass
    
    @abstractmethod
    def update_telegram_notifications(self, user_id: int, enabled: bool) -> bool:
        """Update Telegram notifications setting."""
        pass
    
    @abstractmethod
    def update_default_partners(self, user_id: int, partner_ids: List[str]) -> bool:
        """Update default partners for task creation."""
        pass
    
    @abstractmethod
    def is_sharing_ui_enabled(self, user_id: int) -> bool:
        """Check if sharing UI should be shown for user."""
        pass
    
    @abstractmethod
    def get_telegram_notifications_enabled(self, user_id: int) -> bool:
        """Check if Telegram notifications are enabled for user."""
        pass