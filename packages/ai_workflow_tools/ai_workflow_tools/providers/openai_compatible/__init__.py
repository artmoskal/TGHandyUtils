"""OpenAI chat-completions compatible LLMCallable adapter."""

from ai_workflow_tools.providers.openai_compatible.client import OpenAICompatibleLLMClient
from ai_workflow_tools.providers.openai_compatible.models import (
    ApiKeyAuth,
    NoAuth,
    OpenAICompatibleProviderConfig,
    OpenAICompatibleProviderError,
)

__all__ = [
    "ApiKeyAuth",
    "NoAuth",
    "OpenAICompatibleLLMClient",
    "OpenAICompatibleProviderConfig",
    "OpenAICompatibleProviderError",
]
