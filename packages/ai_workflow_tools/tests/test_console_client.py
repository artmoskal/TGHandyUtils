from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
from ai_workflow_engine import ImageInput, LLMRequest, StructuredLLMNode, ToolSpec, WEAK_MODEL_CLEANER
from ai_workflow_engine.models import WorkflowRunContext, WorkflowUsageSummary
from ai_workflow_engine.budget import WorkflowBudget, WorkflowUsageContext, workflow_usage_scope
from pydantic import BaseModel

from ai_workflow_tools.cli_agents import ConsoleLLMClient, claude_p, codex_exec

pytestmark = pytest.mark.unit


class Verdict(BaseModel):
    label: str


def _node(client: ConsoleLLMClient, **kwargs) -> StructuredLLMNode:
    return StructuredLLMNode(
        name="console_node",
        config=object(),
        output_model=Verdict,
        prompt_template="Classify {item}.",
        input_variables=["item"],
        llm=client,
        **kwargs,
    )


def _fake_flavor(flavor, fake_cli_path: Path):
    return flavor.model_copy(update={"base_argv": [sys.executable, str(fake_cli_path)]})


def _configure_fake_cli(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    *,
    mode: str,
    result: str = '{"label": "ok"}',
    append: bool = False,
    sequence: list[str] | None = None,
) -> Path:
    record_path = tmp_path / f"console-record-{mode}.json"
    monkeypatch.setenv("FAKE_CLI_MODE", mode)
    monkeypatch.setenv("FAKE_CLI_RESULT", result)
    monkeypatch.setenv("FAKE_CLI_RECORD", str(record_path))
    if append:
        monkeypatch.setenv("FAKE_CLI_RECORD_APPEND", "1")
    else:
        monkeypatch.delenv("FAKE_CLI_RECORD_APPEND", raising=False)
    if sequence is not None:
        counter_path = tmp_path / f"counter-{mode}.txt"
        monkeypatch.setenv("FAKE_CLI_RESULTS_JSON", json.dumps(sequence))
        monkeypatch.setenv("FAKE_CLI_COUNTER_FILE", str(counter_path))
    else:
        monkeypatch.delenv("FAKE_CLI_RESULTS_JSON", raising=False)
        monkeypatch.delenv("FAKE_CLI_COUNTER_FILE", raising=False)
    return record_path


async def test_console_claude_structured_node_uses_pre_parse_and_subscription_usage(
    fake_cli_path,
    monkeypatch,
    tmp_path,
):
    record_path = _configure_fake_cli(
        monkeypatch,
        tmp_path,
        mode="envelope",
        result='<think>noise</think>\n```json\n{"label": "clean"}\n```',
    )
    client = ConsoleLLMClient(_fake_flavor(claude_p, fake_cli_path))
    node = _node(client, pre_parse=WEAK_MODEL_CLEANER)
    summary = WorkflowUsageSummary()

    with workflow_usage_scope(
        WorkflowUsageContext(WorkflowRunContext(workflow_id="wf-console", workflow_type="console"), summary, WorkflowBudget())
    ):
        result = await node.run({"item": "mug"})

    assert result.label == "clean"
    usage = summary.events[0]
    assert usage.cost_class == "subscription_notional"
    assert usage.notional_usd == 0.123
    assert usage.estimated_usd is None
    assert usage.input_tokens == 11
    assert usage.output_tokens == 7

    record = json.loads(record_path.read_text(encoding="utf-8"))
    assert record["stdin"] == "user: Classify mug."
    # Text-only completions carry the explicit disable-all switch (G-0.2): no silent
    # CLI-default tool inherit on structured LLM-node calls.
    assert record["argv"] == [str(fake_cli_path), "--output-format", "json", "--tools", ""]
    assert "--mcp-config" not in record["argv"]


