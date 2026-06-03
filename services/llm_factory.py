"""Single place that constructs chat LLM clients for the content-processing stack.

Centralising construction here means model/provider/temperature live in ONE spot for the
new Anki + classifier code. Swapping models or providers later is a one-line change.

NOTE: ParsingService (the legacy reminder path) still builds its own ChatOpenAI internally;
migrating it to this factory is a safe future cleanup once it is under characterization tests.
"""

from langchain_openai import ChatOpenAI

from core.interfaces import IConfig

DEFAULT_MODEL = "gpt-5.4-mini"

# Reasoning / GPT-5-class models only accept the default temperature (1); passing a custom
# temperature errors. We detect these by id prefix and skip the custom temperature.
_FIXED_TEMPERATURE_PREFIXES = ("gpt-5", "o1", "o3", "o4")


def create_chat_llm(config: IConfig, model: str = DEFAULT_MODEL, temperature: float = 0.0) -> ChatOpenAI:
    """Build a configured ChatOpenAI client.

    Raises ValueError (loudly) if the API key is missing - we never fall back to a default.
    """
    if not config.OPENAI_API_KEY:
        raise ValueError("OpenAI API key is required to construct an LLM client")

    params = {"model": model, "openai_api_key": config.OPENAI_API_KEY}
    if not model.startswith(_FIXED_TEMPERATURE_PREFIXES):
        params["temperature"] = temperature
    return ChatOpenAI(**params)
