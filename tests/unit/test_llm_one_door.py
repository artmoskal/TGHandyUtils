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

    config = SimpleNamespace(CHATGPT_BROWSER_API_URL="http://mini.test:8010", OPENAI_API_KEY="k")

    assert isinstance(create_anki_text_llm(config, "chatgpt-web", 0.1), ChatGptBrowserLLMClient)
    assert isinstance(create_anki_chat_model(config, "chatgpt-web", 0.1), ChatGptBrowserChatModel)
    # unregistered model names fall through to the regular metered API door
    api_client = create_anki_text_llm(config, "gpt-5.4-mini", 0.1)
    assert type(api_client).__name__ == "ChatOpenAI"


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
