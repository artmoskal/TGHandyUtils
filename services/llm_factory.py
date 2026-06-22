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
