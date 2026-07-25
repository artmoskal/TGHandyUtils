"""Canonical composition surface for durable observation bundles.

Persisted contracts, writing, and retention have separate owners. This module remains the
stable engine door that composes and exports that complete bundle API.
"""

from ai_workflow_engine.observation_contract import (
    ABANDON_MARKER_NAME,
    ARTIFACT_DIR_NAME,
    ARTIFACT_MANIFEST_NAME,
    BUNDLE_SCHEMA_VERSION,
    BundleStatus,
    COMMIT_MARKER_NAME,
    DEFAULT_ARTIFACT_MAX_BYTES,
    ObservationBundleMetaV3,
    ObservationSegment,
    ProviderEvidenceIntegrity,
    assert_plain_identity,
    load_bundle_meta_v3,
    resolve_child_dir,
)
from ai_workflow_engine.observation_retention import prune_observation_bundles
from ai_workflow_engine.observation_writer import (
    ObservationRunBundle,
    ObservationSequence,
    SequencedDetailSink,
    SequencedTraceSink,
    SequencedUsageSink,
    open_observation_run_bundle,
    write_minimal_abandoned_meta,
)


__all__ = [
    "ABANDON_MARKER_NAME",
    "ARTIFACT_DIR_NAME",
    "ARTIFACT_MANIFEST_NAME",
    "BUNDLE_SCHEMA_VERSION",
    "BundleStatus",
    "COMMIT_MARKER_NAME",
    "DEFAULT_ARTIFACT_MAX_BYTES",
    "ObservationBundleMetaV3",
    "ObservationRunBundle",
    "ObservationSegment",
    "ObservationSequence",
    "ProviderEvidenceIntegrity",
    "SequencedDetailSink",
    "SequencedTraceSink",
    "SequencedUsageSink",
    "assert_plain_identity",
    "load_bundle_meta_v3",
    "open_observation_run_bundle",
    "prune_observation_bundles",
    "resolve_child_dir",
    "write_minimal_abandoned_meta",
]
