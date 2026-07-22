"""B2-B4 red battery: conversation mode, continuity/idempotency, evidence + validation.

Secured-consumer iteration (canonical plan: ai-llm-infra
docs/_discussion/2026-07-21-anki-real-consumer-qualification-plan.md). Target contract for
the ChatGPT-browser IMAGE adapter (`ChatGptBrowserImageGenerator`):

B2 (explicit conversation mode — force-fresh is deleted, no alias):
- config value `WORKFLOW_CHATGPT_BROWSER_CONVERSATION_MODE` with closed values reuse|fresh;
- reuse: payload carries `conversation_mode=reuse` + `conversation_name`, NO `no_cache`
  (server-side reuse already forces no-cache generation);
- fresh: payload carries `conversation_mode=fresh` + `no_cache=true`, NO conversation name;
- any other mode value fails loudly BEFORE HTTP.

B3 (identity): provider-neutral `ImageGenerationRequest.continuity_key` /
`.idempotency_key` map to browser-API `conversation_name` / `idempotency_key`; 409 is a
loud conflict with exactly one request and never a silently minted replacement key.

B4 (evidence + validation): safe provider evidence (mode, generation, reused, rotated,
reference count, normalized V3 freshness schema/strategy/source/state/owned/count) is
retained on `GeneratedImage`; the raw signed `source_url` is NEVER recorded anywhere
(usage events included); base64 is decoded with validation; declared MIME / byte count /
magic signature must match the configured format; reuse without valid freshness evidence
is rejected BEFORE the file is written; every rejected image leaves no partial file.

These tests were captured RED before implementation and now permanently fence the
secured-consumer contract.
"""

from __future__ import annotations

import asyncio
import base64
import os

import pytest

pytestmark = pytest.mark.unit

# 1x1 transparent PNG plus minimal JPEG/WebP signature fixtures. The adapter contract
# validates transport integrity and the declared media envelope; full image decoding stays
# downstream of this provider-neutral transport boundary.
_PNG_BYTES = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg=="
)
_PNG_B64 = base64.b64encode(_PNG_BYTES).decode()
_JPEG_BYTES = b"\xff\xd8\xff\xe0" + b"0" * 32 + b"\xff\xd9"
_JPEG_B64 = base64.b64encode(_JPEG_BYTES).decode()
_WEBP_BYTES = b"RIFF" + (28).to_bytes(4, "little") + b"WEBPVP8 " + b"0" * 20
_WEBP_B64 = base64.b64encode(_WEBP_BYTES).decode()

_FRESHNESS = {
    "schema": 4,
    "strategy": "resultFreshnessFenceV3",
    "source": "captured",
    "state": "result_observed",
    "anchor_owned": True,
    "result_owned": True,
    "result_scope_count": 1,
    "candidate_count": 1,
    "reason": None,
}


def _reply(**overrides):
    data = {
        "status": "completed",
        "image_data_url": f"data:image/png;base64,{_PNG_B64}",
        "mime": "image/png",
        "bytes": len(_PNG_BYTES),
        "reference_images_used": 0,
        "conversation_mode": "reuse",
        "conversation_name": "anki-0123456789abcdef0123456789abcdef",
        "generation": 1,
        "reused": True,
        "rotated": False,
        "freshness": dict(_FRESHNESS),
    }
    data.update(overrides)
    return data


def _fresh_reply(**overrides):
    data = _reply(conversation_mode="fresh")
    for name in ("conversation_name", "generation", "reused", "rotated", "freshness"):
        data.pop(name)
    data.update(overrides)
    return data


class _RecordingPost:
    def __init__(self, reply: dict | None = None, status_code: int = 200):
        self.calls = []
        self.reply = reply if reply is not None else _reply()
        self.status_code = status_code

    def __call__(self, url, json=None, headers=None, timeout=None):
        self.calls.append({"url": url, "json": json or {}, "headers": headers or {}})
        reply, status_code = self.reply, self.status_code

        class _Response:
            def __init__(self):
                self.status_code = status_code
                self.text = str(reply)

            def json(self):
                return reply

        return _Response()


