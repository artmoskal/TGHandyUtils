#!/usr/bin/env python3
"""Hermetic fake CLI used by ai_workflow_tools tests.

Modes are selected with FAKE_CLI_MODE:
- envelope: print a claude-style JSON envelope to stdout.
- artifacts: create screenshot/session/page artifacts under FAKE_CLI_WORKSPACE, then print envelope.
- sleep: create a screenshot, sleep for FAKE_CLI_SLEEP_S, then print a marker.
- result_file: write the final message to --output-last-message or FAKE_CLI_RESULT_FILE.
- garbage: print non-JSON output.
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path


PNG_BYTES = b"\x89PNG\r\n\x1a\nfake-png\n"


def main() -> int:
    argv = sys.argv
    stdin_text = sys.stdin.read()
    mode = os.environ.get("FAKE_CLI_MODE", "envelope")
    result_text = os.environ.get("FAKE_CLI_RESULT", '{"ok": true}')
    workspace = Path(os.environ.get("FAKE_CLI_WORKSPACE", os.getcwd()))
    workspace.mkdir(parents=True, exist_ok=True)
    _record_invocation(argv, stdin_text)

    if mode == "envelope":
        _emit_envelope(result_text)
        return 0
    if mode == "artifacts":
        _write_artifacts(workspace)
        _emit_envelope(result_text)
        return 0
    if mode == "sleep":
        (workspace / "screenshot.png").write_bytes(PNG_BYTES)
        time.sleep(float(os.environ.get("FAKE_CLI_SLEEP_S", "5")))
        print("slept")
        return 0
    if mode == "result_file":
        result_file = _result_file_from_args(argv) or os.environ.get("FAKE_CLI_RESULT_FILE")
        if result_file is None:
            raise SystemExit("result_file mode requires --output-last-message or FAKE_CLI_RESULT_FILE")
        Path(result_file).write_text(result_text, encoding="utf-8")
        print("stdout fallback")
        return 0
    if mode == "garbage":
        print("not json at all")
        return 0
    raise SystemExit(f"unsupported FAKE_CLI_MODE: {mode}")


def _record_invocation(argv: list[str], stdin_text: str) -> None:
    record_path = os.environ.get("FAKE_CLI_RECORD")
    if not record_path:
        return
    Path(record_path).write_text(
        json.dumps({"argv": argv, "stdin": stdin_text}, sort_keys=True),
        encoding="utf-8",
    )


def _emit_envelope(result_text: str) -> None:
    envelope = {
        "result": result_text,
        "total_cost_usd": 0.123,
        "num_turns": 2,
        "duration_ms": 50,
        "usage": {
            "input_tokens": 11,
            "output_tokens": 7,
            "cache_read_input_tokens": 3,
            "cache_creation_input_tokens": 5,
        },
    }
    print(json.dumps(envelope, sort_keys=True))


def _write_artifacts(workspace: Path) -> None:
    (workspace / "screenshot.png").write_bytes(PNG_BYTES)
    (workspace / "session.md").write_text("session notes\n", encoding="utf-8")
    (workspace / "page-1.yml").write_text("url: https://example.test\n", encoding="utf-8")


def _result_file_from_args(argv: list[str]) -> str | None:
    for index, item in enumerate(argv):
        if item == "--output-last-message" and index + 1 < len(argv):
            return argv[index + 1]
    return None


if __name__ == "__main__":
    raise SystemExit(main())
