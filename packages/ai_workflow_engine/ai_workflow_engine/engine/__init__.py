"""Reusable workflow engine primitives."""

# F1.1 (lazy public surface): exports resolve on first attribute access (PEP 562), so a
# trivial consumer pays only for the facilities it touches — no second execution path,
# no lite package; every name below still resolves exactly as before.

from importlib import import_module
from typing import Any

_EXPORTS: dict[str, tuple[str, str]] = {
    "AgentCapability": ("ai_workflow_engine.engine.agent", "AgentCapability"),
    "AgentEpisodePlanner": ("ai_workflow_engine.engine.agent", "AgentEpisodePlanner"),
    "AsyncQueueDetailSink": ("ai_workflow_engine.engine.capabilities", "AsyncQueueDetailSink"),
    "AsyncQueueTraceSink": ("ai_workflow_engine.engine.capabilities", "AsyncQueueTraceSink"),
    "CallbackTraceSink": ("ai_workflow_engine.engine.capabilities", "CallbackTraceSink"),
    "CapabilityCall": ("ai_workflow_engine.engine.capabilities", "CapabilityCall"),
    "CapabilityRegistry": ("ai_workflow_engine.engine.capabilities", "CapabilityRegistry"),
    "CapabilityRuntime": ("ai_workflow_engine.engine.capabilities", "CapabilityRuntime"),
    "CheckpointStore": ("ai_workflow_engine.engine.checkpoints", "CheckpointStore"),
    "DetailSink": ("ai_workflow_engine.engine.capabilities", "DetailSink"),
    "EvaluationController": ("ai_workflow_engine.engine.evaluator", "EvaluationController"),
    "EvaluationPlanner": ("ai_workflow_engine.engine.evaluator", "EvaluationPlanner"),
    "ExternalAdapterCapability": ("ai_workflow_engine.engine.external", "ExternalAdapterCapability"),
    "ExternalProcessCapability": ("ai_workflow_engine.engine.external", "ExternalProcessCapability"),
    "ExternalProcessRequest": ("ai_workflow_engine.engine.external", "ExternalProcessRequest"),
    "HumanClarificationCapability": ("ai_workflow_engine.engine.human", "HumanClarificationCapability"),
    "HumanClarificationChannel": ("ai_workflow_engine.engine.human", "HumanClarificationChannel"),
    "InMemoryCheckpointStore": ("ai_workflow_engine.engine.checkpoints", "InMemoryCheckpointStore"),
    "InMemoryDetailSink": ("ai_workflow_engine.engine.capabilities", "InMemoryDetailSink"),
    "InMemoryExternalWriteSink": ("ai_workflow_engine.engine.external", "InMemoryExternalWriteSink"),
    "InMemoryHumanClarificationChannel": ("ai_workflow_engine.engine.human", "InMemoryHumanClarificationChannel"),
    "InMemoryTraceSink": ("ai_workflow_engine.engine.capabilities", "InMemoryTraceSink"),
    "JsonlCheckpointStore": ("ai_workflow_engine.engine.checkpoints", "JsonlCheckpointStore"),
    "JsonlDetailSink": ("ai_workflow_engine.engine.capabilities", "JsonlDetailSink"),
    "JsonlExternalWriteSink": ("ai_workflow_engine.engine.external", "JsonlExternalWriteSink"),
    "JsonlTraceSink": ("ai_workflow_engine.engine.capabilities", "JsonlTraceSink"),
    "LLMAgentPlanner": ("ai_workflow_engine.engine.agent_planner", "LLMAgentPlanner"),
    "ReplayPlanner": ("ai_workflow_engine.engine.agent_planner", "ReplayPlanner"),
    "RuntimePlanCompiler": ("ai_workflow_engine.engine.capabilities", "RuntimePlanCompiler"),
    "SchedulingDecision": ("ai_workflow_engine.engine.scheduler", "SchedulingDecision"),
    "StructuredLLMNode": ("ai_workflow_engine.engine.llm_node", "StructuredLLMNode"),
    "StructuredOutputError": ("ai_workflow_engine.engine.llm_node", "StructuredOutputError"),
    "TeeDetailSink": ("ai_workflow_engine.engine.capabilities", "TeeDetailSink"),
    "TeeTraceSink": ("ai_workflow_engine.engine.capabilities", "TeeTraceSink"),
    "TraceSink": ("ai_workflow_engine.engine.capabilities", "TraceSink"),
    "WorkflowDecisionPlanner": ("ai_workflow_engine.engine.supervisor", "WorkflowDecisionPlanner"),
    "WorkflowInstrument": ("ai_workflow_engine.engine.instruments", "WorkflowInstrument"),
    "WorkflowInstrumentRegistry": ("ai_workflow_engine.engine.instruments", "WorkflowInstrumentRegistry"),
    "WorkflowLoopController": ("ai_workflow_engine.engine.loop", "WorkflowLoopController"),
    "WorkflowRunner": ("ai_workflow_engine.engine.runner", "WorkflowRunner"),
    "WorkflowScheduler": ("ai_workflow_engine.engine.scheduler", "WorkflowScheduler"),
    "WorkflowStepPlanner": ("ai_workflow_engine.engine.loop", "WorkflowStepPlanner"),
    "WorkflowSupervisor": ("ai_workflow_engine.engine.supervisor", "WorkflowSupervisor"),
    "artifact_result": ("ai_workflow_engine.engine.capabilities", "artifact_result"),
    "assert_checkpoint_payload_safe": ("ai_workflow_engine.engine.checkpoints", "assert_checkpoint_payload_safe"),
    "build_llm_agent_capability": ("ai_workflow_engine.engine.agent_planner", "build_llm_agent_capability"),
    "capability_context_for_goal": ("ai_workflow_engine.engine.capabilities", "capability_context_for_goal"),
    "cleanup_artifacts": ("ai_workflow_engine.engine.artifacts", "cleanup_artifacts"),
    "format_trace_events": ("ai_workflow_engine.engine.capabilities", "format_trace_events"),
    "gather_capabilities": ("ai_workflow_engine.engine.capabilities", "gather_capabilities"),
}

