"""Application initialization and dependency injection setup."""

from dependency_injector.wiring import inject, Provide
from core.container import ApplicationContainer, container
from core.interfaces import ITaskService, IParsingService, IConfig, IOpenAIService, IVoiceProcessingService, IImageProcessingService


def wire_application():
    """Wire the application modules with the DI container."""
    container.wire(modules=[
        "core.initialization",
        "handlers",
        "keyboards.inline"
    ])


def unwire_application():
    """Unwire the application modules."""
    container.unwire()


class ServiceLocator:
    """Service locator pattern to access DI services."""
    
    @staticmethod
    @inject
    def get_task_service(
        task_service: ITaskService = Provide[ApplicationContainer.task_service]
    ) -> ITaskService:
        return task_service
    
    @staticmethod
    @inject
    def get_parsing_service(
        parsing_service: IParsingService = Provide[ApplicationContainer.parsing_service]
    ) -> IParsingService:
        return parsing_service
    
    @staticmethod
    @inject
    def get_config(
        config: IConfig = Provide[ApplicationContainer.config]
    ) -> IConfig:
        return config
    
    @staticmethod
    @inject
    def get_onboarding_service(
        onboarding_service = Provide[ApplicationContainer.onboarding_service]
    ):
        return onboarding_service
    
    @staticmethod
    @inject
    def get_openai_service(
        openai_service: IOpenAIService = Provide[ApplicationContainer.openai_service]
    ) -> IOpenAIService:
        return openai_service
    
    @staticmethod
    @inject
    def get_voice_processing_service(
        voice_service: IVoiceProcessingService = Provide[ApplicationContainer.voice_processing_service]
    ) -> IVoiceProcessingService:
        return voice_service
    
    @staticmethod
    @inject
    def get_image_processing_service(
        image_service: IImageProcessingService = Provide[ApplicationContainer.image_processing_service]
    ) -> IImageProcessingService:
        return image_service


# Global service locator
services = ServiceLocator()