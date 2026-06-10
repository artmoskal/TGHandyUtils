"""Package-suite conftest: safety net + standalone-run support.

Mirrors the host repo's global ChatOpenAI mock so no test can make a real API call, but
conditionally — the standalone clean-venv install does not ship langchain_openai.
"""

from unittest.mock import Mock

import pytest


@pytest.fixture(autouse=True, scope="session")
def mock_openai_globally():
    try:
        from unittest.mock import patch

        with patch("langchain_openai.ChatOpenAI") as mock_chat:
            mock_chat.return_value = Mock()
            yield mock_chat.return_value
    except (ImportError, ModuleNotFoundError, AttributeError):
        yield None
