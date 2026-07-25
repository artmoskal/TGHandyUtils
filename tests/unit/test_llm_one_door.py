"""G-ext1 — one provider door.

Provider clients are constructed ONLY in the sanctioned allow-list modules. Any other
construction site bypasses model selection, LLM_BASE_URL swapping, budget, usage, and
observability. Adding a new construction site is a deliberate reviewed decision made by
extending ALLOWED_CONSTRUCTION_SITES in this test — never by just writing `OpenAI(...)`.
"""

import ast
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

REPO_ROOT = Path(__file__).resolve().parents[2]

PROVIDER_CONSTRUCTORS = {
    "OpenAI",
    "AsyncOpenAI",
    "AzureOpenAI",
    "AsyncAzureOpenAI",
    "ChatOpenAI",
    "ChatAnthropic",
    "Anthropic",
    "AsyncAnthropic",
    # ChatGPT-browser service clients drive a paid subscription session — same one-door
    # rule as API SDKs: construct only via the factory so routing/cost stay centralized.
    "ChatGptBrowserLLMClient",
    "ChatGptBrowserChatModel",
}

ALLOWED_CONSTRUCTION_SITES = {
    Path("services/llm_factory.py"),
    Path("packages/ai_workflow_tools/ai_workflow_tools/media/image_generation.py"),
    # Defines the browser clients (the chat model composes the base client internally).
    Path("packages/ai_workflow_tools/ai_workflow_tools/chatgpt_browser.py"),
    # The L2 tool catalog is a sanctioned construction door (reviewed 2026-07-05):
    # its builders centralize client construction with descriptions + side effects,
    # same one-door intent as llm_factory for product code.
    Path("packages/ai_workflow_tools/ai_workflow_tools/catalog.py"),
}

SCAN_DIRS = ("services", "handlers_modular", "packages", "composition", "core", "platforms")


def _scan_files():
    for base in SCAN_DIRS:
        root = REPO_ROOT / base
        if not root.exists():
            continue
        for path in sorted(root.rglob("*.py")):
            parts = set(path.parts)
            if "__pycache__" in parts or "tests" in parts or "test-results" in parts:
                continue
            if "build" in parts:
                # setuptools in-tree build debris duplicates sources already scanned at
                # their real paths — the guard governs the source tree, not wheel copies
                continue
            if path.name.startswith("test_") or path.name == "conftest.py":
                continue
            yield path


def test_provider_clients_are_constructed_only_in_the_allow_list():
    offenders = []
    for path in _scan_files():
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError:
            continue
        rel = path.relative_to(REPO_ROOT)
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            name = func.id if isinstance(func, ast.Name) else (
                func.attr if isinstance(func, ast.Attribute) else None
            )
            if name in PROVIDER_CONSTRUCTORS and rel not in ALLOWED_CONSTRUCTION_SITES:
                offenders.append(f"{rel}:{node.lineno} constructs {name}(...)")
    assert not offenders, (
        "provider client constructed outside the one-door allow-list "
        "(use services/llm_factory.py, or extend ALLOWED_CONSTRUCTION_SITES as a reviewed decision):\n"
        + "\n".join(offenders)
    )


def test_the_allow_list_itself_is_current():
    """The sanctioned modules must exist and actually construct a provider client —
    a stale allow-list entry is drift in the other direction."""

    for rel in ALLOWED_CONSTRUCTION_SITES:
        path = REPO_ROOT / rel
        assert path.exists(), f"allow-listed module missing: {rel}"
        tree = ast.parse(path.read_text(encoding="utf-8"))
        constructs = any(
            isinstance(node, ast.Call)
            and (
                (isinstance(node.func, ast.Name) and node.func.id in PROVIDER_CONSTRUCTORS)
                or (isinstance(node.func, ast.Attribute) and node.func.attr in PROVIDER_CONSTRUCTORS)
            )
            for node in ast.walk(tree)
        )
        assert constructs, f"allow-listed module no longer constructs a provider client: {rel}"


