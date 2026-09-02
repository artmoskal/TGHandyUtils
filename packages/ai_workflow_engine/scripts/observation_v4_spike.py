#!/usr/bin/env python3
"""Deterministic sizing spike for the observation bundle v4 body representation."""

from __future__ import annotations

import argparse
from collections import Counter
import gzip
import hashlib
import json
from pathlib import Path
import random
import resource
import sys
import tempfile
import time
from typing import Any, Iterable


SEED = "observation-v4-format-spike-v1"
RECORD_COUNT = 1_524
THRESHOLDS = (1_024, 4_096, 16_384, 65_536)
CODECS = ("identity", "gzip")
_WORDS = (
    "accepted", "artifact", "browser", "capability", "checkpoint", "context",
    "correlation", "criterion", "detail", "evidence", "executor", "failure",
    "history", "identity", "input", "invocation", "journal", "lineage", "model",
    "node", "observation", "output", "planner", "provider", "request", "response",
    "result", "retry", "route", "scenario", "segment", "status", "step", "trace",
    "usage", "validation", "value", "workflow",
)


def _canonical_json(value: Any) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")


def _digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _payload(length: int, token: str) -> str:
    if length <= 0:
        return ""
    seed = int.from_bytes(hashlib.sha256(f"{SEED}|{token}".encode()).digest()[:8], "big")
    rng = random.Random(seed)
    pieces: list[str] = []
    size = 0
    while size < length:
        word = _WORDS[rng.randrange(len(_WORDS))]
        suffix = f"-{rng.randrange(10_000):04d}" if rng.random() < 0.22 else ""
        piece = f"{word}{suffix} "
        pieces.append(piece)
        size += len(piece)
    return "".join(pieces)[:length]


def _body_records() -> list[tuple[str, Any, bytes, str]]:
    records: list[tuple[str, Any, bytes, str]] = []
    boundary_sizes = sorted({
        max(1, threshold - 1) for threshold in THRESHOLDS
    } | set(THRESHOLDS) | {threshold + 1 for threshold in THRESHOLDS})

    for index in range(RECORD_COUNT):
        if index < 500:
            value = {
                "kind": "small_unique",
                "index": index,
                "message": _payload(80 + index % 240, f"u{index:04d}-"),
            }
            category = "small_unique"
            body_kind = "json"
        elif index < 800:
            template = index % 12
            value = {
                "kind": "small_repeated",
                "template": template,
                "message": _payload(1_500 + template * 31, f"r{template:02d}-"),
            }
            category = "small_repeated"
            body_kind = "json"
        elif index < 1_100:
            size = boundary_sizes[(index - 800) % len(boundary_sizes)]
            template = (index - 800) % 6
            value = {
                "kind": "threshold",
                "template": template,
                "payload": _payload(size, f"b{template}-"),
            }
            category = f"threshold_{size}"
            body_kind = "json"
        elif index < 1_350:
            template = (index - 1_100) % 20
            value = {
                "kind": "large_repeated",
                "template": template,
                "payload": _payload(128_000 + template * 257, f"L{template:02d}-"),
            }
            category = "large_repeated"
            body_kind = "json"
        elif index < 1_450:
            value = {
                "kind": "large_unique",
                "index": index,
                "payload": _payload(96_000 + index * 13, f"U{index:04d}-"),
            }
            category = "large_unique"
            body_kind = "json"
        else:
            value = _payload(6_000 + index * 7, f"text-{index:04d}-")
            category = "text_unique"
            body_kind = "text"

        encoded = _canonical_json(value) if body_kind == "json" else value.encode("utf-8")
        records.append((body_kind, value, encoded, category))
    return records


def qualification_body_records() -> list[tuple[str, Any, bytes, str]]:
    """Return the deterministic logical bodies shared by spike and release qualification."""

    return _body_records()


def _trace_records() -> list[dict[str, Any]]:
    return [
        {
            "event_id": f"trace-{index:04d}",
            "sequence": index * 3 + 1,
            "phase": "tool:result",
            "node": f"node-{index % 17}",
            "detail_refs": [f"detail-{index:04d}"],
            "metadata": {"attempt": index % 4 + 1, "route": f"route-{index % 9}"},
        }
        for index in range(RECORD_COUNT)
    ]


