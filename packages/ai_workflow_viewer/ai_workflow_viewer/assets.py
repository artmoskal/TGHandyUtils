"""Packaged static assets inlined by the standalone viewer renderers."""

from __future__ import annotations

from functools import lru_cache
from importlib.resources import files


_ASSET_NAMES = frozenset(
    {
        "observation-base.css",
        "index.css",
        "rich-graph.css",
        "rich-graph.js",
    }
)


@lru_cache(maxsize=len(_ASSET_NAMES))
def load_asset_text(name: str) -> str:
    """Load one reviewed package asset; arbitrary resource paths are not accepted."""

    if name not in _ASSET_NAMES:
        raise ValueError(f"unknown viewer asset {name!r}")
    return files("ai_workflow_viewer").joinpath("assets", name).read_text(encoding="utf-8")


__all__ = ["load_asset_text"]
