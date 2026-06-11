"""Per-node model binding scope.

The executor resolves a node's declared ``model_profile`` name against the engine's
``ModelProfile`` registry and installs it here for the duration of that node's invocation.
Structured LLM nodes consult :func:`current_model_profile` at call time:

- built with ``llm_factory`` → the factory is invoked with the profile's model/temperature
  (instances cached per model name);
- plain-callable client → the resolved profile rides on ``LLMRequest.metadata['model_profile']``;
- fixed LangChain ``llm=`` client → **loud error** (RC1): a fixed client cannot honor a profile,
  and silently preferring the client would be a silent downgrade.
"""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from typing import Iterator, Optional

from ai_workflow_engine.models import ModelProfile

_ACTIVE_MODEL_PROFILE: ContextVar[Optional[ModelProfile]] = ContextVar(
    "workflow_model_profile", default=None
)


@contextmanager
def model_profile_scope(profile: Optional[ModelProfile]) -> Iterator[None]:
    token = _ACTIVE_MODEL_PROFILE.set(profile)
    try:
        yield
    finally:
        _ACTIVE_MODEL_PROFILE.reset(token)


def current_model_profile() -> Optional[ModelProfile]:
    return _ACTIVE_MODEL_PROFILE.get()