def _usage_records() -> list[dict[str, Any]]:
    return [
        {
            "event_id": f"usage-{index:04d}",
            "sequence": index * 3 + 3,
            "provider": "generated",
            "model": f"model-{index % 3}",
            "input_tokens": 100 + index % 91,
            "output_tokens": 20 + index % 23,
        }
        for index in range(RECORD_COUNT)
    ]


def _encoded(data: bytes, codec: str) -> bytes:
    if codec == "identity":
        return data
    if codec == "gzip":
        return gzip.compress(data, compresslevel=6, mtime=0)
    raise ValueError(f"unsupported codec: {codec}")


def _envelope_bytes(
    *,
    index: int,
    body_kind: str,
    value: Any,
    body: bytes,
    threshold: int,
    codec: str,
) -> bytes:
    sha256 = _digest(body)
    common = {
        "detail_id": f"detail-{index:04d}",
        "event_id": f"trace-{index:04d}",
        "sequence": index * 3 + 2,
        "kind": "tool_result",
        "content_type": "application/json" if body_kind == "json" else "text/plain",
    }
    if len(body) <= threshold:
        common["body"] = {
            "kind": f"inline_{body_kind}",
            "value": value,
            "sha256": sha256,
            "byte_length": len(body),
        }
    else:
        common["body"] = {
            "kind": "body_ref",
            "sha256": sha256,
            "byte_length": len(body),
            "codec": codec,
        }
    return _canonical_json(common)


def _measure_parse(records: Iterable[dict[str, Any]]) -> dict[str, Any]:
    encoded = b"\n".join(_canonical_json(record) for record in records) + b"\n"
    before_rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    started = time.perf_counter()
    parsed = [json.loads(line) for line in encoded.splitlines() if line]
    elapsed = time.perf_counter() - started
    peak_rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return {
        "bytes": len(encoded),
        "records": len(parsed),
        "parse_seconds": round(elapsed, 6),
        "peak_rss_units": peak_rss,
        "peak_rss_delta_units": max(0, peak_rss - before_rss),
        "rss_unit": "bytes" if sys.platform == "darwin" else "kibibytes",
    }


def _scenario(
    records: list[tuple[str, Any, bytes, str]],
    *,
    threshold: int,
    codec: str,
) -> dict[str, Any]:
    objects: dict[str, bytes] = {}
    envelopes: list[bytes] = []
    referenced_occurrences = 0
    referenced_logical_bytes = 0
    repeated_inline_bytes = 0
    inline_seen: Counter[str] = Counter()
    referenced_seen: Counter[str] = Counter()

    write_started = time.perf_counter()
    for index, (body_kind, value, body, _category) in enumerate(records):
        sha256 = _digest(body)
        envelopes.append(
            _envelope_bytes(
                index=index,
                body_kind=body_kind,
                value=value,
                body=body,
                threshold=threshold,
                codec=codec,
            )
        )
        if len(body) <= threshold:
            if inline_seen[sha256]:
                repeated_inline_bytes += len(body)
            inline_seen[sha256] += 1
            continue
        referenced_occurrences += 1
        referenced_logical_bytes += len(body)
        referenced_seen[sha256] += 1
        objects.setdefault(sha256, _encoded(body, codec))
    write_seconds = time.perf_counter() - write_started

    with tempfile.TemporaryDirectory(prefix="observation-v4-spike-") as temp_dir:
        root = Path(temp_dir)
        object_root = root / "values"
        object_root.mkdir()
        details_path = root / "details.jsonl"
        disk_started = time.perf_counter()
        details_path.write_bytes(b"\n".join(envelopes) + b"\n")
        for sha256, data in objects.items():
            (object_root / sha256).write_bytes(data)
        disk_write_seconds = time.perf_counter() - disk_started
        read_started = time.perf_counter()
        read_bytes = len(details_path.read_bytes())
        read_bytes += sum(len(path.read_bytes()) for path in object_root.iterdir())
        read_seconds = time.perf_counter() - read_started

    reused_occurrences = sum(count - 1 for count in referenced_seen.values() if count > 1)
    reused_logical_bytes = sum(
        (count - 1) * next(len(record[2]) for record in records if _digest(record[2]) == sha256)
        for sha256, count in referenced_seen.items()
        if count > 1
    )
    envelope_bytes = sum(len(item) + 1 for item in envelopes)
    object_bytes = sum(len(item) for item in objects.values())
    return {
        "threshold_bytes": threshold,
        "codec": codec,
        "detail_count": len(records),
        "envelope_bytes": envelope_bytes,
        "object_count": len(objects),
        "object_bytes": object_bytes,
        "physical_bytes": envelope_bytes + object_bytes,
        "read_bytes": read_bytes,
        "referenced_occurrences": referenced_occurrences,
        "referenced_logical_bytes": referenced_logical_bytes,
        "reused_occurrences": reused_occurrences,
        "reuse_hit_rate": round(
            reused_occurrences / referenced_occurrences if referenced_occurrences else 0.0,
            6,
        ),
        "reused_logical_bytes": reused_logical_bytes,
        "repeated_inline_bytes": repeated_inline_bytes,
        "assembly_seconds": round(write_seconds, 6),
        "disk_write_seconds": round(disk_write_seconds, 6),
        "disk_read_seconds": round(read_seconds, 6),
    }


