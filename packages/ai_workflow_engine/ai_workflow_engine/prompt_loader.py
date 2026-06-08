"""Prompt template file loader for workflow prompts."""

import os
from functools import lru_cache
from pathlib import Path
from textwrap import dedent
from typing import Optional


_PROMPT_ROOT_ENV = "AI_WORKFLOW_PROMPT_ROOT"


def _default_prompt_root() -> Path:
    configured = os.getenv(_PROMPT_ROOT_ENV)
    if configured:
        return Path(configured).resolve()
    return (Path.cwd() / "prompts").resolve()


class PromptTemplateLoader:
    """Load prompt template files from a configured root directory."""

    def __init__(self, root: str | Path):
        self.root = Path(root).resolve()

    def load(self, relative_path: str) -> str:
        path = (self.root / relative_path).resolve()
        try:
            path.relative_to(self.root)
        except ValueError as exc:
            raise ValueError(f"Prompt path escapes prompt root: {relative_path}") from exc
        if not path.is_file():
            raise FileNotFoundError(f"Prompt template not found: {path}")
        return dedent(path.read_text(encoding="utf-8")).strip("\n")


@lru_cache(maxsize=None)
def _load_default_prompt_template(relative_path: str, root: str) -> str:
    return PromptTemplateLoader(root).load(relative_path)


def load_prompt_template(relative_path: str, *, root: Optional[str | Path] = None) -> str:
    """Load a prompt template from the default repo prompt root or an injected root."""
    prompt_root = Path(root).resolve() if root is not None else _default_prompt_root()
    return _load_default_prompt_template(relative_path, str(prompt_root))
