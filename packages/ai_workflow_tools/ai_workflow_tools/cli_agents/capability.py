"""CLI-agent capability implementation."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import mimetypes
import tempfile
from pathlib import Path
from typing import Any, Callable, Protocol
from urllib.parse import urlparse

from ai_workflow_engine.engine.external import ExternalProcessCapability, ExternalProcessRequest
from ai_workflow_engine.execution_window import (
    invocation_window_remaining_s,
    resolve_invocation_bound,
)
from ai_workflow_engine.models import (
    CapabilityContext,
    CapabilityResult,
    CapabilitySpec,
    EvidenceRef,
    WorkflowArtifact,
    WorkflowTraceEvent,
    WorkflowUsageEvent,
)
from ai_workflow_engine.parsing import compose_cleaners, extract_fenced_json, extract_first_json_object
from ai_workflow_engine.usage import record_usage_event

from ai_workflow_tools.toolsets import BASH_SIDE_EFFECTS, bash_in_tools

from .assembly import build_cli_agent_invocation, resolve_effective_tools
from .models import CliAgentRequest, CliAgentResult, CliFlavor

AssetLoader = Callable[[EvidenceRef], bytes]


class TraceSink(Protocol):
    def record(self, event: WorkflowTraceEvent) -> None:
        """Record one trace event."""


@dataclass(frozen=True)
class _ParsedCliOutput:
    text: str
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_creation_tokens: int = 0
    num_turns: int | None = None
    duration_ms: int | None = None
    notional_cost_usd: float | None = None


@dataclass(frozen=True)
class _ExecutionBound:
    """The subprocess bound resolved from the engine window + the explicit request timeout.

    ``timeout_s`` drives the subprocess work deadline; ``kill_grace_s`` (terminate→kill→reap)
    is sized to fit inside the completion reserve so the outer engine hard boundary — which
    remains authoritative — is never the thing that reaps the child. ``notice`` is the optional
    provider-neutral finalization notice appended to the prompt when a reserve is active.
    ``error`` is non-None only when no bound could be resolved (missing window AND no explicit
    timeout): the capability fails loudly rather than run unbounded or invent a default.
    """

    timeout_s: float | None = None
    kill_grace_s: float = 10.0
    notice: str | None = None
    source: str | None = None
    soft_s: float | None = None
    hard_s: float | None = None
    reserve_s: float = 0.0
    headroom_s: float = 0.0
    settle_reserve_s: float = 0.0
    error: str | None = None

    def metadata(self) -> dict[str, Any]:
        return {
            "work_timeout_s": self.timeout_s,
            "kill_grace_s": self.kill_grace_s,
            "source": self.source,
            "engine_soft_s": self.soft_s,
            "engine_hard_s": self.hard_s,
            "cleanup_headroom_s": self.headroom_s,
            "settle_reserve_s": self.settle_reserve_s,
        }


class CliAgentCapability:
    """Run one CLI-backed agent episode as an engine capability."""

    DEFAULT_DESCRIPTION = (
        "Bounded CLI-agent episode (claude -p / codex exec): tri-state tool allow-list, "
        "MCP servers, staged input assets, artifact salvage, subscription-honest usage."
    )
    _DEFAULT_KILL_GRACE_S = 10.0

    def __init__(
        self,
        flavor: CliFlavor,
        *,
        name: str = "cli_agent",
        description: str | None = None,
        side_effects: list[str] | None = None,
        asset_loader: AssetLoader | None = None,
        trace_sink: TraceSink | None = None,
        external_runner: ExternalProcessCapability | None = None,
    ) -> None:
        self.flavor = flavor
        self.asset_loader = asset_loader
        self.trace_sink = trace_sink
        self.external_runner = external_runner or ExternalProcessCapability()
        if side_effects is None:
            # Honest default (owner decision 2026-07-04): the default claude_p tool set
            # includes Bash (shell writes + network) and codex_exec always runs
            # --sandbox workspace-write, so an undeclared-side-effect default would make
            # the engine's ledger lie. Pass an explicit list to narrow — a narrowed spec
            # combined with a Bash-bearing tool set is refused pre-spawn.
            side_effects = list(BASH_SIDE_EFFECTS)
        self.spec = CapabilitySpec(
            name=name,
            kind="agent",
            description=description or self.DEFAULT_DESCRIPTION,
            input_model=CliAgentRequest,
            output_model=CliAgentResult,
            side_effects=list(side_effects),
            metered=False,
            timeout_s=None,
            # v0.10: an honest declaration — this capability owns a killable subprocess and can
            # truly hard-stop its work, unlike a generic async handler. ``external``/``agent``
            # kind alone no longer implies process-backed (Phase 2R #5), so we declare it.
            timeout_enforcement="process",
        )

    async def __call__(
        self,
        context: CapabilityContext,
        request: CliAgentRequest | dict[str, Any],
    ) -> CapabilityResult:
        if isinstance(request, dict):
            request = CliAgentRequest.model_validate(request)

        effective_tools = resolve_effective_tools(self.flavor, request)
        required_side_effects: tuple[str, ...] = ()
        reason = ""
        if self.flavor.name == "codex_exec":
            required_side_effects = BASH_SIDE_EFFECTS
            reason = "codex_exec runs a workspace-write subscription CLI episode"
        elif bash_in_tools(effective_tools):
            required_side_effects = BASH_SIDE_EFFECTS
            reason = "the effective tool set includes Bash"

        if required_side_effects:
            missing = [
                effect for effect in required_side_effects if effect not in self.spec.side_effects
            ]
            if missing:
                return CapabilityResult(
                    status="failed",
                    error=(
                        f"cli_agent pre-spawn denial: {reason} but "
                        f"the capability spec does not declare side effects {missing}; declare "
                        "them or narrow the runtime request — the side-effect ledger must not lie"
                    ),
                    metadata={"flavor": self.flavor.name, "pre_spawn_denial": True},
                )

        # v0.10 Phase 3: the engine's execution window drives the subprocess. Resolve the
        # effective bound (engine soft window, narrowed — never enlarged — by any explicit
        # request timeout) BEFORE spawning; a missing bound fails loudly, never runs unbounded.
        bound = self._resolve_execution_bound(context, request)
        if bound.error is not None:
            return CapabilityResult(
                status="failed",
                error=bound.error,
                metadata={"flavor": self.flavor.name, "no_execution_bound": True},
            )

        workspace_dir = request.workspace_dir or tempfile.mkdtemp(prefix="cli_agent_ws_")
        workspace = Path(workspace_dir)
        workspace.mkdir(parents=True, exist_ok=True)
        prompt = request.prompt if bound.notice is None else f"{request.prompt}{bound.notice}"
        effective_request = request.model_copy(
            update={"workspace_dir": str(workspace), "prompt": prompt, "timeout_s": bound.timeout_s}
        )
        input_fingerprints = self._stage_input_assets(workspace, effective_request)
        snapshot = self._snapshot_salvage(workspace, effective_request.salvage_globs)
        invocation = build_cli_agent_invocation(self.flavor, effective_request)

        external = await self.external_runner(
            context,
            ExternalProcessRequest(
                command=invocation.argv,
                cwd=str(workspace),
                timeout_s=bound.timeout_s,
                stdin_data=invocation.stdin_data,
                result_file=invocation.result_file,
                kill_grace_s=bound.kill_grace_s,
                metadata={
                    "flavor": self.flavor.name,
                    "execution_bound_source": bound.source,
                    "process_execution_bound": bound.metadata(),
                },
            ),
        )

        output = external.output if isinstance(external.output, dict) else {}
        parsed_output = parse_cli_process_output(self.flavor, output)
        evidence_refs, workflow_artifacts, new_count = self._salvage_artifacts(
            workspace,
            request.salvage_globs,
            snapshot,
        )
        self._trace(
            "artifacts_salvaged",
            {
                "new_artifact_count": new_count,
                "artifacts": [
                    {
                        "ref_id": ref.ref_id,
                        "role": ref.role,
                        "uri": ref.uri,
                        "media_type": ref.media_type,
                    }
                    for ref in evidence_refs
                ],
            },
        )
        result_status, capability_status = self._status_from_external(external, output)
        stderr = str(output.get("stderr") or "")
        error = external.error if capability_status != "accepted" else None
        result = CliAgentResult(
            status=result_status,
            text=parsed_output.text,
            parsed=self._parse_lenient_json(parsed_output.text) if request.expect_json_result else None,
            artifacts=evidence_refs,
            new_artifact_count=new_count,
            input_fingerprints=input_fingerprints,
            input_tokens=parsed_output.input_tokens,
            output_tokens=parsed_output.output_tokens,
            cache_read_tokens=parsed_output.cache_read_tokens,
            cache_creation_tokens=parsed_output.cache_creation_tokens,
            num_turns=parsed_output.num_turns,
            duration_ms=parsed_output.duration_ms,
            notional_cost_usd=parsed_output.notional_cost_usd,
            returncode=_safe_int_or_none(output.get("returncode")),
            stderr_tail=stderr[-800:],
        )
        self._record_usage(request, result, capability_status=capability_status, error=error)
        agent_metadata = {
            "flavor": self.flavor.name,
            "agent_status": result.status,
            "new_artifact_count": result.new_artifact_count,
            "input_fingerprints": result.input_fingerprints,
            "cost_known": result.notional_cost_usd is not None,
            "execution_bound_s": bound.timeout_s,
            "execution_bound_source": bound.source,
            "process_execution_bound": bound.metadata(),
        }
        # v0.10.1: surface the bounded-capture truth (byte totals / truncation / result-file
        # settlement) from the shared process owner so the CLI door is as observable as a direct
        # external-process run — the same invariant must not drift between doors.
        process_io = external.metadata.get("process_io") if external.metadata else None
        if isinstance(process_io, dict):
            agent_metadata["process_io"] = process_io
        return CapabilityResult(
            status=capability_status,
            output=result,
            error=error,
            artifacts=workflow_artifacts,
            metadata=agent_metadata,
        )

    def _resolve_execution_bound(
        self, context: CapabilityContext, request: CliAgentRequest
    ) -> _ExecutionBound:
        """Resolve the subprocess bound through the ONE engine-owned invocation-window
        surface (`resolve_invocation_bound`): the engine's SOFT window drives the work; an
        explicit ``request.timeout_s`` may only NARROW it, never enlarge it; terminate→kill→
        reap always fits BEFORE the engine hard deadline (the reserve hosts it — with no
        reserve, represented headroom is carved from work time); a missing bound is refused
        loudly, never a resurrected hidden default."""

        window = context.execution_window
        ambient = invocation_window_remaining_s()
        if ambient is not None:
            engine_soft = ambient.soft_s
            engine_hard = ambient.hard_s
        elif window is not None and window.is_bounded:
            engine_soft = window.soft_timeout_s
            engine_hard = window.hard_timeout_s
        else:
            # Standalone/direct calls may still provide an explicit request timeout.
            engine_soft = None
            engine_hard = None
        shared = resolve_invocation_bound(
            engine_soft_s=engine_soft,
            engine_hard_s=engine_hard,
            explicit_timeout_s=request.timeout_s,
            default_kill_grace_s=self._DEFAULT_KILL_GRACE_S,
            require_bound=True,
            owner=f"cli_agent '{self.spec.name}'",
        )
        if shared.error is not None:
            return _ExecutionBound(error=shared.error)
        # The finalize budget the worker is told about = everything between its work bound
        # and the engine hard deadline (declared reserve, or the carved headroom).
        finalize_s = (
            max(0.0, engine_hard - shared.timeout_s) if engine_hard is not None else 0.0
        )
        notice = (
            self._finalization_notice(shared.timeout_s, finalize_s) if finalize_s > 0 else None
        )
        return _ExecutionBound(
            timeout_s=shared.timeout_s,
            kill_grace_s=shared.kill_grace_s,
            notice=notice,
            source="engine_window" if shared.source == "engine_window" else "explicit_request",
            soft_s=engine_soft,
            hard_s=engine_hard,
            reserve_s=finalize_s,
            headroom_s=shared.headroom_s,
            settle_reserve_s=shared.settle_reserve_s,
        )

    @staticmethod
    def _finalization_notice(work_s: float, reserve_s: float) -> str:
        """Provider-neutral finalization notice. Contains NO host paths and NO secrets — only
        the two durations the worker needs to wrap up before the authoritative cutoff."""

        return (
            "\n\n---\n"
            f"Execution window: about {work_s:.0f}s of working time remain before this session "
            f"is stopped, then ~{reserve_s:.0f}s to finalize. Save your result and any artifacts "
            "to the workspace before the working time ends — unsaved work is lost at the deadline."
        )

    def _stage_input_assets(self, workspace: Path, request: CliAgentRequest) -> list[dict[str, Any]]:
        if not request.input_assets:
            return []
        if self.asset_loader is None:
            raise ValueError("CliAgentCapability requires asset_loader when input_assets are provided")

        input_dir = workspace / "inputs"
        input_dir.mkdir(parents=True, exist_ok=True)
        fingerprints: list[dict[str, Any]] = []
        for index, ref in enumerate(request.input_assets, start=1):
            data = self.asset_loader(ref)
            if not isinstance(data, (bytes, bytearray)):
                raise ValueError("asset_loader must return bytes")
            raw = bytes(data)
            filename = f"input-{index}{_input_suffix(ref)}"
            path = input_dir / filename
            path.write_bytes(raw)
            fingerprints.append(
                {
                    "sha12": hashlib.sha256(raw).hexdigest()[:12],
                    "length": len(raw),
                    "role": ref.role,
                    "media_type": ref.media_type,
                    "path": str(path.relative_to(workspace)),
                }
            )
        self._trace("inputs_staged", {"inputs": fingerprints})
        return fingerprints

    def _trace(self, decision: str, metadata: dict[str, Any]) -> None:
        if self.trace_sink is None:
            return
        self.trace_sink.record(WorkflowTraceEvent(node=self.spec.name, decision=decision, metadata=metadata))

    @staticmethod
    def _parse_lenient_json(text: str) -> dict[str, Any] | None:
        if not text.strip():
            return None
        cleaner = compose_cleaners(extract_fenced_json, extract_first_json_object)
        try:
            parsed = json.loads(cleaner(text))
        except json.JSONDecodeError:
            return None
        return parsed if isinstance(parsed, dict) else {"value": parsed}

    def _salvage_artifacts(
        self,
        workspace: Path,
        globs: list[str],
        snapshot: set[Path],
    ) -> tuple[list[EvidenceRef], list[WorkflowArtifact], int]:
        paths = self._collect_salvage_paths(workspace, globs)
        new_paths = {path for path in paths if path not in snapshot}
        evidence_refs: list[EvidenceRef] = []
        workflow_artifacts: list[WorkflowArtifact] = []
        for path in paths:
            role = _artifact_role(path)
            media_type = mimetypes.guess_type(path.name)[0]
            evidence_refs.append(EvidenceRef(role=role, uri=str(path), media_type=media_type))
            workflow_artifacts.append(
                WorkflowArtifact(
                    path=str(path),
                    kind="media" if (media_type or "").startswith("image/") else "file",
                    source=self.flavor.name,
                    owner_node=self.spec.name,
                    metadata={"role": role, "media_type": media_type, "new": path in new_paths},
                )
            )
        return evidence_refs, workflow_artifacts, len(new_paths)

    def _snapshot_salvage(self, workspace: Path, globs: list[str]) -> set[Path]:
        return set(self._collect_salvage_paths(workspace, globs))

    @staticmethod
    def _collect_salvage_paths(workspace: Path, globs: list[str]) -> list[Path]:
        paths: set[Path] = set()
        for pattern in globs:
            iterator = workspace.rglob(pattern) if pattern == "session*.md" else workspace.glob(pattern)
            paths.update(path.resolve() for path in iterator if path.is_file())
        return sorted(paths)

    @staticmethod
    def _status_from_external(external: CapabilityResult, output: dict[str, Any]) -> tuple[str, str]:
        if external.status == "partial":
            return "truncated", "partial"
        returncode = _safe_int_or_none(output.get("returncode"))
        if external.status == "accepted" and returncode == 0:
            return "completed", "accepted"
        return "error", "failed"

    def _record_usage(
        self,
        request: CliAgentRequest,
        result: CliAgentResult,
        *,
        capability_status: str,
        error: str | None,
    ) -> None:
        cost_class = "subscription_notional" if request.subscription_mode else "metered"
        cost_known = result.notional_cost_usd is not None
        record_usage_event(
            WorkflowUsageEvent(
                provider=self.flavor.name,
                operation="tool",
                cost_class=cost_class,
                node=self.spec.name,
                model=request.model or self.flavor.name,
                input_tokens=result.input_tokens,
                output_tokens=result.output_tokens,
                total_tokens=result.input_tokens + result.output_tokens,
                input_token_details={
                    "cache_read": result.cache_read_tokens,
                    "cache_creation": result.cache_creation_tokens,
                },
                estimated_usd=result.notional_cost_usd if cost_class == "metered" else None,
                notional_usd=result.notional_cost_usd if cost_class == "subscription_notional" else None,
                elapsed_ms=result.duration_ms,
                success=capability_status == "accepted",
                error=error,
                metadata={
                    "cost_known": cost_known,
                    "cost_source": cost_class if cost_known else "unknown",
                    "returncode": result.returncode,
                    "new_artifact_count": result.new_artifact_count,
                },
            )
        )


def _input_suffix(ref: EvidenceRef) -> str:
    if ref.media_type:
        guessed = mimetypes.guess_extension(ref.media_type)
        if guessed:
            return guessed
    parsed = urlparse(ref.uri)
    suffix = Path(parsed.path).suffix
    return suffix or ".bin"


def parse_cli_process_output(flavor: CliFlavor, output: dict[str, Any]) -> _ParsedCliOutput:
    """Parse flavor-specific process output into shared usage/text fields."""

    stdout = str(output.get("stdout") or "")
    if flavor.result_source == "stdout_json_envelope":
        try:
            envelope = json.loads(stdout)
        except json.JSONDecodeError:
            return _ParsedCliOutput(text=stdout)
        if not isinstance(envelope, dict):
            return _ParsedCliOutput(text=stdout)
        usage = envelope.get("usage") if isinstance(envelope.get("usage"), dict) else {}
        return _ParsedCliOutput(
            text=_stringify_text(envelope.get("result")),
            input_tokens=_safe_int(usage.get("input_tokens")),
            output_tokens=_safe_int(usage.get("output_tokens")),
            cache_read_tokens=_safe_int(usage.get("cache_read_input_tokens")),
            cache_creation_tokens=_safe_int(usage.get("cache_creation_input_tokens")),
            num_turns=_safe_int_or_none(envelope.get("num_turns")),
            duration_ms=_safe_int_or_none(envelope.get("duration_ms")),
            notional_cost_usd=_safe_float_or_none(envelope.get("total_cost_usd")),
        )
    if flavor.result_source == "result_file":
        return _ParsedCliOutput(text=_stringify_text(output.get("result") or stdout))
    return _ParsedCliOutput(text=stdout)


def _artifact_role(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix in {".png", ".jpg", ".jpeg"}:
        return "screenshot"
    if path.name.startswith("session") and suffix == ".md":
        return "session"
    if path.name.startswith("page-") and suffix in {".yml", ".yaml"}:
        return "page_record"
    return "artifact"


def _stringify_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, (dict, list)):
        return json.dumps(value, sort_keys=True)
    return str(value)


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _safe_int_or_none(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _safe_float_or_none(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
