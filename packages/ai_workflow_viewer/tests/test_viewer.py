"""Standalone viewer consumes engine observability records."""

from __future__ import annotations

import ast
import hashlib
import json
from pathlib import Path

import pytest

from ai_workflow_engine import (
    ObservationDetail,
    ObservationJsonBody,
    ObservationReader,
    ObservationTextBody,
    WorkflowBuilder,
    WorkflowTraceEvent,
    WorkflowUsageEvent,
)
from ai_workflow_engine.observation_contract import ObservationDetailEnvelope
from ai_workflow_engine.observation_values import RunValueStore, body_sha256, persist_observation_body
from ai_workflow_engine.usage_contract import (
    NormalizedTokenUsage,
    NotionalPricingResult,
    NotionalRate,
)
from ai_workflow_viewer import FileEventSource, JsonlObservationViewer

pytestmark = pytest.mark.unit


def _detail(*, value=None, **fields) -> ObservationDetail:
    body = ObservationJsonBody(value={} if value is None else value)
    return ObservationDetail(body=body, digest=body_sha256(body), **fields)


def test_viewer_renders_persisted_pricing_basis_without_recalculation():
    from ai_workflow_viewer import build_observation_graph, observation_graph_to_html

    definition = WorkflowBuilder("pricing-view").step("agent").build()
    graph = build_observation_graph(definition, [], [], [], run_id="pricing-run")
    usage = NormalizedTokenUsage(
        counter_schema="codex_inclusive",
        uncached_input_tokens=100,
        cache_read_input_tokens=20,
        cache_creation_input_tokens=0,
        non_reasoning_output_tokens=20,
        reasoning_output_tokens=10,
        raw_input_tokens=120,
        raw_output_tokens=30,
        raw_total_tokens=150,
    )
    rate = NotionalRate(
        provider="codex_exec",
        model_prefix="gpt-5.4",
        rate_version="rate-v1",
        source="configured_public_rate",
        uncached_input_per_1m=2.5,
        cached_input_per_1m=0.25,
        cache_creation_input_per_1m=2.5,
        output_per_1m=15,
    )
    event = WorkflowUsageEvent(
        node="agent<script>",
        provider="codex_exec",
        model="gpt-5.4-codex",
        cost_class="subscription_notional",
        input_tokens=120,
        output_tokens=30,
        total_tokens=150,
        normalized_usage=usage,
        notional_usd=0.000705,
        notional_pricing=NotionalPricingResult(
            source="configured_public_rate",
            amount_usd=0.000705,
            catalog_version="catalog-v1",
            rate=rate,
        ),
    )

    page = observation_graph_to_html(
        definition,
        graph,
        usage_events=[event],
    )

    assert "$0.000705 notional" in page
    assert "configured public rate · catalog-v1 · rate-v1" in page
    assert (
        "100 uncached input · 20 cached input · 0 cache-created input · "
        "30 output (10 reasoning output)"
    ) in page
    assert "agent&lt;script&gt;" in page
    assert "agent<script>" not in page


