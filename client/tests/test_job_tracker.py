"""Unit tests for PrintJobTracker."""

from __future__ import annotations

import pytest
from datetime import datetime, timezone

from client.printflow.job_tracker import (
    PrintJobTracker,
    TrackedJob,
    JobStatus,
    CANCELLED_STATES,
)


class TestStartJob:
    """Tests for starting jobs."""

    def test_start_job_creates_tracked_job(self) -> None:
        """Starting a job creates a TrackedJob entry."""
        tracker = PrintJobTracker()
        
        job = tracker.start_job(
            printer_serial="PRINTER01",
            printer_ip="192.168.1.100",
            job_id="job-123",
            file_name="test_model.3mf",
        )
        
        assert job.printer_serial == "PRINTER01"
        assert job.job_id == "job-123"
        assert job.file_name == "test_model.3mf"
        assert job.status == JobStatus.PRINTING
        assert job.finished_at is None
        assert job.sent_to_backend is False

    def test_start_job_duplicate_returns_existing(self) -> None:
        """Starting the same job again returns the existing job."""
        tracker = PrintJobTracker()
        
        job1 = tracker.start_job("P1", "192.168.1.1", "job-abc", "file1.3mf")
        job2 = tracker.start_job("P1", "192.168.1.1", "job-abc", "file1.3mf")
        
        assert job1 is job2


class TestFinishJob:
    """Tests for finishing jobs."""

    def test_finish_job_updates_status(self) -> None:
        """Finishing a job updates status and timestamp."""
        tracker = PrintJobTracker()
        
        tracker.start_job("P1", "192.168.1.1", "job-1", "model.3mf")
        job = tracker.finish_job("P1", "job-1")
        
        assert job is not None
        assert job.status == JobStatus.FINISHED
        assert job.finished_at is not None

    def test_finish_unknown_job_returns_none(self) -> None:
        """Finishing an unknown job returns None."""
        tracker = PrintJobTracker()
        
        result = tracker.finish_job("P1", "unknown-job")
        
        assert result is None

    def test_finish_already_finished_job(self) -> None:
        """Finishing an already finished job returns the job without changes."""
        tracker = PrintJobTracker()
        
        tracker.start_job("P1", "192.168.1.1", "job-1", "model.3mf")
        tracker.finish_job("P1", "job-1")
        first_finish_time = tracker.get_all_jobs()[0].finished_at
        
        job = tracker.finish_job("P1", "job-1")
        
        assert job.status == JobStatus.FINISHED
        assert job.finished_at == first_finish_time  # Unchanged


class TestCancelJob:
    """Tests for cancelling jobs."""

    def test_cancel_job_updates_status(self) -> None:
        """Cancelling a job updates status to CANCELLED."""
        tracker = PrintJobTracker()
        
        tracker.start_job("P1", "192.168.1.1", "job-1", "model.3mf")
        job = tracker.cancel_job("P1", "job-1")
        
        assert job is not None
        assert job.status == JobStatus.CANCELLED


class TestMarkAsSent:
    """Tests for marking jobs as sent."""

    def test_mark_as_sent_sets_flag(self) -> None:
        """Marking as sent sets the sent_to_backend flag."""
        tracker = PrintJobTracker()
        
        tracker.start_job("P1", "192.168.1.1", "job-1", "model.3mf")
        tracker.finish_job("P1", "job-1")
        
        success = tracker.mark_as_sent("P1", "job-1", "event-xyz")
        
        assert success is True
        job = tracker.get_all_jobs()[0]
        assert job.sent_to_backend is True
        assert job.backend_event_id == "event-xyz"

    def test_mark_unknown_job_returns_false(self) -> None:
        """Marking an unknown job returns False."""
        tracker = PrintJobTracker()
        
        success = tracker.mark_as_sent("P1", "unknown", "event-1")
        
        assert success is False