async def test_console_codex_reads_result_file_and_keeps_cost_unknown(
    fake_cli_path,
    monkeypatch,
    tmp_path,
):
    record_path = _configure_fake_cli(
        monkeypatch,
        tmp_path,
        mode="result_file",
        result='{"label": "codex"}',
    )
    client = ConsoleLLMClient(_fake_flavor(codex_exec, fake_cli_path))
    node = _node(client)
    summary = WorkflowUsageSummary()

    with workflow_usage_scope(
        WorkflowUsageContext(WorkflowRunContext(workflow_id="wf-console", workflow_type="console"), summary, WorkflowBudget())
    ):
        result = await node.run({"item": "mug"})

    assert result.label == "codex"
    usage = summary.events[0]
    assert usage.cost_class == "subscription_notional"
    assert usage.notional_usd is None
    assert usage.estimated_usd is None
    assert usage.metadata["cost_known"] is False
    assert usage.metadata["cost_source"] == "unknown"

    record = json.loads(record_path.read_text(encoding="utf-8"))
    assert record["stdin"] == ""
    assert "--output-last-message" in record["argv"]
    assert "--config" not in record["argv"]
    assert record["argv"][-1] == "user: Classify mug."


async def test_console_refuses_images_and_tools_before_spawn(fake_cli_path, monkeypatch, tmp_path):
    record_path = _configure_fake_cli(monkeypatch, tmp_path, mode="envelope")
    client = ConsoleLLMClient(_fake_flavor(claude_p, fake_cli_path))

    # Images are STAGED (no longer refused) — covered by the staged-vision tests below.
    with pytest.raises(ValueError, match="cannot host tool-calling"):
        await client(LLMRequest(user="inspect", tools=[ToolSpec(name="click")]))

    assert not record_path.exists()


@pytest.mark.parametrize(
    ("flavor_factory", "mode", "prompt_location"),
    [
        (lambda path: _fake_flavor(claude_p, path), "envelope", "stdin"),
        (lambda path: _fake_flavor(codex_exec, path), "result_file", "argv"),
    ],
)
async def test_console_repair_round_spawns_twice_and_delivers_repair_prompt_per_flavor(
    fake_cli_path,
    flavor_factory,
    mode,
    prompt_location,
    monkeypatch,
    tmp_path,
):
    record_path = _configure_fake_cli(
        monkeypatch,
        tmp_path,
        mode=mode,
        append=True,
        sequence=["utter garbage", '{"label": "repaired"}'],
    )
    client = ConsoleLLMClient(flavor_factory(fake_cli_path))
    node = _node(client)

    result = await node.run({"item": "mug"})

    assert result.label == "repaired"
    records = json.loads(record_path.read_text(encoding="utf-8"))
    assert len(records) == 2
    first_prompt = records[0]["stdin"] if prompt_location == "stdin" else records[0]["argv"][-1]
    second_prompt = records[1]["stdin"] if prompt_location == "stdin" else records[1]["argv"][-1]
    assert first_prompt == "user: Classify mug."
    assert "previous structured-output response was invalid" in second_prompt


# --- ChatGPT-browser LLM client (subscription session over HTTP) -----------------------


class _FakeHttpResponse:
    def __init__(self, payload, status_code=200, text=""):
        self._payload = payload
        self.status_code = status_code
        self.text = text

    def json(self):
        return self._payload


def _browser_client(payload, status_code=200, **kwargs):
    from ai_workflow_tools.chatgpt_browser import ChatGptBrowserLLMClient

    calls = []

    def http_post(url, **post_kwargs):
        calls.append((url, post_kwargs))
        return _FakeHttpResponse(payload, status_code=status_code)

    client = ChatGptBrowserLLMClient("http://mini.test:8010/", http_post=http_post, **kwargs)
    return client, calls


async def test_chatgpt_browser_llm_returns_reply_with_notional_cost():
    client, calls = _browser_client({"status": "completed", "reply": '{"label": "noun"}'})

    response = await client(LLMRequest(system="You classify.", user="Classify dog."))

    assert response.text == '{"label": "noun"}'
    assert response.model == "chatgpt-web"
    assert response.cost_class == "subscription_notional"
    assert response.estimated_usd is None and response.notional_usd is None
    url, kwargs = calls[0]
    assert url == "http://mini.test:8010/ask"
    question = kwargs["json"]["question"]
    assert question.startswith("system: You classify.")
    # force_fresh default: variation token busts the service's identical-question cache
    assert "(request " in question


async def test_chatgpt_browser_llm_force_fresh_off_sends_verbatim_question():
    client, calls = _browser_client(
        {"status": "completed", "reply": "ok"}, force_fresh=False
    )

    await client(LLMRequest(user="Classify dog."))

    assert calls[0][1]["json"]["question"] == "user: Classify dog."


