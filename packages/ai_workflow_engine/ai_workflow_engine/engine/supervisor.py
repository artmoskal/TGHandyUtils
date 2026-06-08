"""Reusable workflow supervisor over registered instruments."""

import json
from typing import Any, Callable, Optional

from ai_workflow_engine.models import WorkflowDecision, WorkflowGoal, WorkflowSupervisorResult
from ai_workflow_engine.engine.instruments import WorkflowInstrumentRegistry
from ai_workflow_engine.engine.llm_node import StructuredLLMNode

ChatLLMFactory = Callable[[Any, str, float], Any]


class WorkflowDecisionPlanner:
    """Optional AI decision gate for selecting an instrument from a registry."""

    _STATIC_PROMPT = """You are a workflow supervisor. Choose exactly one available instrument to handle
the user goal.

Rules:
- Return only valid JSON matching the schema.
- Choose only an instrument listed in Available instruments.
- Prefer the most specific product workflow when the goal type is clear.
- Do not decide product-internal branch details here; those belong inside the selected instrument's
  product graph.

JSON schema:
{format_instructions}"""
    _DYNAMIC_PROMPT = """Goal:
{goal}

Available instruments:
{instruments}

Payload summary:
{payload_summary}"""

    _PROMPT = _STATIC_PROMPT + "\n" + _DYNAMIC_PROMPT

    _REPAIR_PROMPT = """The previous workflow-supervisor output was invalid.

Validation/parsing error:
{error}

Return a corrected JSON object only. Do not include prose or Markdown fences.

Original request:
{original_prompt}"""

    def __init__(
        self,
        config: Any,
        llm: Optional[Any] = None,
        llm_factory: Optional[ChatLLMFactory] = None,
        static_prompt_template: Optional[str] = None,
        dynamic_prompt_template: Optional[str] = None,
        repair_prompt_template: Optional[str] = None,
    ):
        self.config = config
        self._active_registry: Optional[WorkflowInstrumentRegistry] = None
        self._node = StructuredLLMNode(
            name="workflow_supervisor_decision",
            config=config,
            output_model=WorkflowDecision,
            static_prompt_template=static_prompt_template or self._STATIC_PROMPT,
            dynamic_prompt_template=dynamic_prompt_template or self._DYNAMIC_PROMPT,
            dynamic_input_variables=["goal", "instruments", "payload_summary"],
            model_attr="WORKFLOW_SUPERVISOR_MODEL",
            default_model_attr="WORKFLOW_SUPERVISOR_MODEL",
            default_model="gpt-5.5",
            temperature=0.0,
            llm=llm,
            llm_factory=llm_factory,
            validator=self._validate_active_decision,
            repair_prompt_template=repair_prompt_template or self._REPAIR_PROMPT,
        )

    @property
    def llm(self):
        return self._node.llm

    async def plan(
        self,
        goal: WorkflowGoal,
        registry: WorkflowInstrumentRegistry,
        payload: dict[str, Any],
    ) -> WorkflowDecision:
        self._active_registry = registry
        try:
            return await self._node.run(
                {
                    "goal": json.dumps(goal.model_dump(), sort_keys=True),
                    "instruments": json.dumps([spec.model_dump() for spec in registry.list_specs()], sort_keys=True),
                    "payload_summary": self._payload_summary(payload),
                },
                content_hash_input=goal.objective,
            )
        finally:
            self._active_registry = None

    def _validate_active_decision(self, decision: WorkflowDecision) -> None:
        if not self._active_registry:
            return
        self._active_registry.get(decision.instrument_name)

    @staticmethod
    def _payload_summary(payload: dict[str, Any]) -> str:
        try:
            text = json.dumps(payload, default=str, sort_keys=True)
        except TypeError:
            text = str(payload)
        return text[:2000]


class WorkflowSupervisor:
    """Run the instrument chosen for a goal.

    Without a decision planner, the supervisor maps `goal.workflow_type` directly to an instrument
    name. With a planner, it can choose among registered workflows/tools while staying
    product-agnostic.
    """

    def __init__(
        self,
        registry: WorkflowInstrumentRegistry,
        decision_planner: Optional[WorkflowDecisionPlanner] = None,
    ):
        self.registry = registry
        self.decision_planner = decision_planner

    async def run(self, goal: WorkflowGoal, payload: dict[str, Any]) -> WorkflowSupervisorResult:
        if self.decision_planner:
            decision = await self.decision_planner.plan(goal, self.registry, payload)
        else:
            decision = WorkflowDecision(
                instrument_name=goal.workflow_type,
                rationale="Default supervisor routing by workflow_type",
                confidence=1.0,
            )
        instrument = self.registry.get(decision.instrument_name)
        result = await instrument.run(goal, payload)
        return WorkflowSupervisorResult(decision=decision, result=result)
