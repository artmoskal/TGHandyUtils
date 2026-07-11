"""Q0.2: qualification runtime-identity manifest — deterministic, sensitive-value-free."""

import json

import pytest

from tests.support.engine_qualification import (
    QualificationRunIdentity,
    capture_run_identity,
)

pytestmark = pytest.mark.unit


def _identity(**overrides):
    readers = dict(
        cli_version_reader=lambda: "2.1.201 (Claude Code)",
        git_commit_reader=lambda: "f8c9e7b",
        platform_reader=lambda: "Linux-aarch64",
        clock=lambda: "2026-07-11T02:00:00+00:00",
    )
    readers.update(overrides)
    return capture_run_identity("sonnet", **readers)


def test_manifest_captures_exactly_the_six_identity_facts_deterministically():
    identity = _identity()
    payload = identity.model_dump()
    assert payload == {
        "requested_model": "sonnet",
        "cli_version": "2.1.201 (Claude Code)",
        "git_commit": "f8c9e7b",
        "platform": "Linux-aarch64",
        "started_at": "2026-07-11T02:00:00+00:00",
        "finished_at": None,
    }
    # schema is CLOSED: nothing else can ride along into the manifest
    with pytest.raises(Exception):
        QualificationRunIdentity(**{**payload, "environment": "dump"})


def test_manifest_rejects_sensitive_values_loudly():
    for leaky in ("sk-ant-abc123", "Bearer OAUTH thing", "/Users/artemm/.claude"):
        with pytest.raises(ValueError):
            _identity(cli_version_reader=lambda leaky=leaky: leaky)


def test_manifest_requires_a_model_alias():
    with pytest.raises(ValueError, match="requested_model is required"):
        capture_run_identity("", cli_version_reader=lambda: "x",
                             git_commit_reader=lambda: "y",
                             platform_reader=lambda: "z",
                             clock=lambda: "t")


def test_manifest_serializes_without_leaking_beyond_declared_fields():
    identity = _identity()
    serialized = json.dumps(identity.model_dump())
    assert set(json.loads(serialized)) == {
        "requested_model", "cli_version", "git_commit", "platform", "started_at", "finished_at",
    }
