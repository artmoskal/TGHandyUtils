import os
import logging
from pathlib import Path
from typing import Any, Optional, List
from dotenv import load_dotenv
from core.interfaces import IConfig
from ai_workflow_engine.config_loader import WorkflowConfigBundle, load_workflow_config

load_dotenv()


CONFIG_DIR = Path(__file__).resolve().parent / "config"

WORKFLOW_OVERRIDE_MAP = {
    "DEFAULT_MODEL": ("models", "default", "model"),
    "SUPERVISOR_MODEL": ("models", "supervisor", "model"),
    "CARD_MODEL": ("models", "anki_card", "model"),
    "DECISION_MODEL": ("models", "anki_decision", "model"),
    "SCENARIO_MODEL": ("models", "anki_scenario", "model"),
    "RENDER_MODEL": ("models", "anki_render", "model"),
    "QUALITY_MODEL": ("models", "anki_quality", "model"),
    "COMPLEX_SUPERVISOR_MODEL": ("models", "anki_complex_supervisor", "model"),
    "OPENAI_IMAGE_MODEL": ("models", "anki_openai_image", "model"),
    "GEMINI_IMAGE_MODEL": ("models", "anki_gemini_image", "model"),
    "IMAGE_MODEL": ("settings", "anki_image_model"),
    "IMAGE_STABLE_MODEL": ("models", "anki_stable_image", "model"),
    "ELEVENLABS_MODEL": ("models", "anki_elevenlabs_voice", "model"),
    "TASK_PARSING_MODEL": ("settings", "task_parsing_model"),
    "PROMPT_CACHE_KEY_PREFIX": ("settings", "openai_prompt_cache_key_prefix"),
    "PROMPT_CACHE_RETENTION": ("settings", "openai_prompt_cache_retention"),
    "QUALITY_EVALUATION_ENABLED": ("settings", "anki_quality_evaluation_enabled"),
    "QUALITY_INSPECT_IMAGES": ("settings", "anki_quality_inspect_images"),
    "EVALUATE_FALLBACK_CARDS": ("settings", "anki_evaluate_fallback_cards"),
    "MAX_QUALITY_REPAIRS_PER_RUN": ("settings", "anki_max_quality_repairs_per_run"),
    "MAX_IMAGE_GENERATIONS_PER_RUN": ("settings", "anki_max_image_generations_per_run"),
    "IMAGE_GENERATION_ENABLED": ("settings", "anki_image_generation_enabled"),
    "AUTO_IMAGE_GENERATION_ENABLED": ("settings", "anki_auto_image_generation_enabled"),
    "IMAGE_PROVIDER": ("settings", "anki_image_provider"),
    "IMAGE_COMPARE_PROVIDERS": ("settings", "anki_image_compare_providers"),
    "IMAGE_COMPARISON_PRIMARY_PROVIDER": ("settings", "anki_image_comparison_primary_provider"),
    "GEMINI_RESPONSE_FORMAT_ENABLED": ("settings", "anki_gemini_response_format_enabled"),
    "IMAGE_SIZE": ("settings", "anki_image_size"),
    "IMAGE_QUALITY": ("settings", "anki_image_quality"),
    "IMAGE_OUTPUT_FORMAT": ("settings", "anki_image_output_format"),
    "GEMINI_IMAGE_SIZE": ("settings", "anki_gemini_image_size"),
    "IMAGE_PROVIDER_TIMEOUT_SECONDS": ("settings", "anki_image_provider_timeout_seconds"),
    "STYLE_CHARACTER_REFERENCE_IMAGE": ("settings", "anki_style_character_reference_image"),
    "STYLE_DESIGN_REFERENCE_IMAGE": ("settings", "anki_style_design_reference_image"),
    "STYLE_REFERENCE_VERSION": ("settings", "anki_style_reference_version"),
    "VOICE_GENERATION_ENABLED": ("settings", "anki_voice_generation_enabled"),
    "VOICE_PROVIDER": ("settings", "anki_voice_provider"),
    "ELEVENLABS_VOICE_ID": ("settings", "anki_elevenlabs_voice_id"),
    "ELEVENLABS_OUTPUT_FORMAT": ("settings", "anki_elevenlabs_output_format"),
    "ELEVENLABS_TIMEOUT_SECONDS": ("settings", "anki_elevenlabs_timeout_seconds"),
    "ELEVENLABS_USD_PER_1K_CHARS": ("settings", "anki_elevenlabs_usd_per_1k_chars"),
    "USAGE_TRACKING_ENABLED": ("settings", "workflow_usage_tracking_enabled"),
    "SHOW_USAGE_IN_REPLY": ("settings", "workflow_show_usage_in_reply"),
    "MAX_TEXT_CALLS_PER_RUN": ("settings", "workflow_max_text_calls_per_run"),
    "MAX_IMAGE_CALLS_PER_RUN": ("settings", "workflow_max_image_calls_per_run"),
    "MAX_VOICE_CALLS_PER_RUN": ("settings", "workflow_max_voice_calls_per_run"),
    "MAX_ESTIMATED_USD_PER_RUN": ("settings", "workflow_max_estimated_usd_per_run"),
    "MODEL_PRICE_OVERRIDES_JSON": ("settings", "workflow_model_price_overrides_json"),
    "DATABASE_TIMEOUT": ("settings", "database_timeout"),
    "DEFAULT_TASK_PLATFORM": ("settings", "default_task_platform"),
    "SCHEDULER_INTERVAL": ("settings", "scheduler_interval"),
    "THREAD_TIMEOUT": ("settings", "thread_timeout"),
    "ALLOW_SELF_AUTH_REQUESTS": ("settings", "allow_self_auth_requests"),
}


