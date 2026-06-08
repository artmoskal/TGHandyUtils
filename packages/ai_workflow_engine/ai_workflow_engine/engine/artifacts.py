"""Workflow artifact cleanup helpers."""

import logging
import os
from typing import Iterable, List

from ai_workflow_engine.models import WorkflowArtifact

logger = logging.getLogger(__name__)


def cleanup_artifacts(artifacts: Iterable[WorkflowArtifact]) -> List[str]:
    """Remove cleanup-enabled local file artifacts and return removed paths."""
    removed: List[str] = []
    for artifact in artifacts:
        if not artifact.cleanup_on_failure or not artifact.path:
            continue
        try:
            if os.path.exists(artifact.path):
                os.remove(artifact.path)
                removed.append(artifact.path)
        except OSError as exc:
            logger.warning("workflow_artifact_cleanup_failed path=%s error=%s", artifact.path, exc)
    return removed
