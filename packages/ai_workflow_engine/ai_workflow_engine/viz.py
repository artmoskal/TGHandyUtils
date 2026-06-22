"""Static engine introspection helpers."""

from __future__ import annotations

from typing import Any

from ai_workflow_engine.workflow import WorkflowDefinition


def render_prompt_manifest(definition: WorkflowDefinition, registry: Any) -> str:
    """Render the static prompt manifest for a built workflow.

    This intentionally stays in the engine because it introspects registered capabilities and their
    prompt templates. Runtime graph projection and HTML rendering live in ``ai_workflow_viewer``.
    """

    lines = [f"# Prompt manifest: {definition.workflow_id}", ""]
    for node in definition.nodes:
        capability = node.effective_capability()
        lines.append(f"## {node.id}  (kind={node.kind}, capability={capability or '-'})")
        handler = None
        if capability is not None:
            try:
                _spec, handler = registry.get(capability)
            except KeyError:
                lines.append("  (capability not registered)")
                lines.append("")
                continue
        prompts = _extract_prompts(handler)
        if not prompts:
            lines.append("  (no static prompt - deterministic / tool / dynamic capability)")
        for label, text in prompts:
            lines.append(f"  [{label}]")
            lines.extend(f"    {line}" for line in text.splitlines())
        lines.append("")
    return "\n".join(lines)


def _extract_prompts(handler: Any) -> list[tuple[str, str]]:
    """Best-effort prompt extraction from engine-shipped prompt-bearing handlers."""

    if handler is None:
        return []
    prompts = []
    for attr, label in (("static_prompt", "static"), ("dynamic_prompt", "dynamic"), ("prompt", "prompt")):
        template = getattr(getattr(handler, attr, None), "template", None)
        if isinstance(template, str) and template:
            prompts.append((label, template))
    system_prompt = getattr(getattr(handler, "planner", None), "system_prompt", None)
    if isinstance(system_prompt, str) and system_prompt:
        prompts.append(("agent_system", system_prompt))
    return prompts
