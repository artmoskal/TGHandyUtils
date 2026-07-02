"""Product-neutral prompt rendering: prompt FILES with strict variable checks (P1).

Design (agreed 2026-06-27): prompts live in files under a locked prompt root; a
``PromptRef`` names the file + dialect; rendering is STRICT — a missing variable fails
loudly BEFORE any model call — and every render carries template/rendered digests for
observability. ``format`` (Python ``{var}``) is the default dialect; **Jinja is one
optional renderer behind the same interface, never an engine dependency** — requesting
``renderer="jinja"`` without ``jinja2`` installed is a loud error, not a fallback.
"""

from __future__ import annotations

import hashlib
import string
from pathlib import Path
from typing import Any, Dict, List, Literal, Mapping, Optional, Protocol, runtime_checkable

from pydantic import BaseModel, Field

from ai_workflow_engine.prompt_loader import PromptTemplateLoader


class PromptRenderError(ValueError):
    """Loud rendering failure: missing variables, unknown dialect, or unavailable renderer."""


class PromptRef(BaseModel):
    """Reference to a prompt file under the prompt root."""

    path: str
    renderer: Literal["format", "jinja"] = "format"
    # Declared variables (optional): validated against the template at load — a declared
    # variable the template never uses (or vice versa for strict render) is drift.
    variables: List[str] = Field(default_factory=list)
    role: Literal["system", "user", "tool", "plain"] = "plain"
    cache_class: Literal["static", "dynamic", "repair", "unknown"] = "unknown"


class PromptRenderResult(BaseModel):
    ref: PromptRef
    text: str
    renderer: str
    variables_used: List[str] = Field(default_factory=list)
    missing_variables: List[str] = Field(default_factory=list)
    template_digest: str = ""
    rendered_digest: str = ""


@runtime_checkable
class PromptRenderer(Protocol):
    def render(self, ref: PromptRef, values: Mapping[str, Any]) -> PromptRenderResult: ...


def _digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]


def _format_template_fields(template: str) -> List[str]:
    fields = []
    for _literal, field_name, _spec, _conv in string.Formatter().parse(template):
        if field_name:
            fields.append(field_name.split(".")[0].split("[")[0])
    return sorted(set(fields))


class _FormatDialect:
    """Strict Python ``{var}`` formatting (the default)."""

    name = "format"

    def fields(self, template: str) -> List[str]:
        return _format_template_fields(template)

    def render(self, template: str, values: Mapping[str, Any]) -> str:
        return template.format(**values)


class _JinjaDialect:
    """Optional Jinja dialect — StrictUndefined so missing variables fail loudly."""

    name = "jinja"

    def __init__(self) -> None:
        try:
            import jinja2
        except ImportError as exc:  # loud, never a silent fallback to another dialect
            raise PromptRenderError(
                "PromptRef(renderer='jinja') requires the optional jinja2 package — install "
                "it (product dependency) or use renderer='format'"
            ) from exc
        self._jinja2 = jinja2
        self._env = jinja2.Environment(undefined=jinja2.StrictUndefined, autoescape=False)

    def fields(self, template: str) -> List[str]:
        from jinja2 import meta

        return sorted(meta.find_undeclared_variables(self._env.parse(template)))

    def render(self, template: str, values: Mapping[str, Any]) -> str:
        try:
            return self._env.from_string(template).render(dict(values))
        except self._jinja2.UndefinedError as exc:
            raise PromptRenderError(f"jinja render failed: {exc}") from exc


class PromptRenderService:
    """Load + strictly render prompt files from a locked root.

    The engine owns loading/rendering mechanics + traceability; PRODUCTS own the prompt
    files and their content. Template text is cached per path (templates are code — a
    process restart picks up edits, a running workflow never renders half-old prompts).
    """

    def __init__(self, root: str | Path) -> None:
        self.loader = PromptTemplateLoader(root)
        self._templates: Dict[str, str] = {}
        self._dialects: Dict[str, Any] = {}

    def _dialect(self, name: str):
        if name not in self._dialects:
            if name == "format":
                self._dialects[name] = _FormatDialect()
            elif name == "jinja":
                self._dialects[name] = _JinjaDialect()
            else:  # defensive: PromptRef's Literal already constrains this
                raise PromptRenderError(f"unknown prompt renderer: {name!r}")
        return self._dialects[name]

    def load_template(self, ref: PromptRef) -> str:
        """Load (path-locked) + validate declared variables against the template."""

        if ref.path not in self._templates:
            self._templates[ref.path] = self.loader.load(ref.path)
        template = self._templates[ref.path]
        if ref.variables:
            template_fields = set(self._dialect(ref.renderer).fields(template))
            undeclared = sorted(set(ref.variables) - template_fields)
            if undeclared:
                raise PromptRenderError(
                    f"prompt '{ref.path}' declares variables the template never uses: "
                    f"{', '.join(undeclared)} (template has: {sorted(template_fields)})"
                )
        return template

    def render(self, ref: PromptRef, values: Mapping[str, Any]) -> PromptRenderResult:
        template = self.load_template(ref)
        dialect = self._dialect(ref.renderer)
        required = dialect.fields(template)
        missing = sorted(set(required) - set(values.keys()))
        if missing:
            # The whole point: fail BEFORE the model call, naming exactly what is missing.
            raise PromptRenderError(
                f"prompt '{ref.path}' missing variable(s): {', '.join(missing)} "
                f"(required: {required})"
            )
        text = dialect.render(template, values)
        return PromptRenderResult(
            ref=ref,
            text=text,
            renderer=ref.renderer,
            variables_used=required,
            missing_variables=[],
            template_digest=_digest(template),
            rendered_digest=_digest(text),
        )
