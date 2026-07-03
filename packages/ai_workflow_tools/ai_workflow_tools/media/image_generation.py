"""Provider seam for workflow image generation side effects."""

import asyncio
import base64
import logging
import mimetypes
import os
import time
import uuid
from contextlib import ExitStack
from typing import Any, Callable, Protocol

from ai_workflow_tools.media.image_models import GeneratedImage, ImageGenerationRequest
from ai_workflow_engine.models import WorkflowUsageEvent
from ai_workflow_engine.usage import (
    check_budget_before_call,
    record_image_usage,
    record_usage_event,
)

logger = logging.getLogger(__name__)


class ImageGenerationError(Exception):
    """Raised when an image provider fails to produce a usable local artifact."""


class ImageGenerator(Protocol):
    async def generate(self, request: ImageGenerationRequest) -> GeneratedImage:
        """Generate one image and return the stored local artifact."""


def create_image_generator(config: Any) -> ImageGenerator:
    """Create the configured image provider without leaking provider choice into graph code."""

    provider = str(_config_value(config, "WORKFLOW_IMAGE_PROVIDER", "openai")).strip().lower()
    compare_providers = _comparison_providers(config)
    if provider in {"comparison", "compare"} or compare_providers:
        return ComparisonImageGenerator(
            config,
            providers=compare_providers or ["openai", "gemini"],
            primary_provider=str(
                _config_value(
                    config,
                    "WORKFLOW_IMAGE_COMPARISON_PRIMARY_PROVIDER",
                    "",
                )
                or ""
            ),
        )
    if provider in {"openai", "gpt-image", "gpt"}:
        return OpenAIImageGenerator(config)
    if provider in {"gemini", "google", "nano-banana", "nanobanana"}:
        return GeminiImageGenerator(config)
    if provider in {"chatgpt", "chatgpt-browser", "chatgpt-web"}:
        return ChatGptBrowserImageGenerator(config)
    raise ImageGenerationError(
        f"Unsupported WORKFLOW_IMAGE_PROVIDER={provider!r}; expected openai, gemini, or chatgpt"
    )


