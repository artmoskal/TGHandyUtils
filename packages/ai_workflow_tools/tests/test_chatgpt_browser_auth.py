"""B1 red battery: Bearer authentication contract for every ChatGPT-browser client.

Secured-consumer iteration (canonical plan: ai-llm-infra
docs/_discussion/2026-07-21-anki-real-consumer-qualification-plan.md, codex-reviewed
2026-07-21). The deployed provider requires ``Authorization: Bearer`` on every
operational endpoint; the consumer contract is:

- all three clients (async text, sync chat, image generator) accept the token and send
  the header on every operational POST;
- a NON-loopback base URL without a token fails loudly BEFORE any HTTP request leaves
  the process (never a silent unauthenticated paid attempt) — for all three clients;
- explicit loopback-open test mode stays supported (no token for 127.0.0.1/localhost);
- 401 fails loudly on the FIRST response with no retry — for all three clients
  (KEEP-GREEN today: only 429 cycles the retry loop);
- the token never appears on the ACTUAL failure surfaces: raised exception text and
  chain, and the client's log records (the strings every downstream usage event and
  observation detail persist).

These tests were captured RED before implementation and now permanently fence the
authentication contract. The factory-path half
of this battery (production construction through create_anki_text_llm /
create_anki_chat_model with config/env token propagation) lives in
tests/unit/test_llm_one_door.py next to the other factory contracts.
"""

from __future__ import annotations

import asyncio
import logging

import pytest

from ai_workflow_tools.chatgpt_browser import (
    ChatGptBrowserChatModel,
    ChatGptBrowserError,
    ChatGptBrowserLLMClient,
)

pytestmark = pytest.mark.unit


class _RecordingPost:
    """Capture the exact HTTP call a client makes; return a canned response."""

    def __init__(self, reply: dict | None = None, status_code: int = 200):
        self.calls = []
        self.reply = reply if reply is not None else {"status": "completed", "reply": "ok"}
        self.status_code = status_code

    def __call__(self, url, json=None, headers=None, timeout=None):
        self.calls.append({"url": url, "json": json, "headers": headers or {}})
        reply, status_code = self.reply, self.status_code

        class _Response:
            def __init__(self):
                self.status_code = status_code
                self.text = str(reply)

            def json(self):
                return reply

        return _Response()


def _ask(client):
    from ai_workflow_engine.llm_protocol import ChatMessage, LLMRequest

    return asyncio.run(client(LLMRequest(messages=[ChatMessage(role="user", content="hi")])))


class _ImageConfig:
    """The image generator's REAL config surface (WORKFLOW_* attrs, budget gate off)."""

    WORKFLOW_CHATGPT_BROWSER_URL = "http://browser.test:8010"
    WORKFLOW_CHATGPT_BROWSER_TOKEN = "secret-token-3"
    WORKFLOW_CHATGPT_BROWSER_TIMEOUT_SECONDS = 5
    WORKFLOW_CHATGPT_BROWSER_RATE_WAIT_MAX_SECONDS = 1
    WORKFLOW_CHATGPT_BROWSER_CONVERSATION_MODE = "reuse"
    WORKFLOW_USAGE_TRACKING_ENABLED = False


def _image_request(tmp_path):
    from ai_workflow_tools.media.image_models import ImageGenerationRequest

    return ImageGenerationRequest(
        prompt="a cat",
        model="chatgpt",
        output_dir=str(tmp_path),
        output_format="png",
        continuity_key="anki-auth-contract",
        idempotency_key="auth-contract:image:0",
    )


# --------------------------------------------------------------- RED: bearer on the wire


def test_async_text_client_sends_bearer_header():
    post = _RecordingPost()
    client = ChatGptBrowserLLMClient(
        "http://browser.test:8010", bearer_token="secret-token-1", http_post=post
    )
    _ask(client)
    assert post.calls, "the client must reach the recorded transport"
    assert post.calls[0]["headers"].get("Authorization") == "Bearer secret-token-1"


def test_sync_chat_model_sends_bearer_header():
    post = _RecordingPost()
    model = ChatGptBrowserChatModel(
        "http://browser.test:8010", bearer_token="secret-token-2", http_post=post
    )
    model.invoke([("user", "hi")])
    assert post.calls, "the chat model must reach the recorded transport"
    assert post.calls[0]["headers"].get("Authorization") == "Bearer secret-token-2"


