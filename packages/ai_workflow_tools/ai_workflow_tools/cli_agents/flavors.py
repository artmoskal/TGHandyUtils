"""Shipped CLI agent runtime flavors."""

from __future__ import annotations

from .models import CliFlavor


claude_p = CliFlavor(
    name="claude_p",
    prompt_delivery="stdin",
    result_source="stdout_json_envelope",
    base_argv=["claude", "-p"],
)

codex_exec = CliFlavor(
    name="codex_exec",
    prompt_delivery="argv_last",
    result_source="result_file",
    base_argv=["codex", "exec"],
)


__all__ = ["claude_p", "codex_exec"]
