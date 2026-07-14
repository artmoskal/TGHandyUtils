"""Workflow definition validation and LangGraph compilation."""

from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional

from ai_workflow_engine.workflow import END, KNOWN_NODE_KINDS, WorkflowDefinition, WorkflowNode


class WorkflowMachineCompiler:
    """Own preflight, graph wiring, routing, and compiled-machine caching."""

    def __init__(
        self,
        *,
        runtime: Any,
        handlers: Dict[str, Any],
        node_services: Any,
        replay_runtime: Any,
        state_schema: Any,
        subworkflows: Dict[str, WorkflowDefinition],
        model_profiles: Dict[str, Any],
        unsupported_node_error: Callable[[str], Exception],
    ) -> None:
        self._runtime = runtime
        self._handlers = handlers
        self._node_services = node_services
        self._replay_runtime = replay_runtime
        self._state_schema = state_schema
        self._subworkflows = subworkflows
        self._model_profiles = model_profiles
        self._unsupported_node_error = unsupported_node_error
        self._wait_runtime: Any = None
        self._compiled: Dict[tuple[str, str], Any] = {}

    @property
    def compiled(self) -> Dict[tuple[str, str], Any]:
        """Internal inspection surface used by architecture tests."""

        return self._compiled

    def set_model_profiles(self, profiles: Dict[str, Any]) -> None:
        self._model_profiles = profiles

    def set_wait_runtime(self, wait_runtime: Any) -> None:
        self._wait_runtime = wait_runtime

    def supported_kinds(self) -> set[str]:
        return set(self._handlers)

    def register_subworkflow(self, definition: WorkflowDefinition) -> None:
        existing = self._subworkflows.get(definition.workflow_id)
        if existing is not None and existing.definition_digest() != definition.definition_digest():
            self.evict(definition.workflow_id)
        self._subworkflows[definition.workflow_id] = definition

    def evict(self, workflow_id: str) -> None:
        for key in [key for key in self._compiled if key[0] == workflow_id]:
            self._compiled.pop(key, None)

    def compile(self, definition: WorkflowDefinition) -> Any:
        cache_key = (definition.workflow_id, definition.definition_digest())
        cached = self._compiled.get(cache_key)
        if cached is not None:
            return cached

        from langgraph.graph import END as lg_end, START, StateGraph

        graph = StateGraph(self._state_schema)
        for node in definition.nodes:
            handler = self._handlers.get(node.kind)
            if handler is None:
                raise self._unsupported_node_error(
                    f"node '{node.id}' kind '{node.kind}' is not supported by this executor"
                )
            node_callable = handler(self._node_services, definition, node)
            graph.add_node(node.id, self._replay_runtime.wrap(node, node_callable))

        graph.add_edge(START, definition.entry)
        for node in definition.nodes:
            self._wire_edges(graph, definition, node, lg_end)

        compiled = graph.compile()
        self._compiled[cache_key] = compiled
        return compiled

    def preflight(self, definition: WorkflowDefinition) -> Optional[str]:
        structural = definition.validate_graph()
        if structural:
            return "; ".join(structural)
        for node in definition.nodes:
            if (node.wait_policy or {}).get("mode") == "durable" and self._wait_runtime is None:
                return (
                    f"durable wait '{node.id}' requires a configured WaitCoordinator — "
                    "compose one via WorkflowEngineBuilder.with_wait_coordinator(...)"
                )
        known_caps = set(self._runtime.registry.names())
        for node in definition.nodes:
            if node.kind not in KNOWN_NODE_KINDS:
                return f"node '{node.id}' has unsupported kind: {node.kind}"
            if node.kind not in self._handlers:
                return f"node '{node.id}' kind '{node.kind}' is not implemented by this executor"
            if node.kind == "subworkflow":
                ref = node.subworkflow
                if ref is None or ref.workflow_id not in self._subworkflows:
                    return f"subworkflow node '{node.id}' references unregistered workflow"
                nested = self._nested_suspension_error(node.id, ref.workflow_id)
                if nested is not None:
                    return nested
                continue
            for capability in self._required_capabilities(node):
                if capability not in known_caps:
                    return f"node '{node.id}' binds to unregistered capability: {capability}"
            profile_error = self._model_profile_error(node)
            if profile_error:
                return profile_error
        return None

    def _nested_suspension_error(
        self,
        node_id: str,
        child_workflow_id: str,
    ) -> Optional[str]:
        seen: set[str] = set()
        stack = [child_workflow_id]
        while stack:
            workflow_id = stack.pop()
            if workflow_id in seen:
                continue
            seen.add(workflow_id)
            child = self._subworkflows.get(workflow_id)
            if child is None:
                continue
            for child_node in child.nodes:
                if child_node.kind == "human":
                    return (
                        f"subworkflow node '{node_id}' -> workflow '{workflow_id}' contains human "
                        f"node '{child_node.id}': nested suspension is not supported yet — move "
                        "the human gate to the top-level workflow"
                    )
                if child_node.kind == "subworkflow" and child_node.subworkflow is not None:
                    stack.append(child_node.subworkflow.workflow_id)
        return None

    def _model_profile_error(self, node: WorkflowNode) -> Optional[str]:
        if not node.model_profile:
            return None
        if node.model_profile not in self._model_profiles:
            return (
                f"node '{node.id}' references unknown model profile: {node.model_profile} "
                f"(registered: {sorted(self._model_profiles) or 'none'})"
            )
        capability = node.effective_capability()
        if capability:
            try:
                _spec, handler = self._runtime.registry.get(capability)
            except KeyError:
                return None
            if getattr(handler, "accepts_model_profile", None) is False:
                return (
                    f"node '{node.id}' declares model_profile={node.model_profile!r} but capability "
                    f"'{capability}' has a fixed llm client; use llm_factory or drop the binding"
                )
        return None

    @staticmethod
    def _required_capabilities(node: WorkflowNode) -> List[str]:
        capabilities: List[str] = []
        if node.kind in ("step", "human", "planner"):
            capabilities.append(node.capability or node.id)
        elif node.kind == "branch":
            capabilities.append(node.decider or node.capability or node.id)
        elif node.kind == "fanout":
            capabilities.append(node.item_capability or node.capability or node.id)
        elif node.kind == "evaluate":
            if node.evaluator:
                capabilities.append(node.evaluator)
            if node.fallback_capability:
                capabilities.append(node.fallback_capability)
        return [capability for capability in capabilities if capability]

    def _wire_edges(
        self,
        graph: Any,
        definition: WorkflowDefinition,
        node: WorkflowNode,
        lg_end: Any,
    ) -> None:
        outgoing = definition.outgoing(node.id)
        if node.kind == "branch":
            path_map: Dict[str, Any] = {
                transition.label: (lg_end if transition.target == END else transition.target)
                for transition in outgoing
                if transition.policy == "decision" and transition.label
            }
            path_map["__invalid__"] = lg_end
            path_map["__halt__"] = lg_end
        elif node.kind == "evaluate":
            path_map = {
                "accept": lg_end,
                "retry": lg_end,
                "retrace": lg_end,
                "replan": lg_end,
                "halt": lg_end,
            }
            for transition in outgoing:
                if (
                    transition.policy in ("on_accept", "on_reject")
                    and transition.label in path_map
                    and transition.target != END
                ):
                    path_map[transition.label] = transition.target
        else:
            next_transition = next(
                (transition for transition in outgoing if transition.policy == "always"),
                None,
            )
            path_map = {
                "__next__": (
                    lg_end
                    if next_transition is None or next_transition.target == END
                    else next_transition.target
                ),
                "__halt__": lg_end,
            }
            timeout_transition = next(
                (transition for transition in outgoing if transition.policy == "on_timeout"),
                None,
            )
            if timeout_transition is not None:
                path_map["__timeout__"] = (
                    lg_end if timeout_transition.target == END else timeout_transition.target
                )
        graph.add_conditional_edges(node.id, self._route_for(node), path_map)

    @staticmethod
    def _route_for(node: WorkflowNode) -> Callable[[Dict[str, Any]], str]:
        if node.kind == "branch":
            def route(state: Dict[str, Any]) -> str:
                label = state.get("routes", {}).get(node.id)
                if label == "__halt__":
                    return "__halt__"
                return label if label in node.branches else "__invalid__"
        elif node.kind == "evaluate":
            def route(state: Dict[str, Any]) -> str:
                routes = state.get("routes", {})
                if node.id not in routes:
                    raise KeyError(
                        f"evaluate node '{node.id}' produced no route decision — engine "
                        "invariant violated"
                    )
                return routes[node.id]
        else:
            def route(state: Dict[str, Any]) -> str:
                if state.get("routes", {}).get(node.id) == "halt":
                    return "__halt__"
                if state.get("routes", {}).get(node.id) == "__timeout__":
                    return "__timeout__"
                if state.get("node_status", {}).get(node.id) == "failed":
                    return "__halt__"
                return "__next__"
        return route
