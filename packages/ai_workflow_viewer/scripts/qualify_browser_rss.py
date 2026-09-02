#!/usr/bin/env python3
"""Qualify bounded viewer memory with installed wheels and a real browser/server pair."""

from __future__ import annotations

import argparse
import json
import os
import select
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Dict, Iterable, List

import psutil
from playwright.sync_api import sync_playwright


_HELPER_PROGRAM = r'''
import json
import sys
from pathlib import Path

import ai_workflow_engine
import ai_workflow_viewer
from ai_workflow_engine import (
    ObservationDetail,
    ObservationJsonBody,
    WorkflowBuilder,
    WorkflowTraceEvent,
    WorkflowUsageEvent,
    open_observation_run_bundle,
)
from ai_workflow_engine.observation_values import body_sha256
from ai_workflow_viewer import FileEventSource, JsonlObservationViewer, serve_viewer

mode, root_arg, run_id = sys.argv[1:4]
root = Path(root_arg)
if mode == "create":
    size = int(sys.argv[4])
    bundle = open_observation_run_bundle(root, run_id)
    body = ObservationJsonBody(
        value={"payload": "BROWSER-RSS-HEAD" + ("x" * size) + "BROWSER-RSS-TAIL"}
    )
    detail = ObservationDetail(
        detail_id="browser-rss-detail",
        event_id="browser-rss-response",
        invocation_id="browser-rss-invocation",
        kind="llm_response",
        content_type="application/json",
        body=body,
        digest=body_sha256(body),
    )
    bundle.trace_sink.record(
        WorkflowTraceEvent(
            event_id="browser-rss-request",
            node="inspect",
            phase="llm:request",
            invocation_id="browser-rss-invocation",
            detail_capture="capture_mode_off",
        )
    )
    bundle.trace_sink.record(
        WorkflowTraceEvent(
            event_id="browser-rss-response",
            node="inspect",
            phase="llm:response",
            invocation_id="browser-rss-invocation",
            detail_capture="captured",
            detail_refs=[detail.detail_id],
        )
    )
    bundle.detail_sink.record(detail)
    bundle.usage_sink.record(
        WorkflowUsageEvent(
            event_id="browser-rss-usage",
            node="inspect",
            invocation_id="browser-rss-invocation",
            provider="browser-rss",
            operation="chat",
            success=True,
        )
    )
    bundle.finalize(
        WorkflowBuilder("browser-rss-workflow").step("inspect").build(),
        status="completed",
    )
    print(bundle.path.name)
elif mode == "serve":
    origins = {
        "ai_workflow_engine": str(Path(ai_workflow_engine.__file__).resolve()),
        "ai_workflow_viewer": str(Path(ai_workflow_viewer.__file__).resolve()),
    }
    if not all("site-packages" in value for value in origins.values()):
        raise RuntimeError(f"qualification did not import installed wheels: {origins}")
    server = serve_viewer(JsonlObservationViewer(FileEventSource(root)), port=0)
    print(json.dumps({"port": server.server_address[1], "origins": origins}), flush=True)
    server.serve_forever()
else:
    raise RuntimeError(f"unknown helper mode: {mode}")
'''


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--installed-python", required=True)
    parser.add_argument("--chrome-executable", required=True)
    parser.add_argument("--work-dir", required=True)
    parser.add_argument("--record", required=True)
    parser.add_argument("--small-bytes", type=int, default=2 * 1024 * 1024)
    parser.add_argument("--large-bytes", type=int, default=64 * 1024 * 1024)
    parser.add_argument("--rss-slack-kib", type=int, default=32 * 1024)
    return parser


def _require_new_path(path: Path, label: str) -> None:
    if path.exists():
        raise RuntimeError(f"{label} must not already exist: {path}")


def _process_tree_rss(processes: Iterable[psutil.Process]) -> int:
    seen: set[int] = set()
    total = 0
    for root in processes:
        try:
            members = [root, *root.children(recursive=True)]
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
        for process in members:
            if process.pid in seen:
                continue
            seen.add(process.pid)
            try:
                total += process.memory_info().rss
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
    return total


def _browser_roots(profile: Path) -> List[psutil.Process]:
    marker = str(profile.resolve())
    matching: Dict[int, psutil.Process] = {}
    for process in psutil.process_iter(["pid", "cmdline"]):
        try:
            command = " ".join(process.info.get("cmdline") or [])
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
        if marker in command:
            matching[process.pid] = process
    roots = [process for process in matching.values() if process.ppid() not in matching]
    if not roots:
        raise RuntimeError(f"could not identify browser process for profile {marker}")
    return roots


