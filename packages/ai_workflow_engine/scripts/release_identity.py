"""Validate immutable wheel identity transitions between releases.

This release-boundary verifier uses only the standard library and the closed
package vocabulary from ``release_toolchain``.  It is not runtime package code.
"""

from __future__ import annotations

import re
from typing import Any

from release_toolchain import EXPECTED_PACKAGES, VERSION_RE, ReleaseError

_IDENTITY_KEYS = {"version", "sha256"}
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


def _exact_mapping(value: Any, expected: set[str], path: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ReleaseError(f"{path} must be an object")
    actual = set(value)
    missing = expected - actual
    unknown = actual - expected
    if missing:
        raise ReleaseError(f"{path} missing required fields: {sorted(missing)}")
    if unknown:
        raise ReleaseError(f"{path} carries unknown fields: {sorted(unknown)}")
    return value


def _strict_match(value: Any, pattern: re.Pattern[str], path: str) -> str:
    if type(value) is not str or pattern.fullmatch(value) is None:
        raise ReleaseError(f"{path} has invalid format")
    return value


def _identity_matrix(value: Any, label: str) -> dict[str, dict[str, str]]:
    matrix = _exact_mapping(value, set(EXPECTED_PACKAGES), label)
    identities: dict[str, dict[str, str]] = {}
    for package in EXPECTED_PACKAGES:
        path = f"{label}.{package}"
        identity = _exact_mapping(matrix[package], _IDENTITY_KEYS, path)
        identities[package] = {
            "version": _strict_match(identity["version"], VERSION_RE, f"{path}.version"),
            "sha256": _strict_match(identity["sha256"], _SHA256_RE, f"{path}.sha256"),
        }
    return identities


def validate_wheel_identity_transition(predecessor: Any, candidate: Any) -> None:
    """Reject changed wheel bytes that reuse a predecessor package version."""

    previous = _identity_matrix(predecessor, "predecessor")
    current = _identity_matrix(candidate, "candidate")
    reused = [
        package
        for package in EXPECTED_PACKAGES
        if previous[package]["version"] == current[package]["version"]
        and previous[package]["sha256"] != current[package]["sha256"]
    ]
    if reused:
        raise ReleaseError(
            "wheel bytes changed under an unchanged package version: "
            + ", ".join(reused)
        )


__all__ = ["validate_wheel_identity_transition"]
