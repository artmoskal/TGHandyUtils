"""Abstract interfaces for dependency injection."""

from abc import ABC, abstractmethod
from typing import Optional, Dict, Any, List, Tuple, BinaryIO, Union
from models.task import TaskCreate, TaskDB


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


