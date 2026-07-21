"""Unit tests for auto mode: classifier, gate logic, and the 5s-window commit (Stage 4)."""

from unittest.mock import Mock, AsyncMock, patch

import pytest

from core.interfaces import Intent, ProcessingContext
from services.content.intent_resolver import IntentResolver
from services.content.classifier import IntentClassifier
from models.unified_recipient import UnifiedUserPreferences


def _resolver(mode, classifier=None):
    repo = Mock()
    repo.get_preferences.return_value = UnifiedUserPreferences(user_id=1, content_mode=mode)
    return IntentResolver(preferences_repo=repo, classifier=classifier)


@pytest.mark.unit
def test_requires_recipient_gate_only_for_reminder():
    assert _resolver("reminder").requires_recipient_gate(1) is True
    assert _resolver("anki").requires_recipient_gate(1) is False
    assert _resolver("auto").requires_recipient_gate(1) is False
    assert _resolver("reminder").requires_recipient_gate(1, override=Intent.ANKI) is False
    assert _resolver("anki").requires_recipient_gate(1, override=Intent.REMINDER) is True


@pytest.mark.unit
def test_classifier_maps_responses():
    c = IntentClassifier(Mock(OPENAI_API_KEY="k"))
    c._llm = Mock()
    c._llm.invoke.return_value = Mock(content="anki")
    assert c.classify("mitochondria is the powerhouse of the cell") == Intent.ANKI
    c._llm.invoke.return_value = Mock(content="reminder")
    assert c.classify("call dentist tomorrow") == Intent.REMINDER


@pytest.mark.unit
def test_classifier_defaults_reminder_on_error():
    c = IntentClassifier(Mock(OPENAI_API_KEY="k"))
    c._llm = Mock()
    c._llm.invoke.side_effect = Exception("boom")
    assert c.classify("x") == Intent.REMINDER


@pytest.mark.unit
def test_resolver_auto_uses_classifier_with_content():
    clf = Mock()
    clf.classify.return_value = Intent.ANKI
    r = _resolver("auto", classifier=clf)
    assert r.resolve(1, content="some study material") == Intent.ANKI
    clf.classify.assert_called_once()


@pytest.mark.unit
async def test_auto_flow_button_commits_choice_and_cancels_timer():
    from services.content import auto_flow

    status_msg = Mock(edit_text=AsyncMock())
    message = Mock(reply=AsyncMock(return_value=status_msg))
    ctx = ProcessingContext(
        message=message, thread_content=[("U", "x")], user_id=1, owner_name="U", location=None
    )

    fake_processor = Mock()
    fake_processor.process = AsyncMock()

    with patch("services.content.auto_flow.get_processor", return_value=fake_processor):
        await auto_flow.start_auto_window(ctx, Intent.REMINDER)
        token = list(auto_flow._pending.keys())[-1]
        entry = auto_flow._pending[token]

        assert entry["clarification_request"].question == "What should this message become?"
        assert entry["clarification_request"].default_value == Intent.REMINDER.value
        assert entry["clarification_result"].status == "pending"
        assert any(
            event.node == "auto_intent_correction" and event.decision == "partial"
            for event in entry["trace_sink"].events
        )

        committed = await auto_flow.commit_choice(token, Intent.ANKI)

        assert committed is True
        fake_processor.process.assert_awaited_once()
        assert entry["clarification_response"].status == "answered"
        assert entry["clarification_response"].value == Intent.ANKI.value
        assert token not in auto_flow._pending


@pytest.mark.unit
async def test_auto_flow_commit_choice_unknown_token_returns_false():
    from services.content import auto_flow
    assert await auto_flow.commit_choice("nonexistent-token", Intent.ANKI) is False


@pytest.mark.unit
def test_classifier_builds_llm_through_the_routed_factory_with_honest_labels(monkeypatch):
    """Fence (codex 2026-07-21): the behavior tests above inject ``_llm`` directly, so
    reverting the routed-door factory selection or the cost/provider labels would stay
    green without this. Kill both mutations: the classifier must build its client via
    ``create_anki_chat_model`` and meter with the registry's cost_class/provider."""

    from services.content import classifier as classifier_module
    from services.llm_factory import llm_cost_class, llm_provider_label

    built = {}

    class _FakeLLM:
        def invoke(self, messages):
            return Mock(content="anki")

    def fake_factory(config, model, temperature):
        built["model"] = model
        return _FakeLLM()

    metered = {}

    def fake_invoke(llm, messages, **kwargs):
        metered.update(kwargs)
        return llm.invoke(messages)

    monkeypatch.setattr(classifier_module, "create_anki_chat_model", fake_factory)
    monkeypatch.setattr(classifier_module, "invoke_metered_chat", fake_invoke)

    config = Mock(OPENAI_API_KEY="k", ANKI_CARD_MODEL="chatgpt-web")
    classifier = classifier_module.IntentClassifier(config)
    assert classifier.classify("mitochondria is the powerhouse of the cell") == Intent.ANKI

    assert built["model"] == "chatgpt-web", "client must come from the ROUTED factory"
    assert metered["cost_class"] == llm_cost_class("chatgpt-web", config), (
        "metering must carry the registry cost class — phantom metered $0 breaks cost honesty"
    )
    assert metered["provider"] == llm_provider_label("chatgpt-web"), (
        "metering must carry the registry provider label"
    )