def test_image_generator_sends_bearer_header(tmp_path):
    from ai_workflow_tools.media.image_generation import ChatGptBrowserImageGenerator

    post = _RecordingPost(
        reply={
            "status": "completed",
            "image": "data:image/png;base64,aGk=",
            "reference_images_used": 0,
        }
    )
    generator = ChatGptBrowserImageGenerator(_ImageConfig(), http_post=post)
    try:
        asyncio.run(generator.generate(_image_request(tmp_path)))
    except Exception:
        pass  # response/byte validation is B4's battery; the header contract is B1's
    assert post.calls, (
        "the generator must reach the recorded transport — a failure before HTTP means "
        "this test is red for the WRONG reason (codex finding 1)"
    )
    assert post.calls[0]["headers"].get("Authorization") == "Bearer secret-token-3"


# ------------------------------------- RED: missing token fails BEFORE HTTP (all three)


def test_async_text_client_non_loopback_without_token_fails_before_any_request():
    post = _RecordingPost()
    with pytest.raises(ChatGptBrowserError, match="token"):
        ChatGptBrowserLLMClient("http://100.107.180.35:8010", http_post=post)
    assert not post.calls, "no unauthenticated request may leave the process"


def test_sync_chat_model_non_loopback_without_token_fails_before_any_request():
    post = _RecordingPost()
    with pytest.raises(ChatGptBrowserError, match="token"):
        ChatGptBrowserChatModel("http://100.107.180.35:8010", http_post=post)
    assert not post.calls


def test_image_generator_non_loopback_without_token_fails_before_any_request(tmp_path):
    from ai_workflow_tools.media.image_generation import (
        ChatGptBrowserImageGenerator,
        ImageGenerationError,
    )

    class _TokenlessConfig(_ImageConfig):
        WORKFLOW_CHATGPT_BROWSER_URL = "http://100.107.180.35:8010"
        WORKFLOW_CHATGPT_BROWSER_TOKEN = ""

    post = _RecordingPost()
    generator = ChatGptBrowserImageGenerator(_TokenlessConfig(), http_post=post)
    with pytest.raises(ImageGenerationError, match="token"):
        asyncio.run(generator.generate(_image_request(tmp_path)))
    assert not post.calls, "no unauthenticated paid attempt may leave the process"


# ---------------------------------- KEEP-GREEN: loopback-open + 401 loud with NO retry


def test_loopback_open_mode_needs_no_token():
    # Loopback-open is CURRENT behavior and must stay supported through B1 — only
    # non-loopback URLs gain the token requirement.
    post = _RecordingPost()
    client = ChatGptBrowserLLMClient("http://127.0.0.1:8010", http_post=post)
    _ask(client)
    assert post.calls and "Authorization" not in post.calls[0]["headers"]


_UNAUTHORIZED = {"status": "unauthorized", "error": "missing or invalid Authorization: Bearer token"}


def test_async_text_client_401_is_loud_and_never_retried():
    post = _RecordingPost(reply=_UNAUTHORIZED, status_code=401)
    client = ChatGptBrowserLLMClient("http://127.0.0.1:8010", http_post=post)
    with pytest.raises(ChatGptBrowserError, match="401"):
        _ask(client)
    assert len(post.calls) == 1, "401 is a configuration failure — exactly ONE request"


def test_sync_chat_model_401_is_loud_and_never_retried():
    post = _RecordingPost(reply=_UNAUTHORIZED, status_code=401)
    model = ChatGptBrowserChatModel("http://127.0.0.1:8010", http_post=post)
    with pytest.raises(ChatGptBrowserError, match="401"):
        model.invoke([("user", "hi")])
    assert len(post.calls) == 1


def test_image_generator_401_is_loud_and_never_retried(tmp_path):
    from ai_workflow_tools.media.image_generation import (
        ChatGptBrowserImageGenerator,
        ImageGenerationError,
    )

    class _LoopbackConfig(_ImageConfig):
        WORKFLOW_CHATGPT_BROWSER_URL = "http://127.0.0.1:8010"
        WORKFLOW_CHATGPT_BROWSER_TOKEN = ""

    post = _RecordingPost(reply=_UNAUTHORIZED, status_code=401)
    generator = ChatGptBrowserImageGenerator(_LoopbackConfig(), http_post=post)
    with pytest.raises(ImageGenerationError, match="401") as err:
        asyncio.run(generator.generate(_image_request(tmp_path)))
    assert len(post.calls) == 1
    assert "502 usually means" not in str(err.value)


# ----------------------- RED: the token stays off the ACTUAL failure surfaces