async def test_chatgpt_browser_llm_rejects_images_loudly():
    from ai_workflow_tools.chatgpt_browser import ChatGptBrowserError

    client, _ = _browser_client({"status": "completed", "reply": "ok"})
    with pytest.raises(ChatGptBrowserError, match="text-only"):
        await client(
            LLMRequest(
                user="look",
                images=[ImageInput(source="base64", data="aGVsbG8=", media_type="image/png")],
            )
        )


async def test_chatgpt_browser_llm_surfaces_service_down_loudly():
    from ai_workflow_tools.chatgpt_browser import ChatGptBrowserError

    client, _ = _browser_client({"detail": "task was not picked up"}, status_code=502)
    with pytest.raises(ChatGptBrowserError, match="HTTP 502"):
        await client(LLMRequest(user="hello"))


async def test_chatgpt_browser_llm_requires_explicit_base_url():
    from ai_workflow_tools.chatgpt_browser import ChatGptBrowserError, ChatGptBrowserLLMClient

    with pytest.raises(ChatGptBrowserError, match="base_url"):
        ChatGptBrowserLLMClient("")


async def test_chatgpt_browser_llm_empty_reply_is_loud():
    from ai_workflow_tools.chatgpt_browser import ChatGptBrowserError

    client, _ = _browser_client({"status": "completed", "reply": ""})
    with pytest.raises(ChatGptBrowserError, match="no reply"):
        await client(LLMRequest(user="hello"))


async def test_chatgpt_browser_chat_model_invokes_langchain_messages_sync():
    from types import SimpleNamespace

    from ai_workflow_tools.chatgpt_browser import ChatGptBrowserChatModel

    calls = []

    def http_post(url, **kwargs):
        calls.append((url, kwargs))
        return _FakeHttpResponse({"status": "completed", "reply": '[{"type":"basic"}]'})

    model = ChatGptBrowserChatModel("http://mini.test:8010", http_post=http_post, force_fresh=False)
    output = model.invoke(
        [
            SimpleNamespace(type="system", content="You render cards."),
            SimpleNamespace(type="human", content="Make one card about bridges."),
        ]
    )

    assert output.content == '[{"type":"basic"}]'
    question = calls[0][1]["json"]["question"]
    assert question == "system: You render cards.\n\nhuman: Make one card about bridges."


async def test_chatgpt_browser_chat_model_rejects_image_parts_loudly():
    from types import SimpleNamespace

    from ai_workflow_tools.chatgpt_browser import ChatGptBrowserChatModel, ChatGptBrowserError

    model = ChatGptBrowserChatModel("http://mini.test:8010", http_post=lambda *a, **k: None)
    with pytest.raises(ChatGptBrowserError, match="text-only"):
        model.invoke(
            [
                SimpleNamespace(
                    type="human",
                    content=[
                        {"type": "text", "text": "inspect"},
                        {"type": "image_url", "image_url": {"url": "data:image/png;base64,x"}},
                    ],
                )
            ]
        )


async def test_chatgpt_browser_chat_model_service_error_is_loud():
    from types import SimpleNamespace

    from ai_workflow_tools.chatgpt_browser import ChatGptBrowserChatModel, ChatGptBrowserError

    def http_post(url, **kwargs):
        return _FakeHttpResponse({"detail": "task was not picked up"}, status_code=502)

    model = ChatGptBrowserChatModel("http://mini.test:8010", http_post=http_post)
    with pytest.raises(ChatGptBrowserError, match="HTTP 502"):
        model.invoke([SimpleNamespace(type="human", content="hi")])


class _RateLimitedThenOk:
    """First N responses are 429 self-throttle; then the real payload."""

    def __init__(self, payload, *, limited=1, retry_after=3):
        self.payload = payload
        self.limited = limited
        self.retry_after = retry_after
        self.calls = 0

    def __call__(self, url, **kwargs):
        self.calls += 1
        if self.calls <= self.limited:
            return _FakeHttpResponse(
                {"status": "rate_limited", "error": "self-throttle", "retry_after": self.retry_after},
                status_code=429,
            )
        return _FakeHttpResponse(self.payload)