def build_report() -> dict[str, Any]:
    bodies = _body_records()
    trace_records = _trace_records()
    usage_records = _usage_records()
    body_sizes = [len(record[2]) for record in bodies]
    body_digests = Counter(_digest(record[2]) for record in bodies)
    category_counts = Counter(record[3] for record in bodies)
    category_bytes = Counter()
    for _kind, _value, body, category in bodies:
        category_bytes[category] += len(body)

    details_for_parse = [
        json.loads(
            _envelope_bytes(
                index=index,
                body_kind=kind,
                value=value,
                body=body,
                threshold=4_096,
                codec="gzip",
            )
        )
        for index, (kind, value, body, _category) in enumerate(bodies)
    ]
    source_sha256 = _digest(Path(__file__).read_bytes())
    scenarios = [
        _scenario(bodies, threshold=threshold, codec=codec)
        for threshold in THRESHOLDS
        for codec in CODECS
    ]
    stable_scenarios = [
        {
            key: value
            for key, value in scenario.items()
            if not key.endswith("_seconds")
        }
        for scenario in scenarios
    ]
    semantic_metrics = {
        "manifest": _digest(
            _canonical_json(
                {
                    "seed": SEED,
                    "record_count": len(bodies),
                    "body_digests": [_digest(record[2]) for record in bodies],
                    "categories": [record[3] for record in bodies],
                }
            )
        ),
        "scenarios": stable_scenarios,
    }
    return {
        "schema": "observation-v4-spike-v1",
        "source_sha256": source_sha256,
        "seed": SEED,
        "record_count": len(bodies),
        "generator_manifest_sha256": semantic_metrics["manifest"],
        "semantic_metrics_sha256": _digest(_canonical_json(semantic_metrics)),
        "logical_body_bytes": sum(body_sizes),
        "largest_body_bytes": max(body_sizes),
        "unique_complete_bodies": len(body_digests),
        "duplicate_complete_body_occurrences": sum(
            count - 1 for count in body_digests.values() if count > 1
        ),
        "body_size_buckets": {
            "lte_1k": sum(size <= 1_024 for size in body_sizes),
            "1k_to_4k": sum(1_024 < size <= 4_096 for size in body_sizes),
            "4k_to_16k": sum(4_096 < size <= 16_384 for size in body_sizes),
            "16k_to_64k": sum(16_384 < size <= 65_536 for size in body_sizes),
            "gt_64k": sum(size > 65_536 for size in body_sizes),
        },
        "categories": {
            key: {"records": category_counts[key], "logical_bytes": category_bytes[key]}
            for key in sorted(category_counts)
        },
        "stream_parse": {
            "trace": _measure_parse(trace_records),
            "detail_envelopes": _measure_parse(details_for_parse),
            "usage": _measure_parse(usage_records),
            "combined": _measure_parse([*trace_records, *details_for_parse, *usage_records]),
        },
        "scenarios": scenarios,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = build_report()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    print(args.output)
    print(report["generator_manifest_sha256"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
