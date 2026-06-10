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

    def _model_name(self) -> str:
        fallback = getattr(self.config, self.default_model_attr, self.default_model)
        return getattr(self.config, self.model_attr, fallback)

    async def run(
        self,
        values: dict[str, Any],
        *,
        content_hash_input: str = "",
        message_factory: Optional[MessageFactory] = None,
    ) -> Any:
        prompt_bundle = self._format_prompt(values)
        content_hash = hashlib.sha256(content_hash_input.encode("utf-8", errors="ignore")).hexdigest()[:12]
        return await asyncio.to_thread(
            self._invoke_with_retry,
            prompt_bundle,
            content_hash,
            message_factory,
        )

    def _invoke_with_retry(
        self,
        prompt_bundle: PromptBundle,
        content_hash: str,
        message_factory: Optional[MessageFactory],
    ) -> Any:
        last_error = ""
        for attempt in range(1, 2 + self.max_repair_rounds):
            try:
                messages = self._messages_for_attempt(prompt_bundle, attempt, last_error)
                if message_factory:
                    messages = list(message_factory(messages, attempt))
                output = invoke_metered_chat(
                    self.llm,
                    messages,
                    node=self.name,
                    model=self._model_name(),
                    attempt=attempt,
                    metadata={"output_model": self.output_model.__name__},
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
