"""Smallest public engine workflow: typed input -> one deterministic step -> typed output."""

from __future__ import annotations

import asyncio

from pydantic import BaseModel

from ai_workflow_engine import WorkflowBuilder, WorkflowEngineBuilder


class NormalizeInput(BaseModel):
    text: str


class NormalizeOutput(BaseModel):
    normalized: str


def normalize(_context, payload: NormalizeInput) -> NormalizeOutput:
    return NormalizeOutput(normalized=" ".join(payload.text.split()))


async def main() -> None:
    builder = WorkflowEngineBuilder()
    builder.register_capability(
        "normalize",
        normalize,
        kind="deterministic",
        input_model=NormalizeInput,
        output_model=NormalizeOutput,
    )
    builder.register_workflow(WorkflowBuilder("normalize_text").step("normalize").build())
    result = await builder.build().run(
        "normalize_text", NormalizeInput(text="too   many spaces")
    )
    assert result.status == "completed"
    assert result.output == NormalizeOutput(normalized="too many spaces")
    print(result.output.model_dump_json())


if __name__ == "__main__":
    asyncio.run(main())
