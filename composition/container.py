"""Application dependency injection composition root."""

from dependency_injector import containers, providers
from dependency_injector.wiring import Provide, inject

from core.interfaces import (IConfig, ITaskRepository, IParsingService, 
                            IOpenAIService, IVoiceProcessingService, IImageProcessingService,
                            IUserPreferencesRepository, IAuthRequestRepository, Intent)
from config import Config
from database.connection import DatabaseManager
from database.repositories import TaskRepository
from database.unified_recipient_repository import UnifiedRecipientRepository
from database.user_preferences_repository import UserPreferencesRepository
from database.auth_request_repository import AuthRequestRepository
from database.user.user_repository import UserRepository
from services.parsing_service import ParsingService
from services.anki_card_service import AnkiCardService
from services.content.reminder_processor import ReminderProcessor
from services.content.anki_processor import AnkiProcessor
from services.content.intent_resolver import IntentResolver
from services.content.classifier import IntentClassifier
from services.content.anki_generation_graph import AnkiGenerationGraph
from services.content.anki_card_set_planner import AnkiCardSetPlanner
from services.content.anki_scenario_planners import (
    ClozeScenarioPlanner,
    TextScenarioPlanner,
    VisualScenarioPlanner,
)
from services.content.anki_quality_evaluator import AnkiRenderedCardEvaluator
from services.recipient_service import RecipientService
from services.recipient_task_service import RecipientTaskService
from services.openai_service import OpenAIService
from ai_workflow_tools.media.image_generation import create_image_generator
from ai_workflow_tools.media.voice_generation import create_voice_generator
from services.voice_processing import VoiceProcessingService
from services.image_processing import ImageProcessingService
from services.oauth_state_manager import OAuthStateManager
from services.google_oauth_service import GoogleOAuthService
from services.sharing_service import SharingService
from services.user_service import UserService
from services.content.router import configure_processors
from helpers.ui_helpers import format_platform_button
from helpers.task_responses import handle_task_creation_response
from keyboards.recipient import get_post_task_actions_keyboard