# --- Backend registry routing (the pluggable seam behind the one door) ------------------


def test_registered_backend_routes_by_model_name_per_role():
    from types import SimpleNamespace

    from ai_workflow_tools.chatgpt_browser import ChatGptBrowserChatModel, ChatGptBrowserLLMClient
    from services.llm_factory import create_anki_chat_model, create_anki_text_llm

    config = SimpleNamespace(
        CHATGPT_BROWSER_API_URL="http://mini.test:8010",
        WORKFLOW_CHATGPT_BROWSER_TOKEN="test-token",
        OPENAI_API_KEY="k",
    )

    assert isinstance(create_anki_text_llm(config, "chatgpt-web", 0.1), ChatGptBrowserLLMClient)
    assert isinstance(create_anki_chat_model(config, "chatgpt-web", 0.1), ChatGptBrowserChatModel)
    # unregistered model names fall through to the regular metered API door
    # (asserted structurally — other tests may mock ChatOpenAI itself)
    api_client = create_anki_text_llm(config, "gpt-5.4-mini", 0.1)
    assert not isinstance(api_client, (ChatGptBrowserLLMClient, ChatGptBrowserChatModel))


def test_registry_reports_cost_class_vision_and_provider_label():
    from services.llm_factory import llm_cost_class, llm_provider_label, llm_supports_vision

    assert llm_cost_class("chatgpt-web") == "subscription_notional"
    assert llm_cost_class("gpt-5.4-mini") == "metered"
    assert llm_supports_vision("chatgpt-web") is False
    assert llm_supports_vision("gpt-5.4-mini") is True
    assert llm_provider_label("chatgpt-web") == "chatgpt_browser"
    assert llm_provider_label("gpt-5.4-mini") == "openai"


def test_routed_backend_without_url_fails_loudly():
    from types import SimpleNamespace

    import pytest as _pytest

    from services.llm_factory import create_anki_text_llm

    with _pytest.raises(ValueError, match="CHATGPT_BROWSER_API_URL"):
        create_anki_text_llm(SimpleNamespace(CHATGPT_BROWSER_API_URL=""), "chatgpt-web", 0.0)


def test_new_backends_plug_in_without_editing_the_factory():
    """The pluggability contract: registering a backend routes it — no factory edits."""

    from types import SimpleNamespace

    from services.llm_factory import (
        LLMBackend,
        _LLM_BACKENDS,
        create_anki_text_llm,
        llm_cost_class,
    )

    marker = object()
    register_key = "unit-test-local-llm"
    try:
        from services.llm_factory import register_llm_backend

        register_llm_backend(
            LLMBackend(
                build_text=lambda config: marker,
                build_chat=lambda config: marker,
                cost_class="subscription_notional",
                supports_vision=False,
            ),
            register_key,
        )
        assert create_anki_text_llm(SimpleNamespace(), register_key, 0.0) is marker
        assert llm_cost_class(register_key) == "subscription_notional"
    finally:
        _LLM_BACKENDS.pop(register_key, None)


def test_claude_p_backend_routes_with_subscription_facts():
    """claude -p is a registered backend: routable per role, honest cost class, no vision."""

    from types import SimpleNamespace

    from ai_workflow_tools.cli_agents import ConsoleChatModel, ConsoleLLMClient
    from services.llm_factory import (
        create_anki_chat_model,
        create_anki_text_llm,
        llm_cost_class,
        llm_provider_label,
        llm_supports_vision,
    )

    config = SimpleNamespace(ANKI_CLAUDE_P_TIMEOUT_SECONDS=99)

    text_client = create_anki_text_llm(config, "claude-p", 0.0)
    chat_client = create_anki_chat_model(config, "claude-p", 0.0)

    assert isinstance(text_client, ConsoleLLMClient)
    assert text_client.subscription_mode is True
    assert text_client.timeout_s == 99
    assert isinstance(chat_client, ConsoleChatModel)
    assert chat_client.timeout_s == 99
    assert llm_cost_class("claude-p") == "subscription_notional"
    assert llm_provider_label("claude-p") == "claude_p"
    # staged vision: images become workspace files the CLI reads itself
    assert llm_supports_vision("claude-p") is True