async def test_chatgpt_browser_llm_honours_429_retry_after_bounded():
    from ai_workflow_tools.chatgpt_browser import ChatGptBrowserLLMClient

    post = _RateLimitedThenOk({"status": "completed", "reply": "ok"}, limited=2, retry_after=4)
    sleeps = []

    async def sleeper(delay):
        sleeps.append(delay)

    client = ChatGptBrowserLLMClient(
        "http://mini.test:8010", http_post=post, sleeper=sleeper, force_fresh=False
    )
    response = await client(LLMRequest(user="hello"))

    assert response.text == "ok"
    assert sleeps == [4, 4], "must wait exactly the service-provided retry_after"
    assert post.calls == 3


async def test_chatgpt_browser_llm_rate_limit_budget_exhaustion_is_loud():
    from ai_workflow_tools.chatgpt_browser import ChatGptBrowserError, ChatGptBrowserLLMClient

    post = _RateLimitedThenOk({"status": "completed", "reply": "ok"}, limited=99, retry_after=50)
    sleeps = []

    async def sleeper(delay):
        sleeps.append(delay)

    client = ChatGptBrowserLLMClient(
        "http://mini.test:8010",
        http_post=post,
        sleeper=sleeper,
        rate_limit_max_wait_s=60,
    )
    with pytest.raises(ChatGptBrowserError, match="rate-limited beyond"):
        await client(LLMRequest(user="hello"))
    assert sleeps == [50], "one wait fits the 60s budget; the second (100s total) must not"


async def test_chatgpt_browser_chat_model_honours_429():
    from ai_workflow_tools.chatgpt_browser import ChatGptBrowserChatModel

    from types import SimpleNamespace

    post = _RateLimitedThenOk({"status": "completed", "reply": "done"}, limited=1, retry_after=2)
    sleeps = []
    model = ChatGptBrowserChatModel(
        "http://mini.test:8010", http_post=post, force_fresh=False
    )
    model._client._sleeper = sleeps.append

    output = model.invoke([SimpleNamespace(type="human", content="hi")])

    assert output.content == "done"
    assert sleeps == [2]


# --- ConsoleChatModel (sync .invoke over the CLI, for LangChain call sites) --------------


async def test_console_chat_model_invokes_cli_synchronously(fake_cli_path, monkeypatch, tmp_path):
    from types import SimpleNamespace

    from ai_workflow_tools.cli_agents import ConsoleChatModel

    _configure_fake_cli(monkeypatch, tmp_path, mode="envelope", result='[{"type": "basic"}]')
    model = ConsoleChatModel(_fake_flavor(claude_p, fake_cli_path))

    output = model.invoke(
        [
            SimpleNamespace(type="system", content="You render cards."),
            SimpleNamespace(type="human", content="One card about bridges."),
        ]
    )

    assert output.content == '[{"type": "basic"}]'


async def test_console_chat_model_missing_binary_is_loud():
    from types import SimpleNamespace

    from ai_workflow_tools.cli_agents import ConsoleChatModel

    broken = claude_p.model_copy(update={"base_argv": ["definitely-not-a-binary-xyz"]})
    model = ConsoleChatModel(broken)
    with pytest.raises(RuntimeError, match="binary not found"):
        model.invoke([SimpleNamespace(type="human", content="hi")])


async def test_console_chat_model_timeout_uses_the_shared_process_owner(
    fake_cli_path, monkeypatch, tmp_path
):
    from types import SimpleNamespace

    from ai_workflow_tools.cli_agents import ConsoleChatModel

    _configure_fake_cli(monkeypatch, tmp_path, mode="sleep")
    monkeypatch.setenv("FAKE_CLI_SLEEP_S", "30")
    model = ConsoleChatModel(
        _fake_flavor(claude_p, fake_cli_path),
        timeout_s=0.1,
    )

    with pytest.raises(RuntimeError, match="timed out"):
        model.invoke([SimpleNamespace(type="human", content="hi")])


async def test_console_chat_model_rejects_image_parts_loudly(fake_cli_path):
    from types import SimpleNamespace

    from ai_workflow_tools.cli_agents import ConsoleChatModel

    model = ConsoleChatModel(_fake_flavor(claude_p, fake_cli_path))
    with pytest.raises(ValueError, match="text-only"):
        model.invoke(
            [
                SimpleNamespace(
                    type="human",
                    content=[
                        {"type": "text", "text": "look"},
                        {"type": "image_url", "image_url": {"url": "data:image/png;base64,x"}},
                    ],
                )
            ]
        )


# --- Staged vision: images become workspace files the CLI reads itself ------------------


