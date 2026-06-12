"""Reusable structured LLM node with bounded parse/validation retry."""

import asyncio
import hashlib
import json
import logging
from dataclasses import dataclass
from typing import Any, Callable, Optional, Sequence

from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage
from langchain_core.output_parsers import PydanticOutputParser
from langchain_core.prompts import PromptTemplate

from ai_workflow_engine.usage import invoke_metered_chat

logger = logging.getLogger(__name__)

MessageFactory = Callable[[list[BaseMessage], int], Sequence[BaseMessage]]
Validator = Callable[[Any], None]
ChatLLMFactory = Callable[[Any, str, float], Any]


class StructuredOutputError(Exception):
    """Raised when a structured LLM node cannot parse or validate output after retries."""


@dataclass(frozen=True)
class PromptBundle:
    """Formatted prompt sections.

    The `system` section is the cacheable static prefix. The `user` section is the dynamic tail.
    `full_text` is used for repair prompts and logging context.
    """

    system: Optional[str]
    user: str
    full_text: str


class StructuredLLMNode:
    """Run one structured LLM call with Pydantic parsing and one repair attempt."""

    _DEFAULT_REPAIR_PROMPT = """The previous structured-output response was invalid.

Validation/parsing error:
{error}

Return a corrected JSON object only. Do not include prose or Markdown fences.

Original request:
{original_prompt}"""

    def __init__(
        self,
        *,
        name: str,
        config: Any,
        output_model: type,
        prompt_template: Optional[str] = None,
        input_variables: Sequence[str] = (),
        static_prompt_template: Optional[str] = None,
        dynamic_prompt_template: Optional[str] = None,
        static_input_variables: Sequence[str] = (),
        dynamic_input_variables: Sequence[str] = (),
        model_attr: str = "",
        default_model_attr: str = "WORKFLOW_DEFAULT_MODEL",
        default_model: str = "gpt-5.4-mini",
        temperature: float = 0.1,
        llm: Optional[Any] = None,
        llm_factory: Optional[ChatLLMFactory] = None,
        validator: Optional[Validator] = None,
        repair_prompt_template: Optional[str] = None,
        pre_parse: Optional[Callable[[str], str]] = None,
        max_repair_rounds: int = 1,
    ):
        self.name = name
        self.config = config
        self.output_model = output_model
        self.model_attr = model_attr
        self.default_model_attr = default_model_attr
        self.default_model = default_model
        self.temperature = temperature
        self._llm = llm
        self._llm_factory = llm_factory
        self.validator = validator
        # Weak-model output hygiene: optional cleaner applied to raw text before every parse
        # attempt (first AND repairs). See ai_workflow_engine.parsing for stock cleaners.
        self.pre_parse = pre_parse
        self.max_repair_rounds = max(0, max_repair_rounds)
        self.parser = PydanticOutputParser(pydantic_object=output_model)
        partial_variables = {"format_instructions": self.parser.get_format_instructions()}
        self.prompt: Optional[PromptTemplate] = None
        self.static_prompt: Optional[PromptTemplate] = None
        self.dynamic_prompt: Optional[PromptTemplate] = None
        if static_prompt_template or dynamic_prompt_template:
            if not static_prompt_template or not dynamic_prompt_template:
                raise ValueError("Both static_prompt_template and dynamic_prompt_template are required")
            self.static_prompt = PromptTemplate(
                template=static_prompt_template,
                input_variables=list(static_input_variables),
                partial_variables=partial_variables,
            )
            self.dynamic_prompt = PromptTemplate(
                template=dynamic_prompt_template,
                input_variables=list(dynamic_input_variables),
                partial_variables=partial_variables,
            )
        else:
            if prompt_template is None:
                raise ValueError("prompt_template is required when split prompt templates are not provided")
            self.prompt = PromptTemplate(
                template=prompt_template,
                input_variables=list(input_variables),
                partial_variables=partial_variables,
            )
        self.repair_prompt = PromptTemplate(
            template=repair_prompt_template or self._DEFAULT_REPAIR_PROMPT,
            input_variables=["error", "original_prompt"],
        )

    @property
    def llm(self):
        if self._llm is None:
            if self._llm_factory is None:
                raise ValueError("StructuredLLMNode requires either llm or llm_factory")
            model = self._model_name()
            self._llm = self._llm_factory(self.config, model, self.temperature)
        return self._llm

    @property
    def accepts_model_profile(self) -> bool:
        """Whether a per-node model profile can be honored.

        True for factory-built clients (the factory is re-invoked with the profile's model) and
        plain callables (the profile rides on the request metadata). False for a fixed LangChain
        ``llm=`` client — declaring a model_profile on such a node is a loud configuration error
        (RC1), never a silent preference.
        """

        if self._llm is None:
            return self._llm_factory is not None
        from ai_workflow_engine.llm_protocol import is_plain_llm_callable

        return is_plain_llm_callable(self._llm)

    def _llm_for_profile(self, profile):
        """Resolve the chat client for the active model profile (cached per model name)."""

        if profile is None:
            return self.llm
        if not self.accepts_model_profile:
            raise ValueError(
                f"{self.name}: model_profile '{profile.name}' requested but this node has a fixed "
                "llm client; construct it with llm_factory or remove the node's model_profile"
            )
        if not hasattr(self, "_llm_by_model"):
            self._llm_by_model = {}
        if profile.model not in self._llm_by_model:
            self._llm_by_model[profile.model] = self._llm_factory(
                self.config, profile.model, profile.temperature
            )
        return self._llm_by_model[profile.model]

    def _model_name(self) -> str:
        fallback = getattr(self.config, self.default_model_attr, self.default_model)
        return getattr(self.config, self.model_attr, fallback)

    async def run(
        self,
        values: dict[str, Any],
        *,
        content_hash_input: str = "",
        message_factory: Optional[MessageFactory] = None,
        usage_metadata: Optional[dict[str, Any]] = None,
        images: Sequence[Any] = (),
    ) -> Any:
        prompt_bundle = self._format_prompt(values)
        content_hash = hashlib.sha256(content_hash_input.encode("utf-8", errors="ignore")).hexdigest()[:12]
        from ai_workflow_engine.llm_protocol import is_plain_llm_callable
        from ai_workflow_engine.model_binding import current_model_profile

        profile = current_model_profile()
        if profile is not None and not self.accepts_model_profile:
            # RC1: a fixed llm client + a declared model_profile is a configuration conflict.
            raise ValueError(
                f"{self.name}: model_profile '{profile.name}' requested but this node has a fixed "
                "llm client; construct it with llm_factory or remove the node's model_profile"
            )
        if is_plain_llm_callable(self._llm):
            # Plain-callable client (no LangChain): async path, LLMRequest transport,
            # uniform metering with honest cost attribution (RC2).
            return await self._invoke_callable_with_retry(
                prompt_bundle, content_hash, usage_metadata, list(images), profile
            )
        return await self._invoke_blocking_with_retry(
            prompt_bundle,
            content_hash,
            message_factory,
            usage_metadata,
            profile,
        )

    async def _invoke_blocking_with_retry(
        self,
        prompt_bundle: PromptBundle,
        content_hash: str,
        message_factory: Optional[MessageFactory],
        usage_metadata: Optional[dict[str, Any]] = None,
        profile: Any = None,
    ) -> Any:
        worker = asyncio.create_task(
            asyncio.to_thread(
                self._invoke_with_retry,
                prompt_bundle,
                content_hash,
                message_factory,
                usage_metadata,
                profile,
            )
        )
        try:
            return await asyncio.shield(worker)
        except asyncio.CancelledError:
            # Blocking LangChain-style clients cannot be interrupted once their thread is running.
            # Keep the coroutine alive until the thread finishes so scheduler slots are released only
            # after the actual backend call exits; then propagate cancellation to the caller.
            try:
                await asyncio.shield(worker)
            except Exception as exc:
                logger.debug(
                    "structured_llm_node_blocking_cancelled_after_worker_error node=%s error=%s",
                    self.name,
                    exc,
                )
            raise

    async def _invoke_callable_with_retry(
        self,
        prompt_bundle: PromptBundle,
        content_hash: str,
        usage_metadata: Optional[dict[str, Any]],
        images: list,
        profile: Any = None,
    ) -> Any:
        from ai_workflow_engine.llm_protocol import LLMRequest, record_callable_usage
        from ai_workflow_engine.usage import check_budget_before_call

        request_metadata = {"model_profile": profile.model_dump()} if profile is not None else {}
        last_error = ""
        for attempt in range(1, 2 + self.max_repair_rounds):
            try:
                if attempt == 1:
                    request = LLMRequest(
                        system=prompt_bundle.system,
                        user=prompt_bundle.user,
                        images=images,
                        metadata=dict(request_metadata),
                    )
                else:
                    request = LLMRequest(
                        system=None,
                        user=self.repair_prompt.format(
                            error=last_error, original_prompt=prompt_bundle.full_text
                        ),
                        images=images,
                        metadata=dict(request_metadata),
                    )
                check_budget_before_call("chat", self.name)
                response = await self.llm(request)
                record_callable_usage(
                    response,
                    node=self.name,
                    attempt=attempt,
                    metadata={"output_model": self.output_model.__name__, **(usage_metadata or {})},
                    config=self.config,
                )
                parsed = self.parser.parse(self._apply_pre_parse(response.text, attempt, content_hash))
                if self.validator:
                    self.validator(parsed)
                logger.info(
                    "structured_llm_node %s",
                    json.dumps(
                        {
                            "node": self.name,
                            "attempt": attempt,
                            "content_hash": content_hash,
                            "output_model": self.output_model.__name__,
                            "client": "plain_callable",
                        },
                        sort_keys=True,
                    ),
                )
                return parsed
            except Exception as exc:
                last_error = str(exc)
                logger.warning(
                    "structured_llm_node_failed %s",
                    json.dumps(
                        {
                            "node": self.name,
                            "attempt": attempt,
                            "content_hash": content_hash,
                            "error": last_error[:500],
                            "client": "plain_callable",
                        },
                        sort_keys=True,
                    ),
                )
        raise StructuredOutputError(f"{self.name} returned invalid structured output: {last_error}")

    def _invoke_with_retry(
        self,
        prompt_bundle: PromptBundle,
        content_hash: str,
        message_factory: Optional[MessageFactory],
        usage_metadata: Optional[dict[str, Any]] = None,
        profile: Any = None,
    ) -> Any:
        last_error = ""
        for attempt in range(1, 2 + self.max_repair_rounds):
            try:
                messages = self._messages_for_attempt(prompt_bundle, attempt, last_error)
                if message_factory:
                    messages = list(message_factory(messages, attempt))
                output = invoke_metered_chat(
                    self._llm_for_profile(profile),
                    messages,
                    node=self.name,
                    model=profile.model if profile is not None else self._model_name(),
                    attempt=attempt,
                    metadata={"output_model": self.output_model.__name__, **(usage_metadata or {})},
                    config=self.config,
                )
                raw_text = self._message_content(output)
                parsed = self.parser.parse(self._apply_pre_parse(raw_text, attempt, content_hash))
                if self.validator:
                    self.validator(parsed)
                logger.info(
                    "structured_llm_node %s",
                    json.dumps(
                        {
                            "node": self.name,
                            "attempt": attempt,
                            "content_hash": content_hash,
                            "output_model": self.output_model.__name__,
                        },
                        sort_keys=True,
                    ),
                )
                return parsed
            except Exception as exc:
                last_error = str(exc)
                logger.warning(
                    "structured_llm_node_failed %s",
                    json.dumps(
                        {
                            "node": self.name,
                            "attempt": attempt,
                            "content_hash": content_hash,
                            "error": last_error[:500],
                        },
                        sort_keys=True,
                    ),
                )
        raise StructuredOutputError(f"{self.name} returned invalid structured output: {last_error}")

    def _format_prompt(self, values: dict[str, Any]) -> PromptBundle:
        if self.static_prompt and self.dynamic_prompt:
            system_text = self.static_prompt.format(**values)
            user_text = self.dynamic_prompt.format(**values)
            return PromptBundle(
                system=system_text,
                user=user_text,
                full_text=f"{system_text}\n\n{user_text}",
            )
        if not self.prompt:
            raise RuntimeError("StructuredLLMNode prompt configuration is invalid")
        prompt_text = self.prompt.format(**values)
        return PromptBundle(system=None, user=prompt_text, full_text=prompt_text)

    def _apply_pre_parse(self, raw_text: str, attempt: int, content_hash: str) -> str:
        """Run the optional cleaner; log only when it actually changed the payload."""

        if self.pre_parse is None:
            return raw_text
        cleaned = self.pre_parse(raw_text)
        if cleaned != raw_text:
            logger.info(
                "structured_llm_node_pre_parse %s",
                json.dumps(
                    {
                        "node": self.name,
                        "attempt": attempt,
                        "content_hash": content_hash,
                        "original": {
                            "length": len(raw_text),
                            "sha12": hashlib.sha256(raw_text.encode("utf-8", errors="ignore")).hexdigest()[:12],
                        },
                        "cleaned": {
                            "length": len(cleaned),
                            "sha12": hashlib.sha256(cleaned.encode("utf-8", errors="ignore")).hexdigest()[:12],
                        },
                    },
                    sort_keys=True,
                ),
            )
        return cleaned

    def _messages_for_attempt(
        self,
        prompt_bundle: PromptBundle,
        attempt: int,
        last_error: str,
    ) -> list[BaseMessage]:
        if attempt >= 2:
            return [
                HumanMessage(
                    content=self.repair_prompt.format(
                        error=last_error,
                        original_prompt=prompt_bundle.full_text,
                    )
                )
            ]
        if prompt_bundle.system is not None:
            return [SystemMessage(content=prompt_bundle.system), HumanMessage(content=prompt_bundle.user)]
        return [HumanMessage(content=prompt_bundle.user)]

    @staticmethod
    def _message_content(output: Any) -> str:
        content = getattr(output, "content", output)
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            parts = []
            for item in content:
                if isinstance(item, dict):
                    text = item.get("text") or item.get("content")
                    if text:
                        parts.append(str(text))
                else:
                    parts.append(str(item))
            return "\n".join(parts)
        return str(content)