def test_codex_exec_backend_routes_with_subscription_facts():
    from types import SimpleNamespace

    from ai_workflow_tools.cli_agents import ConsoleChatModel, ConsoleLLMClient
    from services.llm_factory import create_anki_text_llm, llm_cost_class, llm_provider_label

    config = SimpleNamespace(ANKI_CODEX_TIMEOUT_SECONDS=77)
    text_client = create_anki_text_llm(config, "codex-exec", 0.0)
    assert isinstance(text_client, ConsoleLLMClient)
    assert text_client.flavor.name == "codex_exec"
    assert text_client.timeout_s == 77
    assert llm_cost_class("codex-exec") == "subscription_notional"
    assert llm_provider_label("codex-exec") == "codex_exec"
    # bare "codex" deliberately NOT a routing key (real-model-name collision risk):
    assert llm_provider_label("codex") == "openai"


def test_codex_exec_cost_class_is_explicit_when_api_key_backed():
    """Cost honesty: codex exec may run from ChatGPT-plan auth or OPENAI_API_KEY auth.
    The latter is metered spend, so products must be able to mark it as metered."""

    from types import SimpleNamespace

    import pytest as _pytest

    from services.llm_factory import create_anki_text_llm, llm_cost_class

    config = SimpleNamespace(ANKI_CODEX_TIMEOUT_SECONDS=77, WORKFLOW_CODEX_COST_CLASS="metered")

    text_client = create_anki_text_llm(config, "codex-exec", 0.0)
    assert text_client.subscription_mode is False
    assert llm_cost_class("codex-exec", config) == "metered"

    bad_config = SimpleNamespace(WORKFLOW_CODEX_COST_CLASS="free-ish")
    with _pytest.raises(ValueError, match="WORKFLOW_CODEX_COST_CLASS"):
        llm_cost_class("codex-exec", bad_config)


@pytest.mark.unit
def test_metered_chat_construction_without_openai_key_fails_loudly():
    """Backend-aware key contract (codex 2026-07-21): a METERED OpenAI model without a
    key must fail at client construction — while a registry backend (chatgpt-web) builds
    without any OpenAI key. Consumers (ParsingService, classifier) rely on the factory
    for this; they carry no eager key gates of their own."""

    from unittest.mock import Mock

    import pytest as _pytest

    from services.llm_factory import create_anki_chat_model

    config = Mock(
        OPENAI_API_KEY="",
        LLM_BASE_URL="",
        CHATGPT_BROWSER_API_URL="http://browser.test:8010",
        WORKFLOW_CHATGPT_BROWSER_TOKEN="test-browser-token",
        WORKFLOW_CHATGPT_BROWSER_TIMEOUT_SECONDS=340,
        WORKFLOW_CHATGPT_BROWSER_RATE_WAIT_MAX_SECONDS=1200,
    )
    with _pytest.raises(ValueError, match="OpenAI API key is required"):
        create_anki_chat_model(config, "gpt-5.4-mini", 0.0)

    assert create_anki_chat_model(config, "chatgpt-web", 0.0) is not None, (
        "a registry backend must construct without an OpenAI key"
    )


# ================= B1 factory path: token propagation to the wire =================
#
# Secured-consumer iteration, codex finding 2 (2026-07-21): the client-level auth tests in
# packages/ai_workflow_tools/tests/test_chatgpt_browser_auth.py construct clients directly —
# production constructs them through create_anki_text_llm / create_anki_chat_model with
# _chatgpt_browser_kwargs(config), which today passes NO token, and config.py exposes none.
# These tests prove the whole production chain: env -> Config -> factory kwargs -> HTTP header.