async def test_console_stages_images_and_allows_read_tool(fake_cli_path, monkeypatch, tmp_path):
    record_path = _configure_fake_cli(monkeypatch, tmp_path, mode="envelope", result='{"label": "red"}')
    client = ConsoleLLMClient(_fake_flavor(claude_p, fake_cli_path))

    response = await client(
        LLMRequest(
            user="What color is the image? JSON only.",
            images=[ImageInput(source="base64", data="aGVsbG8=", media_type="image/png")],
        )
    )

    assert response.text == '{"label": "red"}'
    record = json.loads(record_path.read_text(encoding="utf-8"))
    # image existed ON DISK in the CLI's working directory at invocation time
    assert "inputs/img-1.png" in record["cwd_files"], record["cwd_files"]
    assert record["stdin"].startswith("First read and inspect these image file(s)")
    assert "inputs/img-1.png" in record["stdin"]
    assert "--allowedTools" in record["argv"] and "Read(./inputs/**)" in record["argv"]
    # Staged vision keeps ONLY the scoped Read grant — the disable-all switch must not
    # appear (it would block the Read), and no broader tool default may leak in.
    assert "--tools" not in record["argv"]


async def test_console_codex_attaches_staged_images_after_prompt(
    fake_cli_path, monkeypatch, tmp_path
):
    record_path = _configure_fake_cli(monkeypatch, tmp_path, mode="result_file")
    client = ConsoleLLMClient(_fake_flavor(codex_exec, fake_cli_path))

    await client(
        LLMRequest(
            user="Inspect both images.",
            images=[
                ImageInput(source="base64", data="aGVsbG8=", media_type="image/png"),
                ImageInput(source="base64", data="d29ybGQ=", media_type="image/jpeg"),
            ],
        )
    )

    record = json.loads(record_path.read_text(encoding="utf-8"))
    assert "inputs/img-1.png" in record["cwd_files"]
    assert "inputs/img-2.jpg" in record["cwd_files"]
    prompt_index = next(
        index
        for index, value in enumerate(record["argv"])
        if value.startswith("First read and inspect these image file(s)")
    )
    assert record["argv"][prompt_index + 1 :] == [
        "--image",
        "inputs/img-1.png",
        "inputs/img-2.jpg",
    ]
    assert record["argv"].count("--image") == 1
    assert record["stdin"] == ""


async def test_console_stages_path_source_images_by_copy(fake_cli_path, monkeypatch, tmp_path):
    record_path = _configure_fake_cli(monkeypatch, tmp_path, mode="envelope")
    source_image = tmp_path / "card.png"
    source_image.write_bytes(b"png-bytes")
    client = ConsoleLLMClient(_fake_flavor(claude_p, fake_cli_path))

    await client(
        LLMRequest(
            user="inspect",
            images=[ImageInput(source="path", data=str(source_image), media_type="image/png")],
        )
    )

    record = json.loads(record_path.read_text(encoding="utf-8"))
    assert "inputs/img-1.png" in record["cwd_files"]


async def test_console_unreadable_path_image_is_loud(fake_cli_path, monkeypatch, tmp_path):
    _configure_fake_cli(monkeypatch, tmp_path, mode="envelope")
    client = ConsoleLLMClient(_fake_flavor(claude_p, fake_cli_path))

    with pytest.raises(ValueError, match="unreadable"):
        await client(
            LLMRequest(
                user="inspect",
                images=[ImageInput(source="path", data=str(tmp_path / "missing.png"), media_type="image/png")],
            )
        )


async def test_console_url_source_images_are_rejected_loudly(fake_cli_path, monkeypatch, tmp_path):
    _configure_fake_cli(monkeypatch, tmp_path, mode="envelope")
    client = ConsoleLLMClient(_fake_flavor(claude_p, fake_cli_path))

    with pytest.raises(ValueError, match="cannot fetch"):
        await client(
            LLMRequest(
                user="inspect",
                images=[ImageInput(source="url", data="https://x.test/i.png", media_type="image/png")],
            )
        )


async def test_chatgpt_browser_llm_rejects_tool_calling_loudly():
    from ai_workflow_tools.chatgpt_browser import ChatGptBrowserError

    client, _ = _browser_client({"status": "completed", "reply": "ok"})
    with pytest.raises(ChatGptBrowserError, match="tool-calling"):
        await client(LLMRequest(user="click stuff", tools=[ToolSpec(name="click")]))


