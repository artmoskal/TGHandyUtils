#!/usr/bin/env python3
"""Build and verify a deterministic large v4 fixture using installed packages only."""

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
from pathlib import Path
import runpy
import sys
from typing import Any


_SPIKE = runpy.run_path(str(Path(__file__).with_name("observation_v4_spike.py")))
RECORD_COUNT = _SPIKE["RECORD_COUNT"]
SEED = _SPIKE["SEED"]
qualification_body_records = _SPIKE["qualification_body_records"]


_RUN_ID = "observation-v4-qualification"
_FIXED_TIMESTAMP = "2026-09-02T00:00:00Z"
_ARTIFACT_BYTES = b"observation-v4-qualification-artifact\x00exact"


def _canonical(value: Any) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _require_new(path: Path, label: str) -> None:
    if path.exists():
        raise RuntimeError(f"{label} must not already exist: {path}")


def _selected_records(record_count: int):
    records = qualification_body_records()
    if record_count == RECORD_COUNT:
        return records
    return [
        records[round(index * (RECORD_COUNT - 1) / (record_count - 1))]
        for index in range(record_count)
    ]


def _package_origins(expected: dict[str, str]) -> dict[str, str]:
    import ai_workflow_engine
    import ai_workflow_tools
    import ai_workflow_viewer

    origins = {
        "ai-workflow-engine": str(Path(ai_workflow_engine.__file__).resolve()),
        "ai-workflow-tools": str(Path(ai_workflow_tools.__file__).resolve()),
        "ai-workflow-viewer": str(Path(ai_workflow_viewer.__file__).resolve()),
    }
    for distribution, expected_version in expected.items():
        actual = importlib.metadata.version(distribution)
        if actual != expected_version:
            raise RuntimeError(
                f"installed {distribution} version {actual!r} != expected {expected_version!r}"
            )
    if not all("site-packages" in origin for origin in origins.values()):
        raise RuntimeError(f"qualification imported repository source: {origins}")
    return origins


def _bundle_fingerprint(segment: Path) -> str:
    meta = json.loads((segment / "meta.json").read_text(encoding="utf-8"))
    for physical_fact in ("timestamp", "artifact_manifest_sha256"):
        meta.pop(physical_fact, None)
    artifacts = json.loads((segment / "artifacts.json").read_text(encoding="utf-8"))
    normalized_artifacts = [
        {key: value for key, value in row.items() if key != "source_path"}
        for row in artifacts
    ]
    files = {
        name: _sha256((segment / name).read_bytes())
        for name in ("definition.json", "trace.jsonl", "details.jsonl", "usage.jsonl")
    }
    value_files = {
        str(path.relative_to(segment.parent.parent)): _sha256(path.read_bytes())
        for path in sorted((segment.parent.parent / "values").glob("*/*.body.gz"))
    }
    return _sha256(
        _canonical(
            {
                "meta": meta,
                "artifacts": normalized_artifacts,
                "files": files,
                "values": value_files,
            }
        )
    )