class _BrowserTokenConfig:
    OPENAI_API_KEY = ""
    LLM_BASE_URL = ""
    CHATGPT_BROWSER_API_URL = "http://browser.test:8010"
    WORKFLOW_CHATGPT_BROWSER_TOKEN = "secret-token-factory"
    WORKFLOW_CHATGPT_BROWSER_TIMEOUT_SECONDS = 5
    WORKFLOW_CHATGPT_BROWSER_RATE_WAIT_MAX_SECONDS = 1200


class _FactoryRecordingPost:
    def __init__(self):
        self.calls = []

    def __call__(self, url, json=None, headers=None, timeout=None):
        self.calls.append({"url": url, "headers": headers or {}})

        class _Response:
            status_code = 200
            text = ""

            def json(self):
                return {"status": "completed", "reply": "ok"}

        return _Response()


def test_factory_built_text_client_carries_the_configured_token(monkeypatch):
    import asyncio

    from services.llm_factory import create_anki_text_llm

    post = _FactoryRecordingPost()
    monkeypatch.setattr("requests.post", post)
    client = create_anki_text_llm(_BrowserTokenConfig(), "chatgpt-web", 0.0)
    assert client.rate_limit_max_wait_s == 1200

    from ai_workflow_engine.llm_protocol import ChatMessage, LLMRequest

    asyncio.run(client(LLMRequest(messages=[ChatMessage(role="user", content="hi")])))
    assert post.calls, "the factory-built client must reach the transport"
    assert post.calls[0]["headers"].get("Authorization") == "Bearer secret-token-factory", (
        "the configured token must reach the HTTP header through the PRODUCTION "
        "construction path, not only through direct constructors"
    )


def test_factory_built_chat_model_carries_the_configured_token(monkeypatch):
    from services.llm_factory import create_anki_chat_model

    post = _FactoryRecordingPost()
    monkeypatch.setattr("requests.post", post)
    model = create_anki_chat_model(_BrowserTokenConfig(), "chatgpt-web", 0.0)
    assert model._client.rate_limit_max_wait_s == 1200
    model.invoke([("user", "hi")])
    assert post.calls and post.calls[0]["headers"].get("Authorization") == (
        "Bearer secret-token-factory"
    )


def test_config_sources_browser_token_from_the_agreed_env_name():
    """Secret contract: consumer env CHATGPT_BROWSER_API_TOKEN -> config value
    WORKFLOW_CHATGPT_BROWSER_TOKEN (mirroring the URL's env->WORKFLOW_* pattern), no
    default. Proven in a hermetic subprocess so module state stays untouched."""

    import os
    import subprocess
    import sys

    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import config; print(config.Config.WORKFLOW_CHATGPT_BROWSER_TOKEN)",
        ],
        env={**os.environ, "CHATGPT_BROWSER_API_TOKEN": "tok-from-env"},
        capture_output=True,
        text=True,
        cwd=str(REPO_ROOT),
        timeout=60,
    )
    assert result.returncode == 0, f"config import failed: {result.stderr[-500:]}"
    assert result.stdout.strip() == "tok-from-env"


# ================= B2: force-fresh is DELETED, explicit mode replaces it =================


def test_config_replaces_force_fresh_with_explicit_conversation_mode():
    """Latest-only: the boolean knobs are DELETED (no alias) and the committed Anki mode
    is `reuse`, exposed as WORKFLOW_CHATGPT_BROWSER_CONVERSATION_MODE. Hermetic subprocess
    keeps module state untouched."""

    import os
    import subprocess
    import sys

    code = (
        "import config\n"
        "c = config.Config\n"
        "assert not hasattr(c, 'ANKI_CHATGPT_BROWSER_FORCE_FRESH'), 'old knob alive'\n"
        "assert not hasattr(c, 'WORKFLOW_CHATGPT_BROWSER_FORCE_FRESH'), 'old alias alive'\n"
        "print(c.WORKFLOW_CHATGPT_BROWSER_CONVERSATION_MODE)\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", code],
        env=dict(os.environ),
        capture_output=True,
        text=True,
        cwd=str(REPO_ROOT),
        timeout=60,
    )
    assert result.returncode == 0, f"config contract failed: {result.stderr[-500:]}"
    assert result.stdout.strip() == "reuse", "committed Anki mode is reuse"