class OpenAIImageGenerator:
    """OpenAI Images API implementation.

    Uses `images.generate` when no references are provided and `images.edit` when the request
    includes style/source reference images.
    """

    def __init__(self, config: Any):
        self.config = config
        self._client = None

    @property
    def client(self) -> Any:
        if self._client is None:
            from openai import AsyncOpenAI

            api_key = getattr(self.config, "OPENAI_API_KEY", "")
            if not api_key:
                raise ImageGenerationError("OPENAI_API_KEY is required for image generation")
            self._client = AsyncOpenAI(api_key=api_key)
        return self._client

    async def generate(self, request: ImageGenerationRequest) -> GeneratedImage:
        if not request.prompt.strip():
            raise ImageGenerationError("Image generation prompt is empty")

        if getattr(self.config, "WORKFLOW_USAGE_TRACKING_ENABLED", True):
            check_budget_before_call("image", "generate_image")

        os.makedirs(request.output_dir, exist_ok=True)
        basename = request.output_basename or f"{uuid.uuid4().hex}.{request.output_format}"
        path = os.path.join(request.output_dir, basename)

        start = time.monotonic()
        try:
            kwargs = {
                "model": request.model,
                "prompt": request.prompt,
                "n": 1,
                "size": request.size,
                "quality": request.quality,
                "output_format": request.output_format,
            }
            if not request.model.startswith("gpt-image"):
                kwargs["response_format"] = "b64_json"
            if request.reference_image_paths:
                response = await self._edit_with_references(request, kwargs)
            else:
                response = await self.client.images.generate(**kwargs)
            elapsed_ms = int((time.monotonic() - start) * 1000)

            image_base64 = response.data[0].b64_json if response.data else None
            if not image_base64:
                raise ImageGenerationError("OpenAI image response did not include b64_json")

            with open(path, "wb") as fh:
                fh.write(base64.b64decode(image_base64))

            logger.info(
                "image_generation_artifact model=%s size=%s quality=%s output_format=%s references=%s path=%s",
                request.model,
                request.size,
                request.quality,
                request.output_format,
                len(request.reference_image_paths),
                path,
            )
            response_usage = getattr(response, "usage", None)
            if hasattr(response_usage, "model_dump"):
                usage_metadata = response_usage.model_dump()
            elif isinstance(response_usage, dict):
                usage_metadata = response_usage
            else:
                usage_metadata = {}
            usage_event = None
            if getattr(self.config, "WORKFLOW_USAGE_TRACKING_ENABLED", True):
                usage_event = record_image_usage(
                    node="generate_image",
                    provider="openai",
                    model=request.model,
                    usage=response_usage,
                    request_id=getattr(response, "_request_id", None),
                    elapsed_ms=elapsed_ms,
                    metadata={
                        "size": request.size,
                        "quality": request.quality,
                        "output_format": request.output_format,
                        "reference_image_count": len(request.reference_image_paths),
                        "workflow_id": request.workflow_id,
                    },
                    config=self.config,
                )
            return GeneratedImage(
                path=path,
                basename=basename,
                provider="openai",
                model=request.model,
                size=request.size,
                quality=request.quality,
                output_format=request.output_format,
                reference_image_count=len(request.reference_image_paths),
                style_reference_version=request.style_reference_version,
                usage_metadata=usage_metadata,
                estimated_usd=usage_event.estimated_usd if usage_event else None,
                request_id=getattr(response, "_request_id", None),
            )
        except Exception as exc:
            elapsed_ms = int((time.monotonic() - start) * 1000)
            try:
                if os.path.exists(path):
                    os.remove(path)
            except OSError as cleanup_error:
                logger.debug("image_generation_cleanup_failed path=%s error=%s", path, cleanup_error)
            if getattr(self.config, "WORKFLOW_USAGE_TRACKING_ENABLED", True):
                record_image_usage(
                    node="generate_image",
                    provider="openai",
                    model=request.model,
                    elapsed_ms=elapsed_ms,
                    success=False,
                    error=str(exc)[:500],
                    metadata={
                        "size": request.size,
                        "quality": request.quality,
                        "output_format": request.output_format,
                        "reference_image_count": len(request.reference_image_paths),
                        "workflow_id": request.workflow_id,
                    },
                    config=self.config,
                )
            if isinstance(exc, ImageGenerationError):
                raise
            raise ImageGenerationError(f"OpenAI image generation failed: {exc}") from exc

    async def _edit_with_references(self, request: ImageGenerationRequest, kwargs: dict):
        with ExitStack() as stack:
            files = [stack.enter_context(open(path, "rb")) for path in request.reference_image_paths]
            image_arg = files if len(files) > 1 else files[0]
            return await self.client.images.edit(image=image_arg, **kwargs)