def test_token_never_reaches_exception_text_chain_or_logs(caplog):
    """The raised error string, its full cause chain, and the client module's log records
    are the strings downstream usage events and observation details persist — the token
    must appear in none of them (repr/vars alone is not the real privacy boundary)."""

    post = _RecordingPost(reply=_UNAUTHORIZED, status_code=401)
    client = ChatGptBrowserLLMClient(
        "http://browser.test:8010", bearer_token="secret-token-4", http_post=post
    )
    with caplog.at_level(logging.DEBUG, logger="ai_workflow_tools.chatgpt_browser"):
        with pytest.raises(ChatGptBrowserError) as err:
            _ask(client)

    exc: BaseException | None = err.value
    while exc is not None:
        assert "secret-token-4" not in f"{exc!r}{exc}", (
            "the credential leaked into the exception chain"
        )
        exc = exc.__cause__ or exc.__context__
    for record in caplog.records:
        assert "secret-token-4" not in record.getMessage(), (
            "the credential leaked into client log records"
        )
    assert "secret-token-4" not in repr(client)


@pytest.mark.parametrize("client_kind", ["async", "sync", "image"])
def test_transport_exception_redacts_token_and_provider_url(client_kind, tmp_path):
    secret = "secret-token-transport"

    def post(*_args, **_kwargs):
        raise RuntimeError(
            f"failed Authorization: Bearer {secret} at "
            "https://provider.invalid/signed/private?sig=abc"
        )

    if client_kind == "async":
        client = ChatGptBrowserLLMClient(
            "https://browser.test:8010", bearer_token=secret, http_post=post
        )
        invoke = lambda: _ask(client)
    else:
        if client_kind == "sync":
            client = ChatGptBrowserChatModel(
                "https://browser.test:8010", bearer_token=secret, http_post=post
            )
            invoke = lambda: client.invoke([("user", "hi")])
        else:
            from ai_workflow_tools.media.image_generation import (
                ChatGptBrowserImageGenerator,
            )

            class _TransportFailureConfig(_ImageConfig):
                WORKFLOW_CHATGPT_BROWSER_TOKEN = secret

            client = ChatGptBrowserImageGenerator(
                _TransportFailureConfig(), http_post=post
            )
            invoke = lambda: asyncio.run(client.generate(_image_request(tmp_path)))

    with pytest.raises(Exception) as err:
        invoke()
    current: BaseException | None = err.value
    while current is not None:
        durable_error = f"{current!r}{current}"
        assert secret not in durable_error
        assert "provider.invalid" not in durable_error
        current = current.__cause__ or current.__context__
    assert "[REDACTED]" in str(err.value)
    assert "[REDACTED_URL]" in str(err.value)


@pytest.mark.parametrize(
    "base_url",
    ["ftp://browser.test:8010", "http://user:password@browser.test:8010", "not-a-url"],
)
def test_browser_base_url_rejects_non_http_and_embedded_credentials(base_url):
    with pytest.raises(ChatGptBrowserError, match="HTTP|credentials"):
        ChatGptBrowserLLMClient(base_url, bearer_token="token")


@pytest.mark.parametrize("timeout", [0, -1, float("nan"), float("inf")])
def test_text_clients_reject_non_positive_or_non_finite_timeouts_before_http(timeout):
    post = _RecordingPost()
    with pytest.raises(ChatGptBrowserError, match="finite and positive"):
        ChatGptBrowserLLMClient(
            "http://127.0.0.1:8010",
            timeout_s=timeout,
            http_post=post,
        )
    assert not post.calls


@pytest.mark.parametrize("timeout", [0, -1, float("nan"), float("inf")])
def test_image_client_rejects_non_positive_or_non_finite_timeouts_before_http(tmp_path, timeout):
    from ai_workflow_tools.media.image_generation import (
        ChatGptBrowserImageGenerator,
        ImageGenerationError,
    )

    class _InvalidTimeoutConfig(_ImageConfig):
        WORKFLOW_CHATGPT_BROWSER_TIMEOUT_SECONDS = timeout

    post = _RecordingPost()
    generator = ChatGptBrowserImageGenerator(_InvalidTimeoutConfig(), http_post=post)
    with pytest.raises(ImageGenerationError, match="finite and positive"):
        asyncio.run(generator.generate(_image_request(tmp_path)))
    assert not post.calls


