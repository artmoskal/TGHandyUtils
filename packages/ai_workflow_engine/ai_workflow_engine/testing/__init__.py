"""Adapter conformance kits (dependency-free: plain asserts, no pytest import).

Public surface (documented in the consumer handoffs as
``from ai_workflow_engine.testing import ...``):

- :func:`run_wait_registration_conformance` — the FULL durable-wait lifecycle contract
  every persistent ``WaitCoordinator`` adapter must pass, including the failed/health and
  zero-integrity shape (v0.11.5).
- :func:`run_wait_integrity_conformance` — adapter-specific corruption reporting
  (v0.11.5): products supply inject/repair callbacks for THEIR backend; the runtime
  coordinator protocol gains no corruption controls.
"""

from ai_workflow_engine.testing.wait_contract import (
    run_wait_integrity_conformance,
    run_wait_registration_conformance,
)

__all__ = ["run_wait_integrity_conformance", "run_wait_registration_conformance"]