def _config_attr(config_obj, name: str, default):
    return getattr(config_obj, name, default)


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
    
    user_preferences_repository = providers.Factory(
        UserPreferencesRepository,
        db_manager=database_manager
    )
    
    auth_request_repository = providers.Factory(
        AuthRequestRepository,
        db_manager=database_manager
    )
    
    user_repository = providers.Factory(
        UserRepository,
        db_manager=database_manager
    )
    
    # Services
    parsing_service = providers.Factory(
        ParsingService,
        config=config,
        preferences_repo=user_preferences_repository
    )

    anki_card_service = providers.Factory(
        AnkiCardService,
        config=config
    )
    anki_image_generator = providers.Factory(
        create_image_generator,
        config=config
    )
    anki_voice_generator = providers.Factory(
        create_voice_generator,
        config=config
    )
    anki_card_set_planner = providers.Factory(
        AnkiCardSetPlanner,
        config=config
    )
    anki_text_scenario_planner = providers.Factory(
        TextScenarioPlanner,
        config=config
    )
    anki_cloze_scenario_planner = providers.Factory(
        ClozeScenarioPlanner,
        config=config
    )
    anki_visual_scenario_planner = providers.Factory(
        VisualScenarioPlanner,
        config=config
    )
    anki_quality_evaluator = providers.Factory(
        AnkiRenderedCardEvaluator,
        config=config,
        inspect_images=config.provided.ANKI_QUALITY_INSPECT_IMAGES
    )
    anki_generation_graph = providers.Factory(
        AnkiGenerationGraph,
        anki_card_service=anki_card_service,
        card_set_planner=anki_card_set_planner,
        text_scenario_planner=anki_text_scenario_planner,
        cloze_scenario_planner=anki_cloze_scenario_planner,
        visual_scenario_planner=anki_visual_scenario_planner,
        quality_evaluator=anki_quality_evaluator,
        enable_quality_evaluation=config.provided.ANKI_QUALITY_EVALUATION_ENABLED,
        evaluate_fallback_cards=config.provided.ANKI_EVALUATE_FALLBACK_CARDS,
        max_quality_repairs_per_run=config.provided.ANKI_MAX_QUALITY_REPAIRS_PER_RUN,
        image_generator=anki_image_generator,
        enable_image_generation=config.provided.ANKI_IMAGE_GENERATION_ENABLED,
        enable_auto_image_generation=config.provided.ANKI_AUTO_IMAGE_GENERATION_ENABLED,
        max_image_generations_per_run=config.provided.ANKI_MAX_IMAGE_GENERATIONS_PER_RUN,
        image_model=config.provided.ANKI_IMAGE_MODEL,
        image_size=config.provided.ANKI_IMAGE_SIZE,
        image_quality=config.provided.ANKI_IMAGE_QUALITY,
        image_output_format=config.provided.ANKI_IMAGE_OUTPUT_FORMAT,
        image_provider=providers.Callable(_config_attr, config, "ANKI_IMAGE_PROVIDER", "openai"),
        voice_generator=anki_voice_generator,
        enable_voice_generation=providers.Callable(_config_attr, config, "ANKI_VOICE_GENERATION_ENABLED", True),
        max_voice_generations_per_run=providers.Callable(_config_attr, config, "WORKFLOW_MAX_VOICE_CALLS_PER_RUN", 1),
        voice_model=providers.Callable(_config_attr, config, "ANKI_ELEVENLABS_MODEL", "eleven_multilingual_v2"),
        voice_output_format=providers.Callable(_config_attr, config, "ANKI_ELEVENLABS_OUTPUT_FORMAT", "mp3_44100_128"),
        style_reference_images=providers.List(
            config.provided.ANKI_STYLE_CHARACTER_REFERENCE_IMAGE,
            config.provided.ANKI_STYLE_DESIGN_REFERENCE_IMAGE,
        ),
        style_reference_version=config.provided.ANKI_STYLE_REFERENCE_VERSION,
        capture_observation_detail_text=config.provided.ANKI_OBSERVATION_CAPTURE,
        observation_bundle_dir=config.provided.ANKI_OBSERVATION_DIR,
        observation_retention_limit=config.provided.ANKI_OBSERVATION_HISTORY_LIMIT,
    )

    intent_classifier = providers.Factory(
        IntentClassifier,
        config=config
    )
    intent_resolver = providers.Factory(
        IntentResolver,
        preferences_repo=user_preferences_repository,
        classifier=intent_classifier
    )

    recipient_service = providers.Factory(
        RecipientService,
        repository=unified_recipient_repository,
        preferences_repo=user_preferences_repository,
        config=config
    )
    
    recipient_task_service = providers.Factory(
        RecipientTaskService,
        task_repo=task_repository,
        recipient_service=recipient_service
    )

    # Content-processing seam: intent -> processor (see services/content/router.py)
    reminder_processor = providers.Factory(
        ReminderProcessor,
        parsing_service=parsing_service,
        task_service=recipient_task_service,
        recipient_service=recipient_service,
        task_repository=task_repository,
        task_creation_responder=handle_task_creation_response,
        platform_button_formatter=format_platform_button,
        post_task_keyboard_factory=get_post_task_actions_keyboard,
    )
    anki_processor = providers.Factory(
        AnkiProcessor,
        anki_card_service=anki_card_service,
        preferences_repo=user_preferences_repository,
        anki_graph=anki_generation_graph
    )
    
    
    openai_service = providers.Factory(
        OpenAIService,
        api_key=config.provided.OPENAI_API_KEY,
        config=config,
    )
    
    voice_processing_service = providers.Factory(
        VoiceProcessingService,
        openai_service=openai_service
    )
    
    image_processing_service = providers.Factory(
        ImageProcessingService,
        openai_service=openai_service
    )
    
    # OAuth and Google Calendar services
    oauth_state_manager = providers.Factory(
        OAuthStateManager,
        db_manager=database_manager
    )
    
    google_oauth_service = providers.Factory(
        GoogleOAuthService,
        client_id=config.provided.GOOGLE_CLIENT_ID,
        client_secret=config.provided.GOOGLE_CLIENT_SECRET,
        oauth_state_manager=oauth_state_manager,
    )
    
    # User service
    user_service = providers.Factory(
        UserService,
        user_repository=user_repository
    )
    
    # Sharing service
    sharing_service = providers.Factory(
        SharingService,
        repository=unified_recipient_repository,
        user_service=user_service,
        config=config
    )


# Global container instance
container = ApplicationContainer()

configure_processors({
    Intent.REMINDER: container.reminder_processor,
    Intent.ANKI: container.anki_processor,
})
