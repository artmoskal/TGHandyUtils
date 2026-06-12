import pytest
import ai_workflow_tools
import json
import sys

@pytest.mark.unit
def test_version():
    assert ai_workflow_tools.__version__ == "0.1.0"

@pytest.mark.unit
def test_engine_import():
    from ai_workflow_engine.models import CapabilityContext
    assert CapabilityContext is not None


@pytest.mark.unit
def test_fake_cli_envelope_records_stdin(run_fake_cli, tmp_path):
    completed, record_path, _workspace = run_fake_cli(mode="envelope", tmp_path=tmp_path, input_text="hello agent")

    assert completed.returncode == 0
    envelope = json.loads(completed.stdout)
    assert envelope["result"] == "{\"ok\": true}"
    assert envelope["usage"]["input_tokens"] == 11
    record = json.loads(record_path.read_text())
    assert record["stdin"] == "hello agent"
    assert record["argv"][0] == str(record_path.with_name("fake_cli.py")) or record["argv"][0].endswith("fake_cli.py")


@pytest.mark.unit
def test_fake_cli_artifacts_result_file_garbage_and_sleep_modes(run_fake_cli, tmp_path):
    artifact_run, _record_path, workspace = run_fake_cli(mode="artifacts", tmp_path=tmp_path)
    assert artifact_run.returncode == 0
    assert (workspace / "screenshot.png").read_bytes().startswith(b"\x89PNG")
    assert (workspace / "session.md").read_text() == "session notes\n"
    assert (workspace / "page-1.yml").read_text() == "url: https://example.test\n"

    result_file = tmp_path / "last-message.txt"
    result_run, _record_path, _workspace = run_fake_cli(
        mode="result_file",
        tmp_path=tmp_path,
        args=["--output-last-message", str(result_file)],
    )
    assert result_run.returncode == 0
    assert result_file.read_text() == "{\"ok\": true}"
    assert "stdout fallback" in result_run.stdout

    garbage_run, _record_path, _workspace = run_fake_cli(mode="garbage", tmp_path=tmp_path)
    assert garbage_run.returncode == 0
    assert garbage_run.stdout == "not json at all\n"

    sleep_run, _record_path, _workspace = run_fake_cli(mode="sleep", tmp_path=tmp_path)
    assert sleep_run.returncode == 0
    assert "slept" in sleep_run.stdout
    assert sys.executable
