"""Adapter conformance kits (dependency-free: plain asserts, no pytest import).

Public surface: :func:`run_wait_registration_conformance` — the FULL durable-wait
lifecycle contract every persistent ``WaitCoordinator`` adapter must pass (documented
in the consumer handoffs as ``from ai_workflow_engine.testing import ...``).
"""

from ai_workflow_engine.testing.wait_contract import run_wait_registration_conformance

__all__ = ["run_wait_registration_conformance"]