class _SequencedPost:
    def __init__(self, replies):
        self.replies = list(replies)
        self.calls = []

    def __call__(self, url, json=None, headers=None, timeout=None):
        self.calls.append({"url": url, "json": dict(json or {}), "headers": dict(headers or {})})
        item = self.replies.pop(0)
        if isinstance(item, BaseException):
            raise item
        status_code, reply, response_headers = item

        class _Response:
            text = str(reply)

            def __init__(self):
                self.status_code = status_code
                self.headers = response_headers

            def json(self):
                return reply

        return _Response()


class _Config:
    WORKFLOW_CHATGPT_BROWSER_URL = "http://127.0.0.1:8010"  # loopback-open: no token noise
    WORKFLOW_CHATGPT_BROWSER_TIMEOUT_SECONDS = 5
    WORKFLOW_CHATGPT_BROWSER_RATE_WAIT_MAX_SECONDS = 1
    WORKFLOW_CHATGPT_BROWSER_CONVERSATION_MODE = "reuse"
    WORKFLOW_USAGE_TRACKING_ENABLED = False


def _generator(post, config=None):
    from ai_workflow_tools.media.image_generation import ChatGptBrowserImageGenerator

    return ChatGptBrowserImageGenerator(config or _Config(), http_post=post)


def _request(tmp_path, **overrides):
    from ai_workflow_tools.media.image_models import ImageGenerationRequest

    values = dict(
        prompt="a cat",
        model="chatgpt",
        output_dir=str(tmp_path),
        output_format="png",
        continuity_key="anki-0123456789abcdef0123456789abcdef",
        idempotency_key="run-77:image:0",
    )
    values.update(overrides)
    return ImageGenerationRequest(**values)


def _generate(post, tmp_path, config=None, **request_overrides):
    generator = _generator(post, config)
    return asyncio.run(generator.generate(_request(tmp_path, **request_overrides)))


# ------------------------------------------------ B2: explicit conversation-mode matrix


def test_reuse_mode_sends_conversation_reuse_and_never_no_cache(tmp_path):
    post = _RecordingPost()
    _generate(post, tmp_path)
    payload = post.calls[0]["json"]
    assert payload.get("conversation_mode") == "reuse"
    assert payload.get("conversation_name") == "anki-0123456789abcdef0123456789abcdef"
    assert "no_cache" not in payload, (
        "server-side reuse already forces no-cache generation — sending it is the old "
        "force-fresh semantics leaking back"
    )


def test_fresh_mode_sends_fresh_with_no_cache_and_no_continuity(tmp_path):
    class _FreshConfig(_Config):
        WORKFLOW_CHATGPT_BROWSER_CONVERSATION_MODE = "fresh"

    post = _RecordingPost(
        reply=_fresh_reply()
    )
    _generate(post, tmp_path, config=_FreshConfig(), continuity_key=None)
    payload = post.calls[0]["json"]
    assert payload.get("conversation_mode") == "fresh"
    assert payload.get("no_cache") is True
    assert "conversation_name" not in payload


def test_invalid_conversation_mode_fails_before_any_request(tmp_path):
    from ai_workflow_tools.media.image_generation import ImageGenerationError

    class _BadConfig(_Config):
        WORKFLOW_CHATGPT_BROWSER_CONVERSATION_MODE = "sometimes"

    post = _RecordingPost()
    with pytest.raises(ImageGenerationError, match="reuse|fresh"):
        _generate(post, tmp_path, config=_BadConfig())
    assert not post.calls


def test_reuse_mode_requires_continuity_before_any_request(tmp_path):
    from ai_workflow_tools.media.image_generation import ImageGenerationError

    post = _RecordingPost()
    with pytest.raises(ImageGenerationError, match="continuity|conversation"):
        _generate(post, tmp_path, continuity_key=None)
    assert not post.calls


# --------------------------------------------- B3: identity mapping + conflict handling