def _load_workflow_bundle() -> WorkflowConfigBundle:
    paths = [
        path
        for path in (CONFIG_DIR / "workflow.yaml", CONFIG_DIR / "anki.profile.yaml")
        if path.exists()
    ]
    return load_workflow_config(
        paths,
        env_prefixes=("WORKFLOW", "ANKI"),
        override_map=WORKFLOW_OVERRIDE_MAP,
    )


WORKFLOW_CONFIG = _load_workflow_bundle()


def _setting(name: str, default: Any = None) -> Any:
    return WORKFLOW_CONFIG.settings.get(name, default)


def _model(name: str, default: str) -> str:
    profile = WORKFLOW_CONFIG.models.get(name)
    return profile.model if profile else default


def _bool_setting(name: str, default: bool = False) -> bool:
    value = _setting(name, default)
    if isinstance(value, bool):
        return value
    return str(value).lower() == "true"


def _int_setting(name: str, default: int = 0) -> int:
    return int(_setting(name, default))


def _float_setting(name: str, default: float = 0.0) -> float:
    return float(_setting(name, default))


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return str(raw).strip().lower() not in {"0", "false", "no", "off"}


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        return int(raw)
    except (TypeError, ValueError):
        logging.warning("Invalid %s=%r; using %s", name, raw, default)
        return default