class GeminiImageGenerator:
    """Google Gemini image provider for Nano Banana style backends.

    Uses Gemini's `generateContent` endpoint with text and optional inline image references. The
    graph still works with the provider-neutral `ImageGenerationRequest`; provider-specific knobs
    such as Gemini image size live on config.
    """

    API_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"

    def __init__(self, config: Any, http_post: Callable[..., Any] | None = None):
        self.config = config
        self._http_post = http_post

    async def generate(self, request: ImageGenerationRequest) -> GeneratedImage:
        if not request.prompt.strip():
            raise ImageGenerationError("Image generation prompt is empty")

        api_key = getattr(self.config, "GEMINI_API_KEY", "") or getattr(
            self.config, "GOOGLE_API_KEY", ""
        )
        if not api_key:
            raise ImageGenerationError(
                "GEMINI_API_KEY or GOOGLE_API_KEY is required for Gemini image generation"
            )

        if getattr(self.config, "WORKFLOW_USAGE_TRACKING_ENABLED", True):
            check_budget_before_call("image", "generate_image")

        os.makedirs(request.output_dir, exist_ok=True)
        basename = request.output_basename or f"{uuid.uuid4().hex}.{request.output_format}"
        path = os.path.join(request.output_dir, basename)
        start = time.monotonic()

        try:
            payload = self._build_payload(request)
            response_data = await self._post_json(request.model, api_key, payload)
            elapsed_ms = int((time.monotonic() - start) * 1000)

            image_base64, mime_type = self._extract_image(response_data)
            with open(path, "wb") as fh:
                fh.write(base64.b64decode(image_base64))

            output_image_tokens = _gemini_output_image_tokens(request.model, self.config)
            usage_metadata = self._normalized_usage(response_data, output_image_tokens)
            usage_event = None
            if getattr(self.config, "WORKFLOW_USAGE_TRACKING_ENABLED", True):
                usage_event = record_image_usage(
                    node="generate_image",
                    provider="gemini",
                    model=request.model,
                    usage=usage_metadata,
                    request_id=_gemini_request_id(response_data),
                    elapsed_ms=elapsed_ms,
                    metadata={
                        "provider": "gemini",
                        "mime_type": mime_type,
                        "size": request.size,
                        "quality": request.quality,
                        "output_format": request.output_format,
                        "reference_image_count": len(request.reference_image_paths),
                        "workflow_id": request.workflow_id,
                    },
                    config=self.config,
                )

            logger.info(
                "image_generation_artifact provider=gemini model=%s size=%s quality=%s "
                "output_format=%s references=%s path=%s",
                request.model,
                request.size,
                request.quality,
                request.output_format,
                len(request.reference_image_paths),
                path,
            )
            return GeneratedImage(
                path=path,
                basename=basename,
                provider="gemini",
                model=request.model,
                size=request.size,
                quality=request.quality,
                output_format=request.output_format,
                reference_image_count=len(request.reference_image_paths),
                style_reference_version=request.style_reference_version,
                usage_metadata=usage_metadata,
                estimated_usd=usage_event.estimated_usd if usage_event else None,
                request_id=_gemini_request_id(response_data),
            )
        except Exception as exc:
            elapsed_ms = int((time.monotonic() - start) * 1000)
            try:
                if os.path.exists(path):
                    os.remove(path)
            except OSError as cleanup_error:
                logger.debug("image_generation_cleanup_failed path=%s error=%s", path, cleanup_error)
            if getattr(self.config, "WORKFLOW_USAGE_TRACKING_ENABLED", True):
                record_image_usage(
                    node="generate_image",
                    provider="gemini",
                    model=request.model,
                    elapsed_ms=elapsed_ms,
                    success=False,
                    error=str(exc)[:500],
                    metadata={
                        "provider": "gemini",
                        "size": request.size,
                        "quality": request.quality,
                        "output_format": request.output_format,
                        "reference_image_count": len(request.reference_image_paths),
                        "workflow_id": request.workflow_id,
                    },
                    config=self.config,
                )
            if isinstance(exc, ImageGenerationError):
                raise
            raise ImageGenerationError(f"Gemini image generation failed: {exc}") from exc

    def _build_payload(self, request: ImageGenerationRequest) -> dict[str, Any]:
        parts: list[dict[str, Any]] = [{"text": request.prompt}]
        for path in request.reference_image_paths:
            mime_type = mimetypes.guess_type(path)[0] or "image/png"
            with open(path, "rb") as fh:
                encoded = base64.b64encode(fh.read()).decode("ascii")
            parts.append({"inlineData": {"mimeType": mime_type, "data": encoded}})

        generation_config: dict[str, Any] = {"responseModalities": ["TEXT", "IMAGE"]}
        if _config_bool(self.config, "WORKFLOW_GEMINI_RESPONSE_FORMAT_ENABLED"):
            response_format = _gemini_response_format(request, self.config)
            if response_format:
                generation_config["responseFormat"] = {"image": response_format}

        return {
            "contents": [{"role": "user", "parts": parts}],
            "generationConfig": generation_config,
        }

    async def _post_json(self, model: str, api_key: str, payload: dict[str, Any]) -> dict[str, Any]:
        http_post = self._http_post
        if http_post is None:
            import requests

            http_post = requests.post
        response = await asyncio.to_thread(
            http_post,
            self.API_URL.format(model=model),
            json=payload,
            headers={
                "Content-Type": "application/json",
                "x-goog-api-key": api_key,
            },
            timeout=float(
                _config_value(
                    self.config,
                    "WORKFLOW_IMAGE_PROVIDER_TIMEOUT_SECONDS",
                    120,
                )
            ),
        )
        try:
            data = response.json()
        except Exception as exc:
            raise ImageGenerationError("Gemini image response was not valid JSON") from exc

        status_code = int(getattr(response, "status_code", 200) or 200)
        if status_code >= 400:
            message = data.get("error", {}).get("message") if isinstance(data, dict) else None
            raise ImageGenerationError(
                f"Gemini image generation HTTP {status_code}: {message or getattr(response, 'text', '')}"
            )
        if not isinstance(data, dict):
            raise ImageGenerationError("Gemini image response JSON was not an object")
        return data

    @staticmethod
    def _extract_image(response_data: dict[str, Any]) -> tuple[str, str]:
        for candidate in response_data.get("candidates", []):
            for part in candidate.get("content", {}).get("parts", []):
                inline = part.get("inlineData") or part.get("inline_data")
                if isinstance(inline, dict) and inline.get("data"):
                    return str(inline["data"]), str(inline.get("mimeType") or inline.get("mime_type") or "")
        text_parts = []
        for candidate in response_data.get("candidates", []):
            for part in candidate.get("content", {}).get("parts", []):
                if part.get("text"):
                    text_parts.append(str(part["text"]))
        detail = f"; text response: {' '.join(text_parts)[:300]}" if text_parts else ""
        raise ImageGenerationError(f"Gemini image response did not include inline image data{detail}")

    @staticmethod
    def _normalized_usage(
        response_data: dict[str, Any],
        output_image_tokens: int,
    ) -> dict[str, Any]:
        raw = response_data.get("usageMetadata") or response_data.get("usage_metadata") or {}
        input_tokens = _first_int(raw, "promptTokenCount", "prompt_token_count")
        text_output_tokens = _first_int(raw, "candidatesTokenCount", "candidates_token_count")
        total_tokens = _first_int(raw, "totalTokenCount", "total_token_count")
        output_tokens = max(text_output_tokens, 0) + output_image_tokens
        if total_tokens <= 0 or total_tokens < input_tokens + output_tokens:
            total_tokens = input_tokens + output_tokens
        return {
            "input_tokens": input_tokens,
            "input_tokens_details": _gemini_input_details(raw, input_tokens),
            "output_tokens": output_tokens,
            "output_tokens_details": {
                "text_tokens": text_output_tokens,
                "image_tokens": output_image_tokens,
            },
            "total_tokens": total_tokens,
            "provider_usage_metadata": raw,
        }


