"""Closed renderer input models built from projected observation truth."""

from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel, ConfigDict


class _ViewModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ObservationCanvasView(_ViewModel):
    width: int
    height: int


class ObservationTransitionView(_ViewModel):
    source: str
    target: str
    label: str
    policy: str
    description: str


class ObservationNodeView(_ViewModel):
    id: str
    label: str
    description: str
    kind: str
    kind_label: str
    status: str
    raw_status: str
    attempts: int
    elapsed_ms: int
    usage: str
    metrics: list[str]
    decisions: list[str]
    errors: list[str]
    details: list[dict[str, Any]]
    events: list[dict[str, Any]]
    x: int
    y: int
    width: int
    height: int
    search: str


class ObservationViewData(_ViewModel):
    workflow_id: str
    run_id: Optional[str] = None
    canvas: ObservationCanvasView
    nodes: list[ObservationNodeView]
    transitions: list[ObservationTransitionView]


def seal_observation_view_data(value: dict[str, Any]) -> dict[str, Any]:
    """Validate the renderer boundary once and return its plain JSON-ready shape."""

    return ObservationViewData.model_validate(value).model_dump()


__all__ = [
    "ObservationCanvasView",
    "ObservationNodeView",
    "ObservationTransitionView",
    "ObservationViewData",
    "seal_observation_view_data",
]