def _create_fixture(root: Path, records) -> dict[str, Any]:
    from ai_workflow_engine import (
        ObservationDetail,
        ObservationJsonBody,
        ObservationReader,
        ObservationTextBody,
        WorkflowArtifact,
        WorkflowBuilder,
        WorkflowTraceEvent,
        WorkflowUsageEvent,
        open_observation_run_bundle,
    )
    from ai_workflow_engine.observation_values import body_sha256

    bundle = open_observation_run_bundle(root, _RUN_ID)
    usage_events = []
    body_digests = []
    logical_body_bytes = 0
    for index, (body_kind, value, encoded, category) in enumerate(records):
        request_event_id = f"qualification-request-{index:04d}"
        event_id = f"qualification-result-{index:04d}"
        detail_id = f"qualification-detail-{index:04d}"
        invocation_id = f"qualification-invocation-{index:04d}"
        body = (
            ObservationJsonBody(value=value)
            if body_kind == "json"
            else ObservationTextBody(value=value)
        )
        body_digests.append(body_sha256(body))
        logical_body_bytes += len(encoded)
        bundle.trace_sink.record(
            WorkflowTraceEvent(
                event_id=request_event_id,
                timestamp=_FIXED_TIMESTAMP,
                node=f"node-{index % 17}",
                phase="provider:request",
                decision="started",
                node_status=None,
                attempt=index % 4 + 1,
                invocation_id=invocation_id,
                detail_capture="capture_mode_off",
                metadata={"category": category, "route": f"route-{index % 9}"},
            )
        )
        bundle.trace_sink.record(
            WorkflowTraceEvent(
                event_id=event_id,
                timestamp=_FIXED_TIMESTAMP,
                node=f"node-{index % 17}",
                phase="tool:result",
                decision="accepted",
                node_status="completed",
                attempt=index % 4 + 1,
                invocation_id=invocation_id,
                detail_capture="captured",
                detail_refs=[detail_id],
                metadata={"category": category, "route": f"route-{index % 9}"},
            )
        )
        bundle.detail_sink.record(
            ObservationDetail(
                detail_id=detail_id,
                event_id=event_id,
                invocation_id=invocation_id,
                kind="tool_result",
                content_type="application/json" if body_kind == "json" else "text/plain",
                body=body,
                digest=body_sha256(body),
                metadata={"category": category},
            )
        )
        usage = WorkflowUsageEvent(
            event_id=f"qualification-usage-{index:04d}",
            node=f"node-{index % 17}",
            invocation_id=invocation_id,
            provider="qualification",
            model=f"model-{index % 3}",
            operation="tool",
            input_tokens=100 + index % 91,
            output_tokens=20 + index % 23,
            total_tokens=120 + index % 91 + index % 23,
            estimated_usd=0.000001,
            success=True,
            metadata={"category": category},
        )
        usage_events.append(usage)
        bundle.usage_sink.record(usage)

    artifact_source = root / "qualification-artifact.bin"
    artifact_source.write_bytes(_ARTIFACT_BYTES)
    bundle.finalize(
        WorkflowBuilder("observation-v4-qualification").step("inspect").build(),
        status="completed",
        usage=usage_events,
        artifacts=[
            WorkflowArtifact(
                artifact_id="qualification-artifact",
                path=str(artifact_source),
                kind="file",
                source="qualification",
                owner_node="inspect",
                cleanup_on_failure=False,
                metadata={"media_type": "application/octet-stream", "role": "evidence"},
            )
        ],
    )
    reader = ObservationReader(bundle.path)
    details = list(reader.iter_detail_envelopes())
    traces = list(reader.iter_trace_events())
    usage = list(reader.iter_usage_events())
    reader.validate_record_counts(
        trace_count=len(traces),
        detail_count=len(details),
        usage_count=len(usage),
    )
    manifest = reader.read_artifact_manifest()
    referenced = [detail for detail in details if detail.body.kind == "body_ref"]
    target = max(referenced, key=lambda detail: detail.body.byte_length)
    preview = reader.preview_bytes(target, max_bytes=64 * 1024)
    exact = reader.read_body_bytes(target, max_bytes=target.body.byte_length)
    if preview != exact[: len(preview)] or len(preview) != 64 * 1024:
        raise RuntimeError("targeted detail preview is not the exact bounded prefix")
    return {
        "segment": bundle.path,
        "artifact_source": artifact_source,
        "record_count": len(records),
        "trace_count": len(traces),
        "detail_count": len(details),
        "usage_count": len(usage),
        "body_manifest_sha256": _sha256(_canonical(body_digests)),
        "logical_body_bytes": logical_body_bytes,
        "value_object_count": len(list((reader.run_root / "values").glob("*/*.body.gz"))),
        "target_detail_id": target.detail_id,
        "target_body_sha256": target.body.sha256,
        "target_body_bytes": len(exact),
        "target_body_probe": exact[len(exact) // 2 : len(exact) // 2 + 512].decode(
            "utf-8", errors="strict"
        ),
        "preview_bytes": len(preview),
        "artifact_sha256": manifest[0]["sha256"],
        "bundle_fingerprint": _bundle_fingerprint(bundle.path),
    }