# --- G-0.2 flag-form coverage: joined (=) and kebab spellings must suppress the default -


def test_explicit_tool_flag_detection_covers_joined_and_kebab_forms():
    from ai_workflow_tools.cli_agents.console import _has_explicit_tool_flag

    assert _has_explicit_tool_flag(["--tools", ""])
    assert _has_explicit_tool_flag(["--tools=default"])
    assert _has_explicit_tool_flag(["--allowedTools", "Read"])
    assert _has_explicit_tool_flag(["--allowedTools=Read(./inputs/**)"])
    assert _has_explicit_tool_flag(["--allowed-tools", "Read"])
    assert _has_explicit_tool_flag(["--allowed-tools=Read"])
    assert _has_explicit_tool_flag(["--disallowedTools=Bash"])
    assert _has_explicit_tool_flag(["--disallowed-tools", "Bash"])
    assert not _has_explicit_tool_flag(["--verbose", "--output-format", "json"])
    # Prefix must not false-positive on unrelated flags that merely share a prefix.
    assert not _has_explicit_tool_flag(["--toolsette"])


async def test_console_caller_joined_tool_flag_suppresses_default_no_tools(
    fake_cli_path, monkeypatch, tmp_path
):
    record_path = _configure_fake_cli(monkeypatch, tmp_path, mode="envelope")
    client = ConsoleLLMClient(
        _fake_flavor(claude_p, fake_cli_path), extra_argv=["--allowedTools=Read"]
    )

    await client(LLMRequest(user="hello"))

    record = json.loads(record_path.read_text(encoding="utf-8"))
    assert "--allowedTools=Read" in record["argv"]
    assert "--tools" not in record["argv"], (
        "an explicit =-joined tool flag must suppress the appended --tools \"\" default"
    )


# ------------------------------ Q0.1: console path typed model + CLI budget controls


async def test_console_claude_routed_model_and_budget_become_real_argv(
    fake_cli_path, monkeypatch, tmp_path
):
    """Q0.1 (codex Q-C1): the console path previously only LABELED the response with the
    routed model — now the same source becomes a real `--model` control, and the typed
    budget cap lands as `--max-budget-usd`."""

    from ai_workflow_engine.llm_protocol import LLMRequest

    record_path = _configure_fake_cli(monkeypatch, tmp_path, mode="envelope")
    client = ConsoleLLMClient(
        _fake_flavor(claude_p, fake_cli_path), cli_max_budget_usd=0.10
    )

    response = await client(
        LLMRequest(user="Classify mug.", metadata={"model_profile": {"model": "sonnet"}})
    )

    record = json.loads(record_path.read_text(encoding="utf-8"))
    model_at = record["argv"].index("--model")
    assert record["argv"][model_at + 1] == "sonnet"
    budget_at = record["argv"].index("--max-budget-usd")
    assert record["argv"][budget_at + 1] == "0.1"
    assert response.model == "sonnet", "label and argv must come from the SAME source"


async def test_console_claude_without_profile_or_budget_keeps_prior_argv(
    fake_cli_path, monkeypatch, tmp_path
):
    """Degradation pair: no routed profile + no budget -> argv exactly as before Q0.1."""

    from ai_workflow_engine.llm_protocol import LLMRequest

    record_path = _configure_fake_cli(monkeypatch, tmp_path, mode="envelope")
    client = ConsoleLLMClient(_fake_flavor(claude_p, fake_cli_path))

    await client(LLMRequest(user="Classify mug."))

    record = json.loads(record_path.read_text(encoding="utf-8"))
    assert "--model" not in record["argv"]
    assert "--max-budget-usd" not in record["argv"]


async def test_console_budget_is_positive_only_and_non_claude_rejects_it(fake_cli_path):
    with pytest.raises(ValueError, match="cli_max_budget_usd must be a finite positive"):
        ConsoleLLMClient(_fake_flavor(claude_p, fake_cli_path), cli_max_budget_usd=0)

    from ai_workflow_engine.llm_protocol import LLMRequest

    client = ConsoleLLMClient(
        _fake_flavor(codex_exec, fake_cli_path), cli_max_budget_usd=0.10
    )
    with pytest.raises(ValueError, match="does not support cli_max_budget_usd"):
        await client(LLMRequest(user="x"))