class ChatGptBrowserImageGenerator:
    """ChatGPT-over-API browser service (subscription ChatGPT session over plain HTTP).

    Talks to the always-on control server (`POST /generate_image`) that drives a logged-in
    ChatGPT browser — image cost is covered by the ChatGPT subscription, so usage is recorded
    as ``subscription_notional`` with ``cost_known=false``, never phantom metered USD.

    Service contract handled here (CHATGPT_API.md, FR-1 shipped 2026-07-02):
    - Reference images (style/subject conditioning) are sent as data URLs; the service
      fails loudly if it cannot attach them, and the response echoes
      ``reference_images_used`` — a mismatch raises here (no silent style drop, ever).
    - The service caches identical requests; with ``WORKFLOW_CHATGPT_BROWSER_FORCE_FRESH``
      (default on) the first-class ``no_cache`` flag forces a fresh generation so
      retries/regenerations never replay a previously rejected image.
    - Calls are synchronous and slow (~30–90 s) and run sequentially on one browser; the read
      timeout must exceed the service-side timeout.
    """

    def __init__(
        self,
        config: Any,
        http_post: Callable[..., Any] | None = None,
        sleeper: Callable[..., Any] | None = None,
    ):
        self.config = config
        self._http_post = http_post
        self._sleeper = sleeper

    async def generate(self, request: ImageGenerationRequest) -> GeneratedImage:
        if not request.prompt.strip():
            raise ImageGenerationError("Image generation prompt is empty")
        base_url = str(_config_value(self.config, "WORKFLOW_CHATGPT_BROWSER_URL", "") or "").strip()
        if not base_url:
            raise ImageGenerationError(
                "WORKFLOW_CHATGPT_BROWSER_URL (CHATGPT_BROWSER_API_URL) is required for the "
                "chatgpt browser image provider — no default endpoint is assumed"
            )

        if getattr(self.config, "WORKFLOW_USAGE_TRACKING_ENABLED", True):
            check_budget_before_call("image", "generate_image")

        os.makedirs(request.output_dir, exist_ok=True)
        basename = request.output_basename or f"{uuid.uuid4().hex}.{request.output_format}"
        path = os.path.join(request.output_dir, basename)
        service_timeout = int(
            _config_value(self.config, "WORKFLOW_CHATGPT_BROWSER_TIMEOUT_SECONDS", 340)
        )
        reference_images = [
            {"data_url": _file_data_url(ref_path), "role": "style"}
            for ref_path in request.reference_image_paths
        ]
        payload: dict[str, Any] = {"description": request.prompt, "timeout": service_timeout}
        if reference_images:
            payload["reference_images"] = reference_images
        if _config_bool(self.config, "WORKFLOW_CHATGPT_BROWSER_FORCE_FRESH", True):
            # The service caches identical requests; a retry after a rejected image would
            # otherwise replay the SAME image forever.
            payload["no_cache"] = True

        start = time.monotonic()
        try:
            data = await self._post_json(
                f"{base_url.rstrip('/')}/generate_image",
                payload,
                read_timeout=service_timeout + 30,
            )
            elapsed_ms = int((time.monotonic() - start) * 1000)

            image_data_url = str(data.get("image_data_url") or "")
            if data.get("status") != "completed" or "," not in image_data_url:
                raise ImageGenerationError(
                    f"chatgpt browser image response missing image data (status="
                    f"{data.get('status')!r})"
                )
            if reference_images:
                used = int(data.get("reference_images_used") or 0)
                if used != len(reference_images):
                    # Service-side contract says this cannot happen (it fails loudly), but a
                    # silently style-dropped image is undetectable downstream — enforce here too.
                    raise ImageGenerationError(
                        f"chatgpt browser honored {used} of {len(reference_images)} reference "
                        "images — refusing a style-dropped result"
                    )
            with open(path, "wb") as fh:
                fh.write(base64.b64decode(image_data_url.split(",", 1)[1]))

            self._record_notional_usage(
                request,
                elapsed_ms=elapsed_ms,
                success=True,
                error=None,
                source_url=str(data.get("source_url") or ""),
            )
            logger.info(
                "image_generation_artifact provider=chatgpt_browser size=%s output_format=%s path=%s",
                request.size,
                request.output_format,
                path,
            )
            return GeneratedImage(
                path=path,
                basename=basename,
                provider="chatgpt_browser",
                model="chatgpt-web",
                size=request.size,
                quality=request.quality,
                output_format=request.output_format,
                reference_image_count=len(reference_images),
                style_reference_version=request.style_reference_version,
                usage_metadata={
                    "mime": data.get("mime"),
                    "bytes": data.get("bytes"),
                    "reference_images_used": data.get("reference_images_used"),
                    "cost_class": "subscription_notional",
                    "cost_known": False,
                },
                estimated_usd=None,
                request_id=None,
            )
        except Exception as exc:
            elapsed_ms = int((time.monotonic() - start) * 1000)
            try:
                if os.path.exists(path):
                    os.remove(path)
            except OSError as cleanup_error:
                logger.debug("image_generation_cleanup_failed path=%s error=%s", path, cleanup_error)
            self._record_notional_usage(
                request,
                elapsed_ms=elapsed_ms,
                success=False,
                error=str(exc)[:500],
                source_url="",
            )
            if isinstance(exc, ImageGenerationError):
                raise
            raise ImageGenerationError(f"chatgpt browser image generation failed: {exc}") from exc

    async def _post_json(self, url: str, payload: dict[str, Any], *, read_timeout: float) -> dict[str, Any]:
        from ai_workflow_tools.chatgpt_browser import _retry_after_seconds

        http_post = self._http_post
        if http_post is None:
            import requests

            http_post = requests.post
        # Images are occasional and allowed to be slow: the default budget rides out the
        # service's 15-min account-protection cooldown instead of failing the run.
        budget = float(
            _config_value(self.config, "WORKFLOW_CHATGPT_BROWSER_RATE_WAIT_MAX_SECONDS", 1200)
        )
        sleep = self._sleeper or asyncio.sleep
        waited = 0.0
        while True:
            response = await asyncio.to_thread(
                http_post,
                url,
                json=payload,
                headers={"Content-Type": "application/json"},
                timeout=read_timeout,
            )
            try:
                data = response.json()
            except Exception as exc:
                raise ImageGenerationError("chatgpt browser response was not valid JSON") from exc
            status_code = int(getattr(response, "status_code", 200) or 200)
            if status_code == 429:
                # Documented consumer contract: the service self-throttles to protect the
                # shared ChatGPT account — honour Retry-After, bounded, loud when exhausted.
                delay = _retry_after_seconds(response, data)
                if waited + delay > budget:
                    raise ImageGenerationError(
                        f"chatgpt browser rate-limited beyond the {budget:.0f}s wait budget "
                        f"(waited {waited:.0f}s, next retry_after={delay:.0f}s) — the service "
                        "may be in a circuit-breaker cooldown; try later"
                    )
                logger.info(
                    "chatgpt_browser rate_limited retry_after=%.0fs waited=%.0fs budget=%.0fs",
                    delay,
                    waited,
                    budget,
                )
                result = sleep(delay)
                if asyncio.iscoroutine(result):
                    await result
                waited += delay
                continue
            if status_code >= 400:
                detail = data.get("detail") or data.get("error") if isinstance(data, dict) else None
                raise ImageGenerationError(
                    f"chatgpt browser service HTTP {status_code}: "
                    f"{detail or getattr(response, 'text', '')} "
                    "(502 usually means the logged-in ChatGPT browser/extension is down on the mini)"
                )
            if not isinstance(data, dict):
                raise ImageGenerationError("chatgpt browser response JSON was not an object")
            return data

    def _record_notional_usage(
        self,
        request: ImageGenerationRequest,
        *,
        elapsed_ms: int,
        success: bool,
        error: str | None,
        source_url: str,
    ) -> None:
        if not getattr(self.config, "WORKFLOW_USAGE_TRACKING_ENABLED", True):
            return
        # Subscription browser session: no per-call price exists. cost_known=false — the run
        # stays cost-honest instead of reporting phantom $0 metered spend.
        record_usage_event(
            WorkflowUsageEvent(
                provider="chatgpt_browser",
                operation="image",
                cost_class="subscription_notional",
                node="generate_image",
                model="chatgpt-web",
                estimated_usd=None,
                notional_usd=None,
                elapsed_ms=elapsed_ms,
                success=success,
                error=error,
                metadata={
                    "cost_known": False,
                    "cost_source": "unknown",
                    "size": request.size,
                    "output_format": request.output_format,
                    "reference_image_count": len(request.reference_image_paths),
                    "workflow_id": request.workflow_id,
                    "source_url": source_url,
                },
            )
        )


