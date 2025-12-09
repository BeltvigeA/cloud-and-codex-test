"""Unit tests for PrintCompletionMonitor."""

from __future__ import annotations

import pytest

from client.printflow.completion_monitor import (
    PrintCompletionMonitor,
    CompletionResult,
    COMPLETION_STATES,
    PRINTING_STATES,
)


class TestIsCompleted:
    """Tests for is_print_completed method."""

    def test_completion_state_finish(self) -> None:
        """Detect completion via gcodeState=FINISH."""
        monitor = PrintCompletionMonitor()
        status = {"gcodeState": "FINISH"}
        
        completed, reason = monitor.is_print_completed(status)
        
        assert completed is True
        assert "gcodeState" in reason
        assert "FINISH" in reason

    def test_completion_state_finished(self) -> None:
        """Detect completion via gcodeState=finished (lowercase)."""
        monitor = PrintCompletionMonitor()
        status = {"gcodeState": "finished"}
        
        completed, reason = monitor.is_print_completed(status)
        
        assert completed is True

    def test_completion_state_completed(self) -> None:
        """Detect completion via gcodeState=COMPLETED."""
        monitor = PrintCompletionMonitor()
        status = {"gcodeState": "COMPLETED"}
        
        completed, reason = monitor.is_print_completed(status)
        
        assert completed is True

    def test_completion_progress_100(self) -> None:
        """Detect completion via progressPercent=100."""
        monitor = PrintCompletionMonitor()
        status = {"gcodeState": "IDLE", "progressPercent": 100}
        
        completed, reason = monitor.is_print_completed(status)
        
        assert completed is True
        assert "progressPercent" in reason

    def test_completion_progress_over_100(self) -> None:
        """Handle edge case of progressPercent > 100."""
        monitor = PrintCompletionMonitor()
        status = {"progressPercent": 100.5}
        
        completed, reason = monitor.is_print_completed(status)
        
        assert completed is True

    def test_no_completion_when_printing(self) -> None:
        """Return False when still printing."""
        monitor = PrintCompletionMonitor()
        status = {"gcodeState": "RUNNING", "progressPercent": 45}
        
        completed, reason = monitor.is_print_completed(status)
        
        assert completed is False
        assert reason == ""

    def test_no_completion_idle_no_progress(self) -> None:
        """Return False when IDLE but no progress made (not a completion)."""
        monitor = PrintCompletionMonitor()
        status = {"gcodeState": "IDLE", "progressPercent": 0}
        
        completed, reason = monitor.is_print_completed(status)
        
        assert completed is False

    def test_state_fallback(self) -> None:
        """Detect completion via 'state' field fallback."""
        monitor = PrintCompletionMonitor()
        status = {"state": "complete"}
        
        completed, reason = monitor.is_print_completed(status)
        
        assert completed is True

    def test_gcode_state_alternate_key(self) -> None:
        """Support gcode_state (underscore) key."""
        monitor = PrintCompletionMonitor()
        status = {"gcode_state": "FINISH"}
        
        completed, reason = monitor.is_print_completed(status)
        
        assert completed is True


class TestCheckAndNotify:
    """Tests for check_and_notify method with callbacks and deduplication."""

    def test_callback_invoked_on_completion(self) -> None:
        """Verify callback is called when print completes."""
        monitor = PrintCompletionMonitor(debounce_count=1)
        callback_results: list[CompletionResult] = []
        
        status = {
            "gcodeState": "FINISH",
            "currentJobId": "job-123",
            "fileName": "test_model.3mf",
        }
        
        result = monitor.check_and_notify(
            status_data=status,
            printer_serial="PRINTER001",
            on_completion=lambda r: callback_results.append(r),
        )
        
        assert result.completed is True
        assert len(callback_results) == 1
        assert callback_results[0].file_name == "test_model.3mf"
        assert callback_results[0].job_id == "job-123"

    def test_duplicate_job_not_reported_twice(self) -> None:
        """Ensure same job ID only triggers callback once."""
        monitor = PrintCompletionMonitor(debounce_count=1)
        callback_count = 0
        
        def count_callback(r: CompletionResult) -> None:
            nonlocal callback_count
            callback_count += 1
        
        status = {
            "gcodeState": "FINISH",
            "currentJobId": "job-456",
        }
        
        # First call - should notify
        result1 = monitor.check_and_notify(
            status_data=status,
            printer_serial="PRINTER001",
            on_completion=count_callback,
        )
        
        # Second call - same job, should NOT notify again
        result2 = monitor.check_and_notify(
            status_data=status,
            printer_serial="PRINTER001",
            on_completion=count_callback,
        )
        
        assert result1.completed is True
        assert result2.completed is True
        assert "already reported" in result2.reason
        assert callback_count == 1

    def test_different_printers_tracked_separately(self) -> None:
        """Each printer tracks its own completed jobs."""
        monitor = PrintCompletionMonitor(debounce_count=1)
        callback_count = 0
        
        def count_callback(r: CompletionResult) -> None:
            nonlocal callback_count
            callback_count += 1
        
        status = {
            "gcodeState": "FINISH",
            "currentJobId": "job-789",
        }
        
        # Same job ID but different printers
        monitor.check_and_notify(status, "PRINTER_A", count_callback)
        monitor.check_and_notify(status, "PRINTER_B", count_callback)
        
        assert callback_count == 2

    def test_debounce_requires_multiple_confirmations(self) -> None:
        """Debouncing requires N consecutive confirmations."""
        monitor = PrintCompletionMonitor(debounce_count=3)
        callback_count = 0
        
        def count_callback(r: CompletionResult) -> None:
            nonlocal callback_count
            callback_count += 1
        
        status = {
            "gcodeState": "FINISH",
            "currentJobId": "debounce-job",
        }
        
        # First two calls should NOT trigger callback
        r1 = monitor.check_and_notify(status, "P1", count_callback)
        r2 = monitor.check_and_notify(status, "P1", count_callback)
        
        assert r1.completed is False
        assert r2.completed is False
        assert callback_count == 0
        
        # Third call should trigger callback
        r3 = monitor.check_and_notify(status, "P1", count_callback)
        
        assert r3.completed is True
        assert callback_count == 1

    def test_debounce_resets_on_printing_state(self) -> None:
        """Returning to printing state resets debounce counter."""
        monitor = PrintCompletionMonitor(debounce_count=2)
        
        finish_status = {"gcodeState": "FINISH", "currentJobId": "reset-job"}
        printing_status = {"gcodeState": "RUNNING", "currentJobId": "reset-job"}
        
        # Start debouncing
        r1 = monitor.check_and_notify(finish_status, "P1")
        assert r1.completed is False  # debounce 1/2
        
        # Go back to printing - should reset
        r2 = monitor.check_and_notify(printing_status, "P1")
        assert r2.completed is False
        
        # Start debouncing again from 1
        r3 = monitor.check_and_notify(finish_status, "P1")
        assert r3.completed is False  # debounce 1/2 (reset)

    def test_no_job_id_still_works(self) -> None:
        """Completion detection works even without job ID."""
        monitor = PrintCompletionMonitor(debounce_count=1)
        callback_called = False
        
        def callback(r: CompletionResult) -> None:
            nonlocal callback_called
            callback_called = True
        
        status = {"gcodeState": "FINISH"}  # No currentJobId
        
        result = monitor.check_and_notify(status, "P1", callback)
        
        # Without job ID, we can't deduplicate, but we still notify
        assert result.completed is True
        assert callback_called is True


