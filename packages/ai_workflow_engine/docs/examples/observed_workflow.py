"""One observed workflow: engine writes a bundle; standalone viewer reads the logical run."""

from __future__ import annotations

import asyncio
from pathlib import Path
from tempfile import TemporaryDirectory

from ai_workflow_engine import ObservationConfig, WorkflowBuilder, WorkflowEngineBuilder
from ai_workflow_viewer import FileEventSource, observation_group_to_html


def inspect(_context, payload: dict) -> dict:
    return {"characters": len(payload["text"]), "accepted": True}


async def main() -> None:
    with TemporaryDirectory(prefix="engine-observation-") as directory:
        builder = WorkflowEngineBuilder().with_observation(
            ObservationConfig(enabled=True, bundle_dir=directory, capture="full")
        )
        builder.register_capability("inspect", inspect, kind="deterministic")
        builder.register_workflow(WorkflowBuilder("observed").step("inspect").build())
        result = await builder.build().run("observed", {"text": "inspect me"})
        assert result.status == "completed"
        assert result.observation_bundle_path

        run_id = Path(result.observation_bundle_path).name.split("--", 1)[0]
        group = FileEventSource(directory).read_group(run_id)
        assert group.status == "completed"
        page = Path(directory) / "run.html"
        page.write_text(observation_group_to_html(group), encoding="utf-8")
        print(page)


if __name__ == "__main__":
    asyncio.run(main())