class ComparisonImageGenerator:
    """Generate the same image request through multiple providers and return the primary result."""

    def __init__(
        self,
        config: Any,
        providers: list[str],
        primary_provider: str = "",
    ):
        self.config = config
        self.providers = [_canonical_provider_name(provider) for provider in providers]
        if not self.providers:
            raise ImageGenerationError("Comparison image generation requires at least one provider")
        primary = _canonical_provider_name(primary_provider) if primary_provider else self.providers[0]
        if primary not in self.providers:
            raise ImageGenerationError(
                f"WORKFLOW_IMAGE_COMPARISON_PRIMARY_PROVIDER={primary_provider!r} is not in "
                f"WORKFLOW_IMAGE_COMPARE_PROVIDERS={','.join(self.providers)}"
            )
        self.primary_provider = primary

    async def generate(self, request: ImageGenerationRequest) -> GeneratedImage:
        results: list[GeneratedImage] = []
        errors: list[str] = []
        for provider in self.providers:
            try:
                provider_request = _request_for_provider(request, provider, self.config)
                result = await _generator_for_provider(provider, self.config).generate(provider_request)
                results.append(result)
            except Exception as exc:
                errors.append(f"{provider}: {exc}")
                logger.warning("image_comparison_provider_failed provider=%s error=%s", provider, exc)

        if not results:
            raise ImageGenerationError(
                "All comparison image providers failed: " + "; ".join(errors)
            )

        primary = next((result for result in results if result.provider == self.primary_provider), results[0])
        alternatives = [
            {
                "provider": result.provider,
                "model": result.model,
                "path": result.path,
                "basename": result.basename,
                "estimated_usd": result.estimated_usd,
                "request_id": result.request_id,
            }
            for result in results
            if result.path != primary.path
        ]
        return primary.model_copy(
            update={
                "usage_metadata": {
                    **primary.usage_metadata,
                    "comparison_providers": self.providers,
                    "comparison_primary_provider": primary.provider,
                    "comparison_alternatives": alternatives,
                    "comparison_errors": errors,
                },
            }
        )