async def test_console_claude_conflicting_raw_model_flag_is_loud(fake_cli_path, monkeypatch, tmp_path):
    from ai_workflow_engine.llm_protocol import LLMRequest

    _configure_fake_cli(monkeypatch, tmp_path, mode="envelope")
    client = ConsoleLLMClient(
        _fake_flavor(claude_p, fake_cli_path), extra_argv=["--model", "haiku"]
    )
    with pytest.raises(ValueError, match="conflicting --model"):
        await client(
            LLMRequest(user="x", metadata={"model_profile": {"model": "sonnet"}})
        )


async def test_console_budget_rejects_non_finite_values(fake_cli_path):
    """Q0-C1: the console constructor is the third entry — inf/NaN rejected there too."""

    for bad in (float("inf"), float("-inf"), float("nan")):
        with pytest.raises(ValueError, match="finite"):
            ConsoleLLMClient(_fake_flavor(claude_p, fake_cli_path), cli_max_budget_usd=bad)


async def test_console_error_envelope_raises_typed_failure_with_burn_data(
    fake_cli_path, monkeypatch, tmp_path
):
    """Q-R2: a non-zero claude exit with a result envelope (e.g. error_max_budget_usd)
    raises ConsoleCliError carrying subtype + consumed notional — structured burn data."""

    import json as _json

    from ai_workflow_engine.llm_protocol import LLMRequest

    from ai_workflow_tools.cli_agents import ConsoleCliError

    envelope = _json.dumps(
        {
            "type": "result",
            "subtype": "error_max_budget_usd",
            "is_error": True,
            "result": "",
            "total_cost_usd": 0.112174,
            "usage": {"input_tokens": 3160, "output_tokens": 216},
        }
    )
    record_path = _configure_fake_cli(monkeypatch, tmp_path, mode="envelope", result="ignored")
    monkeypatch.setenv("FAKE_CLI_STDOUT_OVERRIDE", envelope)
    monkeypatch.setenv("FAKE_CLI_EXIT_CODE", "1")
    client = ConsoleLLMClient(_fake_flavor(claude_p, fake_cli_path))

    with pytest.raises(ConsoleCliError) as excinfo:
        await client(LLMRequest(user="x"))

    error = excinfo.value
    assert error.cli_subtype == "error_max_budget_usd"
    assert error.notional_usd == pytest.approx(0.112174)
    assert error.returncode == 1
    assert "error_max_budget_usd" in str(error) and "0.1122" in str(error)


# --------------------------------------------------------------------------------------
# v0.10 Phase 5R (codex finding 1): the console LLM doors consume the ENGINE window.
# Inside a bounded engine invocation, the ambient soft-remaining clamps the console
# subprocess; the client's own timeout may only narrow it — never widen it. Standalone
# calls (no ambient window) keep the explicit bound.
# --------------------------------------------------------------------------------------


class _SpyExternalRunner:
    """Captures the ExternalProcessRequest and returns a valid claude envelope."""

    def __init__(self) -> None:
        self.request = None

    async def __call__(self, _context, request):
        from ai_workflow_engine.models import CapabilityResult

        self.request = request
        stdout = json.dumps(
            {"result": '{"label":"ok"}', "usage": {"input_tokens": 1, "output_tokens": 1}}
        )
        return CapabilityResult(
            status="accepted",
            output={"returncode": 0, "stdout": stdout, "stderr": "", "result": stdout},
        )


async def test_console_llm_client_is_clamped_by_the_ambient_engine_window():
    import time as _t

    from ai_workflow_engine.execution_window import (
        publish_invocation_window,
        reset_invocation_window,
    )

    spy = _SpyExternalRunner()
    client = ConsoleLLMClient(claude_p, timeout_s=240.0, external_runner=spy)

    now = _t.monotonic()
    token = publish_invocation_window(
        soft_deadline_monotonic=now + 7.0,
        hard_deadline_monotonic=now + 8.0,
    )
    try:
        await client(LLMRequest(user="hi"))
    finally:
        reset_invocation_window(token)
    assert spy.request.timeout_s <= 7.0, "the engine window must clamp the console bound"
    assert spy.request.timeout_s > 5.0, "sanity: the clamp is the remaining window, not zero"

    # a TIGHTER explicit client bound still narrows an ample engine window
    tight = ConsoleLLMClient(claude_p, timeout_s=3.0, external_runner=spy)
    now = _t.monotonic()
    token = publish_invocation_window(
        soft_deadline_monotonic=now + 500.0,
        hard_deadline_monotonic=now + 501.0,
    )
    try:
        await tight(LLMRequest(user="hi"))
    finally:
        reset_invocation_window(token)
    assert spy.request.timeout_s == 3.0

    # standalone (no ambient window): the explicit bound stands — no hidden default
    await client(LLMRequest(user="hi"))
    assert spy.request.timeout_s == 240.0


