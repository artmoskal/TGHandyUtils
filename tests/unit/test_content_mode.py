"""Unit tests for content_mode setting + intent resolution (Stage 2)."""

from unittest.mock import Mock

import pytest

from core.interfaces import Intent
from services.content.intent_resolver import IntentResolver
from models.unified_recipient import UnifiedUserPreferences


def _resolver_for(mode):
    repo = Mock()
    repo.get_preferences.return_value = UnifiedUserPreferences(user_id=1, content_mode=mode)
    return IntentResolver(preferences_repo=repo)


@pytest.mark.unit
def test_resolver_reads_anki_mode():
    assert _resolver_for("anki").resolve(1) == Intent.ANKI


@pytest.mark.unit
def test_resolver_reads_reminder_mode():
    assert _resolver_for("reminder").resolve(1) == Intent.REMINDER


@pytest.mark.unit
def test_resolver_auto_without_classifier_falls_back_to_reminder():
    assert _resolver_for("auto").resolve(1) == Intent.REMINDER


@pytest.mark.unit
def test_resolver_override_beats_setting():
    assert _resolver_for("reminder").resolve(1, override=Intent.ANKI) == Intent.ANKI


@pytest.mark.unit
def test_get_mode_returns_raw_setting():
    assert _resolver_for("auto").get_mode(1) == "auto"


@pytest.mark.unit
def test_resolver_no_repo_defaults_reminder():
    assert IntentResolver().resolve(1) == Intent.REMINDER


@pytest.mark.unit
def test_set_content_mode_rejects_invalid():
    from services.recipient_service import RecipientService
    svc = RecipientService(repository=Mock(), preferences_repo=Mock())
    with pytest.raises(ValueError):
        svc.set_content_mode(1, "bogus")