def _gemini_response_format(request: ImageGenerationRequest, config: Any) -> dict[str, str]:
    response_format: dict[str, str] = {}
    aspect_ratio = _aspect_ratio_from_size(request.size)
    if aspect_ratio:
        response_format["aspectRatio"] = aspect_ratio

    image_size = str(_config_value(config, "WORKFLOW_GEMINI_IMAGE_SIZE", "") or "").strip()
    if image_size:
        response_format["imageSize"] = image_size
    return response_format


def _aspect_ratio_from_size(size: str) -> str:
    if not size or size == "auto" or "x" not in size:
        return ""
    width_s, height_s = size.lower().split("x", 1)
    try:
        width = int(width_s)
        height = int(height_s)
    except ValueError:
        return ""
    if width <= 0 or height <= 0:
        return ""
    divisor = _gcd(width, height)
    ratio = f"{width // divisor}:{height // divisor}"
    supported = {"1:1", "3:4", "4:3", "9:16", "16:9"}
    if ratio in supported:
        return ratio
    if width == height:
        return "1:1"
    if width > height:
        return "16:9"
    return "9:16"


def _gcd(a: int, b: int) -> int:
    while b:
        a, b = b, a % b
    return max(a, 1)


def _gemini_output_image_tokens(model: str, config: Any) -> int:
    model_name = model.lower()
    image_size = str(_config_value(config, "WORKFLOW_GEMINI_IMAGE_SIZE", "1K") or "1K").upper()
    if "3" in model_name:
        if "pro" in model_name:
            return 2000 if image_size == "4K" else 1120
        if image_size == "4K":
            return 2520
        if image_size == "2K":
            return 1680
        if image_size in {"512", "512PX", "0.5K"}:
            return 747
        return 1120
    return 1290


