"""Dependency injection container configuration."""

from dependency_injector import containers, providers
from dependency_injector.wiring import Provide, inject

from core.interfaces import (IConfig, ITaskRepository, IUserRepository, IParsingService, 
                            ITaskService, IOpenAIService, IVoiceProcessingService, IImageProcessingService,
                            IPartnerRepository, IUserPreferencesRepository, IPartnerService, IUserPreferencesService)
from config import Config
from database.connection import DatabaseManager
from database.repositories import TaskRepository, UserRepository
from database.partner_repository import PartnerRepository, UserPreferencesRepository
from services.parsing_service import ParsingService
from services.task_service import TaskService
from services.partner_service import PartnerService, UserPreferencesService
from services.onboarding_service import OnboardingService
from services.openai_service import OpenAIService
from services.voice_processing import VoiceProcessingService
from services.image_processing import ImageProcessingService


class ApplicationContainer(containers.DeclarativeContainer):
    """Main application dependency injection container."""
    
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
    user_repository = providers.Factory(
        UserRepository,
        db_manager=database_manager
    )
    partner_repository = providers.Factory(
        PartnerRepository,
        db_manager=database_manager
    )
    user_preferences_repository = providers.Factory(
        UserPreferencesRepository,
        db_manager=database_manager
    )
    
    # Services
    parsing_service = providers.Factory(
        ParsingService,
        config=config
    )
    
    partner_service = providers.Factory(
        PartnerService,
        partner_repo=partner_repository,
        prefs_repo=user_preferences_repository
    )
    user_preferences_service = providers.Factory(
        UserPreferencesService,
        prefs_repo=user_preferences_repository
    )
    
    task_service = providers.Factory(
        TaskService,
        task_repo=task_repository,
        user_repo=user_repository,
        partner_service=partner_service,
        prefs_service=user_preferences_service
    )
    
    onboarding_service = providers.Factory(
        OnboardingService,
        task_service=task_service
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


def get_container() -> ApplicationContainer:
    """Get the global container instance."""
    return container


@inject
def get_task_service(
    task_service: ITaskService = Provide[ApplicationContainer.task_service]
) -> ITaskService:
    """Get task service instance."""
    return task_service


@inject  
def get_parsing_service(
    parsing_service: IParsingService = Provide[ApplicationContainer.parsing_service]
) -> IParsingService:
    """Get parsing service instance."""
    return parsing_service


@inject
def get_task_repository(
    task_repo: ITaskRepository = Provide[ApplicationContainer.task_repository]
) -> ITaskRepository:
    """Get task repository instance."""
    return task_repo


@inject
def get_user_repository(
    user_repo: IUserRepository = Provide[ApplicationContainer.user_repository]
) -> IUserRepository:
    """Get user repository instance."""
    return user_repo


@inject
def get_config(
    config: IConfig = Provide[ApplicationContainer.config]
) -> IConfig:
    """Get config instance."""
    return config


@inject
def get_openai_service(
    openai_service: IOpenAIService = Provide[ApplicationContainer.openai_service]
) -> IOpenAIService:
    """Get OpenAI service instance."""
    return openai_service


@inject
def get_voice_processing_service(
    voice_service: IVoiceProcessingService = Provide[ApplicationContainer.voice_processing_service]
) -> IVoiceProcessingService:
    """Get voice processing service instance."""
    return voice_service


@inject
def get_image_processing_service(
    image_service: IImageProcessingService = Provide[ApplicationContainer.image_processing_service]
) -> IImageProcessingService:
    """Get image processing service instance."""
    return image_service


@inject
def get_partner_service(
    partner_service: IPartnerService = Provide[ApplicationContainer.partner_service]
) -> IPartnerService:
    """Get partner service instance."""
    return partner_service


@inject
def get_user_preferences_service(
    prefs_service: IUserPreferencesService = Provide[ApplicationContainer.user_preferences_service]
) -> IUserPreferencesService:
    """Get user preferences service instance."""
    return prefs_service


@inject
def get_partner_repository(
    partner_repo: IPartnerRepository = Provide[ApplicationContainer.partner_repository]
) -> IPartnerRepository:
    """Get partner repository instance."""
    return partner_repo


@inject
def get_user_preferences_repository(
    prefs_repo: IUserPreferencesRepository = Provide[ApplicationContainer.user_preferences_repository]
) -> IUserPreferencesRepository:
    """Get user preferences repository instance."""
    return prefs_repo