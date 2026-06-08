"""Human clarification capability for workflow pause/resume decisions."""

from __future__ import annotations

import inspect
from typing import Awaitable, Protocol

from ai_workflow_engine.models import (
    CapabilityContext,
    CapabilityResult,
    CapabilitySpec,
    HumanClarificationRequest,
    HumanClarificationResponse,
)


class HumanClarificationChannel(Protocol):
    """Adapter that asks a human question through a product-owned channel."""

    def request(
        self,
        context: CapabilityContext,
        request: HumanClarificationRequest,
    ) -> HumanClarificationResponse | Awaitable[HumanClarificationResponse]:
        ...


class InMemoryHumanClarificationChannel:
    """In-memory channel for tests and short local runs."""

    def __init__(self) -> None:
        self.pending: dict[str, HumanClarificationRequest] = {}
        self.answers: dict[str, HumanClarificationResponse] = {}

    def request(
        self,
        _context: CapabilityContext,
        request: HumanClarificationRequest,
    ) -> HumanClarificationResponse:
        answer = self.answers.pop(request.clarification_id, None)
        if answer:
            self.pending.pop(request.clarification_id, None)
            return answer
        if request.continue_without_answer and request.default_value is not None:
            return HumanClarificationResponse(
                clarification_id=request.clarification_id,
                status="provisional",
                value=request.default_value,
                provisional=True,
                metadata={"continued_without_answer": True},
            )
        self.pending[request.clarification_id] = request
        return HumanClarificationResponse(
            clarification_id=request.clarification_id,
            status="pending",
            metadata={"waiting_for_user": True},
        )

    def submit_answer(
        self,
        clarification_id: str,
        value: str,
        *,
        answer_text: str | None = None,
        metadata: dict[str, object] | None = None,
    ) -> None:
        self.answers[clarification_id] = HumanClarificationResponse(
            clarification_id=clarification_id,
            status="answered",
            value=value,
            answer_text=answer_text,
            metadata=dict(metadata or {}),
        )


class HumanClarificationCapability:
    """Registers a reusable `ask_user` workflow capability."""

    def __init__(
        self,
        channel: HumanClarificationChannel,
        *,
        name: str = "ask_user",
        side_effects: tuple[str, ...] = ("notification",),
    ) -> None:
        self.channel = channel
        self.spec = CapabilitySpec(
            name=name,
            kind="human",
            description="Ask a human for workflow clarification or direction.",
            input_model=HumanClarificationRequest,
            output_model=HumanClarificationResponse,
            side_effects=list(side_effects),
        )

    async def __call__(
        self,
        context: CapabilityContext,
        request: HumanClarificationRequest,
    ) -> CapabilityResult:
        response = self.channel.request(context, request)
        if inspect.isawaitable(response):
            response = await response
        waiting = response.status == "pending"
        return CapabilityResult(
            status="partial" if waiting else "accepted",
            output=response,
            metadata={
                "clarification_id": response.clarification_id,
                "waiting_for_user": waiting,
                "provisional": response.provisional,
            },
        )
