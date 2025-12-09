"""Print job tracking and lifecycle management."""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Callable, Dict, List, Optional, Set


log = logging.getLogger(__name__)


class JobStatus(str, Enum):
    """Print job status states."""
    PRINTING = "printing"
    FINISHED = "finished"
    CANCELLED = "cancelled"


@dataclass
class TrackedJob:
    """Represents a tracked print job."""
    job_id: str
    printer_serial: str
    printer_ip: str
    file_name: str
    status: JobStatus
    started_at: datetime
    finished_at: Optional[datetime] = None
    sent_to_backend: bool = False
    backend_event_id: Optional[str] = None
    
    def to_display_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for GUI display."""
        return {
            "job_id": self.job_id,
            "printer_serial": self.printer_serial,
            "printer_ip": self.printer_ip,
            "file_name": self.file_name,
            "status": self.status.value,
            "started_at": self.started_at.strftime("%H:%M:%S") if self.started_at else "",
            "finished_at": self.finished_at.strftime("%H:%M:%S") if self.finished_at else "",
            "sent": "✓" if self.sent_to_backend else "",
        }


# States that indicate job has been cancelled or failed
CANCELLED_STATES: Set[str] = frozenset({
    "failed",
    "cancelled",
    "canceled",
    "stopped",
    "aborted",
    "error",
})


class PrintJobTracker:
    """
    Track print jobs across all printers.
    
    This class maintains a log of all print jobs and their status transitions.
    It integrates with EventReporter to send events to the backend.
    
    Example usage:
        tracker = PrintJobTracker()
        
        # When a new job starts
        tracker.start_job("SERIAL123", "192.168.1.100", "job-abc", "model.3mf")
        
        # When job finishes
        tracker.finish_job("SERIAL123", "job-abc")
        
        # After reporting to backend
        tracker.mark_as_sent("SERIAL123", "job-abc", "event-xyz")
    """

    def __init__(
        self,
        logger: Optional[logging.Logger] = None,
        on_job_ended: Optional[Callable[[TrackedJob], None]] = None,
    ) -> None:
        """
        Initialize the job tracker.
        
        Args:
            logger: Optional custom logger
            on_job_ended: Callback invoked when a job ends (finished or cancelled)
        """
        self._log = logger or log
        self._on_job_ended = on_job_ended
        
        # All tracked jobs: (printer_serial, job_id) -> TrackedJob
        self._jobs: Dict[tuple[str, str], TrackedJob] = {}
        self._lock = threading.Lock()
        
        # Track currently printing jobs per printer to detect new jobs
        self._current_job_per_printer: Dict[str, str] = {}

    def start_job(
        self,
        printer_serial: str,
        printer_ip: str,
        job_id: str,
        file_name: str,
    ) -> TrackedJob:
        """
        Record a new print job starting.
        
        Args:
            printer_serial: Printer serial number
            printer_ip: Printer IP address
            job_id: Unique job identifier
            file_name: Name of the print file
            
        Returns:
            The created TrackedJob
        """
        key = (printer_serial, job_id)
        
        with self._lock:
            # Check if job already exists
            if key in self._jobs:
                existing = self._jobs[key]
                self._log.debug(
                    "[tracker] Job already exists: %s/%s (status=%s)",
                    printer_serial, job_id[:8], existing.status.value
                )
                return existing
            
            job = TrackedJob(
                job_id=job_id,
                printer_serial=printer_serial,
                printer_ip=printer_ip,
                file_name=file_name,
                status=JobStatus.PRINTING,
                started_at=datetime.now(timezone.utc),
            )
            
            self._jobs[key] = job
            self._current_job_per_printer[printer_serial] = job_id
            
            self._log.info(
                "[tracker] JOB STARTED: printer=%s, job=%s, file=%s",
                printer_serial, job_id[:8] if len(job_id) > 8 else job_id, file_name
            )
            
            return job

    def finish_job(
        self,
        printer_serial: str,
        job_id: str,
    ) -> Optional[TrackedJob]:
        """
        Mark a job as finished.
        
        Args:
            printer_serial: Printer serial number
            job_id: Job identifier
            
        Returns:
            The updated TrackedJob, or None if not found
        """
        return self._end_job(printer_serial, job_id, JobStatus.FINISHED)

    def cancel_job(
        self,
        printer_serial: str,
        job_id: str,
    ) -> Optional[TrackedJob]:
        """
        Mark a job as cancelled.
        
        Args:
            printer_serial: Printer serial number
            job_id: Job identifier
            
        Returns:
            The updated TrackedJob, or None if not found
        """
        return self._end_job(printer_serial, job_id, JobStatus.CANCELLED)

    def _end_job(
        self,
        printer_serial: str,
        job_id: str,
        new_status: JobStatus,
    ) -> Optional[TrackedJob]:
        """End a job with the specified status."""
        key = (printer_serial, job_id)
        
        with self._lock:
            job = self._jobs.get(key)
            if not job:
                self._log.debug(
                    "[tracker] Job not found for end: %s/%s",
                    printer_serial, job_id[:8] if len(job_id) > 8 else job_id
                )
                return None
            
            # Skip if already ended
            if job.status in (JobStatus.FINISHED, JobStatus.CANCELLED):
                self._log.debug(
                    "[tracker] Job already ended: %s/%s (status=%s)",
                    printer_serial, job_id[:8], job.status.value
                )
                return job
            
            # Update job
            job.status = new_status
            job.finished_at = datetime.now(timezone.utc)
            
            # Clear current job for printer
            if self._current_job_per_printer.get(printer_serial) == job_id:
                self._current_job_per_printer.pop(printer_serial, None)
            
            self._log.info(
                "[tracker] JOB %s: printer=%s, job=%s, file=%s",
                new_status.value.upper(),
                printer_serial,
                job_id[:8] if len(job_id) > 8 else job_id,
                job.file_name
            )
        
        # Invoke callback outside lock
        if self._on_job_ended:
            try:
                self._on_job_ended(job)
            except Exception as error:
                self._log.warning(
                    "[tracker] Job ended callback failed: %s", error
                )
        
        return job

    def mark_as_sent(
        self,
        printer_serial: str,
        job_id: str,
        event_id: Optional[str] = None,
    ) -> bool:
        """
        Mark a job as successfully sent to backend.
        
        Args:
            printer_serial: Printer serial number
            job_id: Job identifier
            event_id: Event ID returned from backend
            
        Returns:
            True if job was found and updated
        """
        key = (printer_serial, job_id)
        
        with self._lock:
            job = self._jobs.get(key)
            if not job:
                return False
            
            job.sent_to_backend = True
            job.backend_event_id = event_id
            
            self._log.info(
                "[tracker] JOB SENT: printer=%s, job=%s, event_id=%s",
                printer_serial,
                job_id[:8] if len(job_id) > 8 else job_id,
                event_id[:8] if event_id and len(event_id) > 8 else event_id
            )
            
            return True

    def get_all_jobs(self) -> List[TrackedJob]:
        """Get all tracked jobs, sorted by start time (newest first)."""
        with self._lock:
            jobs = list(self._jobs.values())
        return sorted(jobs, key=lambda j: j.started_at, reverse=True)

    def get_jobs_for_printer(self, printer_serial: str) -> List[TrackedJob]:
        """Get all jobs for a specific printer."""
        with self._lock:
            jobs = [
                job for job in self._jobs.values()
                if job.printer_serial == printer_serial
            ]
        return sorted(jobs, key=lambda j: j.started_at, reverse=True)

    def get_current_job(self, printer_serial: str) -> Optional[TrackedJob]:
        """Get the current printing job for a printer."""
        with self._lock:
            job_id = self._current_job_per_printer.get(printer_serial)
            if not job_id:
                return None
            return self._jobs.get((printer_serial, job_id))

    def get_pending_jobs(self) -> List[TrackedJob]:
        """Get all ended jobs that haven't been sent to backend yet."""
        with self._lock:
            return [
                job for job in self._jobs.values()
                if job.status in (JobStatus.FINISHED, JobStatus.CANCELLED)
                and not job.sent_to_backend
            ]

    def update_from_status(
        self,
        printer_serial: str,
        printer_ip: str,
        status_data: Dict[str, Any],
    ) -> Optional[TrackedJob]:
        """
        Update job tracking based on printer status.
        
        Detects:
        - New jobs starting (job_id changes while printing)
        - Jobs ending (completion or cancellation)
        
        Args:
            printer_serial: Printer serial number
            printer_ip: Printer IP address
            status_data: Status data from printer
            
        Returns:
            TrackedJob if a state change occurred, else None
        """
        job_id = self._extract_job_id(status_data)
        file_name = self._extract_file_name(status_data)
        gcode_state = self._extract_gcode_state(status_data)
        
        if not job_id:
            return None
        
        # Check if this is a new job
        current_job = self.get_current_job(printer_serial)
        is_printing = self._is_printing_state(gcode_state)
        
        if is_printing and (not current_job or current_job.job_id != job_id):
            # New job detected
            return self.start_job(
                printer_serial=printer_serial,
                printer_ip=printer_ip,
                job_id=job_id,
                file_name=file_name or "unknown",
            )
        
        # Check for job end states (handled by completion monitor)
        return None

    def _extract_job_id(self, status_data: Dict[str, Any]) -> Optional[str]:
        """Extract job ID from status data."""
        for key in ("currentJobId", "job_id", "task_id", "print_id"):
            value = status_data.get(key)
            if value and isinstance(value, str) and value.strip():
                return value.strip()
        return None

    def _extract_file_name(self, status_data: Dict[str, Any]) -> Optional[str]:
        """Extract file name from status data."""
        for key in ("fileName", "file_name", "subtask_name", "gcode_file"):
            value = status_data.get(key)
            if value and isinstance(value, str) and value.strip():
                return value.strip()
        return None

    def _extract_gcode_state(self, status_data: Dict[str, Any]) -> Optional[str]:
        """Extract gcode state from status data."""
        for key in ("gcodeState", "gcode_state", "state"):
            value = status_data.get(key)
            if value and isinstance(value, str):
                return value.strip()
        return None

    def _is_printing_state(self, state: Optional[str]) -> bool:
        """Check if state indicates active printing."""
        if not state:
            return False
        normalized = state.strip().lower()
        return normalized in {"printing", "running", "prepare", "preheating"}

    @staticmethod
    def is_cancelled_state(state: Optional[str]) -> bool:
        """Check if state indicates cancellation or failure."""
        if not state:
            return False
        normalized = state.strip().lower()
        return normalized in CANCELLED_STATES
