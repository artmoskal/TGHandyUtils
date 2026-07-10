"""Declarative workflow configuration loading.

The reusable engine owns profile/model parsing and override precedence. Product
applications decide which files and env prefixes to pass in.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path
import json
import os
import re
from typing import Any, Literal, Optional

import yaml
from pydantic import BaseModel, Field, ValidationError

from ai_workflow_engine.models import ModelProfile, WorkflowProfile


OverridePath = tuple[str, ...]

SECRET_KEY_RE = re.compile(
    r"(api[_-]?key|(^|[_-])token($|[_-])|secret|password|client[_-]?secret)",
    re.IGNORECASE,
)
SECRET_VALUE_RE = re.compile(
    r"(sk-[A-Za-z0-9_-]{16,}|xox[baprs]-[A-Za-z0-9-]{16,}|AIza[A-Za-z0-9_-]{16,})"
)

ALLOWED_TOP_LEVEL_KEYS = {"workflow", "models", "settings", "observation"}
WORKFLOW_FIELD_KEYS = set(WorkflowProfile.model_fields)

DEFAULT_ENV_OVERRIDES: dict[str, OverridePath] = {
    "PROFILE": ("workflow", "profile_id"),
    "PROFILE_ID": ("workflow", "profile_id"),
    "WORKFLOW_TYPE": ("workflow", "workflow_type"),
    "FAIL_MODE": ("workflow", "fail_mode"),
    "REQUESTED_CAPABILITIES": ("workflow", "requested_capabilities"),
    "CAPABILITIES": ("workflow", "requested_capabilities"),
    "MAX_STEPS": ("workflow", "limits", "max_steps"),
    "MAX_RETRIES": ("workflow", "limits", "max_retries"),
    "MAX_RETRACE": ("workflow", "limits", "max_retrace"),
    "MAX_TEXT_CALLS": ("workflow", "limits", "max_text_calls"),
    "MAX_TEXT_CALLS_PER_RUN": ("workflow", "limits", "max_text_calls"),
    "MAX_IMAGE_CALLS": ("workflow", "limits", "max_image_calls"),
    "MAX_IMAGE_CALLS_PER_RUN": ("workflow", "limits", "max_image_calls"),
    "MAX_PARALLEL_CHILDREN": ("workflow", "limits", "max_parallel_children"),
    "TIMEOUT_S": ("workflow", "limits", "timeout_s"),
    "MAX_ESTIMATED_USD": ("workflow", "limits", "max_estimated_usd"),
    "MAX_WORKER_CALLS": ("workflow", "limits", "max_worker_calls"),
    "MAX_WORKER_CALLS_PER_RUN": ("workflow", "limits", "max_worker_calls"),
    "MAX_INPUT_TOKENS_PER_CALL": ("workflow", "limits", "max_input_tokens_per_call"),
    "MAX_OUTPUT_TOKENS_PER_CALL": ("workflow", "limits", "max_output_tokens_per_call"),
    "MAX_IMAGES_PER_CALL": ("workflow", "limits", "max_images_per_call"),
    "MAX_ESTIMATED_USD_PER_CALL": ("workflow", "limits", "max_estimated_usd_per_call"),
    "MAX_AUTHORED_FANOUT_ITEMS": ("workflow", "limits", "max_authored_fanout_items"),
    "SCHEDULING_MODE": ("workflow", "scheduling", "mode"),
    "MAX_QUEUE_SIZE": ("workflow", "scheduling", "max_queue_size"),
    "STALE_AFTER_S": ("workflow", "scheduling", "stale_after_s"),
}


class WorkflowConfigError(ValueError):
    """Raised when workflow configuration is invalid or unsafe."""


class ObservationConfig(BaseModel):
    """Application-level observation policy (the engine owns the per-run mechanics).

    Configure once in the application config; when ``enabled`` the engine auto-opens a
    per-run observation bundle, routes trace/detail/usage into it, finalizes it with the
    true terminal status, and prunes old finalized bundles to ``retention_limit``.
    Products never call bundle mechanics in the normal path.
    """

    enabled: bool = False
    bundle_dir: str = "data/observations"
    retention_limit: Optional[int] = None
    capture: Literal["off", "full"] = "full"
    # G1 evidence resolution: "copy" archives run artifacts (screenshots, dumps, salvage)
    # into each bundle so EvidenceRefs stay resolvable for dashboards; artifacts prune
    # WITH the bundle — retention_limit is the single cleanup policy. "off" keeps the
    # honest manifest (what existed, where) without copying bytes.
    artifacts: Literal["copy", "off"] = "copy"
    artifact_max_bytes: int = Field(default=25 * 1024 * 1024, gt=0)


class WorkflowConfigBundle(BaseModel):
    """Loaded reusable workflow configuration."""

    profile: WorkflowProfile
    models: dict[str, ModelProfile] = Field(default_factory=dict)
    settings: dict[str, Any] = Field(default_factory=dict)
    observation: Optional[ObservationConfig] = None
    warnings: list[str] = Field(default_factory=list)

    def model_for(self, name: str) -> ModelProfile:
        try:
            return self.models[name]
        except KeyError as exc:
            raise KeyError(f"Unknown model profile: {name}") from exc


class WorkflowConfigLoader:
    """Load workflow config from defaults, YAML files, and env overrides."""

    def __init__(
        self,
        *,
        env_prefixes: Sequence[str] = ("WORKFLOW",),
        env: Mapping[str, str] | None = None,
        override_map: Mapping[str, OverridePath] | None = None,
    ) -> None:
        self.env_prefixes = tuple(prefix.strip("_") for prefix in env_prefixes if prefix.strip("_"))
        self.env = env if env is not None else os.environ
        self.override_map = {**DEFAULT_ENV_OVERRIDES, **dict(override_map or {})}

    def load(
        self,
        paths: Sequence[str | Path] = (),
        *,
        defaults: Mapping[str, Any] | None = None,
    ) -> WorkflowConfigBundle:
        raw: dict[str, Any] = _deep_copy_dict(defaults or {})
        warnings: list[str] = []
        _guard_no_secrets(raw, source="defaults")

        for path in paths:
            loaded = _read_yaml(path)
            _guard_no_secrets(loaded, source=str(path))
            warnings.extend(_structural_warnings(loaded, source=str(path)))
            raw = _deep_merge(raw, loaded)

        warnings.extend(self._apply_env_overrides(raw))
        warnings.extend(_structural_warnings(raw, source="merged config"))
        return _bundle_from_raw(raw, warnings)

    def _apply_env_overrides(self, raw: dict[str, Any]) -> list[str]:
        warnings: list[str] = []
        for prefix in self.env_prefixes:
            marker = f"{prefix}_"
            for env_key, env_value in self.env.items():
                if not env_key.startswith(marker):
                    continue
                logical_key = env_key[len(marker) :]
                path = self._path_for_logical_key(logical_key)
                if path is None:
                    warnings.append(f"Unsupported env override ignored: {env_key}")
                    continue
                current = _get_nested(raw, path)
                _set_nested(raw, path, _coerce_value(env_value, current))
        return warnings

    def _path_for_logical_key(self, logical_key: str) -> OverridePath | None:
        if logical_key in self.override_map:
            return self.override_map[logical_key]
        if logical_key.startswith("MODEL_"):
            return _model_override_path(logical_key[len("MODEL_") :])
        if logical_key.startswith("SETTING_"):
            setting_name = logical_key[len("SETTING_") :].lower()
            return ("settings", setting_name)
        if logical_key.startswith("CONSTRAINT_"):
            constraint_name = logical_key[len("CONSTRAINT_") :].lower()
            return ("workflow", "constraints", constraint_name)
        return None


def load_workflow_config(
    paths: Sequence[str | Path] = (),
    *,
    defaults: Mapping[str, Any] | None = None,
    env_prefixes: Sequence[str] = ("WORKFLOW",),
    env: Mapping[str, str] | None = None,
    override_map: Mapping[str, OverridePath] | None = None,
) -> WorkflowConfigBundle:
    """Convenience wrapper around WorkflowConfigLoader."""

    return WorkflowConfigLoader(
        env_prefixes=env_prefixes,
        env=env,
        override_map=override_map,
    ).load(paths, defaults=defaults)


def assert_no_config_secrets(data: Mapping[str, Any], *, source: str = "config") -> None:
    """Public guard for tests and product config scanners."""

    _guard_no_secrets(dict(data), source=source)


def _bundle_from_raw(raw: Mapping[str, Any], warnings: list[str]) -> WorkflowConfigBundle:
    workflow_raw = dict(raw.get("workflow") or {})
    if "workflow_type" not in workflow_raw:
        raise WorkflowConfigError("workflow.workflow_type is required")

    model_profiles: dict[str, ModelProfile] = {}
    for name, model_raw in dict(raw.get("models") or {}).items():
        if not isinstance(model_raw, Mapping):
            raise WorkflowConfigError(f"models.{name} must be an object")
        data = {"name": name, **dict(model_raw)}
        model_profiles[str(name)] = ModelProfile.model_validate(data)

    try:
        profile = WorkflowProfile.model_validate(workflow_raw)
    except ValidationError as exc:
        raise WorkflowConfigError(f"workflow profile validation failed: {exc}") from exc

    settings = dict(raw.get("settings") or {})
    observation_raw = raw.get("observation")
    observation = None
    if observation_raw is not None:
        if not isinstance(observation_raw, Mapping):
            raise WorkflowConfigError("observation must be an object")
        observation = ObservationConfig.model_validate(dict(observation_raw))
    return WorkflowConfigBundle(
        profile=profile,
        models=model_profiles,
        settings=settings,
        observation=observation,
        warnings=warnings,
    )


def _read_yaml(path: str | Path) -> dict[str, Any]:
    config_path = Path(path)
    with config_path.open("r", encoding="utf-8") as fh:
        loaded = yaml.safe_load(fh) or {}
    if not isinstance(loaded, dict):
        raise WorkflowConfigError(f"{config_path} must contain a YAML object")
    return loaded


def _deep_copy_dict(data: Mapping[str, Any]) -> dict[str, Any]:
    return json.loads(json.dumps(data))


def _deep_merge(base: Mapping[str, Any], override: Mapping[str, Any]) -> dict[str, Any]:
    merged = _deep_copy_dict(base)
    for key, value in override.items():
        if isinstance(value, Mapping) and isinstance(merged.get(key), Mapping):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = _deep_copy_value(value)
    return merged


def _deep_copy_value(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _deep_copy_value(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_deep_copy_value(item) for item in value]
    return value


def _structural_warnings(data: Mapping[str, Any], *, source: str) -> list[str]:
    warnings: list[str] = []
    for key in sorted(data):
        if key not in ALLOWED_TOP_LEVEL_KEYS:
            warnings.append(f"{source}: unknown top-level key ignored by engine loader: {key}")
    workflow = data.get("workflow")
    if isinstance(workflow, Mapping):
        for key in sorted(workflow):
            if key not in WORKFLOW_FIELD_KEYS:
                warnings.append(f"{source}: unknown workflow key ignored by profile model: {key}")
    return warnings


def _guard_no_secrets(value: Any, *, source: str, path: tuple[str, ...] = ()) -> None:
    if isinstance(value, Mapping):
        for key, item in value.items():
            key_text = str(key)
            next_path = (*path, key_text)
            if SECRET_KEY_RE.search(key_text) and _has_non_empty_value(item):
                dotted = ".".join(next_path)
                raise WorkflowConfigError(f"{source} contains secret-like key: {dotted}")
            _guard_no_secrets(item, source=source, path=next_path)
        return
    if isinstance(value, list):
        for index, item in enumerate(value):
            _guard_no_secrets(item, source=source, path=(*path, str(index)))
        return
    if isinstance(value, str) and SECRET_VALUE_RE.search(value):
        dotted = ".".join(path) or "<root>"
        raise WorkflowConfigError(f"{source} contains secret-like value at {dotted}")


def _has_non_empty_value(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, (list, tuple, set, dict)):
        return bool(value)
    return True


def _get_nested(data: Mapping[str, Any], path: OverridePath) -> Any:
    cursor: Any = data
    for key in path:
        if not isinstance(cursor, Mapping) or key not in cursor:
            return None
        cursor = cursor[key]
    return cursor


def _set_nested(data: dict[str, Any], path: OverridePath, value: Any) -> None:
    cursor = data
    for key in path[:-1]:
        child = cursor.get(key)
        if not isinstance(child, dict):
            child = {}
            cursor[key] = child
        cursor = child
    cursor[path[-1]] = value


def _coerce_value(raw: str, current: Any) -> Any:
    text = raw.strip()
    if isinstance(current, bool):
        return text.lower() in {"1", "true", "yes", "on"}
    if isinstance(current, int) and not isinstance(current, bool):
        return int(text)
    if isinstance(current, float):
        return float(text)
    if isinstance(current, list):
        return [item.strip() for item in text.split(",") if item.strip()]
    if isinstance(current, dict):
        return json.loads(text)
    if text.lower() in {"true", "false"}:
        return text.lower() == "true"
    if text.lower() in {"none", "null"}:
        return None
    if text.startswith("[") or text.startswith("{"):
        return json.loads(text)
    if re.fullmatch(r"-?\d+", text):
        return int(text)
    if re.fullmatch(r"-?\d+\.\d+", text):
        return float(text)
    return raw


def _model_override_path(model_key: str) -> OverridePath:
    suffix_map: dict[str, str] = {
        "_PROVIDER": "provider",
        "_TEMPERATURE": "temperature",
        "_TIMEOUT_S": "timeout_s",
        "_MAX_RETRIES": "max_retries",
        "_MODEL": "model",
    }
    for suffix, field in suffix_map.items():
        if model_key.endswith(suffix):
            name = model_key[: -len(suffix)].lower()
            return ("models", name, field)
    return ("models", model_key.lower(), "model")