class TestGetJobs:
    """Tests for retrieving jobs."""

    def test_get_all_jobs_sorted_newest_first(self) -> None:
        """get_all_jobs returns jobs sorted by start time (newest first)."""
        tracker = PrintJobTracker()
        
        tracker.start_job("P1", "192.168.1.1", "job-1", "file1.3mf")
        tracker.start_job("P1", "192.168.1.1", "job-2", "file2.3mf")
        tracker.start_job("P1", "192.168.1.1", "job-3", "file3.3mf")
        
        jobs = tracker.get_all_jobs()
        
        assert len(jobs) == 3
        assert jobs[0].job_id == "job-3"  # Newest first

    def test_get_jobs_for_printer(self) -> None:
        """get_jobs_for_printer filters by printer serial."""
        tracker = PrintJobTracker()
        
        tracker.start_job("P1", "192.168.1.1", "job-1", "file1.3mf")
        tracker.start_job("P2", "192.168.1.2", "job-2", "file2.3mf")
        tracker.start_job("P1", "192.168.1.1", "job-3", "file3.3mf")
        
        p1_jobs = tracker.get_jobs_for_printer("P1")
        p2_jobs = tracker.get_jobs_for_printer("P2")
        
        assert len(p1_jobs) == 2
        assert len(p2_jobs) == 1

    def test_get_pending_jobs(self) -> None:
        """get_pending_jobs returns ended jobs not yet sent."""
        tracker = PrintJobTracker()
        
        tracker.start_job("P1", "192.168.1.1", "job-1", "file1.3mf")
        tracker.start_job("P1", "192.168.1.1", "job-2", "file2.3mf")
        tracker.finish_job("P1", "job-1")
        tracker.finish_job("P1", "job-2")
        tracker.mark_as_sent("P1", "job-1", "event-1")
        
        pending = tracker.get_pending_jobs()
        
        assert len(pending) == 1
        assert pending[0].job_id == "job-2"


class TestCallback:
    """Tests for the on_job_ended callback."""

    def test_callback_invoked_on_finish(self) -> None:
        """Callback is invoked when job finishes."""
        ended_jobs: list[TrackedJob] = []
        tracker = PrintJobTracker(on_job_ended=lambda j: ended_jobs.append(j))
        
        tracker.start_job("P1", "192.168.1.1", "job-1", "model.3mf")
        tracker.finish_job("P1", "job-1")
        
        assert len(ended_jobs) == 1
        assert ended_jobs[0].status == JobStatus.FINISHED

    def test_callback_invoked_on_cancel(self) -> None:
        """Callback is invoked when job is cancelled."""
        ended_jobs: list[TrackedJob] = []
        tracker = PrintJobTracker(on_job_ended=lambda j: ended_jobs.append(j))
        
        tracker.start_job("P1", "192.168.1.1", "job-1", "model.3mf")
        tracker.cancel_job("P1", "job-1")
        
        assert len(ended_jobs) == 1
        assert ended_jobs[0].status == JobStatus.CANCELLED


class TestIsCancelledState:
    """Tests for is_cancelled_state static method."""

    def test_cancelled_states(self) -> None:
        """Correctly identifies cancelled states."""
        assert PrintJobTracker.is_cancelled_state("FAILED") is True
        assert PrintJobTracker.is_cancelled_state("cancelled") is True
        assert PrintJobTracker.is_cancelled_state("STOPPED") is True
        assert PrintJobTracker.is_cancelled_state("error") is True

    def test_non_cancelled_states(self) -> None:
        """Correctly rejects non-cancelled states."""
        assert PrintJobTracker.is_cancelled_state("RUNNING") is False
        assert PrintJobTracker.is_cancelled_state("FINISH") is False
        assert PrintJobTracker.is_cancelled_state("IDLE") is False
        assert PrintJobTracker.is_cancelled_state(None) is False


class TestUpdateFromStatus:
    """Tests for update_from_status method."""

    def test_detects_new_printing_job(self) -> None:
        """Detects a new job when printer starts printing."""
        tracker = PrintJobTracker()
        
        status = {
            "currentJobId": "new-job-1",
            "fileName": "new_model.3mf",
            "gcodeState": "RUNNING",
        }
        
        job = tracker.update_from_status("P1", "192.168.1.1", status)
        
        assert job is not None
        assert job.job_id == "new-job-1"
        assert job.status == JobStatus.PRINTING

    def test_no_update_when_same_job(self) -> None:
        """No new job created when same job continues."""
        tracker = PrintJobTracker()
        
        status = {
            "currentJobId": "job-1",
            "fileName": "model.3mf",
            "gcodeState": "RUNNING",
        }
        
        job1 = tracker.update_from_status("P1", "192.168.1.1", status)
        job2 = tracker.update_from_status("P1", "192.168.1.1", status)
        
        assert job1 is not None
        assert job2 is None  # No new job created