def test_served_viewer_projects_persisted_pricing_basis_over_real_http(tmp_path):
    import threading
    import urllib.request

    from ai_workflow_viewer import FileEventSource, serve_viewer

    definition = WorkflowBuilder("pricing-served").step("agent").build()
    usage = NormalizedTokenUsage(
        counter_schema="codex_inclusive",
        uncached_input_tokens=100,
        cache_read_input_tokens=20,
        cache_creation_input_tokens=0,
        non_reasoning_output_tokens=20,
        reasoning_output_tokens=10,
        raw_input_tokens=120,
        raw_output_tokens=30,
        raw_total_tokens=150,
    )
    rate = NotionalRate(
        provider="codex_exec",
        model_prefix="gpt-5.4",
        rate_version="served-rate-v1",
        source="configured_proxy_rate",
        uncached_input_per_1m=2.5,
        cached_input_per_1m=0.25,
        cache_creation_input_per_1m=2.5,
        output_per_1m=15.0,
    )
    _write_bundle(
        tmp_path,
        "pricing-served-run",
        definition,
        trace_events=[
            WorkflowTraceEvent(
                node="agent",
                node_status="completed",
                phase="node:result",
                run_id="pricing-served-run",
                sequence=1,
            )
        ],
        usage_events=[
            WorkflowUsageEvent(
                node="agent",
                provider="codex_exec",
                model="gpt-5.4-codex",
                cost_class="subscription_notional",
                input_tokens=120,
                output_tokens=30,
                total_tokens=150,
                normalized_usage=usage,
                notional_usd=0.000705,
                notional_pricing=NotionalPricingResult(
                    source="configured_proxy_rate",
                    amount_usd=0.000705,
                    catalog_version="served-catalog-v1",
                    rate=rate,
                ),
                run_id="pricing-served-run",
                sequence=2,
            )
        ],
    )
    server = serve_viewer(JsonlObservationViewer(FileEventSource(tmp_path)), port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        root = f"http://127.0.0.1:{server.server_address[1]}"
        page = urllib.request.urlopen(
            f"{root}/?run_id=pricing-served-run", timeout=5
        ).read().decode("utf-8")
        assert "$0.000705 notional" in page
        assert "configured proxy rate · served-catalog-v1 · served-rate-v1" in page
        assert "100 uncached input" in page
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_viewer_truth_owners_have_one_way_dependencies():
    """Storage, grouping, projection, and presentation stay separate owners."""

    root = Path(__file__).parents[1] / "ai_workflow_viewer"
    sources = {
        name: (root / name).read_text(encoding="utf-8")
        for name in (
            "assets.py",
            "detail_presentation.py",
            "observation_data.py",
            "grouping.py",
            "detail_delivery.py",
            "projection.py",
            "rendering.py",
            "view_models.py",
            "event_source.py",
            "observability.py",
            "server.py",
            "static_export.py",
        )
    }

    def imports(source: str) -> set[str]:
        found: set[str] = set()
        for node in ast.walk(ast.parse(source)):
            if isinstance(node, ast.ImportFrom) and node.module:
                found.add(node.module)
            elif isinstance(node, ast.Import):
                found.update(alias.name for alias in node.names)
        return found

    data_imports = imports(sources["observation_data.py"])
    grouping_imports = imports(sources["grouping.py"])
    projection_imports = imports(sources["projection.py"])
    source_imports = imports(sources["event_source.py"])
    detail_delivery_imports = imports(sources["detail_delivery.py"])
    detail_presentation_imports = imports(sources["detail_presentation.py"])
    static_export_imports = imports(sources["static_export.py"])
    asset_imports = imports(sources["assets.py"])
    view_model_imports = imports(sources["view_models.py"])
    server_imports = imports(sources["server.py"])

    forbidden_presentation = ("html", "http", "urllib", "ai_workflow_viewer.server")

    def forbidden(found: set[str]) -> set[str]:
        return {
            name
            for name in found
            if any(name == prefix or name.startswith(f"{prefix}.") for prefix in forbidden_presentation)
        }

    assert not forbidden(data_imports)
    assert not forbidden(grouping_imports)
    assert not forbidden(projection_imports)
    assert not any(name.startswith("ai_workflow_engine") for name in asset_imports)
    assert not forbidden(view_model_imports)
    assert "pathlib" not in grouping_imports, "grouping must consume decoded facts, not storage"
    assert "ai_workflow_viewer.event_source" not in grouping_imports
    assert "ai_workflow_viewer.observability" not in grouping_imports
    assert "ai_workflow_viewer.observability" not in projection_imports
    assert "ai_workflow_viewer.server" not in source_imports
    assert "ai_workflow_viewer.server" not in detail_delivery_imports
    assert "ai_workflow_viewer.rendering" not in detail_delivery_imports
    assert "ai_workflow_viewer.server" not in detail_presentation_imports
    assert "ai_workflow_viewer.server" not in static_export_imports
    assert "html" not in server_imports, "HTTP transport must delegate HTML rendering"

    event_tree = ast.parse(sources["event_source.py"])
    render_tree = ast.parse(sources["observability.py"])
    rendering_tree = ast.parse(sources["rendering.py"])
    event_defs = {
        node.name for node in event_tree.body if isinstance(node, (ast.FunctionDef, ast.ClassDef))
    }
    render_defs = {
        node.name for node in render_tree.body if isinstance(node, (ast.FunctionDef, ast.ClassDef))
    }
    rendering_defs = {
        node.name
        for node in rendering_tree.body
        if isinstance(node, (ast.FunctionDef, ast.ClassDef))
    }
    server_defs = {
        node.name
        for node in ast.parse(sources["server.py"]).body
        if isinstance(node, (ast.FunctionDef, ast.ClassDef))
    }
    assert not {"_canonical_partition", "_usage_totals_from_events"} & event_defs
    assert not {"_external_outcome", "_next_status", "_add_usage_cost"} & render_defs
    assert not {"_external_outcome", "_next_status", "_add_usage_cost"} & rendering_defs
    assert "ai_workflow_viewer.rendering" in imports(sources["observability.py"])
    assert "ai_workflow_viewer.observability" not in imports(sources["rendering.py"])
    assert not {"_run_row", "_usage_label"} & server_defs


def test_packaged_assets_are_exactly_inlined_and_view_data_is_closed():
    from ai_workflow_engine import WorkflowBuilder
    from ai_workflow_viewer.assets import load_asset_text
    from ai_workflow_viewer.observability import (
        _observation_base_css,
        _observation_view_data,
        _rich_graph_css,
        _rich_graph_js,
        build_observation_graph,
        observation_graph_to_html,
    )
    from ai_workflow_viewer.view_models import ObservationViewData

    assert _observation_base_css() == "\n" + load_asset_text("observation-base.css")
    assert _rich_graph_css() == "\n" + load_asset_text("rich-graph.css")
    assert _rich_graph_js() == "\n" + load_asset_text("rich-graph.js")
    with pytest.raises(ValueError, match="unknown viewer asset"):
        load_asset_text("../secret")

    definition = WorkflowBuilder("asset-smoke").step("inspect").build()
    graph = build_observation_graph(definition, [])
    view_data = _observation_view_data(definition, graph)
    assert set(view_data) == {"workflow_id", "run_id", "canvas", "nodes", "transitions"}
    with pytest.raises(ValueError, match="extra_forbidden"):
        ObservationViewData.model_validate({**view_data, "invented_status": "completed"})
    page = observation_graph_to_html(definition, graph)
    assert ".graph-node" in page
    assert "const dataEl = document.getElementById" in page
    assert '<link rel="stylesheet"' not in page and '<script src="' not in page

    package_toml = (Path(__file__).parents[1] / "pyproject.toml").read_text(encoding="utf-8")
    assert '"assets/*.css"' in package_toml and '"assets/*.js"' in package_toml


def _write_bundle(
    base,
    run_id: str,
    definition,
    *,
    trace_events=None,
    details=None,
    usage_events=None,
    dir_name=None,
    meta_extra=None,
):
    run_root = base / run_id
    run_path = run_root / "segments" / (dir_name or run_id)
    run_path.mkdir(parents=True)
    value_store = RunValueStore(run_root)
    persisted_details = []
    for detail in details or []:
        if isinstance(detail, ObservationDetailEnvelope):
            persisted_details.append(detail)
            continue
        logical = detail.model_copy(update={"run_id": detail.run_id or run_id})
        persisted_details.append(
            ObservationDetailEnvelope(
                **logical.model_dump(exclude={"body", "digest"}),
                body=persist_observation_body(value_store, logical.body),
            )
        )
    (run_path / "definition.json").write_text(definition.model_dump_json(), encoding="utf-8")
    (run_path / "trace.jsonl").write_text(
        "\n".join(event.model_dump_json() for event in (trace_events or [])) + "\n",
        encoding="utf-8",
    )
    (run_path / "details.jsonl").write_text(
        "\n".join(detail.model_dump_json() for detail in persisted_details) + "\n",
        encoding="utf-8",
    )
    (run_path / "usage.jsonl").write_text(
        "\n".join(event.model_dump_json() for event in (usage_events or [])) + "\n",
        encoding="utf-8",
    )
    stream_hashes = {
        name: hashlib.sha256((run_path / filename).read_bytes()).hexdigest()
        for name, filename in {
            "trace": "trace.jsonl",
            "detail": "details.jsonl",
            "usage": "usage.jsonl",
        }.items()
    }
    # Full strict v4 meta — the loader rejects anything less; totals/counts are
    # derived from the actual inputs, ``meta_extra`` overrides (e.g. segment identity).
    usage = usage_events or []
    metered = [
        float(event.estimated_usd)
        for event in usage
        if getattr(event, "cost_class", "metered") == "metered" and event.estimated_usd is not None
    ]
    notional = [float(event.notional_usd) for event in usage if event.notional_usd is not None]
    (run_path / "meta.json").write_text(
        json.dumps(
            {
                "bundle_schema_version": 4,
                "run_id": run_id,
                "workflow_id": definition.workflow_id,
                "status": "completed",
                "timestamp": "2026-06-21T20:00:00Z",
                "definition_path": "definition.json",
                "trace_path": "trace.jsonl",
                "detail_path": "details.jsonl",
                "usage_path": "usage.jsonl",
                "definition_digest": definition.definition_digest(),
                "artifact_manifest_path": "artifacts.json",
                "artifact_root": "artifacts",
                "value_store_layout": "run-scoped-sha256-gzip-v1",
                "inline_body_max_bytes": 4096,
                "artifact_count": 0,
                "artifacts_copied": 0,
                "trace_count": len(trace_events or []),
                "detail_count": len(persisted_details),
                "usage_count": len(usage),
                "trace_sha256": stream_hashes["trace"],
                "detail_sha256": stream_hashes["detail"],
                "usage_sha256": stream_hashes["usage"],
                "incomplete_streams": [],
                "stream_diagnostic": None,
                "usage_totals_scope": "run_cumulative_at_finalize",
                "total_tokens": sum(event.total_tokens for event in usage),
                "metered_usd": round(sum(metered), 6) if metered else None,
                "notional_usd": round(sum(notional), 6) if notional else None,
                "segment_id": dir_name or run_id,
                "segment_index": 0,
                "segment_kind": "initial",
                "attempt": None,
                "provider_evidence": {"integrity": "complete", "diagnostic": None},
                **(meta_extra or {}),
            }
        ),
        encoding="utf-8",
    )
    return run_path


def test_jsonl_observation_viewer_renders_html_from_public_contracts(tmp_path):
    definition = WorkflowBuilder("viewer").step("plan").step("render").build()
    event = WorkflowTraceEvent(
        node="plan",
        decision="llm:prompt",
        phase="llm:request",
        invocation_id="inv-viewer-test",
        detail_capture="captured",
        detail_refs=["detail-1"],
        run_id="run-1",
        sequence=1,
    )
    detail = _detail(
        detail_id="detail-1",
        event_id=event.event_id,
        invocation_id="inv-viewer-test",
        kind="rendered_prompt",
        content_type="application/json",
        value={"prompt": "viewer prompt"},
        run_id="run-1",
        sequence=2,
    )
    run_path = _write_bundle(
        tmp_path,
        "run-1",
        definition,
        trace_events=[
            event,
            WorkflowTraceEvent(
                node="plan",
                decision="accepted",
                phase="llm:response",
                invocation_id="inv-viewer-test",
                detail_capture="capture_mode_off",
                run_id="run-1",
                sequence=3,
            ),
        ],
        usage_events=[
            WorkflowUsageEvent(
                node="plan",
                invocation_id="inv-viewer-test",
                total_tokens=9,
                estimated_usd=0.01,
                run_id="run-1",
                sequence=4,
            )
        ],
        details=[detail],
    )

    viewer = JsonlObservationViewer.from_run_bundle(run_path, title="Viewer test")

    html = viewer.html()
    assert "Viewer test" in html
    assert "Investigation Graph" in html
    assert 'data-node-id="plan"' in html
    assert "flowchart TD" in html
    assert "9 tok" in html
    assert "metered $0.0100" in html
    assert detail.digest in html
    assert "<dialog" in html
    assert "Copy raw" in html
    assert [record["type"] for record in viewer.event_records()] == ["trace", "detail", "trace", "usage"]


def test_compact_viewer_never_opens_referenced_detail_bodies(tmp_path, monkeypatch):
    definition = WorkflowBuilder("compact-viewer").step("inspect").build()
    detail = _detail(
        detail_id="large-detail",
        event_id="event-large",
        invocation_id="inv-large",
        kind="tool_result",
        content_type="application/json",
        value={"payload": "x" * 100_000},
        run_id="compact-run",
        sequence=2,
    )
    run_path = _write_bundle(
        tmp_path,
        "compact-run",
        definition,
        trace_events=[
            WorkflowTraceEvent(
                node="inspect",
                event_id="event-large",
                phase="tool:request",
                invocation_id="inv-large",
                detail_capture="captured",
                detail_refs=["large-detail"],
                run_id="compact-run",
                sequence=1,
            ),
            WorkflowTraceEvent(
                node="inspect",
                event_id="event-complete",
                phase="tool:result",
                invocation_id="inv-large",
                detail_capture="capture_mode_off",
                run_id="compact-run",
                sequence=3,
            ),
        ],
        usage_events=[
            WorkflowUsageEvent(
                node="inspect",
                invocation_id="inv-large",
                run_id="compact-run",
                sequence=4,
            )
        ],
        details=[detail],
    )
    persisted = next(ObservationReader(run_path).iter_detail_envelopes())
    assert persisted.body.kind == "body_ref"

    def body_open_is_a_bug(*_args, **_kwargs):
        raise AssertionError("compact viewer opened a referenced body")

    monkeypatch.setattr(ObservationReader, "iter_body_bytes", body_open_is_a_bug)
    viewer = JsonlObservationViewer.from_run_bundle(run_path)
    html = viewer.html()
    assert "large-detail" in html
    assert detail.digest in html
    assert str(persisted.body.byte_length) in html
    assert 'data-load-detail="/detail/compact-run/compact-run/large-detail' in html
    assert "mode=preview" in html
    assert "mode=download" in html
    assert "x" * 10_000 not in html


def test_static_observation_export_keeps_index_compact_and_detail_bodies_inert(tmp_path):
    from ai_workflow_engine.observation_values import canonical_json_bytes
    from ai_workflow_viewer import export_observation_group

    definition = WorkflowBuilder("static-detail-export").step("inspect").build()
    hostile = "</script><script>globalThis.owned=true</script>"
    value = {"payload": hostile + ("MIDDLE" * 20_000) + "STATIC-TAIL"}
    detail = _detail(
        detail_id="static-large-detail",
        event_id="static-event",
        kind="tool_result",
        content_type="application/json",
        value=value,
        run_id="static-run",
        sequence=2,
    )
    _write_bundle(
        tmp_path,
        "static-run",
        definition,
        trace_events=[
            WorkflowTraceEvent(
                node="inspect",
                event_id="static-event",
                phase="tool:result",
                detail_capture="captured",
                detail_refs=["static-large-detail"],
                run_id="static-run",
                sequence=1,
            )
        ],
        details=[detail],
    )
    target = tmp_path / "export"

    index_path = export_observation_group(
        FileEventSource(tmp_path).read_group("static-run"),
        target,
    )

    index = index_path.read_text(encoding="utf-8")
    preview_pages = list((target / "details").glob("detail-*.html"))
    exact_bodies = list((target / "details").glob("body-*.bin"))
    assert index_path == target / "index.html"
    assert len(index.encode("utf-8")) < 300_000
    assert hostile not in index and "STATIC-TAIL" not in index
    assert len(preview_pages) == 1
    assert len(exact_bodies) == 1
    assert preview_pages[0].name in index
    assert exact_bodies[0].name in index

    preview_page = preview_pages[0].read_text(encoding="utf-8")
    assert "&lt;/script&gt;&lt;script&gt;globalThis.owned=true&lt;/script&gt;" in preview_page
    assert hostile not in preview_page
    assert "STATIC-TAIL" not in preview_page
    assert "Preview limited to 65536 of" in preview_page
    expected = canonical_json_bytes(value)
    assert exact_bodies[0].read_bytes() == expected
    assert hashlib.sha256(expected).hexdigest() in exact_bodies[0].name


def test_served_detail_endpoint_previews_and_downloads_one_validated_body(tmp_path):
    import threading
    import urllib.request

    from ai_workflow_engine.observation_values import canonical_json_bytes
    from ai_workflow_viewer import serve_viewer

    definition = WorkflowBuilder("served-detail").step("inspect").build()
    value = {"payload": "HEAD-SENTINEL" + ("x" * 100_000) + "TAIL-SENTINEL"}
    detail = _detail(
        detail_id="served-large-detail",
        event_id="served-detail-event",
        invocation_id="served-invocation",
        kind="tool_result",
        content_type="application/json",
        value=value,
        run_id="served-detail-run",
        sequence=3,
    )
    run_path = _write_bundle(
        tmp_path,
        "served-detail-run",
        definition,
        trace_events=[
            WorkflowTraceEvent(
                node="inspect",
                event_id="served-request-event",
                phase="llm:request",
                invocation_id="served-invocation",
                detail_capture="capture_mode_off",
                run_id="served-detail-run",
                sequence=1,
            ),
            WorkflowTraceEvent(
                node="inspect",
                event_id="served-detail-event",
                phase="llm:response",
                invocation_id="served-invocation",
                detail_capture="captured",
                detail_refs=["served-large-detail"],
                run_id="served-detail-run",
                sequence=2,
            )
        ],
        usage_events=[
            WorkflowUsageEvent(
                node="inspect",
                invocation_id="served-invocation",
                run_id="served-detail-run",
                sequence=4,
            )
        ],
        details=[detail],
    )
    persisted = ObservationReader(run_path).get_detail("served-large-detail")
    expected = canonical_json_bytes(value)
    assert persisted.body.kind == "body_ref"
    assert len(expected) == persisted.body.byte_length

    server = serve_viewer(JsonlObservationViewer(FileEventSource(tmp_path)), port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        root = f"http://127.0.0.1:{server.server_address[1]}"
        route = (
            f"{root}/detail/served-detail-run/{run_path.name}/served-large-detail"
            "?invocation_id=served-invocation"
        )
        with urllib.request.urlopen(f"{route}&mode=preview", timeout=5) as response:
            preview = response.read()
            assert response.status == 200
            assert response.headers["Content-Type"] == "text/plain; charset=utf-8"
            assert response.headers["X-Content-Type-Options"] == "nosniff"
            assert response.headers["X-Observation-SHA256"] == persisted.body.sha256
            assert response.headers["X-Observation-Byte-Length"] == str(len(expected))
            assert response.headers["X-Observation-Truncated"] == "true"
        assert preview == expected[: 64 * 1024]
        assert b"HEAD-SENTINEL" in preview
        assert b"TAIL-SENTINEL" not in preview

        with urllib.request.urlopen(f"{route}&mode=download", timeout=5) as response:
            downloaded = response.read()
            assert response.status == 200
            assert response.headers["Content-Type"] == "application/octet-stream"
            assert response.headers["Content-Disposition"].startswith("attachment;")
            assert response.headers["Content-Security-Policy"] == "sandbox"
        assert downloaded == expected
        assert hashlib.sha256(downloaded).hexdigest() == persisted.body.sha256
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_detail_delivery_rejects_sources_without_the_segment_reader():
    from ai_workflow_viewer.detail_delivery import prepare_detail_delivery

    with pytest.raises(ValueError, match="does not support bounded detail delivery"):
        prepare_detail_delivery(
            object(),
            run_id="run",
            segment_id="segment",
            detail_id="detail",
            invocation_id=None,
            mode="preview",
        )


def test_served_detail_endpoint_rejects_binding_paths_and_corruption_without_leak(tmp_path):
    import threading
    import urllib.error
    import urllib.request

    from ai_workflow_viewer import serve_viewer

    definition = WorkflowBuilder("served-detail-attacks").step("inspect").build()
    secret = "BODY-SECRET-NEVER-IN-ERROR"
    detail = _detail(
        detail_id="attack-detail",
        event_id="attack-event",
        invocation_id="correct-invocation",
        kind="llm_response",
        content_type="application/json",
        value={"payload": secret + ("z" * 100_000)},
        run_id="attack-run",
        sequence=3,
    )
    run_path = _write_bundle(
        tmp_path,
        "attack-run",
        definition,
        trace_events=[
            WorkflowTraceEvent(
                node="inspect",
                event_id="attack-request-event",
                phase="llm:request",
                invocation_id="correct-invocation",
                detail_capture="capture_mode_off",
                run_id="attack-run",
                sequence=1,
            ),
            WorkflowTraceEvent(
                node="inspect",
                event_id="attack-event",
                phase="llm:response",
                invocation_id="correct-invocation",
                detail_capture="captured",
                detail_refs=["attack-detail"],
                run_id="attack-run",
                sequence=2,
            ),
        ],
        usage_events=[
            WorkflowUsageEvent(
                node="inspect",
                invocation_id="correct-invocation",
                run_id="attack-run",
                sequence=4,
            )
        ],
        details=[detail],
    )
    reader = ObservationReader(run_path)
    persisted = reader.get_detail("attack-detail")
    assert persisted.body.kind == "body_ref"

    server = serve_viewer(JsonlObservationViewer(FileEventSource(tmp_path)), port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        root = f"http://127.0.0.1:{server.server_address[1]}"
        attacks = [
            (
                "/detail/attack-run/"
                f"{run_path.name}/attack-detail?invocation_id=wrong&mode=download",
                500,
            ),
            (
                "/detail/attack-run/%2E%2E/attack-detail"
                "?invocation_id=correct-invocation&mode=download",
                500,
            ),
            (
                "/detail/attack-run/"
                f"{run_path.name}/missing-detail?invocation_id=correct-invocation&mode=download",
                404,
            ),
        ]
        for path, expected_status in attacks:
            with pytest.raises(urllib.error.HTTPError) as caught:
                urllib.request.urlopen(root + path, timeout=5)
            assert caught.value.code == expected_status
            assert secret.encode() not in caught.value.read()

        object_path = reader.value_store.object_path(persisted.body.sha256)
        damaged = bytearray(object_path.read_bytes())
        damaged[-1] ^= 0x01
        object_path.write_bytes(damaged)

        corrupt_route = (
            f"{root}/detail/attack-run/{run_path.name}/attack-detail"
            "?invocation_id=correct-invocation&mode=download"
        )
        with pytest.raises(urllib.error.HTTPError) as caught:
            urllib.request.urlopen(corrupt_route, timeout=5)
        assert caught.value.code == 500
        assert secret.encode() not in caught.value.read()

        page = urllib.request.urlopen(f"{root}/?run_id=attack-run", timeout=5).read()
        assert b"attack-detail" in page
        assert secret.encode() not in page
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_v4_viewer_preview_memory_does_not_scale_with_referenced_body_bytes(tmp_path):
    import os
    import subprocess
    import sys

    fixture_script = r'''
import sys
from pathlib import Path
from ai_workflow_engine import (
    ObservationDetail, ObservationJsonBody, WorkflowBuilder, WorkflowTraceEvent,
    WorkflowUsageEvent,
    open_observation_run_bundle,
)
from ai_workflow_engine.observation_values import body_sha256

root, run_id, size = Path(sys.argv[1]), sys.argv[2], int(sys.argv[3])
bundle = open_observation_run_bundle(root, run_id)
body = ObservationJsonBody(value={"payload": "RSS-HEAD" + ("x" * size) + "RSS-TAIL"})
detail = ObservationDetail(
    detail_id="rss-detail", event_id="rss-event", invocation_id="rss-invocation",
    kind="llm_response", content_type="application/json", body=body,
    digest=body_sha256(body),
)
bundle.trace_sink.record(WorkflowTraceEvent(
    event_id="rss-request", node="inspect", phase="llm:request",
    invocation_id="rss-invocation", detail_capture="capture_mode_off",
))
bundle.trace_sink.record(WorkflowTraceEvent(
    event_id="rss-event", node="inspect", phase="llm:response",
    invocation_id="rss-invocation", detail_capture="captured",
    detail_refs=["rss-detail"],
))
bundle.detail_sink.record(detail)
bundle.usage_sink.record(WorkflowUsageEvent(
    event_id="rss-usage", node="inspect", invocation_id="rss-invocation",
    provider="rss-test", operation="chat", success=True,
))
bundle.finalize(WorkflowBuilder("rss-viewer").step("inspect").build(), status="completed")
print(bundle.path.name)
'''
    measure_script = r'''
import json
import platform
import resource
import sys
from pathlib import Path
from ai_workflow_viewer import FileEventSource, observation_group_to_html
from ai_workflow_viewer.detail_delivery import prepare_detail_delivery

root, run_id, segment_id = Path(sys.argv[1]), sys.argv[2], sys.argv[3]
before = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
source = FileEventSource(root)
group = source.read_group(run_id)
page = observation_group_to_html(group)
assert "rss-detail" in page and "RSS-HEAD" not in page and "RSS-TAIL" not in page
delivery = prepare_detail_delivery(
    source, run_id=run_id, segment_id=segment_id, detail_id="rss-detail",
    invocation_id="rss-invocation", mode="preview",
)
preview = b"".join(delivery.chunks)
assert len(preview) == 64 * 1024 and b"RSS-HEAD" in preview and b"RSS-TAIL" not in preview
after = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
scale = 1 if platform.system() != "Darwin" else 1024
print(json.dumps({"rss_growth_kib": max(0, after - before) // scale,
                  "page_bytes": len(page.encode("utf-8")),
                  "preview_bytes": len(preview)}))
'''
    child_env = {
        key: value for key, value in os.environ.items() if not key.startswith("COVERAGE")
    }
    child_env["PYTHONHASHSEED"] = "0"

    def measured(size: int, run_id: str) -> dict[str, int]:
        root = tmp_path / run_id
        created = subprocess.run(
            [sys.executable, "-c", fixture_script, str(root), run_id, str(size)],
            check=True,
            capture_output=True,
            text=True,
            env=child_env,
        )
        segment_id = created.stdout.strip().splitlines()[-1]
        completed = subprocess.run(
            [sys.executable, "-c", measure_script, str(root), run_id, segment_id],
            check=True,
            capture_output=True,
            text=True,
            env=child_env,
        )
        return json.loads(completed.stdout.strip().splitlines()[-1])

    small = measured(2 * 1024 * 1024, "rss-viewer-small")
    large = measured(32 * 1024 * 1024, "rss-viewer-large")
    assert small["preview_bytes"] == large["preview_bytes"] == 64 * 1024
    assert large["page_bytes"] <= small["page_bytes"] + 4 * 1024
    assert large["rss_growth_kib"] <= small["rss_growth_kib"] + 12 * 1024, (
        "viewer RSS grew with referenced body bytes: "
        f"small={small['rss_growth_kib']} KiB, large={large['rss_growth_kib']} KiB"
    )


def test_viewer_rejects_meta_record_count_drift(tmp_path):
    definition = WorkflowBuilder("count-drift").step("inspect").build()
    run_path = _write_bundle(tmp_path, "count-run", definition)
    meta = json.loads((run_path / "meta.json").read_text(encoding="utf-8"))
    meta["trace_count"] = 1
    (run_path / "meta.json").write_text(json.dumps(meta), encoding="utf-8")

    with pytest.raises(ValueError, match="trace_count disagrees.*expected 1, read 0"):
        FileEventSource(run_path).read()


def test_jsonl_observation_viewer_lists_runs_for_multi_run_bundle_sources(tmp_path):
    definition = WorkflowBuilder("viewer").step("plan").build()
    run_1_event = WorkflowTraceEvent(
        node="plan",
        decision="accepted",
        detail_refs=["detail-1"],
        run_id="run-1",
        sequence=1,
    )
    run_2_event = WorkflowTraceEvent(
        node="plan",
        decision="accepted",
        detail_refs=["detail-2"],
        run_id="run-2",
        sequence=1,
    )
    _write_bundle(
        tmp_path,
        "run-1",
        definition,
        trace_events=[run_1_event],
        details=[
            _detail(
                detail_id="detail-1",
                event_id=run_1_event.event_id,
                run_id="run-1",
                kind="rendered_prompt",
                value={"source": "run-1"},
                sequence=2,
            ),
        ],
    )
    _write_bundle(
        tmp_path,
        "run-2",
        definition,
        trace_events=[run_2_event],
        details=[
            _detail(
                detail_id="detail-2",
                event_id=run_2_event.event_id,
                run_id="run-2",
                kind="rendered_prompt",
                value={"source": "run-2"},
                sequence=2,
            ),
        ],
    )

    viewer = JsonlObservationViewer.from_run_bundle(tmp_path)
    index = viewer.html()
    assert "Workflow observations" in index
    assert "run-1" in index
    assert "run-2" in index
    assert "?run_id=run-2" in index

    selected = JsonlObservationViewer.from_run_bundle(tmp_path, run_id="run-2")
    html = selected.html()

    assert "&quot;source&quot;: &quot;run-2&quot;" in html
    assert "&quot;source&quot;: &quot;run-1&quot;" not in html
    assert [record["record"]["run_id"] for record in selected.event_records()] == ["run-2", "run-2"]


# ---------------------------------------------------------------------------
# W4.3/W4.4/W4.5 — grouped logical-run reading, honest merge, truthful render
# ---------------------------------------------------------------------------


def _segment_meta(run_id, segment_id, index, *, kind=None, digest="dig-1", status="completed", timestamp=None, attempt=None):
    # v2 forbids unknown keys — parent_segment_id left the contract with R1 (lineage is
    # logical), so the helper emits only current segment identity.
    return {
        "run_id": run_id,
        "segment_id": segment_id,
        "segment_index": index,
        "segment_kind": kind or ("initial" if index == 0 else "resume"),
        "definition_digest": digest,
        "usage_totals_scope": "run_cumulative_at_finalize",
        "status": status,
        "timestamp": timestamp or f"2026-07-11T10:0{index}:00Z",
        "total_tokens": 0,
        "metered_usd": None,
        "notional_usd": None,
        "usage_count": 0,
        "attempt": attempt,
    }


def _mark(run_path, marker):
    import json as _json

    (run_path / marker).write_text(_json.dumps({}), encoding="utf-8")


def _segment_path(base: Path, run_id: str, segment_id: str | None = None) -> Path:
    return base / run_id / "segments" / (segment_id or run_id)


def _write_group(tmp_path, *, duplicate_usage_id=False, second_digest="dig-1"):
    """Suspension segment + resume segment of one logical run, per-segment sequences
    RESTARTING at 1 (the exact shape that broke the old global sequence check)."""

    from ai_workflow_engine import WorkflowBuilder

    definition = WorkflowBuilder("grouped").step("gate").step("finish").build()
    real_digest = definition.definition_digest()
    usage_0 = WorkflowUsageEvent(
        node="gate", total_tokens=7, estimated_usd=0.01, cost_class="metered",
        run_id="logical-run", sequence=3, event_id="usage-a",
    )
    _write_bundle(
        tmp_path,
        "logical-run",
        definition,
        trace_events=[
            WorkflowTraceEvent(node="gate", decision="llm:prompt", run_id="logical-run", sequence=1, event_id="t-1"),
            WorkflowTraceEvent(node="gate", node_status="requires_user_input", phase="node:result", run_id="logical-run", sequence=2, event_id="t-2"),
        ],
        usage_events=[usage_0],
        meta_extra=_segment_meta(
            "logical-run", "logical-run", 0, status="requires_user_input", digest=real_digest
        ) | {"total_tokens": 7, "metered_usd": 0.01, "usage_count": 1},
    )
    resume_usage = [
        WorkflowUsageEvent(
            node="finish", total_tokens=5, estimated_usd=0.02, cost_class="metered",
            run_id="logical-run", sequence=2, event_id="usage-b",
        )
    ]
    if duplicate_usage_id:
        resume_usage.append(
            WorkflowUsageEvent(
                node="finish", total_tokens=7, estimated_usd=0.01, cost_class="metered",
                run_id="logical-run", sequence=3, event_id="usage-a",  # copied from segment 0
            )
        )
    _write_bundle(
        tmp_path,
        "logical-run",
        definition,
        dir_name="logical-run--s001",
        trace_events=[
            WorkflowTraceEvent(node="gate", node_status="completed", phase="node:result", run_id="logical-run", sequence=1, event_id="t-3"),
            WorkflowTraceEvent(node="finish", node_status="completed", phase="node:result", run_id="logical-run", sequence=4, event_id="t-4"),
        ],
        usage_events=resume_usage,
        meta_extra=_segment_meta(
            "logical-run", "logical-run--s001", 1,
            digest=(real_digest if second_digest == "dig-1" else second_digest),
            status="completed",
        ) | {
            "total_tokens": 12,
            "metered_usd": 0.03,
            "usage_count": len(resume_usage),
        },
    )
    return definition


def test_read_group_merges_segments_ordered_with_per_segment_sequences(tmp_path):
    """W4.3: merged order is (segment_index, per-segment sequence); sequences restarting
    per segment are VALID (the old global duplicate-sequence check would have exploded);
    event ids stay globally unique; single-bundle read() semantics are untouched."""

    from ai_workflow_viewer import FileEventSource

    _write_group(tmp_path)
    source = FileEventSource(tmp_path)
    group = source.read_group("logical-run")

    assert group.run_id == "logical-run"
    assert [segment.segment_index for segment in group.segments] == [0, 1]
    assert group.status == "completed"
    assert [record.event_id for record in group.records] == ["t-1", "t-2", "usage-a", "t-3", "usage-b", "t-4"], (
        "merge must order by (segment_index, per-segment sequence)"
    )
    # duplicate sequence NUMBERS across segments are legal — each segment restarts at 1
    assert {record.sequence for record in group.records if record.type == "trace"} == {1, 2, 4}
    # single-bundle semantics unchanged: reading one segment alone still works
    single = FileEventSource(_segment_path(tmp_path, "logical-run", "logical-run--s001")).read()
    assert single.run_id == "logical-run" and len(single.records) == 3


def test_damaged_group_segment_never_falls_back_to_a_partial_page(tmp_path):
    import threading
    import urllib.error
    import urllib.request

    import pytest as _pytest

    from ai_workflow_viewer import FileEventSource, JsonlObservationViewer, serve_viewer

    _write_group(tmp_path)
    (_segment_path(tmp_path, "logical-run", "logical-run--s001") / "definition.json").unlink()
    viewer = JsonlObservationViewer(FileEventSource(tmp_path))

    with _pytest.raises(ValueError, match="corrupt segment.*missing definition.json"):
        viewer.html("logical-run")

    server = serve_viewer(viewer, port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        root = f"http://127.0.0.1:{server.server_address[1]}"
        with _pytest.raises(urllib.error.HTTPError) as caught:
            urllib.request.urlopen(f"{root}/?run_id=logical-run", timeout=5)
        assert caught.value.code == 500
        body = caught.value.read().decode("utf-8")
        assert "corrupt segment" in body and "missing definition.json" in body
        assert "<html" not in body.lower()

        with _pytest.raises(urllib.error.HTTPError) as missing:
            urllib.request.urlopen(f"{root}/?run_id=unknown-run", timeout=5)
        assert missing.value.code == 404
    finally:
        server.shutdown()
        thread.join(timeout=5)


def test_read_group_aggregates_usage_once_and_labels_cumulative(tmp_path):
    """W4.4: group spend = segment-local events counted ONCE (7 + 5 tokens), while the
    engine's cumulative-at-finalize meta (12 on the resumed segment) is reported under its
    own label — summing metas would double-charge the pre-suspension half."""

    from ai_workflow_viewer import FileEventSource

    _write_group(tmp_path)
    group = FileEventSource(tmp_path).read_group("logical-run")

    assert group.usage_totals["total_tokens"] == 12  # 7 + 5, each event exactly once
    assert group.usage_totals["usage_count"] == 2
    assert group.usage_totals["metered_usd"] == 0.03
    assert group.usage_totals["scope"] == "actual_all_attempts"
    assert group.canonical_usage_totals["total_tokens"] == 12, (
        "with no non-canonical attempts, actual == canonical"
    )
    assert group.non_canonical_usage_totals["usage_count"] == 0
    assert group.cumulative_meta_totals["scope"] == "run_cumulative_at_finalize"
    assert group.cumulative_meta_totals["total_tokens"] == 12, (
        "cumulative label comes from the NEWEST segment's meta, never a sum of metas"
    )
    meta_sum = sum(segment.data.meta.total_tokens for segment in group.segments)
    assert meta_sum == 19 and group.usage_totals["total_tokens"] != meta_sum, (
        "the naive per-segment meta sum (19) double-charges — the reader must not use it"
    )


def test_read_group_duplicate_event_id_across_segments_is_loud(tmp_path):
    """W4.4: the same usage event id appearing in two segments is double-counted money —
    the group reader refuses instead of silently merging."""

    import pytest as _pytest

    from ai_workflow_viewer import FileEventSource

    _write_group(tmp_path, duplicate_usage_id=True)
    with _pytest.raises(ValueError, match="Duplicate observation event id across segments"):
        FileEventSource(tmp_path).read_group("logical-run")


def test_read_group_uses_highest_committed_durable_attempt_as_canonical(tmp_path):
    """At-least-once delivery may leave several committed durable attempts at one
    logical index; the highest attempt ordinal is the canonical machine history."""

    from ai_workflow_viewer import FileEventSource

    definition = _write_group(tmp_path)
    first_path = _segment_path(tmp_path, "logical-run", "logical-run--s001")
    first_meta_path = first_path / "meta.json"
    first_meta = json.loads(first_meta_path.read_text(encoding="utf-8"))
    first_meta["attempt"] = 1
    first_meta_path.write_text(json.dumps(first_meta), encoding="utf-8")
    _mark(first_path, "commit.json")

    second_id = "logical-run--s001-r2"
    second_path = _write_bundle(
        tmp_path,
        "logical-run",
        definition,
        dir_name=second_id,
        trace_events=[
            WorkflowTraceEvent(
                node="finish",
                node_status="completed",
                phase="node:result",
                decision="second-attempt-evidence",
                run_id="logical-run",
                sequence=1,
                event_id="second-attempt-event",
            )
        ],
        meta_extra=_segment_meta(
            "logical-run",
            second_id,
            1,
            digest=definition.definition_digest(),
            attempt=2,
        ),
    )
    _mark(second_path, "commit.json")

    group = FileEventSource(tmp_path).read_group("logical-run")

    assert [segment.segment_id for segment in group.segments] == ["logical-run", second_id]
    assert [segment.segment_id for segment in group.non_canonical] == [
        "logical-run--s001"
    ]
    assert [record.event_id for record in group.records if record.type == "trace"][-1] == (
        "second-attempt-event"
    )


def test_read_group_lineage_corruption_is_loud(tmp_path):
    """W4.3/R1: committed-ordinal collision, a gapped canonical chain, and definition
    splits each refuse loudly — no half-true merge. Physical parent pointers left the
    contract; the LOGICAL chain (contiguous committed indexes) is what integrity means."""

    import pytest as _pytest

    from ai_workflow_engine import WorkflowBuilder
    from ai_workflow_viewer import FileEventSource

    definition = WorkflowBuilder("grouped").step("gate").build()

    grouped_definition = WorkflowBuilder("grouped").step("gate").step("finish").build()

    # committed-ordinal collision: two COMMITTED durable attempts share (index, attempt) —
    # impossible under single-claimant CAS, therefore corruption
    _write_group(tmp_path)
    for name in ("logical-run--s001", "logical-run--s001-r2x"):
        _write_bundle(
            tmp_path, "logical-run", grouped_definition, dir_name=name,
            meta_extra=_segment_meta(
                "logical-run", name, 1, digest=grouped_definition.definition_digest(),
                attempt=1,
            ),
        ) if name != "logical-run--s001" else None
    # give BOTH index-1 attempts the same ordinal + commit markers
    import json as _json

    s001_meta = _segment_path(tmp_path, "logical-run", "logical-run--s001") / "meta.json"
    meta = _json.loads(s001_meta.read_text(encoding="utf-8"))
    meta["attempt"] = 1
    s001_meta.write_text(_json.dumps(meta), encoding="utf-8")
    _mark(_segment_path(tmp_path, "logical-run", "logical-run--s001"), "commit.json")
    _mark(_segment_path(tmp_path, "logical-run", "logical-run--s001-r2x"), "commit.json")
    with _pytest.raises(ValueError, match="share ordinal"):
        FileEventSource(tmp_path).read_group("logical-run")

    # broken chain: the suspension half was deleted by hand -> not contiguous
    import shutil

    shutil.rmtree(_segment_path(tmp_path, "logical-run", "logical-run--s001-r2x"))
    shutil.rmtree(_segment_path(tmp_path, "logical-run"))
    with _pytest.raises(ValueError, match="contiguous|chain"):
        FileEventSource(tmp_path).read_group("logical-run")

    # forged/stale meta digest: the file recompute is authoritative (W4R.2)
    for path in tmp_path.iterdir():
        shutil.rmtree(path)
    _write_group(tmp_path, second_digest="dig-FORGED")
    with _pytest.raises(ValueError, match="claims definition digest.*recomputes"):
        FileEventSource(tmp_path).read_group("logical-run")

    # ACTUAL definition split: correct metas, but segment 1 executes a different machine
    for path in tmp_path.iterdir():
        shutil.rmtree(path)
    other_definition = WorkflowBuilder("grouped").step("gate").step("finish").step("extra").build()
    _write_bundle(
        tmp_path, "split-run", definition,
        meta_extra=_segment_meta(
            "split-run", "split-run", 0, digest=definition.definition_digest()
        ),
    )
    _write_bundle(
        tmp_path, "split-run", other_definition, dir_name="split-run--s001",
        meta_extra=_segment_meta(
            "split-run", "split-run--s001", 1,
            digest=other_definition.definition_digest(),
        ),
    )
    with _pytest.raises(ValueError, match="DIFFERENT actual workflow definitions"):
        FileEventSource(tmp_path).read_group("split-run")

    # gapped chain: canonical history at indexes 0 and 2 with nothing committed at 1 —
    # execution cannot skip a logical position, so the history is incomplete/corrupt
    for path in tmp_path.iterdir():
        shutil.rmtree(path)
    digest = definition.definition_digest()
    _write_bundle(
        tmp_path, "skip-run", definition,
        meta_extra=_segment_meta("skip-run", "skip-run", 0, digest=digest),
    )
    _write_bundle(
        tmp_path, "skip-run", definition, dir_name="skip-run--s002",
        meta_extra=_segment_meta("skip-run", "skip-run--s002", 2, digest=digest),
    )
    with _pytest.raises(ValueError, match="contiguous"):
        FileEventSource(tmp_path).read_group("skip-run")

    # wrong kind: a continuation claiming to be 'initial'
    for path in tmp_path.iterdir():
        shutil.rmtree(path)
    _write_bundle(
        tmp_path, "kind-run", definition,
        meta_extra=_segment_meta("kind-run", "kind-run", 0, digest=digest),
    )
    _write_bundle(
        tmp_path, "kind-run", definition, dir_name="kind-run--s001",
        meta_extra=_segment_meta(
            "kind-run", "kind-run--s001", 1, digest=digest, kind="initial"
        ),
    )
    with _pytest.raises(ValueError, match="invalid observation-bundle meta|illegal kind|starts with exactly one"):
        FileEventSource(tmp_path).read_group("kind-run")


def test_pre_v4_bundle_is_loud_directly_but_isolated_from_healthy_groups(tmp_path):
    """The latest-only viewer rejects v3 directly while keeping unrelated v4 history usable."""

    import json as _json
    import shutil as _shutil
    import urllib.error
    import urllib.request

    import pytest as _pytest

    from ai_workflow_viewer import FileEventSource, serve_viewer

    from ai_workflow_engine import WorkflowBuilder

    definition = WorkflowBuilder("legacy").step("gate").build()
    _write_group(tmp_path)  # current-contract group beside the relic
    # A v2 relic is a different closed wire contract, not a partially-readable v3 bundle.
    relic = _write_bundle(
        tmp_path,
        "old-run",
        definition,
        trace_events=[
            WorkflowTraceEvent(
                node="gate",
                node_status="completed",
                phase="node:result",
                run_id="old-run",
                sequence=1,
                event_id="o-1",
            )
        ],
    )
    old_meta = _json.loads((relic / "meta.json").read_text(encoding="utf-8"))
    old_meta["bundle_schema_version"] = 3
    old_meta.pop("provider_evidence")
    (relic / "meta.json").write_text(_json.dumps(old_meta), encoding="utf-8")

    with _pytest.raises(ValueError, match="no v3 reader"):
        FileEventSource(relic).read()
    source = FileEventSource(tmp_path)
    assert source.read_group("logical-run").status == "completed"
    groups = {row["run_id"]: row for row in source.list_groups()}
    assert groups["logical-run"]["status"] == "completed"
    assert groups["old-run"]["status"] == "corrupt"
    assert "corrupt" in JsonlObservationViewer(source).html()
    with _pytest.raises(ValueError, match="corrupt segment.*unsupported"):
        source.read_group("old-run")

    # the served door answers with the loud message — never a plausible page
    server = serve_viewer(JsonlObservationViewer(source), port=0)
    import threading

    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        root = f"http://127.0.0.1:{server.server_address[1]}"
        with _pytest.raises(urllib.error.HTTPError) as caught:
            urllib.request.urlopen(f"{root}/?run_id=old-run", timeout=5)
        assert caught.value.code == 500
        body = caught.value.read().decode("utf-8")
        assert "no v3 reader" in body and "<html" not in body.lower()
    finally:
        server.shutdown()
        thread.join(timeout=5)

    # Malformed v3: an unknown key is rejected directly and listed as corruption.
    _shutil.rmtree(relic)
    meta_path = _segment_path(tmp_path, "logical-run") / "meta.json"
    meta = _json.loads(meta_path.read_text(encoding="utf-8"))
    meta["parent_segment_id"] = "ghost"
    meta_path.write_text(_json.dumps(meta), encoding="utf-8")
    with _pytest.raises(ValueError, match="parent_segment_id"):
        source.read_group("logical-run")
    assert {
        row["run_id"]: row["status"] for row in source.list_groups()
    }["logical-run"] == "corrupt"


def test_group_html_renders_one_truthful_lifecycle(tmp_path):
    """W4.5 (Q-R5 reproducer): the node that suspended in segment 0 and completed in
    segment 1 renders COMPLETED — never a false still-running/suspended state — and the
    segment strip + honest usage line are present with semantic markup."""

    from ai_workflow_viewer import FileEventSource, observation_group_to_html

    _write_group(tmp_path)
    group = FileEventSource(tmp_path).read_group("logical-run")
    html = observation_group_to_html(group)

    assert 'data-segment-index="0"' in html and 'data-segment-index="1"' in html
    assert 'data-kind="resume"' in html
    assert "Run segments (2)" in html
    assert "logical-run" in html
    assert "actual spend (all attempts, counted once)" in html
    assert "12 tokens" in html
    # the gate node must show its FINAL truth in the node table: completed, not suspended
    import re as _re

    gate_row = _re.search(r"<tr[^>]*>\s*<td><code>gate</code></td>.*?</tr>", html, _re.S)
    assert gate_row and "completed" in gate_row.group(0), (
        "suspended-then-completed node must render completed in the grouped view"
    )
    assert "suspended" not in (gate_row.group(0)), gate_row.group(0)


def test_single_resumed_segment_read_is_no_longer_empty(tmp_path):
    """Q-R5 regression (the original defect): reading the RESUMED segment's bundle alone
    now yields records attributable to the logical run id — before W4 the meta carried a
    '--resume-<uuid>' id while events carried the logical id, so the projection filtered
    everything out and rendered an empty run."""

    from ai_workflow_viewer import FileEventSource, build_observation_graph

    _write_group(tmp_path)
    data = FileEventSource(_segment_path(tmp_path, "logical-run", "logical-run--s001")).read()
    assert data.run_id == "logical-run"
    graph = build_observation_graph(
        data.definition, data.trace_events, data.usage_events, data.details, run_id=data.run_id
    )
    assert graph.timeline, "resumed-segment projection must not be empty"
    assert graph.nodes["finish"].status == "completed"


def test_read_group_reports_abandoned_attempts_without_merging_them(tmp_path):
    """W4R.1 reader side: abandoned crash-attempts appear as typed evidence on the group
    (with their partial spend included in actual economics), but never enter canonical
    records, group status, or the index chain — and their duplicate index vs the successful
    retry is NOT a lineage error."""

    from ai_workflow_engine import WorkflowBuilder
    from ai_workflow_viewer import FileEventSource

    definition = WorkflowBuilder("grouped").step("gate").step("finish").build()
    digest = definition.definition_digest()
    _write_group(tmp_path)
    _write_bundle(
        tmp_path, "logical-run", definition, dir_name="logical-run--s001-dead",
        trace_events=[
            WorkflowTraceEvent(node="finish", decision="start", run_id="logical-run", sequence=1, event_id="dead-t1"),
        ],
        usage_events=[
            WorkflowUsageEvent(
                node="finish", total_tokens=3, estimated_usd=0.005,
                cost_class="metered", run_id="logical-run", sequence=2,
                event_id="dead-u1",
            ),
            WorkflowUsageEvent(
                node="finish", total_tokens=2, notional_usd=0.007,
                cost_class="subscription_notional", run_id="logical-run", sequence=3,
                event_id="dead-u2",
                provider_reported_notional_usd=0.007,
                notional_pricing=NotionalPricingResult(
                    source="provider_reported",
                    amount_usd=0.007,
                    catalog_version="provider-reported",
                ),
            ),
            WorkflowUsageEvent(
                node="finish", total_tokens=1, cost_class="metered",
                run_id="logical-run", sequence=4, event_id="dead-u3",
            ),
        ],
        meta_extra=_segment_meta(
            "logical-run", "logical-run--s001-dead", 1, digest=digest,
            )
            | {
                "status": "abandoned",
                "usage_count": 3,
                "total_tokens": 6,
                "metered_usd": 0.005,
                "notional_usd": 0.007,
            },
        )

    group = FileEventSource(tmp_path).read_group("logical-run")
    assert [segment.segment_index for segment in group.segments] == [0, 1]
    assert group.status == "completed"
    assert [segment.segment_id for segment in group.non_canonical] == ["logical-run--s001-dead"]
    assert group.non_canonical[0].disposition == "abandoned"
    # W4R.3 cost honesty: the crashed attempt's paid call is REAL spend — the primary
    # actual total includes it; the canonical/non-canonical split never hides money.
    assert group.usage_totals["total_tokens"] == 18, "actual spend includes abandoned work"
    assert group.usage_totals["metered_usd"] == 0.035
    assert group.usage_totals["notional_usd"] == 0.007
    assert group.usage_totals["unknown_cost_count"] == 1
    assert group.canonical_usage_totals["total_tokens"] == 12
    assert group.non_canonical_usage_totals["total_tokens"] == 6
    assert group.non_canonical_usage_totals["notional_usd"] == 0.007
    assert group.non_canonical_usage_totals["unknown_cost_count"] == 1
    assert group.usage_totals["total_tokens"] == (
        group.canonical_usage_totals["total_tokens"]
        + group.non_canonical_usage_totals["total_tokens"]
    ), "actual = canonical + non-canonical, nothing double-counted"
    assert all(record.event_id != "dead-t1" for record in group.records), (
        "non-canonical machine EVENTS still never merge into canonical history"
    )
    assert group.non_canonical[0].data.usage_events[0].estimated_usd == 0.005
    from ai_workflow_viewer import observation_group_to_html

    rendered = observation_group_to_html(group)
    assert "actual spend (all attempts, counted once)" in rendered
    assert "notional $0.007" in rendered
    assert "unknown-cost events 1" in rendered
    assert "non-canonical attempts: 6 tokens" in rendered


def test_wait_terminal_segment_closes_the_group_and_fake_definitions_are_loud(tmp_path):
    """W3R.3b reader side: a `wait_terminal` evidence segment (suspension failed with no
    continuation) is a legal FINAL chain member — the group reads `failed`, so viewers and
    retention agree with coordinator truth. Its definition.json is the REGISTERED bytes
    and is recomputed like any segment: a wait_terminal carrying a DIFFERENT machine is
    refused (the old exemption is gone)."""

    import pytest as _pytest

    from ai_workflow_engine import WorkflowBuilder
    from ai_workflow_viewer import FileEventSource

    definition = WorkflowBuilder("grouped").step("gate").step("finish").build()
    digest = definition.definition_digest()
    _write_bundle(
        tmp_path, "wfail-run", definition,
        trace_events=[WorkflowTraceEvent(node="gate", node_status="requires_user_input", phase="node:result", run_id="wfail-run", sequence=1, event_id="w-0")],
        meta_extra=_segment_meta("wfail-run", "wfail-run", 0, digest=digest, status="requires_user_input"),
    )
    _write_bundle(
        tmp_path, "wfail-run", definition, dir_name="wfail-run--s001-wfail",
        trace_events=[WorkflowTraceEvent(node="gate", decision="wait:failed", node_status="failed", phase="node:result", error="digest mismatch", run_id="wfail-run", sequence=1, event_id="w-1")],
        meta_extra=_segment_meta(
            "wfail-run", "wfail-run--s001-wfail", 1,
            digest=digest, kind="wait_terminal", status="failed",
        ),
    )

    group = FileEventSource(tmp_path).read_group("wfail-run")
    assert [segment.kind for segment in group.segments] == ["initial", "wait_terminal"]
    assert group.status == "failed", (
        "a terminally failed wait must close the group — no suspended lie for retention"
    )

    # forge: same meta, but the evidence file carries a DIFFERENT machine
    other = WorkflowBuilder("grouped").step("gate").step("finish").step("extra").build()
    (_segment_path(tmp_path, "wfail-run", "wfail-run--s001-wfail") / "definition.json").write_text(
        other.model_dump_json(), encoding="utf-8"
    )
    with _pytest.raises(ValueError, match="claims definition digest.*recomputes"):
        FileEventSource(tmp_path).read_group("wfail-run")


def test_served_viewer_renders_the_whole_logical_run(tmp_path):
    """R3/F5 (permanent): the SERVED surface (JsonlObservationViewer -> serve_viewer) uses
    the grouped API — a suspended-then-resumed run renders as ONE completed lifecycle with
    one index row per logical run; the resumed half is no longer unreachable and the gate
    no longer reads suspended forever (the original Q-R5 defect, now closed end to end)."""

    import re as _re

    from ai_workflow_viewer import FileEventSource

    _write_group(tmp_path)
    viewer = JsonlObservationViewer(FileEventSource(tmp_path), title="Grouped serve test")

    rows = viewer.runs()
    assert [row["run_id"] for row in rows] == ["logical-run"], (
        "one index row per LOGICAL run — segments must not render as duplicate rows"
    )
    assert rows[0]["status"] == "completed" and rows[0]["segment_count"] == 2

    html = viewer.html("logical-run")
    assert "Run segments (2)" in html, "the served page is the grouped render"
    gate_row = _re.search(r"<tr[^>]*>\s*<td><code>gate</code></td>.*?</tr>", html, _re.S)
    assert gate_row and "completed" in gate_row.group(0)
    assert "suspended" not in gate_row.group(0), (
        "the suspended-then-completed gate must render completed through the SERVER path"
    )

    records = viewer.event_records("logical-run")
    assert len(records) == 6, "SSE/event stream carries the merged canonical records"


def test_chooser_and_detail_agree_on_double_local_resume(tmp_path):
    """R0R3-C2 (codex probe, permanent): the run chooser (list_groups) derives its counts
    from the SAME canonical selection as the detail page (read_group) — two local resumes
    at one index are 2 canonical + 1 superseded on BOTH surfaces, never 3 + 0."""

    from ai_workflow_engine import WorkflowBuilder
    from ai_workflow_viewer import FileEventSource

    definition = WorkflowBuilder("grouped").step("gate").step("finish").build()
    digest = definition.definition_digest()
    _write_bundle(
        tmp_path, "dl-run", definition,
        trace_events=[WorkflowTraceEvent(node="gate", node_status="requires_user_input", phase="node:result", run_id="dl-run", sequence=1, event_id="d-0")],
        meta_extra=_segment_meta("dl-run", "dl-run", 0, digest=digest, status="requires_user_input"),
    )
    for suffix, ts, event in (("aaa", "2026-07-12T10:01:00Z", "d-1"), ("bbb", "2026-07-12T10:02:00Z", "d-2")):
        _write_bundle(
            tmp_path, "dl-run", definition, dir_name=f"dl-run--s001-{suffix}",
            trace_events=[WorkflowTraceEvent(node="finish", node_status="completed", phase="node:result", run_id="dl-run", sequence=1, event_id=event)],
            meta_extra=_segment_meta(
                "dl-run", f"dl-run--s001-{suffix}", 1, digest=digest, timestamp=ts
            ),
        )

    source = FileEventSource(tmp_path)
    group = source.read_group("dl-run")
    assert len(group.segments) == 2 and len(group.non_canonical) == 1
    assert group.non_canonical[0].disposition == "superseded"
    assert group.segments[1].segment_id == "dl-run--s001-bbb", "latest committed local wins"

    rows = {row["run_id"]: row for row in source.list_groups()}
    assert rows["dl-run"]["segment_count"] == 2, (
        "the chooser must count CANONICAL segments exactly like the detail page"
    )
    assert rows["dl-run"]["non_canonical_count"] == 1


def test_related_run_id_filters_without_merging(tmp_path):
    """W5R.6: two INDEPENDENT runs sharing one Related-run ID stay two runs — the chooser
    exposes and filters by the related id (no state/history merge), one resumed run keeps
    ONE run id across its segments, and uncorrelated rows stay clear."""

    from ai_workflow_engine import WorkflowBuilder
    from ai_workflow_viewer import FileEventSource, JsonlObservationViewer, observation_group_to_html

    definition = WorkflowBuilder("cases").step("gate").build()
    digest = definition.definition_digest()
    for run_id, corr in (("case9-a", "case-9"), ("case9-b", "case-9"), ("lone-run", None)):
        extra = _segment_meta(run_id, run_id, 0, digest=digest)
        if corr:
            extra["correlation_id"] = corr
        _write_bundle(
            tmp_path, run_id, definition,
            trace_events=[WorkflowTraceEvent(node="gate", node_status="completed", phase="node:result", run_id=run_id, sequence=1, event_id=f"e-{run_id}")],
            meta_extra=extra,
        )

    source = FileEventSource(tmp_path)
    rows = {r["run_id"]: r for r in source.list_groups()}
    assert rows["case9-a"]["related_run_id"] == "case-9"
    assert rows["lone-run"]["related_run_id"] is None

    related = source.list_groups(related_run_id="case-9")
    assert sorted(r["run_id"] for r in related) == ["case9-a", "case9-b"], (
        "the filter selects the case's runs WITHOUT merging them — two rows, two run ids"
    )
    assert all(r["segment_count"] == 1 for r in related)

    html = observation_group_to_html(source.read_group("case9-a"))
    assert "Related-run ID <code>case-9</code>" in html and "Run ID <code>case9-a</code>" in html
    index = JsonlObservationViewer(source).index_html()
    assert "Related-run ID" in index and index.count("case-9") >= 2


def test_group_related_run_identity_drift_is_corruption(tmp_path):
    """W5RR.3: one run has ONE related-run identity — segments claiming different ids,
    or present-vs-absent drift between same-contract segments, refuse loudly on the
    detail read and show status=corrupt on the chooser; legacy all-absent groups stay
    valid; a consistent group exposes ONE validated value on BOTH surfaces."""

    import pytest as _pytest

    from ai_workflow_engine import WorkflowBuilder
    from ai_workflow_viewer import FileEventSource

    definition = WorkflowBuilder("grouped").step("gate").step("finish").build()
    digest = definition.definition_digest()

    def seg(run, name, idx, corr, **kw):
        extra = _segment_meta(run, name, idx, digest=digest, **kw)
        if corr is not None:
            extra["correlation_id"] = corr
        _write_bundle(tmp_path, run, definition, dir_name=name, meta_extra=extra)

    # (a) two different ids -> loud + corrupt row
    seg("drift-run", "drift-run", 0, "case-a", status="requires_user_input")
    seg("drift-run", "drift-run--s001-x", 1, "case-b")
    source = FileEventSource(tmp_path)
    with _pytest.raises(ValueError, match="DIFFERENT related-run ids"):
        source.read_group("drift-run")
    row = {r["run_id"]: r for r in source.list_groups()}["drift-run"]
    assert row["status"] == "corrupt", "the chooser must not silently pick first/newest"

    # (b) present-vs-absent drift between same-contract segments -> loud
    seg("half-corr", "half-corr", 0, "case-c", status="requires_user_input")
    seg("half-corr", "half-corr--s001-y", 1, None)
    with _pytest.raises(ValueError, match="identity drift"):
        source.read_group("half-corr")

    # (b2) an ABANDONED attempt claiming a different case is the SAME corruption —
    # its spend counts in actual economics, so its identity must match the group's
    seg("aband-drift", "aband-drift", 0, "case-d", status="requires_user_input")
    extra = _segment_meta("aband-drift", "aband-drift--s001-x", 1, digest=digest)
    extra["correlation_id"] = "case-ELSE"
    extra["status"] = "abandoned"
    _write_bundle(tmp_path, "aband-drift", definition, dir_name="aband-drift--s001-x", meta_extra=extra)
    with _pytest.raises(ValueError, match="DIFFERENT related-run ids"):
        source.read_group("aband-drift")
    assert {r["run_id"]: r for r in source.list_groups()}["aband-drift"]["status"] == "corrupt"

    # (c) consistent group: ONE validated value on detail AND chooser
    seg("good-run", "good-run", 0, "case-ok", status="requires_user_input")
    seg("good-run", "good-run--s001-z", 1, "case-ok")
    group = source.read_group("good-run")
    assert group.related_run_id == "case-ok"
    rows = {r["run_id"]: r for r in source.list_groups()}
    assert rows["good-run"]["related_run_id"] == "case-ok"


def test_served_viewer_filter_is_real_http_behavior(tmp_path):
    """W5RR.4: the SERVED chooser filters by Related-run ID — query parsed, exactly the
    case's rows rendered (still separate runs), links preserve the filter, clear-filter
    present, and the no-match state is explicit."""

    from ai_workflow_engine import WorkflowBuilder
    from ai_workflow_viewer import FileEventSource, JsonlObservationViewer

    definition = WorkflowBuilder("cases").step("gate").build()
    digest = definition.definition_digest()
    for run_id, corr in (("f-a", "case-9"), ("f-b", "case-9"), ("f-lone", None)):
        extra = _segment_meta(run_id, run_id, 0, digest=digest)
        if corr:
            extra["correlation_id"] = corr
        _write_bundle(
            tmp_path, run_id, definition,
            trace_events=[WorkflowTraceEvent(node="gate", node_status="completed", phase="node:result", run_id=run_id, sequence=1, event_id=f"e-{run_id}")],
            meta_extra=extra,
        )

    viewer = JsonlObservationViewer(FileEventSource(tmp_path), title="filter test")
    page = viewer.html(related_run_id="case-9")  # what do_GET renders for ?related_run_id=
    assert "Filtered by Related-run ID" in page and "clear filter" in page
    assert "f-a" in page and "f-b" in page and "f-lone" not in page, (
        "exactly the case's runs render — still two separate rows"
    )
    assert 'href="?run_id=f-a&related_run_id=case-9"' in page, (
        "open links preserve the active filter for back-navigation"
    )
    assert 'href="?related_run_id=case-9"' in viewer.index_html(), (
        "the Related-run ID cell is the clickable filter control"
    )
    empty = viewer.html(related_run_id="case-none")
    assert "No runs for Related-run ID" in empty and "case-none" in empty

    # OUTERMOST boundary: the real HTTP handler must parse the query itself —
    # calling viewer.html directly would leave do_GET's parsing untested.
    import threading
    import urllib.request

    from ai_workflow_viewer import serve_viewer

    server = serve_viewer(viewer, port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        host, port = server.server_address[:2]
        with urllib.request.urlopen(
            f"http://{host}:{port}/?related_run_id=case-9", timeout=5
        ) as response:
            served = response.read().decode("utf-8")
        assert "f-a" in served and "f-b" in served and "f-lone" not in served, (
            "the SERVED page must apply the query filter, not just the Python API"
        )
        assert "Filtered by Related-run ID" in served
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_group_html_resolves_artifacts_with_previews_and_skip_reasons(tmp_path):
    """Q4.2 repair: artifact IDs resolve through artifacts.json into USER-FACING evidence —
    image artifacts render a preview + clickable bundle-local link, uncopied entries show
    their honest skip reason, and a caller-supplied href base keeps links relative."""

    import json as _json

    from ai_workflow_engine import WorkflowBuilder
    from ai_workflow_viewer import FileEventSource, observation_group_to_html

    definition = WorkflowBuilder("arty").step("gate").build()
    digest = definition.definition_digest()
    _write_bundle(
        tmp_path, "art-run", definition,
        trace_events=[WorkflowTraceEvent(node="gate", node_status="completed", phase="node:result", run_id="art-run", sequence=1, event_id="a-1")],
        meta_extra=_segment_meta("art-run", "art-run", 0, digest=digest),
    )
    bundle = _segment_path(tmp_path, "art-run")
    (bundle / "artifacts").mkdir()
    (bundle / "artifacts" / "frame.png").write_bytes(b"\x89PNG fake")
    (bundle / "artifacts.json").write_text(_json.dumps([
        {"artifact_id": "art-1", "bundle_path": "artifacts/frame.png", "copied": True,
         "kind": "media", "media_type": "image/png", "owner_node": "gate",
         "size_bytes": 9, "skip_reason": None},
        {"artifact_id": "art-2", "bundle_path": None, "copied": False,
         "kind": "media", "media_type": "image/png", "owner_node": "gate",
         "size_bytes": 999, "skip_reason": "exceeds artifact_max_bytes"},
    ]), encoding="utf-8")

    group = FileEventSource(tmp_path).read_group("art-run")
    page = observation_group_to_html(group, artifact_href_for=lambda p: "../rel/art-run")
    assert '<img src="../rel/art-run/artifacts/frame.png"' in page, "image preview rendered"
    assert '<a href="../rel/art-run/artifacts/frame.png">' in page, "clickable bundle-local link"
    assert "NOT archived: exceeds artifact_max_bytes" in page, "honest skip reason"
    default_page = observation_group_to_html(group)  # absolute-path fallback stays clickable
    assert f'{bundle}/artifacts/frame.png' in default_page


def test_served_artifact_links_resolve_over_real_http(tmp_path):
    """Invariant sweep (Q4.2 sibling surface): the SERVED group page must link archived
    artifacts through a route the browser can actually fetch — /artifact/... returns the
    manifest-listed bytes with the manifest media type, while non-manifest files (meta.json)
    and traversal shapes 404 even though they exist on disk."""

    import json as _json
    import threading
    import urllib.request
    from urllib.error import HTTPError

    from ai_workflow_engine import WorkflowBuilder
    from ai_workflow_viewer import FileEventSource, JsonlObservationViewer, serve_viewer

    definition = WorkflowBuilder("served-arty").step("gate").build()
    digest = definition.definition_digest()
    _write_bundle(
        tmp_path, "served-art-run", definition,
        trace_events=[WorkflowTraceEvent(node="gate", node_status="completed", phase="node:result", run_id="served-art-run", sequence=1, event_id="sa-1")],
        meta_extra=_segment_meta("served-art-run", "served-art-run", 0, digest=digest),
    )
    bundle = _segment_path(tmp_path, "served-art-run")
    (bundle / "artifacts").mkdir()
    png = b"\x89PNG served"
    (bundle / "artifacts" / "frame.png").write_bytes(png)
    (tmp_path / "outside.secret").write_bytes(b"NEVER SERVED")
    (bundle / "artifacts.json").write_text(_json.dumps([
        {"artifact_id": "sa-art", "bundle_path": "artifacts/frame.png", "copied": True,
         "media_type": "image/png", "owner_node": "gate", "size_bytes": len(png)},
        # hostile manifest row: even a LISTED entry must not escape its segment dir
        {"artifact_id": "sa-evil", "bundle_path": "../outside.secret", "copied": True,
         "media_type": "text/plain", "owner_node": "gate", "size_bytes": 12},
    ]), encoding="utf-8")

    viewer = JsonlObservationViewer(FileEventSource(tmp_path))
    server = serve_viewer(viewer, port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        root = f"http://127.0.0.1:{server.server_address[1]}"
        page = urllib.request.urlopen(f"{root}/?run_id=served-art-run", timeout=5).read().decode()
        href = "/artifact/served-art-run/served-art-run/artifacts/frame.png"
        assert f'<img src="{href}"' in page and f'<a href="{href}"' in page, page[-2000:]
        fetched = urllib.request.urlopen(root + href, timeout=5)
        assert fetched.headers["Content-Type"] == "image/png"
        assert fetched.read() == png, "served bytes must be the archived artifact"
        for bad in (
            "/artifact/served-art-run/served-art-run/meta.json",       # exists, NOT in manifest
            "/artifact/served-art-run/served-art-run/artifacts/../meta.json",  # traversal shape
            "/artifact/other-run/served-art-run/artifacts/frame.png",  # wrong run
            "/artifact/served-art-run/served-art-run/../outside.secret",  # manifest-listed but escapes
        ):
            try:
                urllib.request.urlopen(root + bad, timeout=5)
                raise AssertionError(f"{bad} must 404, not serve bundle internals")
            except HTTPError as err:
                assert err.code == 404
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_external_nodes_are_labeled_never_silently_disconnected(tmp_path):
    """User-settled UX honesty (Q4.2 round): activity outside the declared graph (fanout
    item calls, provenance markers) renders as EXTERNAL cards with an explicit
    "outside declared graph" note + a canvas legend — and the legend appears ONLY when
    such nodes exist, so ordinary pages carry no dead boilerplate."""

    from ai_workflow_engine import WorkflowBuilder
    from ai_workflow_viewer import FileEventSource, observation_group_to_html

    definition = WorkflowBuilder("ext-label").step("declared_step").build()
    digest = definition.definition_digest()
    _write_bundle(
        tmp_path, "ext-run", definition,
        trace_events=[
            WorkflowTraceEvent(node="declared_step", node_status="completed", phase="node:result", run_id="ext-run", sequence=1, event_id="x-1"),
            # fanout-item-style activity: node id NOT in the definition
            WorkflowTraceEvent(node="probe_item", node_status="completed", phase="tool:result", run_id="ext-run", sequence=2, event_id="x-2"),
        ],
        meta_extra=_segment_meta("ext-run", "ext-run", 0, digest=digest),
    )
    page = observation_group_to_html(FileEventSource(tmp_path).read_group("ext-run"))
    assert "outside declared graph — no wiring recorded" in page, "external card must be labeled"
    assert "EXTERNAL cards are recorded activity outside" in page, "canvas legend missing"
    assert page.count("outside declared graph — no wiring recorded") == 1, "only the external card is labeled"

    _write_bundle(
        tmp_path / "plain", "plain-run", definition,
        trace_events=[WorkflowTraceEvent(node="declared_step", node_status="completed", phase="node:result", run_id="plain-run", sequence=1, event_id="p-1")],
        meta_extra=_segment_meta("plain-run", "plain-run", 0, digest=digest),
    )
    plain = observation_group_to_html(FileEventSource(tmp_path / "plain").read_group("plain-run"))
    assert "EXTERNAL cards are recorded activity" not in plain, "legend must be conditional"
    assert "outside declared graph — no wiring recorded" not in plain


def test_external_mixed_outcomes_never_masked_as_completed(tmp_path):
    """Codex pre-tag finding: an external node with 2 accepted + 1 failed calls read
    "completed" because status aggregation was last-write-wins. External activity must show
    neutral "mixed" + an explicit tally; all-accepted stays completed; declared nodes keep
    their engine-owned status untouched."""

    from ai_workflow_engine import WorkflowBuilder
    from ai_workflow_viewer import FileEventSource, observation_group_to_html

    definition = WorkflowBuilder("mixed-ext").step("declared_step").build()
    digest = definition.definition_digest()
    _write_bundle(
        tmp_path, "mixed-run", definition,
        trace_events=[
            WorkflowTraceEvent(node="declared_step", node_status="completed", phase="node:result", run_id="mixed-run", sequence=1, event_id="m-1"),
            WorkflowTraceEvent(node="probe_item", phase="tool:result", decision="accepted", run_id="mixed-run", sequence=2, event_id="m-2"),
            WorkflowTraceEvent(node="probe_item", phase="tool:result", decision="failed", severity="error", error="unparseable", run_id="mixed-run", sequence=3, event_id="m-3"),
            WorkflowTraceEvent(node="probe_item", phase="tool:result", decision="accepted", run_id="mixed-run", sequence=4, event_id="m-4"),
            WorkflowTraceEvent(node="clean_item", phase="tool:result", decision="accepted", run_id="mixed-run", sequence=5, event_id="m-5"),
        ],
        meta_extra=_segment_meta("mixed-run", "mixed-run", 0, digest=digest),
    )
    page = observation_group_to_html(FileEventSource(tmp_path).read_group("mixed-run"))
    assert "run-status-mixed" in page, "mixed external must carry the neutral status class"
    assert "2 accepted · 1 failed" in page, "outcome tally chip missing"
    assert page.count('class="graph-node run-status-mixed') == 1, (
        "all-accepted external (clean_item) must NOT be mixed"
    )

    import re
    declared_card = re.search(r'<article class="graph-node run-status-(\w+)[^"]*"[^>]*data-node-id="declared_step"', page)
    assert declared_card and declared_card.group(1) == "completed", "declared node keeps engine-owned status"


def test_served_artifacts_roundtrip_hostile_names_and_never_serve_active_content(tmp_path):
    """Codex release-review findings 7+8: (7) archive basenames keep spaces/#/%/unicode, so
    hrefs must percent-encode per segment and the route must decode before the manifest
    compare; (8) the route must never serve active content inline — raster allow-list only,
    everything else forced to opaque attachment; nosniff + CSP sandbox on every response."""

    import json as _json
    import threading
    import urllib.request
    from urllib.parse import quote

    from ai_workflow_engine import WorkflowBuilder
    from ai_workflow_viewer import FileEventSource, JsonlObservationViewer, serve_viewer

    definition = WorkflowBuilder("hostile-arty").step("gate").build()
    digest = definition.definition_digest()
    _write_bundle(
        tmp_path, "hostile-run", definition,
        trace_events=[WorkflowTraceEvent(node="gate", node_status="completed", phase="node:result", run_id="hostile-run", sequence=1, event_id="h-1")],
        meta_extra=_segment_meta("hostile-run", "hostile-run", 0, digest=digest),
    )
    bundle = _segment_path(tmp_path, "hostile-run")
    (bundle / "artifacts").mkdir()
    hostile_name = "frame 1 #50% ünïcode.png"
    png = b"\x89PNG hostile"
    (bundle / "artifacts" / hostile_name).write_bytes(png)
    svg = b'<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>'
    (bundle / "artifacts" / "evil.svg").write_bytes(svg)
    (bundle / "artifacts.json").write_text(_json.dumps([
        {"artifact_id": "h-png", "bundle_path": f"artifacts/{hostile_name}", "copied": True,
         "media_type": "image/png", "owner_node": "gate", "size_bytes": len(png)},
        {"artifact_id": "h-svg", "bundle_path": "artifacts/evil.svg", "copied": True,
         "media_type": "image/svg+xml", "owner_node": "gate", "size_bytes": len(svg)},
    ]), encoding="utf-8")

    viewer = JsonlObservationViewer(FileEventSource(tmp_path))
    server = serve_viewer(viewer, port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        root = f"http://127.0.0.1:{server.server_address[1]}"
        page = urllib.request.urlopen(f"{root}/?run_id=hostile-run", timeout=5).read().decode()
        encoded = quote(f"artifacts/{hostile_name}".split("/")[0], safe="") + "/" + quote(hostile_name, safe="")
        href = f"/artifact/hostile-run/hostile-run/{encoded}"
        assert f'href="{href}"' in page, "href must percent-encode spaces/#/%/unicode"
        fetched = urllib.request.urlopen(root + href, timeout=5)
        assert fetched.read() == png, "decoded route must resolve the raw manifest name"
        assert fetched.headers["Content-Type"] == "image/png"
        assert fetched.headers["X-Content-Type-Options"] == "nosniff"
        assert fetched.headers["Content-Security-Policy"] == "sandbox"

        assert '<img src="/artifact/hostile-run/hostile-run/artifacts/evil.svg"' not in page, (
            "active SVG must never be inlined as a preview"
        )
        svg_resp = urllib.request.urlopen(f"{root}/artifact/hostile-run/hostile-run/artifacts/evil.svg", timeout=5)
        assert svg_resp.headers["Content-Type"] == "application/octet-stream", (
            "active content must be served as opaque bytes, not its declared type"
        )
        assert svg_resp.headers["Content-Disposition"] == "attachment"
        assert svg_resp.headers["X-Content-Type-Options"] == "nosniff"
        assert svg_resp.read() == svg
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_external_mixed_status_is_graph_layer_truth_across_all_views(tmp_path):
    """A1/A4/A5: external outcome status is computed ONCE in build_observation_graph, so the
    ObservationNode, the Nodes table, the card, and any JSON consumer all agree — no
    view-layer recount that lets the table say 'completed' while the card says 'mixed'.
    Also locks the disjoint tally (accepted-with-warning counts once) and typed-node_status
    success visibility."""

    import re

    from ai_workflow_engine import WorkflowBuilder
    from ai_workflow_viewer.observability import build_observation_graph, observation_graph_to_html

    definition = WorkflowBuilder("mix-graph").step("declared").build()

    def ev(**k):
        return WorkflowTraceEvent(run_id="r", **k)

    events = [
        ev(node="declared", node_status="completed", phase="node:result", sequence=1, event_id="1"),
        ev(node="ext", phase="tool:result", decision="accepted", sequence=2, event_id="2"),
        ev(node="ext", phase="tool:result", decision="failed", severity="error", error="boom", sequence=3, event_id="3"),
        ev(node="ext", phase="tool:result", decision="accepted", sequence=4, event_id="4"),
    ]
    graph = build_observation_graph(definition, events, [], [])
    assert graph.nodes["ext"].status == "mixed", "status is graph-layer truth, not view-local"
    assert (graph.nodes["ext"].outcome_accepted, graph.nodes["ext"].outcome_failed) == (2, 1)
    assert graph.nodes["declared"].status == "completed", "declared node untouched"

    page = observation_graph_to_html(definition, graph)
    assert "run-status-mixed" in page and "2 accepted · 1 failed" in page
    ext_row = re.search(r"<tr>(?:(?!</tr>).)*?<code>ext</code>.*?</tr>", page, re.S).group(0)
    assert 'class="status-mixed"' in ext_row, "Nodes table must NOT say completed for a mixed node"

    # A4: accepted-with-warning counts once as accepted → status completed (not failed), no tally
    g2 = build_observation_graph(definition, [
        ev(node="declared", node_status="completed", phase="node:result", sequence=1, event_id="1"),
        ev(node="ext", phase="tool:result", decision="accepted", severity="error", error="warn", sequence=2, event_id="2"),
    ], [], [])
    assert g2.nodes["ext"].status == "completed"
    assert (g2.nodes["ext"].outcome_accepted, g2.nodes["ext"].outcome_failed) == (1, 0)

    # A5: typed-node_status success (no decision) is seen → mixed with the failure
    g3 = build_observation_graph(definition, [
        ev(node="declared", node_status="completed", phase="node:result", sequence=1, event_id="1"),
        ev(node="ext", node_status="completed", phase="tool:result", sequence=2, event_id="2"),
        ev(node="ext", phase="tool:result", decision="failed", severity="error", error="x", sequence=3, event_id="3"),
    ], [], [])
    assert g3.nodes["ext"].status == "mixed"
    assert (g3.nodes["ext"].outcome_accepted, g3.nodes["ext"].outcome_failed) == (1, 1)

    # Patch regression: a later success must not erase an earlier partial/suspended
    # external outcome. These statuses are legitimate typed engine vocabulary, not
    # neutral events, and therefore participate in the shared graph-layer aggregate.
    g4 = build_observation_graph(definition, [
        ev(node="declared", node_status="completed", phase="node:result", sequence=1, event_id="1"),
        ev(node="ext", node_status="partial", phase="tool:result", sequence=2, event_id="2"),
        ev(node="ext", decision="accepted", phase="tool:result", sequence=3, event_id="3"),
    ], [], [])
    assert g4.nodes["ext"].status == "partial"
    assert (g4.nodes["ext"].outcome_accepted, g4.nodes["ext"].outcome_partial) == (1, 1)
    assert "1 accepted · 1 partial" in observation_graph_to_html(definition, g4)

    g5 = build_observation_graph(definition, [
        ev(node="declared", node_status="completed", phase="node:result", sequence=1, event_id="1"),
        ev(node="ext", node_status="requires_user_input", phase="node:result", sequence=2, event_id="2"),
        ev(node="ext", decision="accepted", phase="tool:result", sequence=3, event_id="3"),
    ], [], [])
    assert g5.nodes["ext"].status == "suspended"
    assert (g5.nodes["ext"].outcome_accepted, g5.nodes["ext"].outcome_suspended) == (1, 1)


def test_timeline_escapes_hostile_trace_error_markup():
    """Trace errors can include provider/process/site text and must stay inert in HTML."""

    from ai_workflow_viewer.observability import (
        build_observation_graph,
        observation_graph_to_html,
    )

    definition = WorkflowBuilder("timeline-xss").step("inspect").build()
    hostile_error = '<script>alert("timeline-xss")</script>'
    graph = build_observation_graph(
        definition,
        [
            WorkflowTraceEvent(
                node="inspect",
                phase="tool:result",
                decision="failed",
                severity="error",
                error=hostile_error,
                run_id="timeline-run",
                sequence=1,
                event_id="timeline-hostile-error",
            )
        ],
    )

    page = observation_graph_to_html(definition, graph)
    timeline = page.split('<section id="timeline">', 1)[1].split("</section>", 1)[0]

    assert hostile_error not in timeline
    assert "error: &lt;script&gt;alert(&quot;timeline-xss&quot;)&lt;/script&gt;" in timeline


def test_artifact_section_is_robust_and_escapes_untrusted_manifest_fields(tmp_path):
    """A2/A3: hostile/corrupt manifests can neither inject markup nor crash the page.
    size_bytes is escaped like every field; non-list/non-dict manifests degrade to the
    'unreadable' notice instead of raising."""

    import json as _json

    from ai_workflow_viewer.observability import _artifact_section_html

    (tmp_path / "artifacts").mkdir()
    (tmp_path / "artifacts" / "a.png").write_bytes(b"x")
    (tmp_path / "artifacts.json").write_text(_json.dumps([
        {"artifact_id": "x", "bundle_path": "artifacts/a.png", "copied": True,
         "media_type": "image/png", "owner_node": "n", "size_bytes": "<img src=x onerror=alert(1)>"}]))
    page = _artifact_section_html(str(tmp_path))
    assert "<img src=x onerror=alert(1)>" not in page, "size_bytes must be escaped"
    assert "&lt;img src=x onerror=alert(1)&gt;" in page

    for shape in ({"artifacts": []}, ["oops"], [None], "scalar", 42):
        (tmp_path / "artifacts.json").write_text(_json.dumps(shape))
        out = _artifact_section_html(str(tmp_path))  # must not raise
        assert "unreadable" in out, f"shape {shape!r} must degrade, not crash"

    # A7: the default (file://) base is a percent-encoded file URL, not a raw path — so
    # exported/local hrefs survive '#'/'%'/space/unicode in the bundle directory.
    (tmp_path / "artifacts.json").write_text(_json.dumps([
        {"artifact_id": "x", "bundle_path": "artifacts/a.png", "copied": True,
         "media_type": "image/png", "owner_node": "n", "size_bytes": 1}]))
    import re as _re
    default_href = _re.search(r'href="([^"]*a\.png)"', _artifact_section_html(str(tmp_path))).group(1)
    assert default_href.startswith("file://"), f"default base must be a file URL, got {default_href!r}"


def test_served_route_404s_missing_but_stays_loud_on_malformed_and_corruption(tmp_path):
    """A3/A6: the /artifact route returns 404 for a manifest-shape mismatch (via the shared
    loader), but a corrupt group lineage must NOT be silently 404'd — it stays loud."""

    import json as _json
    import threading
    import urllib.request
    from urllib.error import HTTPError

    from ai_workflow_engine import WorkflowBuilder
    from ai_workflow_viewer import FileEventSource, JsonlObservationViewer, serve_viewer

    definition = WorkflowBuilder("route-robust").step("gate").build()
    digest = definition.definition_digest()
    _write_bundle(
        tmp_path, "rr-run", definition,
        trace_events=[WorkflowTraceEvent(node="gate", node_status="completed", phase="node:result", run_id="rr-run", sequence=1, event_id="g-1")],
        meta_extra=_segment_meta("rr-run", "rr-run", 0, digest=digest),
    )
    bundle = _segment_path(tmp_path, "rr-run")
    (bundle / "artifacts").mkdir()
    (bundle / "artifacts" / "a.png").write_bytes(b"x")
    (bundle / "artifacts.json").write_text(_json.dumps({"artifacts": []}))  # wrong shape

    viewer = JsonlObservationViewer(FileEventSource(tmp_path))
    server = serve_viewer(viewer, port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        root = f"http://127.0.0.1:{server.server_address[1]}"
        try:
            urllib.request.urlopen(f"{root}/artifact/rr-run/rr-run/artifacts/a.png", timeout=5)
            raise AssertionError("malformed manifest must 404, not serve or crash")
        except HTTPError as err:
            assert err.code == 404
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)



def test_artifact_response_lets_group_corruption_propagate(tmp_path):
    """A6 (direct): _artifact_response catches ONLY absence. A viewer whose grouped read
    raises a lineage-corruption error must let it propagate — never convert it to a silent
    404 (which a blanket `except Exception` would)."""

    import pytest

    from ai_workflow_viewer.server import _artifact_response

    class _CorruptViewer:
        def _read_group(self, run_id):
            raise ValueError("duplicate committed attempt at ordinal 0 — corrupt lineage")

    with pytest.raises(ValueError, match="corrupt lineage"):
        _artifact_response(_CorruptViewer(), "/artifact/r/seg/artifacts/a.png")

    # absence still degrades to None (→ 404), not a raise
    class _AbsentViewer:
        def _read_group(self, run_id):
            raise FileNotFoundError(run_id)

    assert _artifact_response(_AbsentViewer(), "/artifact/r/seg/artifacts/a.png") is None


def test_execution_window_timeout_and_retrace_project_from_persisted_truth(tmp_path):
    """v0.10 Phase 3: the generic viewer surfaces the engine's execution window (soft/hard/
    clamps/enforcement), the timeout reason, and the retrace round/target at the relevant node
    — ALL from persisted trace metadata, never inferred. A timeout recorded WITHOUT a window
    says 'not recorded' instead of guessing durations."""

    from ai_workflow_engine import WorkflowBuilder
    from ai_workflow_viewer.observability import (
        _observation_view_data,
        build_observation_graph,
        observation_graph_to_html,
    )

    definition = (
        WorkflowBuilder("bounded-run").step("worker").step("slow").step("gate").step("legacy").build()
    )
    window = {
        "requested_timeout_s": 10.0,
        "capability_timeout_s": None,
        "run_remaining_s": 10.0,
        "soft_timeout_s": 8.0,
        "hard_timeout_s": 10.0,
        "completion_reserve_s": 2.0,
        "request_source": "planner_task",
        "limiting_sources": ["run_limit"],
        "clamps": ["run_remaining"],
        "enforcement": "process",
    }
    process_bound = {
        "work_timeout_s": 7.0,
        "kill_grace_s": 1.0,
        "source": "engine_window",
        "engine_soft_s": 8.0,
        "engine_hard_s": 10.0,
        "cleanup_headroom_s": 1.0,
        "settle_reserve_s": 0.25,
    }

    def ev(**k):
        return WorkflowTraceEvent(run_id="r", phase="tool:result", **k)

    events = [
        # bounded SUCCESS -> full window projected (not only the timeout path)
        ev(node="worker", decision="accepted", sequence=1, event_id="1",
           metadata={
               "execution_window": window,
               "process_execution_bound": process_bound,
           }),
        # timeout WITH a window -> window + timeout reason
        ev(node="slow", decision="partial", severity="error", sequence=2, event_id="2",
           metadata={"timeout_reason": "execution_window_exceeded", "execution_window": window}),
        # retrace route recorded at the evaluator node
        ev(node="gate", decision="retrace", sequence=3, event_id="3",
           metadata={"retrace": 1, "retrace_target": "worker", "action": "retrace_to"}),
        # timeout WITHOUT a recorded window (older bundle) -> "not recorded", never guessed
        ev(node="legacy", decision="partial", severity="error", sequence=4, event_id="4",
           metadata={"timeout_reason": "execution_window_exceeded"}),
    ]

    graph = build_observation_graph(definition, events, [], [])
    metrics = {n["id"]: n["metrics"] for n in _observation_view_data(definition, graph)["nodes"]}

    assert "window: soft 8s / hard 10s (clamped: run_remaining) [process]" in metrics["worker"]
    assert (
        "process: work 7s / cleanup 1s / settle 0.25s (reserved: 1s) [engine_window]"
        in metrics["worker"]
    )
    assert any(m.startswith("window: soft 8s / hard 10s") for m in metrics["slow"])
    assert "timeout: execution_window_exceeded" in metrics["slow"]
    assert "retrace round 1 → worker" in metrics["gate"]
    # the honest "not recorded" — a timeout with no persisted window is never back-filled
    assert "window: not recorded" in metrics["legacy"]
    assert "timeout: execution_window_exceeded" in metrics["legacy"]
    assert not any("soft" in m for m in metrics["legacy"]), "must not invent a window"

    page = observation_graph_to_html(definition, graph)
    assert "window: soft 8s / hard 10s" in page
    assert "process: work 7s / cleanup 1s / settle 0.25s" in page
    assert "retrace round 1" in page
    assert "window: not recorded" in page


def test_malformed_runtime_metrics_degrade_without_crashing_or_inventing_values():
    from ai_workflow_engine import WorkflowBuilder
    from ai_workflow_viewer.observability import (
        _observation_view_data,
        build_observation_graph,
        observation_graph_to_html,
    )

    definition = WorkflowBuilder("malformed-runtime").step("worker").build()
    event = WorkflowTraceEvent(
        run_id="r",
        node="worker",
        phase="tool:result",
        decision="partial",
        sequence=1,
        event_id="1",
        metadata={
            "execution_window": {
                "soft_timeout_s": None,
                "hard_timeout_s": 10.0,
                "clamps": ["run_remaining", {"untrusted": "shape"}],
                "enforcement": "process",
            },
            "process_execution_bound": {
                "work_timeout_s": "not-a-number",
                "kill_grace_s": 1.5,
                "settle_reserve_s": float("nan"),
                "cleanup_headroom_s": float("inf"),
            },
        },
    )

    graph = build_observation_graph(definition, [event], [], [])
    metrics = _observation_view_data(definition, graph)["nodes"][0]["metrics"]
    page = observation_graph_to_html(definition, graph)
    rendered_metrics = " ".join(metrics).lower()

    assert "window: hard 10s (clamped: run_remaining) [process]" in metrics
    assert "process: cleanup 1.5s" in metrics
    assert "not-a-number" not in rendered_metrics
    assert "nan" not in rendered_metrics
    assert "inf" not in rendered_metrics
    assert "malformed-runtime" in page


def test_process_io_settlement_truth_is_summarized_not_dumped(tmp_path):
    """v0.10.1: the viewer projects a CONCISE process-I/O settlement summary (byte totals,
    truncation, result-file rejection) from persisted trace truth — it must never copy the
    captured flood into the page."""

    from ai_workflow_engine import WorkflowBuilder
    from ai_workflow_viewer.observability import (
        _observation_view_data,
        build_observation_graph,
        observation_graph_to_html,
    )

    definition = WorkflowBuilder("proc-run").step("probe").build()

    def ev(**k):
        return WorkflowTraceEvent(run_id="r", phase="tool:result", **k)

    events = [
        ev(
            node="probe",
            decision="failed",
            severity="error",
            sequence=1,
            event_id="1",
            error="external process result file rejected: result path is a symlink",
            metadata={
                "process_io": {
                    "stdout": {"total_bytes": 6 * 1024 * 1024, "retained_bytes": 1024,
                               "limit_bytes": 1024 * 1024, "truncated": True},
                    "stderr": {"total_bytes": 12, "retained_bytes": 12,
                               "limit_bytes": 256 * 1024, "truncated": False},
                    "result_file": {"status": "unsafe", "kind": "symlink",
                                    "total_bytes": None, "limit_bytes": None,
                                    "reason": "result path is a symlink"},
                }
            },
        ),
    ]
    graph = build_observation_graph(definition, events, [], [])
    metrics = {n["id"]: n["metrics"] for n in _observation_view_data(definition, graph)["nodes"]}

    probe = " || ".join(metrics["probe"])
    assert "capture: stdout 6.0 MiB (truncated)" in probe
    assert "result file: unsafe (symlink)" in probe

    page = observation_graph_to_html(definition, graph)
    assert "capture: stdout 6.0 MiB (truncated)" in page
    assert "result file: unsafe (symlink)" in page
    # the flood itself is NEVER copied into the page (summary only)
    assert "F" * 10000 not in page


def test_raw_detail_surface_uses_the_same_bounded_renderer_as_graph_details():
    """A large detail appears in graph and raw panes, but neither may duplicate it unbounded."""

    from ai_workflow_engine import WorkflowBuilder
    from ai_workflow_viewer.observability import build_observation_graph, observation_graph_to_html

    definition = WorkflowBuilder("large-detail").step("probe").build()
    body = "HEAD-SENTINEL" + ("MIDDLE" * 30_000) + "TAIL-SENTINEL"
    event = WorkflowTraceEvent(
        node="probe",
        phase="tool:result",
        decision="partial",
        detail_refs=["large-detail"],
        run_id="r",
        sequence=1,
    )
    text_body = ObservationTextBody(value=body)
    detail = ObservationDetail(
        detail_id="large-detail",
        event_id=event.event_id,
        kind="tool_result",
        body=text_body,
        digest=body_sha256(text_body),
        run_id="r",
        sequence=2,
    )

    graph = build_observation_graph(definition, [event], [], [detail], run_id="r")
    page = observation_graph_to_html(definition, graph)

    assert "HEAD-SENTINEL" in page and "TAIL-SENTINEL" in page
    assert "truncated in this view" in page
    assert "MIDDLE" * 10_000 not in page
    assert len(page) < 300_000, f"detail was duplicated into the page without a display cap: {len(page)}"


def test_single_detail_body_owner_renders_the_complete_structured_value():
    """The sole logical body is the complete representation used by every viewer pane."""

    from ai_workflow_engine import WorkflowBuilder
    from ai_workflow_viewer.observability import build_observation_graph, observation_graph_to_html

    definition = WorkflowBuilder("structured-detail").step("probe").build()
    event = WorkflowTraceEvent(
        node="probe",
        phase="tool:result",
        detail_refs=["structured-detail"],
        run_id="r",
        sequence=1,
    )
    detail = _detail(
        detail_id="structured-detail",
        event_id=event.event_id,
        kind="tool_result",
        value={"canonical": "structured-json", "nested": {"count": 2}},
        run_id="r",
        sequence=2,
    )

    page = observation_graph_to_html(
        definition,
        build_observation_graph(definition, [event], [], [detail], run_id="r"),
    )

    assert "structured-json" in page
    assert "&quot;count&quot;: 2" in page


def test_decision_text_never_sets_terminal_status(tmp_path):
    """M11 rejection lock: the deleted old-contract inference stays deleted —
    (a) a declared node whose events carry ONLY decision text (including the pre-typed
    ``flow:authored`` announcement shape) reads RUNNING, never completed/failed;
    (b) the typed ``node_status`` stays the one terminal authority;
    (c) external activity classifies ONLY the closed CapabilityStatus vocabulary — the
    old strings (``valid``/``answered``/``provisional``/``denied``) tally nothing."""

    from ai_workflow_engine import WorkflowBuilder
    from ai_workflow_viewer import build_observation_graph

    definition = WorkflowBuilder("m11").step("plan").step("author").build()
    graph = build_observation_graph(
        definition,
        [
            WorkflowTraceEvent(node="plan", decision="accepted", run_id="r", sequence=1),
            WorkflowTraceEvent(node="author", decision="flow:authored", run_id="r", sequence=2),
            WorkflowTraceEvent(node="ext.tool", decision="valid", run_id="r", sequence=3),
            WorkflowTraceEvent(node="ext.tool", decision="answered", run_id="r", sequence=4),
            WorkflowTraceEvent(node="ext.tool", decision="denied", run_id="r", sequence=5),
            WorkflowTraceEvent(node="ext.ok", decision="accepted", run_id="r", sequence=6),
        ],
        run_id="r",
    )
    assert graph.nodes["plan"].status == "running", (
        "decision text alone must never complete a declared node (M11)"
    )
    assert graph.nodes["author"].status == "running", (
        "flow:authored WITHOUT node_status is the pre-typed shape — progress at most"
    )
    ext = graph.nodes["ext.tool"]
    assert (
        ext.outcome_accepted, ext.outcome_partial, ext.outcome_failed, ext.outcome_suspended
    ) == (0, 0, 0, 0), "old-contract decision strings are neutral for the external tally"
    assert ext.status == "running", "neutral activity keeps the folded progress value"
    assert graph.nodes["ext.ok"].status == "completed"
    assert graph.nodes["ext.ok"].outcome_accepted == 1

    typed = build_observation_graph(
        definition,
        [
            WorkflowTraceEvent(
                node="plan", decision="accepted", node_status="completed",
                phase="node:result", run_id="r", sequence=1,
            ),
        ],
        run_id="r",
    )
    assert typed.nodes["plan"].status == "completed", "typed node_status stays authoritative"


def test_boundary_attacks_are_refused_on_every_viewer_surface(tmp_path):
    """C2-1/C2-2/C2-3 (codex Iteration-2 review): the strict boundary is strict on EVERY
    public/untrusted surface — (a) run-id path syntax never reaches a join (Python and HTTP
    doors); (b) a symlinked bundle file escaping its directory is rejected; (c) a forged
    definition digest fails the SINGLE read exactly like the grouped one; (d) a damaged
    meta-only segment stays visible and loud, never disappears; (e) /events commits 200 only
    after the strict read, and /artifact maps contract violations to the plain 500."""

    import json as _json
    import shutil as _shutil
    import threading
    import urllib.error
    import urllib.request

    import pytest as _pytest

    from ai_workflow_viewer import FileEventSource, serve_viewer

    definition = _write_group(tmp_path)
    source = FileEventSource(tmp_path)

    # (a) identity guard: Python door...
    with _pytest.raises(ValueError, match="path syntax"):
        source.read("../escape")

    # (b) symlinked evidence escaping the bundle directory
    sym_run = _write_bundle(
        tmp_path, "sym-run", definition,
        trace_events=[WorkflowTraceEvent(node="gate", node_status="completed", phase="node:result", run_id="sym-run", sequence=1, event_id="s-1")],
        meta_extra=_segment_meta("sym-run", "sym-run", 0, digest=definition.definition_digest()),
    )
    outside = tmp_path.parent / "evil-trace.jsonl"
    outside.write_text("", encoding="utf-8")
    (sym_run / "trace.jsonl").unlink()
    (sym_run / "trace.jsonl").symlink_to(outside)
    with _pytest.raises(ValueError, match="escapes .*sym-run/segments/sym-run"):
        FileEventSource(sym_run).read()
    _shutil.rmtree(sym_run)

    # (c) forged digest fails the SINGLE read too (was group-only)
    forge_run = _write_bundle(
        tmp_path, "forge-run", definition,
        trace_events=[WorkflowTraceEvent(node="gate", node_status="completed", phase="node:result", run_id="forge-run", sequence=1, event_id="f-1")],
        meta_extra=_segment_meta("forge-run", "forge-run", 0, digest=definition.definition_digest()),
    )
    meta = _json.loads((forge_run / "meta.json").read_text(encoding="utf-8"))
    meta["definition_digest"] = "deadbeefdeadbeef"
    (forge_run / "meta.json").write_text(_json.dumps(meta), encoding="utf-8")
    with _pytest.raises(ValueError, match="claims definition digest.*recomputes"):
        FileEventSource(forge_run).read()

    # (d) damaged meta-only segment: VISIBLE in the chooser, loud on read
    damaged = _segment_path(tmp_path, "damaged-run")
    damaged.mkdir(parents=True)
    base_meta = _json.loads(
        (_segment_path(tmp_path, "logical-run") / "meta.json").read_text(encoding="utf-8")
    )
    base_meta.update(run_id="damaged-run", segment_id="damaged-run")
    (damaged / "meta.json").write_text(_json.dumps(base_meta), encoding="utf-8")
    rows = {row["run_id"] for row in source.list_groups()}
    assert "damaged-run" in rows, "a damaged segment must stay visible, never disappear"
    with _pytest.raises(ValueError, match="corrupt segment.*missing definition.json"):
        source.read_group("damaged-run")

    # (e) served doors: query traversal, missing run, and contract violations are plain
    # errors with the raising contract's message — never a 200 that drops or half-renders
    server = serve_viewer(JsonlObservationViewer(source), port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        root = f"http://127.0.0.1:{server.server_address[1]}"
        with _pytest.raises(urllib.error.HTTPError) as caught:
            urllib.request.urlopen(f"{root}/?run_id=../escape", timeout=5)
        assert caught.value.code == 500
        assert "path syntax" in caught.value.read().decode("utf-8")

        with _pytest.raises(urllib.error.HTTPError) as caught:
            urllib.request.urlopen(f"{root}/events?run_id=unknown-run", timeout=5)
        assert caught.value.code == 404, "SSE must refuse BEFORE claiming success"

        with _pytest.raises(urllib.error.HTTPError) as caught:
            urllib.request.urlopen(f"{root}/events?run_id=forge-run", timeout=5)
        assert caught.value.code == 500
        assert "claims definition digest" in caught.value.read().decode("utf-8")

        with _pytest.raises(urllib.error.HTTPError) as caught:
            urllib.request.urlopen(f"{root}/artifact/forge-run/forge-run/artifacts/x.png", timeout=5)
        assert caught.value.code == 500
        assert "claims definition digest" in caught.value.read().decode("utf-8")
    finally:
        server.shutdown()
        thread.join(timeout=5)


def test_symlinked_child_bundles_are_refused_on_every_viewer_surface(tmp_path):
    """C2GR-1 attack (2)+(4): a child symlink named like a valid run id and pointing to an
    OUTSIDE valid v3 bundle never lists, reads, groups, or serves — loudly, not silently
    skipped; a deliberately symlinked CONFIGURED base stays functional (base trusted,
    children not)."""

    import threading
    import urllib.error
    import urllib.request

    import pytest as _pytest

    from ai_workflow_engine import WorkflowBuilder
    from ai_workflow_viewer import FileEventSource, serve_viewer

    definition = WorkflowBuilder("linked").step("gate").build()
    outside = tmp_path / "outside"
    _write_bundle(
        outside, "link-run", definition,
        trace_events=[WorkflowTraceEvent(node="gate", node_status="completed", phase="node:result", run_id="link-run", sequence=1, event_id="l-1")],
        meta_extra=_segment_meta("link-run", "link-run", 0, digest=definition.definition_digest()),
    )
    root = tmp_path / "root"
    root.mkdir()
    _write_group(root)  # healthy content beside the trap
    (root / "link-run").symlink_to(outside / "link-run")
    # An INSIDE-pointing alias (resolves within root) is the case the containment fallback
    # cannot catch — only the is-symlink rejection refuses it. Both must be loud, never a
    # silently duplicated run.
    (root / "alias-run").symlink_to(root / "logical-run")
    source = FileEventSource(root)

    with _pytest.raises(ValueError, match="symlink"):
        source.read("link-run")
    with _pytest.raises(ValueError, match="symlink"):
        source.read("alias-run")
    with _pytest.raises(ValueError, match="symlink"):
        source.list_runs()
    with _pytest.raises(ValueError, match="symlink"):
        source.list_groups()
    with _pytest.raises(ValueError, match="symlink"):
        source.read_group("logical-run")  # the scan refuses loudly, never skips the trap

    server = serve_viewer(JsonlObservationViewer(source), port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        base_url = f"http://127.0.0.1:{server.server_address[1]}"
        with _pytest.raises(urllib.error.HTTPError) as caught:
            urllib.request.urlopen(f"{base_url}/?run_id=link-run", timeout=5)
        assert caught.value.code == 500
        assert "symlink" in caught.value.read().decode("utf-8")
    finally:
        server.shutdown()
        thread.join(timeout=5)

    # (4) symlinked CONFIGURED base is supported: same content, alias root, full group read
    clean_root = tmp_path / "clean-root"
    clean_root.mkdir()
    _write_group(clean_root)
    alias = tmp_path / "alias-root"
    alias.symlink_to(clean_root)
    group = FileEventSource(alias).read_group("logical-run")
    assert group.status == "completed" and len(group.segments) == 2