async def test_console_llm_client_inherits_and_records_a_real_engine_window():
    """The real engine -> StructuredLLMNode -> console transport path shares one bound and
    projects the process enforcement decision onto persisted trace truth."""

    from ai_workflow_engine import WorkflowEngineBuilder, run_single_llm
    from ai_workflow_engine.models import RuntimeLimits, WorkflowProfile

    spy = _SpyExternalRunner()
    client = ConsoleLLMClient(claude_p, timeout_s=240.0, external_runner=spy)
    engine = (
        WorkflowEngineBuilder()
        .with_profile(
            WorkflowProfile(
                workflow_type="console_llm_bound",
                limits=RuntimeLimits(timeout_s=5.0),
            )
        )
        .build()
    )

    result = await run_single_llm(
        client,
        "Return a verdict for {item}.",
        Verdict,
        {"item": "mug"},
        engine=engine,
        name="bounded_console_llm",
        return_result=True,
    )

    assert result.status == "completed" and result.output.label == "ok"
    process_bound = spy.request.metadata["process_execution_bound"]
    assert 0 < process_bound["work_timeout_s"] < process_bound["engine_hard_s"] <= 5.0
    assert process_bound["cleanup_headroom_s"] == 0.0
    llm_events = [
        event
        for event in result.trace
        if event.node == "bounded_console_llm" and event.phase == "llm:response"
    ]
    assert llm_events[-1].metadata["process_execution_bound"]["engine_hard_s"] <= 5.0


def test_console_chat_model_is_clamped_by_the_ambient_engine_window():
    import time as _t
    from types import SimpleNamespace

    from ai_workflow_engine.execution_window import (
        publish_invocation_window,
        reset_invocation_window,
    )

    from ai_workflow_tools.cli_agents.console import ConsoleChatModel

    spy = _SpyExternalRunner()
    model = ConsoleChatModel(claude_p, timeout_s=240.0, external_runner=spy)

    now = _t.monotonic()
    token = publish_invocation_window(
        soft_deadline_monotonic=now + 6.0,
        hard_deadline_monotonic=now + 7.0,
    )
    try:
        model.invoke([SimpleNamespace(type="user", content="hi")])
    finally:
        reset_invocation_window(token)
    assert spy.request.timeout_s <= 6.0, "the engine window must clamp the chat-model bound"
    assert spy.request.timeout_s > 4.0

    # standalone: explicit bound stands
    model.invoke([SimpleNamespace(type="user", content="hi")])
    assert spy.request.timeout_s == 240.0


async def test_console_chat_model_inherits_a_real_engine_window():
    from types import SimpleNamespace

    from ai_workflow_engine import WorkflowEngineBuilder, run_single_llm
    from ai_workflow_engine.models import RuntimeLimits, WorkflowProfile
    from ai_workflow_tools.cli_agents.console import ConsoleChatModel

    spy = _SpyExternalRunner()
    model = ConsoleChatModel(claude_p, timeout_s=240.0, external_runner=spy)
    engine = (
        WorkflowEngineBuilder()
        .with_profile(
            WorkflowProfile(
                workflow_type="console_chat_bound",
                limits=RuntimeLimits(timeout_s=5.0),
            )
        )
        .build()
    )

    result = await run_single_llm(
        model,
        "Return a verdict for {item}.",
        Verdict,
        {"item": "mug"},
        engine=engine,
        name="bounded_console_chat",
        return_result=True,
    )

    assert result.status == "completed" and result.output.label == "ok"
    assert 0 < spy.request.timeout_s < 5.0
    process_bound = spy.request.metadata["process_execution_bound"]
    assert process_bound["work_timeout_s"] == spy.request.timeout_s
    llm_events = [
        event
        for event in result.trace
        if event.node == "bounded_console_chat" and event.phase == "llm:response"
    ]
    assert llm_events[-1].metadata["process_execution_bound"]["engine_hard_s"] <= 5.0