def _gemini_input_details(raw: dict[str, Any], input_tokens: int) -> dict[str, int]:
    cached = _first_int(raw, "cachedContentTokenCount", "cached_content_token_count")
    details = {
        "text_tokens": input_tokens,
        "image_tokens": 0,
    }
    if cached:
        details["cached_tokens"] = cached
    return details


def _gemini_request_id(response_data: dict[str, Any]) -> str | None:
    value = response_data.get("responseId") or response_data.get("response_id")
    return str(value) if value else None


def _first_int(data: dict[str, Any], *keys: str) -> int:
    for key in keys:
        value = data.get(key)
        try:
            return int(value)
        except (TypeError, ValueError):
            continue
    return 0


def _comparison_providers(config: Any) -> list[str]:
    raw = str(_config_value(config, "WORKFLOW_IMAGE_COMPARE_PROVIDERS", "") or "")
    return [_canonical_provider_name(value) for value in raw.split(",") if value.strip()]


def _generator_for_provider(provider: str, config: Any) -> ImageGenerator:
    canonical = _canonical_provider_name(provider)
    if canonical == "openai":
        return OpenAIImageGenerator(config)
    if canonical == "gemini":
        return GeminiImageGenerator(config)
    if canonical == "chatgpt":
        return ChatGptBrowserImageGenerator(config)
    raise ImageGenerationError(f"Unsupported image provider {provider!r}")


