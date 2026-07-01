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
}

ALLOWED_CONSTRUCTION_SITES = {
    Path("services/llm_factory.py"),
    Path("packages/ai_workflow_tools/ai_workflow_tools/media/image_generation.py"),
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
