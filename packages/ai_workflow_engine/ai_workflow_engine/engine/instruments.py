"""Registry for product-agnostic workflow instruments."""

import inspect
from dataclasses import dataclass
from typing import Any, Callable, Dict, List

from ai_workflow_engine.models import WorkflowGoal, WorkflowInstrumentSpec

InstrumentHandler = Callable[[WorkflowGoal, Dict[str, Any]], Any]


@dataclass(frozen=True)
class WorkflowInstrument:
    """A bound callable capability available to a future supervisor."""

    spec: WorkflowInstrumentSpec
    handler: InstrumentHandler

    async def run(self, goal: WorkflowGoal, payload: Dict[str, Any]) -> Any:
        result = self.handler(goal, payload)
        if inspect.isawaitable(result):
            return await result
        return result


class WorkflowInstrumentRegistry:
    """Small explicit registry for sub-workflows, LLM calls, and primitive tools."""

    def __init__(self):
        self._instruments: Dict[str, WorkflowInstrument] = {}

    def register(self, spec: WorkflowInstrumentSpec, handler: InstrumentHandler) -> None:
        if spec.name in self._instruments:
            raise ValueError(f"Workflow instrument already registered: {spec.name}")
        self._instruments[spec.name] = WorkflowInstrument(spec=spec, handler=handler)

    def get(self, name: str) -> WorkflowInstrument:
        try:
            return self._instruments[name]
        except KeyError as exc:
            raise KeyError(f"Unknown workflow instrument: {name}") from exc

    def list_specs(self) -> List[WorkflowInstrumentSpec]:
        return [instrument.spec for instrument in self._instruments.values()]