def test_request_identity_fields_map_to_browser_api_names(tmp_path):
    post = _RecordingPost()
    _generate(post, tmp_path)
    payload = post.calls[0]["json"]
    assert payload.get("conversation_name") == "anki-0123456789abcdef0123456789abcdef"
    assert payload.get("idempotency_key") == "run-77:image:0"


def test_browser_image_adapter_requires_idempotency_before_any_request(tmp_path):
    from ai_workflow_tools.media.image_generation import ImageGenerationError

    post = _RecordingPost()
    with pytest.raises(ImageGenerationError, match="idempotency"):
        _generate(post, tmp_path, idempotency_key=None)
    assert not post.calls


def test_ten_operations_keep_one_continuity_name_and_distinct_operation_keys(tmp_path):
    post = _RecordingPost()
    for slot in range(10):
        _generate(post, tmp_path, idempotency_key=f"run-77:image:{slot}")

    assert {call["json"]["conversation_name"] for call in post.calls} == {
        "anki-0123456789abcdef0123456789abcdef"
    }
    assert [call["json"]["idempotency_key"] for call in post.calls] == [
        f"run-77:image:{slot}" for slot in range(10)
    ]


def test_429_retry_reuses_byte_equal_payload_and_same_idempotency_key(tmp_path):
    post = _SequencedPost(
        [
            (429, {"error": "slow down", "retry_after": 1}, {"Retry-After": "1"}),
            (200, _reply(), {}),
        ]
    )
    sleeps = []
    generator = _generator(post)
    generator._sleeper = lambda seconds: sleeps.append(seconds)

    asyncio.run(generator.generate(_request(tmp_path)))

    assert len(post.calls) == 2
    assert post.calls[0]["json"] == post.calls[1]["json"]
    assert post.calls[0]["json"]["idempotency_key"] == "run-77:image:0"
    assert sleeps == [1.0]


@pytest.mark.parametrize("budget", [-1, float("nan"), float("inf")])
def test_image_adapter_rejects_unbounded_wait_budgets_before_http(tmp_path, budget):
    from ai_workflow_tools.media.image_generation import (
        ChatGptBrowserImageGenerator,
        ImageGenerationError,
    )

    class _InvalidBudgetConfig(_Config):
        WORKFLOW_CHATGPT_BROWSER_RATE_WAIT_MAX_SECONDS = budget

    post = _RecordingPost()
    generator = ChatGptBrowserImageGenerator(_InvalidBudgetConfig(), http_post=post)
    with pytest.raises(ImageGenerationError, match="finite and non-negative"):
        asyncio.run(generator.generate(_request(tmp_path)))
    assert not post.calls


def test_uncertain_transport_timeout_is_never_redispatched(tmp_path):
    from ai_workflow_tools.media.image_generation import ImageGenerationError

    post = _SequencedPost([TimeoutError("outcome unknown")])
    with pytest.raises(ImageGenerationError, match="outcome unknown"):
        _generate(post, tmp_path)
    assert len(post.calls) == 1, "an uncertain paid send must never be repeated automatically"


def test_409_conflict_is_loud_with_exactly_one_request(tmp_path):
    # KEEP-GREEN: today's generic >=400 branch already raises loudly on the first
    # response with no retry — the idempotency contract builds on this staying true.
    from ai_workflow_tools.media.image_generation import ImageGenerationError

    post = _RecordingPost(reply={"error": "idempotency conflict"}, status_code=409)
    with pytest.raises(ImageGenerationError, match="409"):
        _generate(post, tmp_path)
    assert len(post.calls) == 1, "409 must never mint a replacement key or retry"


# ------------------------------------------------- B4: evidence retained, privacy kept


def test_safe_reuse_evidence_is_retained_on_the_generated_image(tmp_path):
    post = _RecordingPost()
    image = _generate(post, tmp_path)
    evidence = image.generation_evidence
    assert evidence is not None
    assert evidence.conversation_mode == "reuse"
    assert evidence.generation == 1
    assert evidence.reused is True and evidence.rotated is False
    assert evidence.freshness.model_dump() == _FRESHNESS, (
        "the normalized V3 freshness evidence must ride the artifact for audit"
    )


