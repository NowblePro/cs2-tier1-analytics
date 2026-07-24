import threading
import time

from app.web.job_runtime import JobCoordinator, PeriodicScheduler


def _wait_until(predicate, timeout: float = 2) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return False


def test_job_coordinator_runs_jobs_and_cancels_queued_work():
    first_started = threading.Event()
    release_first = threading.Event()
    updates: list[tuple[str, str | None]] = []
    coordinator = JobCoordinator(
        on_update=lambda job_id, values: updates.append((job_id, values.get("status")))
    )

    def runner(job_id: str, payload: object) -> None:
        coordinator.set_job(job_id, status="running")
        if payload == "first":
            first_started.set()
            release_first.wait(timeout=2)
        coordinator.set_job(job_id, status="completed")

    coordinator.register_runner("test", runner)
    try:
        coordinator.enqueue("test", "job-1", "first", {"job_id": "job-1", "status": "queued"})
        assert first_started.wait(timeout=2)
        coordinator.enqueue("test", "job-2", "second", {"job_id": "job-2", "status": "queued"})

        assert coordinator.get_job("job-2")["queue_position"] == 1
        assert coordinator.cancel("job-2") == {
            "ok": True,
            "job_id": "job-2",
            "status": "cancelling",
        }

        release_first.set()
        assert _wait_until(lambda: coordinator.get_job("job-1")["status"] == "completed")
        assert _wait_until(lambda: coordinator.get_job("job-2")["status"] == "cancelled")
        assert ("job-1", "running") in updates
        assert ("job-1", "completed") in updates
    finally:
        release_first.set()
        coordinator.stop()


def test_job_coordinator_rejects_unknown_cancellation():
    coordinator = JobCoordinator()
    assert coordinator.cancel("missing") == {"ok": False, "error": "Active job not found"}


def test_periodic_scheduler_runs_callback_and_stops():
    called = threading.Event()
    scheduler = PeriodicScheduler(called.set, interval_seconds=0.01)
    scheduler.start()
    try:
        assert called.wait(timeout=1)
        assert scheduler.running is True
    finally:
        scheduler.stop()
    assert scheduler.running is False


def test_job_watchdog_requests_cancellation_for_stalled_runner():
    started = threading.Event()
    release = threading.Event()
    coordinator = JobCoordinator(stale_after_seconds=0.05)

    def runner(job_id: str, _payload: object) -> None:
        coordinator.set_job(job_id, status="running")
        started.set()
        release.wait(timeout=1)

    coordinator.register_runner("test", runner)
    try:
        coordinator.enqueue("test", "stalled", None, {"job_id": "stalled", "status": "queued"})
        assert started.wait(timeout=1)
        assert _wait_until(lambda: coordinator.get_job("stalled")["status"] == "cancelling")
        assert coordinator.cancel_event("stalled").is_set()
        assert "stopped responding" in coordinator.get_job("stalled")["error"]
    finally:
        release.set()
        coordinator.stop()
