"""Clean dependency injection container - recipient system only."""

from dependency_injector import containers, providers
from dependency_injector.wiring import Provide, inject

from core.interfaces import (IConfig, ITaskRepository, IParsingService, 
                            IOpenAIService, IVoiceProcessingService, IImageProcessingService)
from config import Config
from database.connection import DatabaseManager
from database.repositories import TaskRepository
from database.unified_recipient_repository import UnifiedRecipientRepository
from services.parsing_service import ParsingService
from services.recipient_service import RecipientService
from services.recipient_task_service import RecipientTaskService
from services.openai_service import OpenAIService
from services.voice_processing import VoiceProcessingService
from services.image_processing import ImageProcessingService


class ApplicationContainer(containers.DeclarativeContainer):
    """Clean application dependency injection container."""
    
    # Configuration
    config = providers.Singleton(Config)
    
    # Database
    database_manager = providers.Singleton(
        DatabaseManager,
        database_path=config.provided.DATABASE_PATH,
        timeout=config.provided.DATABASE_TIMEOUT
    )
    
    # Repositories
    task_repository = providers.Factory(
        TaskRepository,
        db_manager=database_manager
    )
    
    unified_recipient_repository = providers.Factory(
        UnifiedRecipientRepository,
        db_manager=database_manager
    )
    
    # Services
    parsing_service = providers.Factory(
        ParsingService,
        config=config
    )
    
    recipient_service = providers.Factory(
        RecipientService,
        repository=unified_recipient_repository
    )
    
    recipient_task_service = providers.Factory(
        RecipientTaskService,
        task_repo=task_repository,
        recipient_service=recipient_service
    )
    
    
    openai_service = providers.Factory(
        OpenAIService,
        api_key=config.provided.OPENAI_API_KEY
    )
    
    voice_processing_service = providers.Factory(
        VoiceProcessingService,
        openai_service=openai_service
    )
    
    image_processing_service = providers.Factory(
        ImageProcessingService,
        openai_service=openai_service
    )


# Global container instance
container = ApplicationContainer()