def test_config_sources_browser_conversation_mode_from_deployment_env():
    """The Ansible-rendered WORKFLOW_* key must reach the setting Config reads."""

    import os
    import subprocess
    import sys

    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import config; print(config.Config.WORKFLOW_CHATGPT_BROWSER_CONVERSATION_MODE)",
        ],
        env={**os.environ, "WORKFLOW_CHATGPT_BROWSER_CONVERSATION_MODE": "fresh"},
        capture_output=True,
        text=True,
        cwd=str(REPO_ROOT),
        timeout=60,
    )
    assert result.returncode == 0, f"config import failed: {result.stderr[-500:]}"
    assert result.stdout.strip() == "fresh"


def test_factory_kwargs_no_longer_carry_force_fresh():
    from services.llm_factory import _chatgpt_browser_kwargs

    class _Cfg:
        CHATGPT_BROWSER_API_URL = "http://127.0.0.1:8010"
        WORKFLOW_CHATGPT_BROWSER_TIMEOUT_SECONDS = 5

    assert "force_fresh" not in _chatgpt_browser_kwargs(_Cfg()), (
        "the deleted knob must not survive as a factory kwarg"
    )


def test_force_fresh_is_deleted_repo_wide():
    """Deletion proof over every permanent source/config/doc/deployment surface.

    Tests and historical discussion are excluded because they name the removed contract as
    evidence. Build/cache/VCS directories are excluded because they are not source truth.
    """

    offenders = []
    permanent_suffixes = {".py", ".md", ".toml", ".yaml", ".yml", ".example"}
    excluded_parts = {
        ".git",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        "__pycache__",
        "build",
        "dist",
        "graphify-out",
        "node_modules",
        "tests",
    }
    for path in REPO_ROOT.rglob("*"):
        relative = path.relative_to(REPO_ROOT)
        if not path.is_file() or excluded_parts.intersection(relative.parts):
            continue
        if relative.parts[:2] == ("docs", "_discussion"):
            continue
        if path.suffix not in permanent_suffixes and path.name not in {
            ".env.example",
            "Dockerfile",
        }:
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        if "FORCE_FRESH" in text or "force_fresh" in text:
            offenders.append(str(relative))
    assert not offenders, f"force-fresh still referenced in production sources: {offenders}"


def test_compose_env_precedence_cannot_shadow_dotenv_browser_credentials():
    """The real test container inherits URL/token from env_file, never empty passthroughs."""

    import os
    import re

    from dotenv import dotenv_values

    compose = (REPO_ROOT / "infra" / "docker-compose.test.yml").read_text(encoding="utf-8")
    for key in ("CHATGPT_BROWSER_API_URL", "CHATGPT_BROWSER_API_TOKEN"):
        assert not re.search(rf"^\s*-\s*{key}=", compose, re.MULTILINE), (
            f"{key} in environment would outrank env_file even when interpolated empty"
        )
    assert 'env_file: "${TG_TEST_ENV_FILE:-../.env}"' in compose
    test_wrapper = (REPO_ROOT / "test.sh").read_text(encoding="utf-8")
    assert 'TG_TEST_ENV_FILE="$REPO_ROOT/.env"' in test_wrapper
    assert 'mktemp "${TMPDIR:-/tmp}/tghandy-test-env.XXXXXX"' in test_wrapper
    assert ': > ../.env' not in test_wrapper and "touch ../.env" not in test_wrapper

    declared = dotenv_values(REPO_ROOT / ".env") if (REPO_ROOT / ".env").exists() else {}
    if os.getenv("RUNNING_IN_DOCKER") == "1":
        for key in ("CHATGPT_BROWSER_API_URL", "CHATGPT_BROWSER_API_TOKEN"):
            if declared.get(key):
                assert os.getenv(key, "") == declared[key], (
                    f"the .env-declared {key} must reach the container unshadowed"
                )