def _request_for_provider(
    request: ImageGenerationRequest,
    provider: str,
    config: Any,
) -> ImageGenerationRequest:
    model = _model_for_provider(provider, request.model, config)
    suffix = _canonical_provider_name(provider)
    basename = request.output_basename
    if basename:
        stem, ext = os.path.splitext(basename)
        basename = f"{stem}-{suffix}{ext or f'.{request.output_format}'}"
    return request.model_copy(update={"model": model, "output_basename": basename})


def _model_for_provider(provider: str, requested_model: str, config: Any) -> str:
    canonical = _canonical_provider_name(provider)
    if canonical == "openai":
        configured = str(_config_value(config, "WORKFLOW_OPENAI_IMAGE_MODEL", "") or "").strip()
        if configured:
            return configured
        return requested_model if requested_model.startswith("gpt-image") else "gpt-image-2"
    if canonical == "gemini":
        configured = str(_config_value(config, "WORKFLOW_GEMINI_IMAGE_MODEL", "") or "").strip()
        if configured:
            return configured
        return requested_model if requested_model.startswith("gemini-") else "gemini-3.1-flash-image"
    if canonical == "chatgpt":
        # The browser service exposes no model choice; the label keeps usage/artifacts honest.
        return "chatgpt-web"
    raise ImageGenerationError(f"Unsupported image provider {provider!r}")


def _canonical_provider_name(provider: str) -> str:
    value = str(provider or "").strip().lower()
    aliases = {
        "gpt": "openai",
        "gpt-image": "openai",
        "google": "gemini",
        "nano-banana": "gemini",
        "nanobanana": "gemini",
        "chatgpt-browser": "chatgpt",
        "chatgpt-web": "chatgpt",
    }
    return aliases.get(value, value)


def _config_value(config: Any, name: str, default: Any = None) -> Any:
    value = getattr(config, name, None)
    if value not in (None, ""):
        return value
    return default


def _config_bool(config: Any, name: str, default: bool = False) -> bool:
    value = _config_value(config, name, default)
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return bool(value)


def _file_data_url(path: str) -> str:
    """Encode a local image file as a data URL (the service's own round-trip format)."""

    mime = mimetypes.guess_type(path)[0] or "image/png"
    try:
        with open(path, "rb") as fh:
            encoded = base64.b64encode(fh.read()).decode("ascii")
    except OSError as exc:
        raise ImageGenerationError(f"reference image unreadable: {path}: {exc}") from exc
    return f"data:{mime};base64,{encoded}"