def test_raw_source_url_is_never_recorded_anywhere(tmp_path, monkeypatch):
    from ai_workflow_tools.media import image_generation as module

    events = []
    monkeypatch.setattr(module, "record_usage_event", lambda event: events.append(event))
    post = _RecordingPost(reply=_reply(source_url="https://chatgpt.example/signed/abc123"))
    image = _generate(post, tmp_path)

    assert events, "the notional usage event must still be recorded"
    for event in events:
        blob = event.model_dump_json()
        assert "chatgpt.example/signed" not in blob, (
            "the raw signed source URL is private provider data — it must not be "
            "retained in usage metadata"
        )
        assert "source_url" not in (event.metadata or {}), (
            "the source_url key itself must leave the usage metadata contract"
        )
    assert "chatgpt.example/signed" not in image.model_dump_json()


def test_non_validating_base64_acceptance_is_rejected(tmp_path):
    """Today's decode silently DROPS non-alphabet characters and accepts the payload
    (verified: the sample decodes without error under validate=False and is rejected
    under validate=True) — strict validating decode must refuse it loudly."""
    from ai_workflow_tools.media.image_generation import ImageGenerationError

    corrupted = _PNG_B64[:20] + "!!" + _PNG_B64[20:]
    post = _RecordingPost(reply=_reply(image_data_url=f"data:image/png;base64,{corrupted}"))
    with pytest.raises(ImageGenerationError):
        _generate(post, tmp_path)
    assert not os.listdir(tmp_path), "a rejected image must leave no partial file"


def test_magic_signature_mismatch_is_rejected_with_cleanup(tmp_path):
    from ai_workflow_tools.media.image_generation import ImageGenerationError

    post = _RecordingPost(
        reply=_reply(
            image_data_url=f"data:image/png;base64,{_JPEG_B64}",
            bytes=len(base64.b64decode(_JPEG_B64)),
        )
    )
    with pytest.raises(ImageGenerationError, match="signature|magic|format"):
        _generate(post, tmp_path)
    assert not os.listdir(tmp_path)


def test_byte_count_mismatch_is_rejected_with_cleanup(tmp_path):
    from ai_workflow_tools.media.image_generation import ImageGenerationError

    post = _RecordingPost(reply=_reply(bytes=len(_PNG_BYTES) + 999))
    with pytest.raises(ImageGenerationError, match="byte"):
        _generate(post, tmp_path)
    assert not os.listdir(tmp_path)


def test_reuse_without_valid_freshness_is_rejected_before_writing(tmp_path):
    from ai_workflow_tools.media.image_generation import ImageGenerationError

    post = _RecordingPost(reply=_reply(freshness=None))
    with pytest.raises(ImageGenerationError, match="freshness"):
        _generate(post, tmp_path)
    assert not os.listdir(tmp_path)


def test_reuse_response_from_a_different_conversation_is_rejected(tmp_path):
    from ai_workflow_tools.media.image_generation import ImageGenerationError

    post = _RecordingPost(reply=_reply(conversation_name="anki-other-conversation"))
    with pytest.raises(ImageGenerationError, match="conversation identity"):
        _generate(post, tmp_path)
    assert not os.listdir(tmp_path)


@pytest.mark.parametrize(
    "freshness",
    [
        {**_FRESHNESS, "strategy": "resultFreshnessFenceV2"},
        {**_FRESHNESS, "result_scope_count": 0},
        {**_FRESHNESS, "candidate_count": True},
        {**_FRESHNESS, "private_url": "https://chatgpt.example/private"},
    ],
)
def test_reuse_freshness_contract_is_closed_and_range_checked(tmp_path, freshness):
    from ai_workflow_tools.media.image_generation import ImageGenerationError

    post = _RecordingPost(reply=_reply(freshness=freshness))
    with pytest.raises(ImageGenerationError, match="freshness"):
        _generate(post, tmp_path)
    assert not os.listdir(tmp_path)


