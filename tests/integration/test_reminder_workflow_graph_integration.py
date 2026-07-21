"""Live graph-integration check for the reminder ("todoist") workflow on the engine.

Mirrors ``test_anki_workflow_graph_integration.py``: the engine runs the real declarative
reminder graph with a LIVE parse capability — ``ParsingService`` routed through
``services.llm_factory`` under ``REMINDER_GRAPH_TEST_MODEL`` (e.g. ``chatgpt-web`` for the
browser-subscription backend). Task creation uses a recording fake so the run makes NO
external Todoist/platform write; the live surface under test is engine orchestration +
real LLM generation + parse→create threading + usage accounting.
"""

import os

import pytest

from config import Config
from core.interfaces import ServiceResult
from services.content.reminder_generation_graph import ReminderGenerationGraph
from services.parsing_service import ParsingService

pytestmark = pytest.mark.integration

# empty-string env (e.g. a compose passthrough with no host export) means "unset"
GRAPH_PARSE_MODEL = os.getenv("REMINDER_GRAPH_TEST_MODEL") or "gpt-5.4-mini"


class _RecordingTaskService:
    """Same shape the graph's create capability calls via asyncio.to_thread."""

    def __init__(self) -> None:
        self.calls = []

    def create_task_for_recipients(self, **kwargs):
        self.calls.append(kwargs)
        return ServiceResult.success_with_data("✅ recorded (no external write)", {"add_actions": []})


def _configure_parse_model() -> Config:
    from services.llm_factory import llm_provider_label

    # The prerequisite depends on the SELECTED backend, never on OpenAI specifically:
    # registry backends (chatgpt-web / claude-p) must run without an OpenAI key. After
    # the paid opt-in, a configured-but-unavailable backend must FAIL, never skip
    # (llm_factory raises loudly when e.g. chatgpt-web is selected without its URL).
    if llm_provider_label(GRAPH_PARSE_MODEL) == "openai" and (
        not Config.OPENAI_API_KEY or Config.OPENAI_API_KEY == "test_key_not_used"
    ):
        pytest.skip("OPENAI_API_KEY not configured for a metered OpenAI parse model")
    Config.TASK_PARSING_MODEL = GRAPH_PARSE_MODEL
    return Config()


@pytest.mark.api
@pytest.mark.asyncio
async def test_reminder_workflow_live_parse_threads_into_create():
    """The engine-run reminder graph parses REAL content with the routed model and
    threads the parsed task into the create capability exactly once."""

    config = _configure_parse_model()
    parsing = ParsingService(config)
    task_svc = _RecordingTaskService()
    graph = ReminderGenerationGraph(parsing, task_svc)

    content = "Remind me to water the office plants tomorrow at 9am"
    out = await graph.run(
        content=content,
        owner_id=12345,
        owner_name="Art",
        location="Lisbon",
        chat_id=11,
        message_id=22,
    )

    task_data = out["task_data"]
    assert task_data, f"live parse produced no task_data: {out!r}"
    assert isinstance(task_data.get("title"), str) and task_data["title"].strip(), (
        f"live parse must produce a non-empty title: {task_data!r}"
    )
    assert task_data.get("due_time"), f"live parse must schedule a due time: {task_data!r}"
    assert "T" in str(task_data["due_time"]), (
        f"due_time must be an ISO timestamp: {task_data['due_time']!r}"
    )

    # parse output threaded into create exactly once, with passthrough context intact
    assert len(task_svc.calls) == 1, "create capability must run exactly once"
    call = task_svc.calls[0]
    assert call["user_id"] == 12345
    assert call["title"] == task_data["title"]
    assert call["due_time"] == task_data["due_time"]
    assert call["chat_id"] == 11 and call["message_id"] == 22
    assert out["create_result"]["success"] is True

    # the run happened inside the engine's usage scope and the LIVE call was accounted —
    # requiring a SUCCESSFUL event proves the parse came from the routed model rather
    # than the silent static fallback (failed provider calls record success=False events).
    from services.llm_factory import llm_cost_class, llm_provider_label

    usage = out["usage"]
    assert usage is not None, "run must produce an engine usage summary"
    parse_events = [
        event
        for event in usage.events
        if event.operation == "chat" and event.node == "task_parser" and event.success
    ]
    assert parse_events, (
        "the live parse must record a SUCCESSFUL chat usage event — failed calls also "
        "record events, and without this filter the static fallback silently masks a "
        f"broken provider: {usage.events!r}"
    )
    assert parse_events[0].cost_class == llm_cost_class(GRAPH_PARSE_MODEL, config), (
        f"cost honesty: {parse_events[0].cost_class!r} must match the routed backend"
    )
    assert parse_events[0].provider == llm_provider_label(GRAPH_PARSE_MODEL), (
        f"provider label must name the routed backend: {parse_events[0].provider!r}"
    )