def _qualify_viewer(first: dict[str, Any], export_root: Path) -> dict[str, Any]:
    from ai_workflow_viewer import FileEventSource, export_observation_group, observation_group_to_html

    segment = first["segment"]
    run_root = segment.parent.parent
    source = FileEventSource(run_root.parent)
    group = source.read_group(_RUN_ID)
    page = observation_group_to_html(group)
    if first["target_body_probe"] in page:
        raise RuntimeError("compact viewer embedded retained body content")
    index = export_observation_group(group, export_root)
    detail_pages = list((export_root / "details").glob("detail-*.html"))
    if len(detail_pages) != first["detail_count"]:
        raise RuntimeError(
            f"static export wrote {len(detail_pages)} detail pages, expected {first['detail_count']}"
        )
    [exported_artifact] = export_root.glob("artifacts/segment-*/downloads/*.artifact.download")
    first["artifact_source"].unlink()
    if exported_artifact.read_bytes() != _ARTIFACT_BYTES:
        raise RuntimeError("static export did not retain exact artifact bytes")
    return {
        "group_status": group.status,
        "index_sha256": _sha256(index.read_bytes()),
        "detail_page_count": len(detail_pages),
        "exported_artifact_sha256": _sha256(exported_artifact.read_bytes()),
        "source_deletion_portable": True,
    }


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--work-dir", type=Path, required=True)
    parser.add_argument("--record", type=Path, required=True)
    parser.add_argument("--record-count", type=int, default=RECORD_COUNT)
    parser.add_argument("--engine-version", required=True)
    parser.add_argument("--tools-version", required=True)
    parser.add_argument("--viewer-version", required=True)
    args = parser.parse_args(argv)
    work_dir = args.work_dir.resolve()
    record = args.record.resolve()
    _require_new(work_dir, "qualification work directory")
    _require_new(record, "qualification record")
    if not 2 <= args.record_count <= RECORD_COUNT:
        raise RuntimeError(f"record-count must be within 2..{RECORD_COUNT}")
    origins = _package_origins(
        {
            "ai-workflow-engine": args.engine_version,
            "ai-workflow-tools": args.tools_version,
            "ai-workflow-viewer": args.viewer_version,
        }
    )
    records = _selected_records(args.record_count)
    work_dir.mkdir(parents=True)
    first = _create_fixture(work_dir / "fixture-a", records)
    second = _create_fixture(work_dir / "fixture-b", records)
    if first["bundle_fingerprint"] != second["bundle_fingerprint"]:
        raise RuntimeError("two deterministic v4 generations produced different bundle facts")
    viewer = _qualify_viewer(first, work_dir / "static-export")
    result = {
        "schema": "observation-v4-installed-qualification-v1",
        "passed": True,
        "seed": SEED,
        "record_count": args.record_count,
        "full_corpus": args.record_count == RECORD_COUNT,
        "origins": origins,
        "bundle_fingerprint": first["bundle_fingerprint"],
        "body_manifest_sha256": first["body_manifest_sha256"],
        "logical_body_bytes": first["logical_body_bytes"],
        "value_object_count": first["value_object_count"],
        "target_detail_id": first["target_detail_id"],
        "target_body_sha256": first["target_body_sha256"],
        "target_body_bytes": first["target_body_bytes"],
        "preview_bytes": first["preview_bytes"],
        "artifact_sha256": first["artifact_sha256"],
        "deterministic_second_generation": True,
        "allowed_physical_deltas": [
            "meta.timestamp",
            "artifacts[].source_path",
            "meta.artifact_manifest_sha256 (binds source_path)",
        ],
        "viewer": viewer,
        "fixture_root": str(first["segment"].parent.parent),
    }
    record.parent.mkdir(parents=True, exist_ok=True)
    record.write_text(json.dumps(result, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
