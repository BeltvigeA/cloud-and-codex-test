"""Print workflow automation module for automated print sequences."""

from __future__ import annotations

from typing import Optional

from .completion_monitor import (
    PrintCompletionMonitor,
    CompletionResult,
    COMPLETION_STATES,
    FAILED_STATES,
    PRINTING_STATES,
)
from .job_tracker import PrintJobTracker, TrackedJob, JobStatus


def make_job_key(job_id: Optional[str], file_name: str) -> str:
    """Create a consistent key for job deduplication.
    
    This ensures both StatusSubscriber and JobTracker use the same
    format for tracking jobs that don't have a job_id.
    
    Args:
        job_id: The job ID if available, can be None or empty string.
        file_name: The file name to use as fallback.
        
    Returns:
        The job_id if truthy, otherwise "_local_{file_name}".
    """
    if job_id:
        return job_id
    return f"_local_{file_name}"


__all__ = [
    "PrintCompletionMonitor",
    "CompletionResult",
    "COMPLETION_STATES",
    "FAILED_STATES",
    "PRINTING_STATES",
    "PrintJobTracker",
    "TrackedJob",
    "JobStatus",
    "make_job_key",
]