def _read_server_ready(process: subprocess.Popen, timeout_s: float = 20.0) -> dict:
    if process.stdout is None:
        raise RuntimeError("viewer server stdout was not captured")
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if process.poll() is not None:
            stderr = process.stderr.read() if process.stderr is not None else ""
            raise RuntimeError(f"viewer server exited before readiness: {stderr[-2000:]}")
        ready, _, _ = select.select([process.stdout], [], [], 0.1)
        if ready:
            return json.loads(process.stdout.readline())
    raise RuntimeError("viewer server readiness timed out")


def _create_fixture(
    installed_python: Path,
    helper: Path,
    root: Path,
    run_id: str,
    body_bytes: int,
    label: str,
) -> str:
    created = subprocess.run(
        [
            str(installed_python),
            "-I",
            str(helper),
            "create",
            str(root),
            run_id,
            str(body_bytes),
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    if created.returncode != 0:
        raise RuntimeError(
            f"installed fixture creation failed for {label}: "
            f"stdout={created.stdout[-2000:]!r}, stderr={created.stderr[-2000:]!r}"
        )
    return created.stdout.strip().splitlines()[-1]


def _start_server(
    installed_python: Path,
    helper: Path,
    root: Path,
    run_id: str,
) -> tuple[subprocess.Popen, dict]:
    server = subprocess.Popen(
        [str(installed_python), "-I", str(helper), "serve", str(root), run_id],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        return server, _read_server_ready(server)
    except BaseException:
        _stop_server(server)
        raise


def _sample_process_trees(
    stop: threading.Event,
    server: subprocess.Popen,
    browser_roots: List[psutil.Process],
    peaks: Dict[str, int],
    errors: List[str],
) -> None:
    try:
        server_process = psutil.Process(server.pid)
        while not stop.wait(0.01):
            peaks["server"] = max(peaks["server"], _process_tree_rss([server_process]))
            if browser_roots:
                peaks["browser"] = max(peaks["browser"], _process_tree_rss(browser_roots))
    except BaseException as exc:
        errors.append(f"{type(exc).__name__}: {exc}")


def _assert_bounded_preview(
    page,
    detail_requests: List[str],
    console_errors: List[str],
) -> None:
    initial = page.content()
    if detail_requests:
        raise RuntimeError("initial viewer page loaded a detail body")
    if "BROWSER-RSS-HEAD" in initial or "BROWSER-RSS-TAIL" in initial:
        raise RuntimeError("initial viewer page embedded retained body content")
    page.locator("details.observation-detail summary").first.click()
    button = page.locator("[data-load-detail]").first
    target = button.get_attribute("data-preview-target")
    if not target:
        raise RuntimeError("viewer preview target is missing")
    button.click()
    page.wait_for_selector('[data-load-detail][data-loaded="true"]')
    preview = page.locator(f"#{target}").text_content() or ""
    page.wait_for_timeout(500)
    if len(detail_requests) != 1 or "mode=preview" not in detail_requests[0]:
        raise RuntimeError(f"unexpected detail requests: {detail_requests}")
    if len(preview.encode("utf-8")) != 64 * 1024:
        raise RuntimeError("browser preview is not exactly 64 KiB")
    if "BROWSER-RSS-HEAD" not in preview or "BROWSER-RSS-TAIL" in preview:
        raise RuntimeError("browser preview is not a bounded prefix")
    if console_errors:
        raise RuntimeError(f"browser console errors: {console_errors}")


def _exercise_browser(
    *,
    url: str,
    profile: Path,
    chrome_executable: Path,
    server: subprocess.Popen,
) -> tuple[Dict[str, int], Dict[str, int], List[str]]:
    peaks = {"server": 0, "browser": 0}
    baselines = {"server": 0, "browser": 0}
    browser_roots: List[psutil.Process] = []
    detail_requests: List[str] = []
    console_errors: List[str] = []
    sampler_errors: List[str] = []
    stop = threading.Event()
    sampler = None
    with sync_playwright() as playwright:
        context = playwright.chromium.launch_persistent_context(
            str(profile),
            executable_path=str(chrome_executable),
            headless=True,
        )
        try:
            browser_roots[:] = _browser_roots(profile)
            page = context.pages[0] if context.pages else context.new_page()
            page.wait_for_timeout(500)
            baselines["server"] = _process_tree_rss([psutil.Process(server.pid)])
            baselines["browser"] = _process_tree_rss(browser_roots)
            peaks.update(baselines)
            if baselines["server"] <= 0 or baselines["browser"] <= 0:
                raise RuntimeError(f"RSS sampler recorded no settled baseline: {baselines}")
            sampler = threading.Thread(
                target=_sample_process_trees,
                args=(stop, server, browser_roots, peaks, sampler_errors),
                daemon=True,
            )
            sampler.start()
            page.on(
                "request",
                lambda request: detail_requests.append(request.url)
                if "/detail/" in request.url
                else None,
            )
            page.on(
                "console",
                lambda message: console_errors.append(message.text)
                if message.type == "error"
                else None,
            )
            page.goto(url, wait_until="domcontentloaded")
            page.wait_for_timeout(500)
            _assert_bounded_preview(page, detail_requests, console_errors)
        finally:
            stop.set()
            if sampler is not None:
                sampler.join(timeout=2)
            sampler_stuck = sampler is not None and sampler.is_alive()
            context.close()
            if sampler_stuck:
                raise RuntimeError("RSS sampler did not stop")
    if sampler_errors:
        raise RuntimeError(f"RSS sampler failed: {sampler_errors}")
    if peaks["server"] <= 0 or peaks["browser"] <= 0:
        raise RuntimeError(f"RSS sampler recorded no process-tree evidence: {peaks}")
    return baselines, peaks, detail_requests


def _stop_server(server: subprocess.Popen) -> None:
    server.terminate()
    try:
        server.wait(timeout=5)
    except subprocess.TimeoutExpired:
        server.kill()
        server.wait(timeout=5)


def _trial(
    *,
    label: str,
    body_bytes: int,
    installed_python: Path,
    chrome_executable: Path,
    helper: Path,
    work_dir: Path,
) -> dict:
    root = work_dir / label / "bundles"
    profile = work_dir / label / "chrome-profile"
    run_id = f"browser-rss-{label}"
    root.parent.mkdir(parents=True)
    segment_id = _create_fixture(
        installed_python,
        helper,
        root,
        run_id,
        body_bytes,
        label,
    )
    server, ready = _start_server(installed_python, helper, root, run_id)
    try:
        url = f"http://127.0.0.1:{ready['port']}/?run_id={run_id}"
        baselines, peaks, detail_requests = _exercise_browser(
            url=url,
            profile=profile,
            chrome_executable=chrome_executable,
            server=server,
        )
        return {
            "body_bytes": body_bytes,
            "segment_id": segment_id,
            "server_peak_rss_kib": peaks["server"] // 1024,
            "browser_peak_rss_kib": peaks["browser"] // 1024,
            "server_baseline_rss_kib": baselines["server"] // 1024,
            "browser_baseline_rss_kib": baselines["browser"] // 1024,
            "server_growth_rss_kib": (
                max(0, peaks["server"] - baselines["server"]) // 1024
            ),
            "browser_growth_rss_kib": (
                max(0, peaks["browser"] - baselines["browser"]) // 1024
            ),
            "preview_bytes": 64 * 1024,
            "detail_request_count": len(detail_requests),
            "origins": ready["origins"],
        }
    finally:
        _stop_server(server)


def main(argv: List[str]) -> int:
    args = _parser().parse_args(argv)
    installed_python = Path(os.path.abspath(args.installed_python))
    if not installed_python.is_file():
        raise RuntimeError(f"installed Python does not exist: {installed_python}")
    chrome_executable = Path(args.chrome_executable).resolve(strict=True)
    work_dir = Path(args.work_dir).resolve()
    record = Path(args.record).resolve()
    _require_new_path(work_dir, "work directory")
    _require_new_path(record, "record")
    if args.small_bytes <= 0 or args.large_bytes <= args.small_bytes:
        raise RuntimeError("large-bytes must be greater than positive small-bytes")
    if args.rss_slack_kib < 0:
        raise RuntimeError("rss-slack-kib must be nonnegative")
    work_dir.mkdir(parents=True)
    record.parent.mkdir(parents=True, exist_ok=True)
    helper = work_dir / "installed_browser_rss_helper.py"
    helper.write_text(_HELPER_PROGRAM, encoding="utf-8")

    small = _trial(
        label="small",
        body_bytes=args.small_bytes,
        installed_python=installed_python,
        chrome_executable=chrome_executable,
        helper=helper,
        work_dir=work_dir,
    )
    large = _trial(
        label="large",
        body_bytes=args.large_bytes,
        installed_python=installed_python,
        chrome_executable=chrome_executable,
        helper=helper,
        work_dir=work_dir,
    )
    for key in ("server_growth_rss_kib", "browser_growth_rss_kib"):
        if large[key] > small[key] + args.rss_slack_kib:
            raise RuntimeError(
                f"{key} scales with retained body bytes: "
                f"small={small[key]} KiB, large={large[key]} KiB, "
                f"slack={args.rss_slack_kib} KiB"
            )
    chrome_version = subprocess.run(
        [str(chrome_executable), "--version"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    result = {
        "schema": "viewer-browser-rss-v2",
        "passed": True,
        "installed_python": str(installed_python),
        "chrome_executable": str(chrome_executable),
        "chrome_version": chrome_version,
        "rss_slack_kib": args.rss_slack_kib,
        "small": small,
        "large": large,
    }
    record.write_text(json.dumps(result, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
