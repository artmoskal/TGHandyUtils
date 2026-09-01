"""Presentation of compact detail envelopes without opening referenced bodies."""

from __future__ import annotations

from collections.abc import Callable, Mapping
import html
import re
from typing import Any

from ai_workflow_engine.models import ObservationDetail
from ai_workflow_engine.observation_contract import ObservationDetailEnvelope
from ai_workflow_engine.observation_values import render_observation_body_text


DETAIL_BODY_DISPLAY_LIMIT = 64 * 1024
ObservationDisplayDetail = ObservationDetail | ObservationDetailEnvelope
DetailHrefFor = Callable[[ObservationDisplayDetail], Mapping[str, str]]


def detail_view(
    detail: ObservationDisplayDetail,
    href_for: DetailHrefFor | None = None,
) -> dict[str, Any]:
    body = _bounded_detail_body(_detail_body(detail))
    return {
        "id": detail.detail_id,
        "kind": detail.kind,
        "storage": _detail_storage(detail),
        "content_type": detail.content_type,
        "digest": _detail_sha256(detail),
        "summary": _detail_summary(detail, body),
        "body": body,
        "anchor": _dom_id("detail", detail.detail_id),
        "links": _detail_links(detail, href_for),
    }


def detail_row(
    detail: ObservationDisplayDetail,
    href_for: DetailHrefFor | None = None,
) -> str:
    body = _bounded_detail_body(_detail_body(detail))
    digest = f" digest={_detail_sha256(detail)}"
    detail_dom_id = _dom_id("detail", detail.detail_id)
    dialog_id = _dom_id("detail_dialog", detail.detail_id)
    raw_id = _dom_id("detail_raw", detail.detail_id)
    links = _detail_links(detail, href_for)
    actions = _detail_actions(
        detail,
        links=links,
        dialog_id=dialog_id,
        raw_id=raw_id,
    )
    return (
        f'<details class="observation-detail" id="{html.escape(detail_dom_id, quote=True)}">'
        f"<summary><code>{html.escape(detail.detail_id)}</code> {html.escape(detail.kind)} "
        f"({html.escape(_detail_storage(detail))}){html.escape(digest)}</summary>"
        f'<div class="detail-actions">{actions}'
        '<span class="copy-status" aria-live="polite"></span>'
        "</div>"
        f"<pre>{html.escape(body)}</pre>"
        f'<dialog class="observation-dialog" id="{html.escape(dialog_id, quote=True)}">'
        '<div class="dialog-header">'
        f'<div class="dialog-title">{html.escape(detail.kind)} · <code>{html.escape(detail.detail_id)}</code></div>'
        '<button type="button" data-close-dialog>Close</button>'
        "</div>"
        '<div class="dialog-body">'
        f'<pre id="{html.escape(raw_id, quote=True)}">{html.escape(body)}</pre>'
        "</div>"
        "</dialog>"
        "</details>"
    )


def _detail_actions(
    detail: ObservationDisplayDetail,
    *,
    links: dict[str, str],
    dialog_id: str,
    raw_id: str,
) -> str:
    actions: list[str] = []
    if detail.body.kind == "body_ref" and links.get("preview"):
        actions.append(
            f'<button type="button" data-load-detail="{html.escape(links["preview"], quote=True)}" '
            f'data-preview-target="{html.escape(raw_id, quote=True)}" '
            f'data-dialog-target="{html.escape(dialog_id, quote=True)}">Open bounded preview</button>'
        )
        actions.append(
            f'<button type="button" data-copy-target="{html.escape(raw_id, quote=True)}">Copy preview</button>'
        )
    elif detail.body.kind == "body_ref" and links.get("page"):
        actions.append(
            f'<a href="{html.escape(links["page"], quote=True)}">Open bounded preview</a>'
        )
    else:
        actions.extend(
            (
                f'<button type="button" data-open-dialog="{html.escape(dialog_id, quote=True)}">Expand</button>',
                f'<button type="button" data-copy-target="{html.escape(raw_id, quote=True)}">Copy raw</button>',
            )
        )
    if links.get("download"):
        actions.append(
            f'<a href="{html.escape(links["download"], quote=True)}" download>Download exact</a>'
        )
    return "".join(actions)


def _detail_links(
    detail: ObservationDisplayDetail,
    href_for: DetailHrefFor | None,
) -> dict[str, str]:
    if href_for is None:
        return {}
    links = href_for(detail)
    return {
        key: str(value)
        for key, value in links.items()
        if key in {"preview", "download", "page"} and str(value)
    }


