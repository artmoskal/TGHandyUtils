"""Clean recipient DI container configuration."""

from dependency_injector import containers, providers
from dependency_injector.wiring import Provide, inject

from core.recipient_interfaces import (
    IUserPlatformRepository, ISharedRecipientRepository, 
    IUserPreferencesV2Repository, IRecipientService
)
from database.connection import DatabaseManager
from database.recipient_repositories import (
    UserPlatformRepository, SharedRecipientRepository, UserPreferencesV2Repository
)
from services.recipient_service import RecipientService
from services.recipient_task_service import RecipientTaskService
from services.unified_recipient_service import UnifiedRecipientService
from core.interfaces import ITaskRepository


class RecipientContainer(containers.DeclarativeContainer):
    """DI container for clean recipient system."""
    
    # Database manager (from main container)
    database_manager = providers.Dependency()
    task_repository = providers.Dependency()
    
    # Repositories
    user_platform_repository = providers.Factory(
        UserPlatformRepository,
        db_manager=database_manager
    )
    
    shared_recipient_repository = providers.Factory(
        SharedRecipientRepository,
        db_manager=database_manager
    )
    
    user_preferences_v2_repository = providers.Factory(
        UserPreferencesV2Repository,
        db_manager=database_manager
    )
    
    # Services
    recipient_service = providers.Factory(
        RecipientService,
        platform_repo=user_platform_repository,
        shared_repo=shared_recipient_repository,
        prefs_repo=user_preferences_v2_repository
    )
    
    unified_recipient_service = providers.Factory(
        UnifiedRecipientService,
        recipient_service=recipient_service
    )
    
    recipient_task_service = providers.Factory(
        RecipientTaskService,
        task_repo=task_repository,
        recipient_service=recipient_service,
        unified_recipient_service=unified_recipient_service
    )


# Global recipient container instance
recipient_container = RecipientContainer()


@inject
def get_recipient_service(
    service: IRecipientService = Provide[RecipientContainer.recipient_service]
) -> IRecipientService:
    """Get recipient service instance."""
    return service


@inject
def get_recipient_task_service(
    service = Provide[RecipientContainer.recipient_task_service]
):
    """Get recipient task service instance."""
    return service


@inject
def get_user_platform_repository(
    repo: IUserPlatformRepository = Provide[RecipientContainer.user_platform_repository]
) -> IUserPlatformRepository:
    """Get user platform repository instance."""
    return repo


@inject
def get_shared_recipient_repository(
    repo: ISharedRecipientRepository = Provide[RecipientContainer.shared_recipient_repository]
) -> ISharedRecipientRepository:
    """Get shared recipient repository instance."""
    return repo


@inject
def get_user_preferences_v2_repository(
    repo: IUserPreferencesV2Repository = Provide[RecipientContainer.user_preferences_v2_repository]
) -> IUserPreferencesV2Repository:
    """Get user preferences repository instance."""
    return repo


@inject
def get_unified_recipient_service(
    service = Provide[RecipientContainer.unified_recipient_service]
):
    """Get unified recipient service instance."""
    return service


def wire_recipient_container(main_container):
    """Wire recipient container with main container dependencies."""
    recipient_container.database_manager.override(main_container.database_manager)
    recipient_container.task_repository.override(main_container.task_repository)
    
    # Wire modules
    recipient_container.wire(modules=[
        "handlers",
        "keyboards.recipient",
        "core.recipient_container"
    ])