@pytest.mark.parametrize("client_kind", ["async", "sync"])
def test_text_clients_project_structured_terminal_error_without_private_fields(client_kind):
    body = {
        "status": "uncertain",
        "task_id": "image-task-42",
        "error": {
            "code": "IMAGE_GENERATION_WEDGED",
            "message": "result missing at https://provider.invalid/task secret-token-1",
            "dom": "<main>private</main>",
        },
        "retry_requires_new_idempotency_key": True,
        "prompt": "private prompt must not survive",
    }
    post = _RecordingPost(reply=body, status_code=409)
    if client_kind == "async":
        client = ChatGptBrowserLLMClient(
            "http://browser.test:8010",
            bearer_token="secret-token-1",
            http_post=post,
        )
        invoke = lambda: _ask(client)
    else:
        client = ChatGptBrowserChatModel(
            "http://browser.test:8010",
            bearer_token="secret-token-1",
            http_post=post,
        )
        invoke = lambda: client.invoke([("user", "hi")])

    with pytest.raises(ChatGptBrowserError) as raised:
        invoke()

    message = str(raised.value)
    assert "HTTP 409" in message
    assert "status=uncertain" in message
    assert "task_id=image-task-42" in message
    assert "code=IMAGE_GENERATION_WEDGED" in message
    assert "retry_requires_new_idempotency_key=true" in message
    assert "[REDACTED_URL]" in message and "[REDACTED]" in message
    assert "private prompt" not in message
    assert "<main>" not in message
    assert "extension is down" not in message
    assert len(post.calls) == 1


def test_image_client_projects_structured_terminal_error_into_usage_without_private_fields(
    tmp_path, monkeypatch
):
    from ai_workflow_tools.media import image_generation as module
    from ai_workflow_tools.media.image_generation import (
        ChatGptBrowserImageGenerator,
        ImageGenerationError,
    )

    token = _ImageConfig.WORKFLOW_CHATGPT_BROWSER_TOKEN
    post = _RecordingPost(
        reply={
            "status": "uncertain",
            "task_id": "image-task-43",
            "error": {
                "code": "IMAGE_GENERATION_WEDGED",
                "message": f"no result at https://provider.invalid/task?token={token}",
                "image_bytes": "private",
            },
            "retry_requires_new_idempotency_key": True,
            "raw_provider_envelope": {"prompt": "private"},
        },
        status_code=409,
    )
    events = []
    monkeypatch.setattr(module, "record_usage_event", lambda event: events.append(event))
    generator = ChatGptBrowserImageGenerator(_ImageConfig(), http_post=post)

    with pytest.raises(ImageGenerationError) as raised:
        asyncio.run(generator.generate(_image_request(tmp_path)))

    assert len(post.calls) == 1
    assert len(events) == 1 and events[0].success is False
    durable = events[0].error or ""
    assert durable == str(raised.value)
    assert "status=uncertain" in durable
    assert "task_id=image-task-43" in durable
    assert "code=IMAGE_GENERATION_WEDGED" in durable
    assert "retry_requires_new_idempotency_key=true" in durable
    assert token not in durable and "provider.invalid" not in durable
    assert "image_bytes" not in durable and "raw_provider_envelope" not in durable
    assert "extension is down" not in durable


@pytest.mark.parametrize("client_kind", ["async", "sync"])
def test_outage_guidance_is_specific_to_http_502(client_kind):
    post = _RecordingPost(reply={"status": "failed", "error": "executor offline"}, status_code=502)
    if client_kind == "async":
        client = ChatGptBrowserLLMClient(
            "http://browser.test:8010", bearer_token="token", http_post=post
        )
        invoke = lambda: _ask(client)
    else:
        client = ChatGptBrowserChatModel(
            "http://browser.test:8010", bearer_token="token", http_post=post
        )
        invoke = lambda: client.invoke([("user", "hi")])

    with pytest.raises(ChatGptBrowserError, match="extension is down"):
        invoke()


def test_error_projection_handles_fastapi_wrapper_and_rejects_truthy_string_flags():
    from ai_workflow_tools.chatgpt_browser_contract import provider_error_detail

    message = provider_error_detail(
        409,
        {
            "status": None,
            "detail": {
                "status": "uncertain",
                "task_id": "wrapped-task-9",
                "error": {"code": "WEDGED", "message": "no result", "dom": "private"},
                "retry_requires_new_idempotency_key": "false",
                "prompt": "private",
            },
        },
    )

    assert "status=uncertain" in message
    assert "task_id=wrapped-task-9" in message
    assert "code=WEDGED" in message and "error=no result" in message
    assert "retry_requires_new_idempotency_key" not in message
    assert "private" not in message and "dom" not in message and "prompt" not in message