def _bounded_detail_body(body: str) -> str:
    if len(body) <= DETAIL_BODY_DISPLAY_LIMIT:
        return body
    head = DETAIL_BODY_DISPLAY_LIMIT // 2
    tail = DETAIL_BODY_DISPLAY_LIMIT - head
    dropped = len(body) - head - tail
    return (
        f"{body[:head]}\n"
        f"...[{dropped} chars truncated in this view — full record in the bundle]...\n"
        f"{body[-tail:]}"
    )


def _detail_body(detail: ObservationDisplayDetail) -> str:
    if detail.body.kind != "body_ref":
        return render_observation_body_text(detail.body)
    return (
        f"Body stored out of line ({detail.body.byte_length} bytes, "
        f"sha256 {detail.body.sha256}). Open it through the bounded detail reader."
    )


def _detail_storage(detail: ObservationDisplayDetail) -> str:
    if detail.body.kind in {"json", "text"}:
        return f"logical_{detail.body.kind}"
    return detail.body.kind


def _detail_sha256(detail: ObservationDisplayDetail) -> str:
    return detail.digest if isinstance(detail, ObservationDetail) else detail.body.sha256


def _detail_summary(detail: ObservationDisplayDetail, body: str) -> str:
    if detail.body.kind in {"json", "inline_json"}:
        highlights = _json_highlights(detail.body.value)
        if highlights:
            return "; ".join(highlights)
    return _truncate(_single_line(body), 420)


_INTERESTING_DETAIL_KEYS = (
    "validation_error", "error", "decision", "status", "card_kind", "count",
    "strategy", "image_role", "rationale", "accepted", "repair_strategy", "issues",
    "guidance", "fallback_used", "fallback_reason", "cleaned_content", "source_content",
    "question", "answer", "text",
)


def _json_highlights(value: Any) -> list[str]:
    highlights: list[str] = []
    _collect_highlights(value, highlights, depth=0)
    if highlights:
        return list(dict.fromkeys(highlights))
    return _shape_summary(value)


def _collect_highlights(item: Any, highlights: list[str], *, depth: int) -> None:
    if len(highlights) >= 8 or depth > 3:
        return
    if isinstance(item, dict):
        _collect_mapping_highlights(item, highlights, depth=depth)
    elif isinstance(item, list):
        for child in item[:4]:
            _collect_highlights(child, highlights, depth=depth + 1)
            if len(highlights) >= 8:
                break


def _collect_mapping_highlights(
    item: dict[Any, Any],
    highlights: list[str],
    *,
    depth: int,
) -> None:
    for key in _INTERESTING_DETAIL_KEYS:
        if key not in item or len(highlights) >= 8:
            continue
        rendered = _compact_value(item[key])
        if rendered:
            highlights.append(f"{key}: {rendered}")
    for child in item.values():
        if len(highlights) >= 8:
            break
        if isinstance(child, (dict, list)):
            _collect_highlights(child, highlights, depth=depth + 1)


def _shape_summary(value: Any) -> list[str]:
    if isinstance(value, dict):
        keys = ", ".join(list(value)[:8])
        return [f"keys: {keys}"] if keys else []
    if isinstance(value, list):
        return [f"{len(value)} item{'s' if len(value) != 1 else ''}"]
    return []


def _compact_value(value: Any) -> str:
    if value in (None, "", [], {}):
        return ""
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, str):
        return _truncate(_single_line(value), 130)
    if isinstance(value, list):
        return _compact_list(value)
    if isinstance(value, dict):
        return _compact_mapping(value)
    return _truncate(_single_line(str(value)), 130)


def _compact_list(value: list[Any]) -> str:
    primitive = [item for item in value if not isinstance(item, (dict, list))]
    if primitive:
        return _truncate(", ".join(_single_line(str(item)) for item in primitive[:4]), 130)
    return f"{len(value)} item{'s' if len(value) != 1 else ''}"


def _compact_mapping(value: dict[Any, Any]) -> str:
    for key in _INTERESTING_DETAIL_KEYS:
        if key not in value:
            continue
        rendered = _compact_value(value[key])
        if rendered:
            return rendered
    return f"keys {', '.join(list(value)[:4])}"


def _single_line(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def _truncate(value: str, limit: int) -> str:
    if len(value) <= limit:
        return value
    return value[: max(0, limit - 1)].rstrip() + "…"


def _dom_id(prefix: str, value: str) -> str:
    token = re.sub(r"[^0-9A-Za-z_-]+", "_", value or "unknown")
    return f"obs-{prefix}-{token}"


__all__ = ["DETAIL_BODY_DISPLAY_LIMIT", "DetailHrefFor", "detail_row", "detail_view"]