__all__ = [
    "AgentCapability",
    "AgentEpisodePlanner",
    "LLMAgentPlanner",
    "ReplayPlanner",
    "build_llm_agent_capability",
    "StructuredLLMNode",
    "StructuredOutputError",
    "WorkflowLoopController",
    "WorkflowStepPlanner",
    "CapabilityRegistry",
    "CapabilityRuntime",
    "CapabilityCall",
    "CallbackTraceSink",
    "AsyncQueueDetailSink",
    "AsyncQueueTraceSink",
    "DetailSink",
    "InMemoryDetailSink",
    "InMemoryTraceSink",
    "JsonlDetailSink",
    "JsonlTraceSink",
    "TeeDetailSink",
    "TeeTraceSink",
    "CheckpointStore",
    "InMemoryCheckpointStore",
    "JsonlCheckpointStore",
    "assert_checkpoint_payload_safe",
    "RuntimePlanCompiler",
    "EvaluationController",
    "EvaluationPlanner",
    "ExternalAdapterCapability",
    "InMemoryExternalWriteSink",
    "JsonlExternalWriteSink",
    "ExternalProcessCapability",
    "ExternalProcessRequest",
    "HumanClarificationCapability",
    "HumanClarificationChannel",
    "InMemoryHumanClarificationChannel",
    "TraceSink",
    "WorkflowDecisionPlanner",
    "WorkflowInstrument",
    "WorkflowInstrumentRegistry",
    "WorkflowRunner",
    "SchedulingDecision",
    "WorkflowScheduler",
    "WorkflowSupervisor",
    "artifact_result",
    "capability_context_for_goal",
    "format_trace_events",
    "gather_capabilities",
    "cleanup_artifacts",
]


def __getattr__(name: str) -> Any:
    try:
        module_path, attr = _EXPORTS[name]
    except KeyError:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}") from None
    value = getattr(import_module(module_path), attr)
    globals()[name] = value  # cache: subsequent access skips __getattr__
    return value


def __dir__() -> list[str]:
    return sorted(set(__all__) | set(globals()))