def test_data_url_mime_must_match_declared_mime(tmp_path):
    from ai_workflow_tools.media.image_generation import ImageGenerationError

    post = _RecordingPost(reply=_reply(mime="image/jpeg"))
    with pytest.raises(ImageGenerationError, match="MIME|mime"):
        _generate(post, tmp_path)
    assert not os.listdir(tmp_path)


def test_declared_mime_must_match_requested_output_format(tmp_path):
    from ai_workflow_tools.media.image_generation import ImageGenerationError

    post = _RecordingPost(
        reply=_reply(
            image_data_url=f"data:image/jpeg;base64,{_JPEG_B64}",
            mime="image/jpeg",
            bytes=len(base64.b64decode(_JPEG_B64)),
        )
    )
    with pytest.raises(ImageGenerationError, match="format|MIME|mime"):
        _generate(post, tmp_path, output_format="png")
    assert not os.listdir(tmp_path)


def test_truncated_but_valid_base64_is_rejected(tmp_path):
    from ai_workflow_tools.media.image_generation import ImageGenerationError

    truncated = base64.b64encode(_PNG_BYTES[:6]).decode()
    post = _RecordingPost(
        reply=_reply(
            image_data_url=f"data:image/png;base64,{truncated}",
            bytes=6,
        )
    )
    with pytest.raises(ImageGenerationError, match="signature|magic|format"):
        _generate(post, tmp_path)
    assert not os.listdir(tmp_path)


def test_webp_envelope_and_signature_are_supported(tmp_path):
    post = _RecordingPost(
        reply=_fresh_reply(
            image_data_url=f"data:image/webp;base64,{_WEBP_B64}",
            mime="image/webp",
            bytes=len(_WEBP_BYTES),
        )
    )

    class _FreshConfig(_Config):
        WORKFLOW_CHATGPT_BROWSER_CONVERSATION_MODE = "fresh"

    image = _generate(
        post,
        tmp_path,
        config=_FreshConfig(),
        output_format="webp",
        continuity_key=None,
    )
    assert open(image.path, "rb").read() == _WEBP_BYTES
    assert image.generation_evidence.conversation_mode == "fresh"


def test_jpeg_envelope_and_signature_are_supported(tmp_path):
    post = _RecordingPost(
        reply=_fresh_reply(
            image_data_url=f"data:image/jpeg;base64,{_JPEG_B64}",
            mime="image/jpeg",
            bytes=len(_JPEG_BYTES),
        )
    )

    class _FreshConfig(_Config):
        WORKFLOW_CHATGPT_BROWSER_CONVERSATION_MODE = "fresh"

    image = _generate(
        post,
        tmp_path,
        config=_FreshConfig(),
        output_format="jpeg",
        continuity_key=None,
    )
    assert open(image.path, "rb").read() == _JPEG_BYTES
    assert image.generation_evidence.conversation_mode == "fresh"


def test_fresh_response_rejects_present_null_reuse_fields(tmp_path):
    from ai_workflow_tools.media.image_generation import ImageGenerationError

    post = _RecordingPost(reply=_fresh_reply(generation=None))

    class _FreshConfig(_Config):
        WORKFLOW_CHATGPT_BROWSER_CONVERSATION_MODE = "fresh"

    with pytest.raises(ImageGenerationError, match="reuse-only"):
        _generate(post, tmp_path, config=_FreshConfig(), continuity_key=None)
    assert not os.listdir(tmp_path)


def test_reference_count_mismatch_stays_enforced(tmp_path):
    # KEEP-GREEN: the style-drop fence exists today and must survive the rework.
    from ai_workflow_tools.media.image_generation import ImageGenerationError

    ref = tmp_path / "ref.png"
    ref.write_bytes(_PNG_BYTES)
    post = _RecordingPost(reply=_reply(reference_images_used=1))
    with pytest.raises(ImageGenerationError, match="reference"):
        _generate(
            post,
            tmp_path,
            reference_image_paths=[str(ref), str(ref)],
        )
    files = set(os.listdir(tmp_path))
    assert files == {"ref.png"}, "a rejected image must leave no partial file"