class Config(IConfig):
    """Centralized configuration management."""
    
    # Telegram Bot Configuration
    TELEGRAM_BOT_TOKEN: str = os.getenv('TELEGRAM_BOT_TOKEN', '')
    
    # OpenAI Configuration
    OPENAI_API_KEY: str = os.getenv('OPENAI_API_KEY', '')
    # Optional OpenAI-compatible base URL (e.g. ollama at http://host:11434/v1) for local/offline
    # testing. When set, create_chat_llm points the chat client here and skips the OpenAI key/cache.
    LLM_BASE_URL: str = os.getenv('LLM_BASE_URL', '')
    GEMINI_API_KEY: str = os.getenv('GEMINI_API_KEY', '')
    # Optional OpenAI prompt-cache routing hint. Prompt layout remains provider-neutral; this only
    # improves cache affinity for OpenAI-backed chat calls.
    OPENAI_PROMPT_CACHE_KEY_PREFIX: str = os.getenv(
        'OPENAI_PROMPT_CACHE_KEY_PREFIX',
        str(_setting('openai_prompt_cache_key_prefix', 'tghandyutils')),
    )
    OPENAI_PROMPT_CACHE_RETENTION: str = os.getenv(
        'OPENAI_PROMPT_CACHE_RETENTION',
        str(_setting('openai_prompt_cache_retention', '')),
    )
    TASK_PARSING_MODEL: str = str(_setting('task_parsing_model', 'gpt-5.4-mini'))
    ANKI_CARD_MODEL: str = _model('anki_card', 'gpt-5.4-mini')
    WORKFLOW_DEFAULT_MODEL: str = _model('default', ANKI_CARD_MODEL)
    # Per-node model profiles. These let cheap gates and stronger scenario/rendering nodes diverge.
    # CODE-DEFAULT layer only: API models. The LIVE layer (config/anki.profile.yaml)
    # currently routes all four roles to 'claude-p'. Routable backend names any role can
    # use: 'claude-p', 'codex-exec', 'chatgpt-web' (services/llm_factory.py registry).
    ANKI_DECISION_MODEL: str = _model('anki_decision', ANKI_CARD_MODEL)
    ANKI_SCENARIO_MODEL: str = _model('anki_scenario', ANKI_CARD_MODEL)
    ANKI_RENDER_MODEL: str = _model('anki_render', ANKI_CARD_MODEL)
    ANKI_QUALITY_MODEL: str = _model('anki_quality', ANKI_CARD_MODEL)
    ANKI_COMPLEX_SUPERVISOR_MODEL: str = _model('anki_complex_supervisor', 'gpt-5.5')
    WORKFLOW_SUPERVISOR_MODEL: str = _model('supervisor', ANKI_COMPLEX_SUPERVISOR_MODEL)
    ANKI_QUALITY_EVALUATION_ENABLED: bool = _bool_setting('anki_quality_evaluation_enabled', True)
    ANKI_QUALITY_INSPECT_IMAGES: bool = _bool_setting('anki_quality_inspect_images', True)
    ANKI_EVALUATE_FALLBACK_CARDS: bool = _bool_setting('anki_evaluate_fallback_cards', False)
    ANKI_MAX_QUALITY_REPAIRS_PER_RUN: int = _int_setting('anki_max_quality_repairs_per_run', 1)
    ANKI_MAX_IMAGE_GENERATIONS_PER_RUN: int = _int_setting('anki_max_image_generations_per_run', 1)
    # Generated-visual card support. Explicit `[i gen]` is enabled by default; automatic
    # AI-chosen image generation is controlled separately to avoid surprise image API cost.
    ANKI_IMAGE_GENERATION_ENABLED: bool = _bool_setting('anki_image_generation_enabled', True)
    ANKI_AUTO_IMAGE_GENERATION_ENABLED: bool = _bool_setting('anki_auto_image_generation_enabled', False)
    # Default image provider is the ChatGPT-browser subscription service. Reference
    # images are supported (FR-1 shipped 2026-07-02): PPLA style refs ride along and a
    # style-dropped result is refused loudly.
    ANKI_IMAGE_PROVIDER: str = str(_setting('anki_image_provider', 'chatgpt')).lower()
    WORKFLOW_IMAGE_PROVIDER: str = ANKI_IMAGE_PROVIDER
    ANKI_IMAGE_COMPARE_PROVIDERS: str = str(_setting('anki_image_compare_providers', ''))
    WORKFLOW_IMAGE_COMPARE_PROVIDERS: str = ANKI_IMAGE_COMPARE_PROVIDERS
    ANKI_IMAGE_COMPARISON_PRIMARY_PROVIDER: str = str(_setting('anki_image_comparison_primary_provider', ''))
    WORKFLOW_IMAGE_COMPARISON_PRIMARY_PROVIDER: str = ANKI_IMAGE_COMPARISON_PRIMARY_PROVIDER
    ANKI_OPENAI_IMAGE_MODEL: str = _model('anki_openai_image', 'gpt-image-2')
    WORKFLOW_OPENAI_IMAGE_MODEL: str = ANKI_OPENAI_IMAGE_MODEL
    ANKI_GEMINI_IMAGE_MODEL: str = _model('anki_gemini_image', 'gemini-3.1-flash-image')
    WORKFLOW_GEMINI_IMAGE_MODEL: str = ANKI_GEMINI_IMAGE_MODEL
    ANKI_GEMINI_RESPONSE_FORMAT_ENABLED: bool = _bool_setting('anki_gemini_response_format_enabled', False)
    WORKFLOW_GEMINI_RESPONSE_FORMAT_ENABLED: bool = ANKI_GEMINI_RESPONSE_FORMAT_ENABLED
    _DEFAULT_ANKI_IMAGE_MODEL_BY_PROVIDER = {
        'openai': 'gpt-image-2',
        'gpt-image': 'gpt-image-2',
        'gemini': ANKI_GEMINI_IMAGE_MODEL,
        'google': ANKI_GEMINI_IMAGE_MODEL,
        'nano-banana': ANKI_GEMINI_IMAGE_MODEL,
        'nanobanana': ANKI_GEMINI_IMAGE_MODEL,
        'chatgpt': 'chatgpt-web',
        'chatgpt-browser': 'chatgpt-web',
        'chatgpt-web': 'chatgpt-web',
        'comparison': ANKI_OPENAI_IMAGE_MODEL,
        'compare': ANKI_OPENAI_IMAGE_MODEL,
    }
    ANKI_IMAGE_MODEL: str = str(
        _setting(
            'anki_image_model',
            _DEFAULT_ANKI_IMAGE_MODEL_BY_PROVIDER.get(ANKI_IMAGE_PROVIDER, 'gpt-image-2'),
        )
    )
    ANKI_IMAGE_STABLE_MODEL: str = _model('anki_stable_image', 'gpt-image-2-2026-04-21')
    ANKI_IMAGE_SIZE: str = str(_setting('anki_image_size', '1536x1024'))
    ANKI_IMAGE_QUALITY: str = str(_setting('anki_image_quality', 'low'))
    ANKI_IMAGE_OUTPUT_FORMAT: str = str(_setting('anki_image_output_format', 'png'))
    ANKI_GEMINI_IMAGE_SIZE: str = str(_setting('anki_gemini_image_size', ''))
    WORKFLOW_GEMINI_IMAGE_SIZE: str = ANKI_GEMINI_IMAGE_SIZE
    ANKI_IMAGE_PROVIDER_TIMEOUT_SECONDS: int = _int_setting('anki_image_provider_timeout_seconds', 120)
    WORKFLOW_IMAGE_PROVIDER_TIMEOUT_SECONDS: int = ANKI_IMAGE_PROVIDER_TIMEOUT_SECONDS
    # ChatGPT-browser service (subscription ChatGPT over HTTP on the always-on box).
    # No default endpoint: selecting the provider without the URL fails loudly.
    # Calls are synchronous and slow (image ~30-90s, sequential single browser) — the
    # timeout is the SERVICE-side budget; the HTTP read timeout adds +30s on top.
    CHATGPT_BROWSER_API_URL: str = os.getenv('CHATGPT_BROWSER_API_URL', '')
    WORKFLOW_CHATGPT_BROWSER_URL: str = CHATGPT_BROWSER_API_URL
    ANKI_CHATGPT_BROWSER_TIMEOUT_SECONDS: int = _int_setting('anki_chatgpt_browser_timeout_seconds', 340)
    WORKFLOW_CHATGPT_BROWSER_TIMEOUT_SECONDS: int = ANKI_CHATGPT_BROWSER_TIMEOUT_SECONDS
    # The service caches identical prompts; force-fresh appends a variation token so
    # retries/regenerations produce a new image instead of replaying the cache.
    ANKI_CHATGPT_BROWSER_FORCE_FRESH: bool = _bool_setting('anki_chatgpt_browser_force_fresh', True)
    WORKFLOW_CHATGPT_BROWSER_FORCE_FRESH: bool = ANKI_CHATGPT_BROWSER_FORCE_FRESH
    # Image generation may ride out the service's 15-min account-protection cooldown
    # (owner decision 2026-07-03: images are occasional; waiting beats failing — the
    # TG user is kept informed by the processor's progress ticker).
    ANKI_CHATGPT_BROWSER_RATE_WAIT_MAX_SECONDS: int = _int_setting('anki_chatgpt_browser_rate_wait_max_seconds', 1200)
    WORKFLOW_CHATGPT_BROWSER_RATE_WAIT_MAX_SECONDS: int = ANKI_CHATGPT_BROWSER_RATE_WAIT_MAX_SECONDS
    # claude -p console backend (routing key 'claude-p' in any anki role model setting).
    # Runtime requirement: the claude CLI installed + authenticated in the bot process env.
    ANKI_CLAUDE_P_TIMEOUT_SECONDS: int = _int_setting('anki_claude_p_timeout_seconds', 240)
    # codex exec console backend (routing key 'codex-exec'); binary + auth required at runtime.
    ANKI_CODEX_TIMEOUT_SECONDS: int = _int_setting('anki_codex_timeout_seconds', 240)
    ANKI_STYLE_CHARACTER_REFERENCE_IMAGE: str = str(_setting('anki_style_character_reference_image', ''))
    ANKI_STYLE_DESIGN_REFERENCE_IMAGE: str = str(_setting('anki_style_design_reference_image', ''))
    ANKI_STYLE_REFERENCE_VERSION: str = str(_setting('anki_style_reference_version', ''))
    # Optional Anki language-card voice generation.
    ELEVENLABS_API_KEY: str = os.getenv('ELEVENLABS_API_KEY', '')
    ANKI_VOICE_GENERATION_ENABLED: bool = _bool_setting('anki_voice_generation_enabled', True)
    ANKI_VOICE_PROVIDER: str = str(_setting('anki_voice_provider', 'elevenlabs')).lower()
    WORKFLOW_VOICE_PROVIDER: str = ANKI_VOICE_PROVIDER
    ANKI_ELEVENLABS_VOICE_ID: str = str(_setting('anki_elevenlabs_voice_id', ''))
    WORKFLOW_ELEVENLABS_VOICE_ID: str = ANKI_ELEVENLABS_VOICE_ID
    ANKI_ELEVENLABS_MODEL: str = _model('anki_elevenlabs_voice', 'eleven_multilingual_v2')
    WORKFLOW_ELEVENLABS_MODEL: str = ANKI_ELEVENLABS_MODEL
    ANKI_ELEVENLABS_OUTPUT_FORMAT: str = str(_setting('anki_elevenlabs_output_format', 'mp3_44100_128'))
    WORKFLOW_ELEVENLABS_OUTPUT_FORMAT: str = ANKI_ELEVENLABS_OUTPUT_FORMAT
    ANKI_ELEVENLABS_TIMEOUT_SECONDS: int = _int_setting('anki_elevenlabs_timeout_seconds', 60)
    WORKFLOW_ELEVENLABS_TIMEOUT_SECONDS: int = ANKI_ELEVENLABS_TIMEOUT_SECONDS
    # Optional because ElevenLabs pricing depends on account/plan. Example: 0.20 means $0.20 / 1k chars.
    ANKI_ELEVENLABS_USD_PER_1K_CHARS: float = _float_setting('anki_elevenlabs_usd_per_1k_chars', 0)
    WORKFLOW_ELEVENLABS_USD_PER_1K_CHARS: float = ANKI_ELEVENLABS_USD_PER_1K_CHARS
    WORKFLOW_MAX_VOICE_CALLS_PER_RUN: int = _int_setting('workflow_max_voice_calls_per_run', 1)
    WORKFLOW_USAGE_TRACKING_ENABLED: bool = _bool_setting('workflow_usage_tracking_enabled', True)
    WORKFLOW_SHOW_USAGE_IN_REPLY: bool = _bool_setting('workflow_show_usage_in_reply', True)
    # When true, send a separate debug message with the engine trace (key flow, decisions, LLM
    # outputs, timings, cost) after delivery. Configurable via WORKFLOW_DEBUG_TRACE_ENABLED.
    WORKFLOW_DEBUG_TRACE_ENABLED: bool = _bool_setting('workflow_debug_trace_enabled', True)
    WORKFLOW_MAX_TEXT_CALLS_PER_RUN: int = _int_setting('workflow_max_text_calls_per_run', 16)
    WORKFLOW_MAX_IMAGE_CALLS_PER_RUN: int = _int_setting('workflow_max_image_calls_per_run', 1)
    # 0 disables USD budget enforcement. Token/image-call caps still apply.
    WORKFLOW_MAX_ESTIMATED_USD_PER_RUN: float = _float_setting('workflow_max_estimated_usd_per_run', 0)
    # Optional JSON: {"model-name": {"input_per_1m": 0.1, "output_per_1m": 0.4}}
    WORKFLOW_MODEL_PRICE_OVERRIDES_JSON: str = str(_setting('workflow_model_price_overrides_json', ''))
    ANKI_OBSERVATION_CAPTURE: bool = _env_bool('ANKI_OBSERVATION_CAPTURE', True)
    ANKI_OBSERVATION_DIR: str = os.getenv(
        'ANKI_OBSERVATION_DIR',
        os.getenv('OBSERVATION_DIR', 'data/observations'),
    )
    ANKI_OBSERVATION_HISTORY_LIMIT: int = max(0, _env_int('ANKI_OBSERVATION_HISTORY_LIMIT', 100))
    
    # Database Configuration
    DATABASE_PATH: str = os.getenv('DATABASE_PATH', 'data/db/tasks.db')
    DATABASE_TIMEOUT: int = _int_setting('database_timeout', 30)
    
    # Platform Configuration
    DEFAULT_TASK_PLATFORM: str = str(_setting('default_task_platform', 'todoist'))
    SUPPORTED_PLATFORMS: List[str] = ['todoist', 'trello', 'google_calendar']
    
    # Google Calendar OAuth Configuration
    GOOGLE_CLIENT_ID: str = os.getenv('GOOGLE_CLIENT_ID', '')
    GOOGLE_CLIENT_SECRET: str = os.getenv('GOOGLE_CLIENT_SECRET', '')
    
    # Scheduler Configuration
    SCHEDULER_INTERVAL: int = _int_setting('scheduler_interval', 20)
    
    # Threading Configuration
    THREAD_TIMEOUT: float = _float_setting('thread_timeout', 1.0)
    
    # Logging Configuration
    LOG_LEVEL: str = os.getenv('LOG_LEVEL', 'DEBUG')
    LOG_FORMAT: str = os.getenv('LOG_FORMAT', '%(asctime)s - %(name)s - %(levelname)s - %(message)s')
    LOG_FILE: str = os.getenv('LOG_FILE', 'data/logs/bot.log')
    
    # Testing Configuration
    ALLOW_SELF_AUTH_REQUESTS: bool = _bool_setting('allow_self_auth_requests', False)
    
    @classmethod
    def validate(cls) -> None:
        """Validate configuration and raise errors for missing required values."""
        if not cls.TELEGRAM_BOT_TOKEN:
            raise ValueError("TELEGRAM_BOT_TOKEN is required")
        
        if not cls.OPENAI_API_KEY:
            raise ValueError("OPENAI_API_KEY is required")
        
        if cls.DEFAULT_TASK_PLATFORM not in cls.SUPPORTED_PLATFORMS:
            raise ValueError(f"DEFAULT_TASK_PLATFORM must be one of {cls.SUPPORTED_PLATFORMS}")
        
        # Google Calendar validation
        if cls.GOOGLE_CLIENT_ID and not cls.GOOGLE_CLIENT_SECRET:
            raise ValueError("GOOGLE_CLIENT_SECRET required when GOOGLE_CLIENT_ID is set")
        
        if cls.GOOGLE_CLIENT_SECRET and not cls.GOOGLE_CLIENT_ID:
            raise ValueError("GOOGLE_CLIENT_ID required when GOOGLE_CLIENT_SECRET is set")
        
        # Platform support validation
        if 'google_calendar' in cls.SUPPORTED_PLATFORMS and not cls.GOOGLE_CLIENT_ID:
            logging.warning("Google Calendar enabled in SUPPORTED_PLATFORMS but credentials not configured")
    
    @classmethod
    def get_log_level(cls) -> int:
        """Convert string log level to logging constant."""
        return getattr(logging, cls.LOG_LEVEL.upper(), logging.DEBUG)
    
    @classmethod
    def ensure_directories(cls) -> None:
        """Ensure required directories exist."""
        os.makedirs(os.path.dirname(cls.DATABASE_PATH), exist_ok=True)
        os.makedirs(os.path.dirname(cls.LOG_FILE), exist_ok=True)

# Remove global instance - use DI container instead
