"""Single place that constructs model clients for the content-processing stack.

Centralising construction here means model/provider/temperature/base-url live in ONE spot, and the
LLM_BASE_URL local-endpoint override (offline/local testing) covers every product call routed
through here. Swapping models or providers later is a one-line change.

- create_chat_llm: the chat LLM (LangChain) — the default for chat workflows.
- create_openai_client: a raw AsyncOpenAI client for the few non-chat SDK calls that the chat client
  can't express (vision image analysis, whisper audio). Prefer create_chat_llm otherwise.

Product code should construct model clients HERE, not build its own provider client — a direct
provider call is an explicit escape hatch, not the default.
"""

from dataclasses import dataclass
from typing import Any, Callable, Optional

from langchain_openai import ChatOpenAI
from openai import AsyncOpenAI

from core.interfaces import IConfig
from services.openai_cache import openai_prompt_cache_kwargs

DEFAULT_MODEL = "gpt-5.4-mini"

# Reasoning / GPT-5-class models only accept the default temperature (1); passing a custom
# temperature errors. We detect these by id prefix and skip the custom temperature.
_FIXED_TEMPERATURE_PREFIXES = ("gpt-5", "o1", "o3", "o4")


def create_chat_llm(config: IConfig, model: str = DEFAULT_MODEL, temperature: float = 0.0) -> ChatOpenAI:
    """Build a configured ChatOpenAI client.

    Raises ValueError (loudly) if the API key is missing - we never fall back to a default.
    """
    # Testing/offline escape hatch: point at a local OpenAI-compatible endpoint (e.g. ollama).
    # When LLM_BASE_URL is set we skip the OpenAI key requirement + OpenAI-only cache kwargs.
    base_url = getattr(config, "LLM_BASE_URL", "") or ""
    if base_url:
        return ChatOpenAI(
            model=model,
            base_url=base_url,
            openai_api_key=(config.OPENAI_API_KEY or "local-no-key"),
            temperature=temperature,
        )

    if not config.OPENAI_API_KEY:
        raise ValueError("OpenAI API key is required to construct an LLM client")

    params = {"model": model, "openai_api_key": config.OPENAI_API_KEY}
    if not model.startswith(_FIXED_TEMPERATURE_PREFIXES):
        params["temperature"] = temperature
    cache_kwargs = openai_prompt_cache_kwargs(config, model=model)
    if cache_kwargs:
        params["model_kwargs"] = cache_kwargs
    return ChatOpenAI(**params)


def create_openai_client(config: IConfig, api_key: str = "") -> AsyncOpenAI:
    """Build a configured raw AsyncOpenAI client for non-chat SDK calls (vision, audio).

    Respects the same LLM_BASE_URL local-endpoint override as create_chat_llm. Raises ValueError
    (loudly) if there is neither a key nor a local endpoint - we never silently fall back.
    """
    key = api_key or getattr(config, "OPENAI_API_KEY", None)
    base_url = getattr(config, "LLM_BASE_URL", "") or ""
    if base_url:
        return AsyncOpenAI(api_key=(key or "local-no-key"), base_url=base_url)
    if not key:
        raise ValueError("OpenAI API key is required to construct an OpenAI client")
    return AsyncOpenAI(api_key=key)


# --- Pluggable LLM backend registry ------------------------------------------------------
#
# Two layers, deliberately different:
# - ENGINE layer is pluggable by protocol: nodes accept any ``LLMCallable`` — a new
#   executor family needs zero engine (and zero factory) edits to exist.
# - PRODUCT layer (here) is the wiring table: a ROUTING KEY used as a model name in the
#   existing per-role settings (ANKI_DECISION_MODEL / ANKI_SCENARIO_MODEL /
#   ANKI_RENDER_MODEL / ANKI_QUALITY_MODEL, defaulting through ANKI_CARD_MODEL) maps to a
#   registered backend. Per-role stupid-vs-smart selection therefore needs no new config
#   surface, and new backends REGISTER here — the factory functions never grow another
#   if/elif, and call sites ask the registry (cost class, vision) instead of hardcoding
#   backend names.
#
# Unregistered model names fall through to the regular metered ChatOpenAI door, so plain
# API models keep working untouched.