class TestClearCompletedJobs:
    """Tests for clear_completed_jobs method."""

    def test_clear_specific_printer(self) -> None:
        """Clear completed jobs for specific printer."""
        monitor = PrintCompletionMonitor(debounce_count=1)
        
        status = {"gcodeState": "FINISH", "currentJobId": "job-1"}
        
        monitor.check_and_notify(status, "PRINTER_A")
        monitor.check_and_notify(status, "PRINTER_B")
        
        assert "job-1" in monitor.get_completed_jobs("PRINTER_A")
        assert "job-1" in monitor.get_completed_jobs("PRINTER_B")
        
        monitor.clear_completed_jobs("PRINTER_A")
        
        assert "job-1" not in monitor.get_completed_jobs("PRINTER_A")
        assert "job-1" in monitor.get_completed_jobs("PRINTER_B")

    def test_clear_all_printers(self) -> None:
        """Clear completed jobs for all printers."""
        monitor = PrintCompletionMonitor(debounce_count=1)
        
        status = {"gcodeState": "FINISH", "currentJobId": "job-2"}
        
        monitor.check_and_notify(status, "PRINTER_A")
        monitor.check_and_notify(status, "PRINTER_B")
        
        monitor.clear_completed_jobs()
        
        assert len(monitor.get_completed_jobs("PRINTER_A")) == 0
        assert len(monitor.get_completed_jobs("PRINTER_B")) == 0


class TestExtractMethods:
    """Tests for data extraction helper methods."""

    def test_extract_progress_percent(self) -> None:
        """Extract progress from progressPercent key."""
        monitor = PrintCompletionMonitor()
        status = {"progressPercent": 75.5}
        
        progress = monitor._extract_progress(status)
        
        assert progress == 75.5

    def test_extract_progress_mc_percent(self) -> None:
        """Extract progress from mc_percent key."""
        monitor = PrintCompletionMonitor()
        status = {"mc_percent": 50}
        
        progress = monitor._extract_progress(status)
        
        assert progress == 50.0

    def test_extract_file_name(self) -> None:
        """Extract file name from various keys."""
        monitor = PrintCompletionMonitor()
        
        assert monitor._extract_file_name({"fileName": "test.3mf"}) == "test.3mf"
        assert monitor._extract_file_name({"file_name": "model.gcode"}) == "model.gcode"
        assert monitor._extract_file_name({"subtask_name": "print.3mf"}) == "print.3mf"

    def test_extract_job_id(self) -> None:
        """Extract job ID from various keys."""
        monitor = PrintCompletionMonitor()
        
        assert monitor._extract_job_id({"currentJobId": "abc123"}) == "abc123"
        assert monitor._extract_job_id({"job_id": "xyz789"}) == "xyz789"
        assert monitor._extract_job_id({"task_id": "task001"}) == "task001"


class TestCompletionResult:
    """Tests for CompletionResult dataclass."""

    def test_bool_completed(self) -> None:
        """CompletionResult is truthy when completed."""
        result = CompletionResult(completed=True, reason="test")
        assert bool(result) is True

    def test_bool_not_completed(self) -> None:
        """CompletionResult is falsy when not completed."""
        result = CompletionResult(completed=False, reason="")
        assert bool(result) is False

    def test_immutable(self) -> None:
        """CompletionResult is frozen/immutable."""
        result = CompletionResult(completed=True, reason="test")
        
        with pytest.raises(AttributeError):
            result.completed = False  # type: ignore
