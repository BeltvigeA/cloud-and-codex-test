"""Print completion detection and monitoring."""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable, Dict, Optional, Set, Tuple


log = logging.getLogger(__name__)


@dataclass(frozen=True)
class CompletionResult:
    """Result of a print completion check."""
    completed: bool
    reason: str
    job_id: Optional[str] = None
    file_name: Optional[str] = None
    printer_serial: Optional[str] = None
    timestamp: Optional[str] = None

    def __bool__(self) -> bool:
        return self.completed


# Canonical states indicating print job has finished
COMPLETION_STATES: Set[str] = frozenset({
    "finish",
    "finished",
    "completed",
    "complete",
})

# States indicating print job has failed or was cancelled
FAILED_STATES: Set[str] = frozenset({
    "failed",
    "cancelled",
    "canceled",
    "stopped",
    "aborted",
    "error",
})

# States indicating printer is actively printing
PRINTING_STATES: Set[str] = frozenset({
    "running",
    "printing",
    "prepare",
    "preheating",
    "slicing",
})

# Timeout for progress=100 fallback detection (seconds)
PROGRESS_100_TIMEOUT: float = 10.0


class PrintCompletionMonitor:
    """
    Monitor print jobs and detect completion.
    
    This class provides a standalone, reusable way to detect when a print
    job has completed. It tracks job IDs to avoid duplicate notifications
    and uses multiple detection methods for robustness.
    
    Example usage:
        monitor = PrintCompletionMonitor()
        
        def on_complete(result: CompletionResult):
            print(f"Print finished: {result.file_name}")
        
        # Called each time status is received
        result = monitor.check_and_notify(
            status_data=printer_status,
            printer_serial="ABC123",
            on_completion=on_complete
        )
    """

    # Number of consecutive completion confirmations before reporting
    DEBOUNCE_COUNT: int = 1

    def __init__(
        self,
        logger: Optional[logging.Logger] = None,
        debounce_count: int = 1,
        progress_100_timeout: float = 10.0,
    ) -> None:
        """
        Initialize the completion monitor.
        
        Args:
            logger: Optional custom logger
            debounce_count: Number of consecutive confirmations required
        """
        self._log = logger or log
        self._debounce_count = max(1, debounce_count)
        self._progress_100_timeout = max(1.0, float(progress_100_timeout))
        
        # Track completed jobs per printer: serial -> set of job IDs
        self._completed_jobs: Dict[str, Set[str]] = {}
        self._lock = threading.Lock()
        
        # Track consecutive completion counts for debouncing
        # Key: (serial, job_id), Value: consecutive count
        self._pending_completions: Dict[Tuple[str, str], int] = {}
        
        # Track previous printing state per printer for transition detection
        self._was_printing: Dict[str, bool] = {}
        
        # Track when progress reached 100% for timeout-based detection
        # Key: (serial, job_id), Value: timestamp (monotonic)
        self._progress_100_since: Dict[Tuple[str, str], float] = {}

    def is_print_completed(
        self,
        status_data: Dict[str, Any],
    ) -> Tuple[bool, str]:
        """
        Check if a print job has completed based on status data.
        
        Detection priority:
        1. gcodeState in completion states (FINISH, COMPLETED, etc.)
        2. progressPercent >= 100
        3. Transition from PRINTING to IDLE (uses state tracking)
        
        Args:
            status_data: Status dictionary from printer
            
        Returns:
            Tuple of (completed: bool, reason: str)
        """
        # Method 1: Check gcodeState for explicit completion
        gcode_state = self._extract_gcode_state(status_data)
        if gcode_state:
            normalized = gcode_state.strip().lower()
            if normalized in COMPLETION_STATES:
                return True, f"gcodeState={gcode_state}"
        
        # Method 2: Check progress percentage
        progress = self._extract_progress(status_data)
        if progress is not None and progress >= 100.0:
            return True, f"progressPercent={progress}"
        
        # Method 3: Detection via state field
        state = self._extract_state(status_data)
        if state:
            normalized_state = state.strip().lower()
            if normalized_state in COMPLETION_STATES:
                return True, f"state={state}"
        
        return False, ""

    def check_and_notify(
        self,
        status_data: Dict[str, Any],
        printer_serial: str,
        on_completion: Optional[Callable[[CompletionResult], None]] = None,
    ) -> CompletionResult:
        """
        Check completion status and invoke callback if completed.
        
        This method handles:
        - Debouncing (requires multiple confirmations)
        - Duplicate prevention (same job ID not reported twice)
        - Callback invocation
        - Logging
        
        Args:
            status_data: Status dictionary from printer
            printer_serial: Printer serial number
            on_completion: Optional callback invoked on first completion detection
            
        Returns:
            CompletionResult with completion status and details
        """
        job_id = self._extract_job_id(status_data)
        file_name = self._extract_file_name(status_data)
        
        # Check if print is completed
        completed, reason = self.is_print_completed(status_data)
        
        # Track printing state for transition detection
        is_printing = self._is_currently_printing(status_data)
        was_printing = self._was_printing.get(printer_serial, False)
        self._was_printing[printer_serial] = is_printing
        
        # If not completed, reset any pending debounce counter
        if not completed:
            if job_id:
                key = (printer_serial, job_id)
                self._pending_completions.pop(key, None)
            return CompletionResult(
                completed=False,
                reason="",
                job_id=job_id,
                file_name=file_name,
                printer_serial=printer_serial,
            )
        
        # Check if this job was already reported
        # Use job_id if available, otherwise fall back to file_name for deduplication
        dedup_key = job_id or file_name or "unknown"
        
        with self._lock:
            reported_jobs = self._completed_jobs.get(printer_serial, set())
            if dedup_key in reported_jobs:
                # Already reported, don't report again (debug level to reduce spam)
                self._log.debug(
                    "[completion] Already reported for %s: %s",
                    printer_serial,
                    dedup_key[:20] if dedup_key else "unknown",
                )
                return CompletionResult(
                    completed=True,
                    reason=f"{reason} (already reported)",
                    job_id=job_id,
                    file_name=file_name,
                    printer_serial=printer_serial,
                )
        
        # Debounce: require multiple consecutive confirmations
        if job_id:
            key = (printer_serial, job_id)
            count = self._pending_completions.get(key, 0) + 1
            self._pending_completions[key] = count
            
            if count < self._debounce_count:
                self._log.debug(
                    "[completion] Pending confirmation %d/%d for %s job=%s",
                    count,
                    self._debounce_count,
                    printer_serial,
                    job_id[:8] if job_id else "unknown",
                )
                return CompletionResult(
                    completed=False,
                    reason=f"debounce {count}/{self._debounce_count}",
                    job_id=job_id,
                    file_name=file_name,
                    printer_serial=printer_serial,
                )
        
        # Completion confirmed - record and notify
        timestamp = datetime.now(timezone.utc).isoformat()
        
        with self._lock:
            if printer_serial not in self._completed_jobs:
                self._completed_jobs[printer_serial] = set()
            # Always add dedup_key (job_id or file_name) to prevent repeated logging
            self._completed_jobs[printer_serial].add(dedup_key)
            # Clean up pending counter
            if job_id:
                key = (printer_serial, job_id)
                self._pending_completions.pop(key, None)
        
        result = CompletionResult(
            completed=True,
            reason=reason,
            job_id=job_id,
            file_name=file_name,
            printer_serial=printer_serial,
            timestamp=timestamp,
        )
        
        # Log the completion
        self._log.info(
            "✅ PRINT COMPLETED: printer=%s, job=%s, file=%s, reason=%s",
            printer_serial,
            job_id[:8] if job_id else "unknown",
            file_name or "unknown",
            reason,
        )
        
        # Invoke callback if provided
        if on_completion:
            try:
                on_completion(result)
            except Exception as error:
                self._log.warning(
                    "Completion callback failed for %s: %s",
                    printer_serial,
                    error,
                )
        
        return result

    def clear_completed_jobs(self, printer_serial: Optional[str] = None) -> None:
        """
        Clear the record of completed jobs.
        
        Args:
            printer_serial: If provided, clear only for this printer.
                           If None, clear all.
        """
        with self._lock:
            if printer_serial:
                self._completed_jobs.pop(printer_serial, None)
                # Also clear pending completions for this printer
                keys_to_remove = [
                    key for key in self._pending_completions
                    if key[0] == printer_serial
                ]
                for key in keys_to_remove:
                    self._pending_completions.pop(key, None)
            else:
                self._completed_jobs.clear()
                self._pending_completions.clear()
        
        self._log.debug(
            "[completion] Cleared completed jobs for %s",
            printer_serial or "all printers",
        )

    def get_completed_jobs(self, printer_serial: str) -> Set[str]:
        """Get the set of completed job IDs for a printer."""
        with self._lock:
            return self._completed_jobs.get(printer_serial, set()).copy()

    def _is_currently_printing(self, status_data: Dict[str, Any]) -> bool:
        """Check if the printer is currently printing."""
        gcode_state = self._extract_gcode_state(status_data)
        if gcode_state:
            normalized = gcode_state.strip().lower()
            if normalized in PRINTING_STATES:
                return True
        
        state = self._extract_state(status_data)
        if state:
            normalized = state.strip().lower()
            if normalized in PRINTING_STATES:
                return True
        
        # Also check progress - if > 0 and < 100, likely printing
        progress = self._extract_progress(status_data)
        if progress is not None and 0 < progress < 100:
            return True
        
        return False

    def is_print_failed(
        self,
        status_data: Dict[str, Any],
    ) -> Tuple[bool, str]:
        """
        Check if a print job has failed or been cancelled.
        
        Args:
            status_data: Status dictionary from printer
            
        Returns:
            Tuple of (failed: bool, reason: str)
        """
        # Check gcodeState for failure states
        gcode_state = self._extract_gcode_state(status_data)
        if gcode_state:
            normalized = gcode_state.strip().lower()
            if normalized in FAILED_STATES:
                return True, f"gcodeState={gcode_state}"
        
        # Check general state field
        state = self._extract_state(status_data)
        if state:
            normalized = state.strip().lower()
            if normalized in FAILED_STATES:
                return True, f"state={state}"
        
        return False, ""

    def _extract_gcode_state(self, status_data: Dict[str, Any]) -> Optional[str]:
        """Extract gcode state from status data."""
        for key in ("gcodeState", "gcode_state", "subtask_name"):
            value = status_data.get(key)
            if value and isinstance(value, str):
                return value.strip()
        return None

    def _extract_state(self, status_data: Dict[str, Any]) -> Optional[str]:
        """Extract general state from status data."""
        for key in ("state", "printer_state", "job_state"):
            value = status_data.get(key)
            if value and isinstance(value, str):
                return value.strip()
        return None

    def _extract_progress(self, status_data: Dict[str, Any]) -> Optional[float]:
        """Extract progress percentage from status data."""
        for key in ("progressPercent", "mc_percent", "progress", "percentage"):
            value = status_data.get(key)
            if value is not None:
                try:
                    return float(value)
                except (TypeError, ValueError):
                    continue
        return None

    def _extract_job_id(self, status_data: Dict[str, Any]) -> Optional[str]:
        """Extract job ID from status data."""
        for key in ("currentJobId", "job_id", "task_id", "print_id"):
            value = status_data.get(key)
            if value and isinstance(value, str):
                return value.strip()
        return None

    def _extract_file_name(self, status_data: Dict[str, Any]) -> Optional[str]:
        """Extract file name from status data."""
        for key in ("fileName", "file_name", "subtask_name", "gcode_file"):
            value = status_data.get(key)
            if value and isinstance(value, str):
                return value.strip()
        return None