@dataclass(frozen=True)
class LLMBackend:
    """One registered non-default backend: builders + the facts call sites need."""

    build_text: Callable[[IConfig], Any]  # plain LLMCallable (engine structured nodes)
    build_chat: Callable[[IConfig], Any]  # sync .invoke(messages) (LangChain call sites)
    cost_class: str = "metered"
    supports_vision: bool = True
    provider: str = "openai"  # usage-event label (dashboards group by it)


_LLM_BACKENDS: dict[str, LLMBackend] = {}


def register_llm_backend(backend: LLMBackend, *routing_keys: str) -> None:
    """Plug in a backend under one or more routing keys (model-name tokens)."""

    for key in routing_keys:
        _LLM_BACKENDS[str(key).strip().lower()] = backend


def _backend_for(model: str) -> Optional[LLMBackend]:
    return _LLM_BACKENDS.get(str(model or "").strip().lower())


def llm_cost_class(model: str) -> str:
    """Cost class for metering wrappers: 'metered' unless the backend says otherwise."""

    backend = _backend_for(model)
    return backend.cost_class if backend else "metered"


def llm_provider_label(model: str) -> str:
    """Usage-event provider label for this model's backend ('openai' for the API door)."""

    backend = _backend_for(model)
    return backend.provider if backend else "openai"


def llm_supports_vision(model: str) -> bool:
    """Whether image parts can be sent to this model's backend (API door: yes)."""

    backend = _backend_for(model)
    return backend.supports_vision if backend else True


def create_anki_text_llm(config: IConfig, model: str = DEFAULT_MODEL, temperature: float = 0.0):
    """ChatLLMFactory for Anki structured text nodes with per-role backend routing."""

    backend = _backend_for(model)
    if backend is not None:
        return backend.build_text(config)
    return create_chat_llm(config, model=model, temperature=temperature)


def create_anki_chat_model(config: IConfig, model: str = DEFAULT_MODEL, temperature: float = 0.0):
    """Same routing for sync ``llm.invoke(messages)`` call sites (the render path).

    Callers metering via ``invoke_metered_chat`` should pass
    ``cost_class=llm_cost_class(model)`` so subscription backends never report
    phantom metered USD.
    """

    backend = _backend_for(model)
    if backend is not None:
        return backend.build_chat(config)
    return create_chat_llm(config, model=model, temperature=temperature)


# --- Built-in backend: ChatGPT-browser service (subscription session over HTTP) ----------

def _chatgpt_browser_kwargs(config: IConfig) -> dict:
    url = str(getattr(config, "CHATGPT_BROWSER_API_URL", "") or "").strip()
    if not url:
        raise ValueError(
            "CHATGPT_BROWSER_API_URL is required to route a 'chatgpt-web' model — "
            "no default endpoint is assumed"
        )
    return {
        "base_url": url,
        "timeout_s": float(getattr(config, "WORKFLOW_CHATGPT_BROWSER_TIMEOUT_SECONDS", 340)),
        "force_fresh": bool(getattr(config, "WORKFLOW_CHATGPT_BROWSER_FORCE_FRESH", True)),
    }


def _build_chatgpt_browser_text(config: IConfig):
    from ai_workflow_tools.chatgpt_browser import ChatGptBrowserLLMClient

    return ChatGptBrowserLLMClient(**_chatgpt_browser_kwargs(config))


def _build_chatgpt_browser_chat(config: IConfig):
    from ai_workflow_tools.chatgpt_browser import ChatGptBrowserChatModel

    return ChatGptBrowserChatModel(**_chatgpt_browser_kwargs(config))


register_llm_backend(
    LLMBackend(
        build_text=_build_chatgpt_browser_text,
        build_chat=_build_chatgpt_browser_chat,
        cost_class="subscription_notional",  # rides the ChatGPT subscription, no per-call price
        supports_vision=False,  # /ask is text-only
        provider="chatgpt_browser",
    ),
    "chatgpt-web",
    "chatgpt",
    "chatgpt-browser",
)
