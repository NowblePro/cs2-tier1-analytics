from __future__ import annotations

import logging
import queue
import threading
import time
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any


logger = logging.getLogger(__name__)
JobRunner = Callable[[str, object], None]
JobUpdateHook = Callable[[str, dict[str, Any]], None]


class JobCoordinator:
    def __init__(
        self,
        on_update: JobUpdateHook | None = None,
        stale_after_seconds: float = 30 * 60,
    ) -> None:
        self._jobs: dict[str, dict[str, Any]] = {}
        self._cancel_events: dict[str, threading.Event] = {}
        self._runners: dict[str, JobRunner] = {}
        self._lock = threading.Lock()
        self._queue: queue.Queue[tuple[str, str, object] | None] = queue.Queue()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._watchdog_thread: threading.Thread | None = None
        self._on_update = on_update
        self._stale_after_seconds = stale_after_seconds
        self._heartbeat: dict[str, float] = {}

    def register_runner(self, kind: str, runner: JobRunner) -> None:
        self._runners[kind] = runner

    def enqueue(self, kind: str, job_id: str, payload: object, metadata: dict[str, Any]) -> None:
        heartbeat_at = datetime.now(UTC).isoformat()
        with self._lock:
            self._jobs[job_id] = {**metadata, "heartbeat_at": heartbeat_at}
            self._cancel_events[job_id] = threading.Event()
            self._heartbeat[job_id] = time.monotonic()
        self.start()
        self._queue.put((kind, job_id, payload))

    def set_job(self, job_id: str, **updates: Any) -> None:
        heartbeat_at = datetime.now(UTC).isoformat()
        with self._lock:
            self._jobs[job_id].update(updates, heartbeat_at=heartbeat_at)
            self._heartbeat[job_id] = time.monotonic()
        if self._on_update:
            self._on_update(job_id, updates)

    def set_progress(self, job_id: str, progress: dict[str, object]) -> None:
        heartbeat_at = datetime.now(UTC).isoformat()
        with self._lock:
            self._jobs[job_id]["progress"] = progress
            self._jobs[job_id]["heartbeat_at"] = heartbeat_at
            self._heartbeat[job_id] = time.monotonic()

    def get_job(self, job_id: str) -> dict[str, Any] | None:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                return None
            result = dict(job)
            if result.get("status") == "queued":
                queued_ids = [
                    item_id
                    for item_id, item in self._jobs.items()
                    if item.get("status") == "queued"
                ]
                result["queue_position"] = queued_ids.index(job_id) + 1 if job_id in queued_ids else None
            return result

    def get_progress(self, job_id: str) -> dict[str, object] | None:
        with self._lock:
            progress = self._jobs[job_id].get("progress")
            return dict(progress) if isinstance(progress, dict) else None

    def cancel_event(self, job_id: str) -> threading.Event:
        with self._lock:
            return self._cancel_events[job_id]

    def cancel(self, job_id: str) -> dict[str, Any]:
        with self._lock:
            job = self._jobs.get(job_id)
            cancel_event = self._cancel_events.get(job_id)
            if job is None or cancel_event is None:
                return {"ok": False, "error": "Active job not found"}
            if job.get("status") not in {"queued", "running", "cancelling"}:
                return {"ok": False, "error": f"Job is already {job.get('status')}"}
            cancel_event.set()
            job["status"] = "cancelling"
        return {"ok": True, "job_id": job_id, "status": "cancelling"}

    def active_job(self) -> dict[str, Any] | None:
        with self._lock:
            job = next(
                (
                    item
                    for item in self._jobs.values()
                    if item.get("status") in {"queued", "running", "cancelling"}
                ),
                None,
            )
            return dict(job) if job else None

    @property
    def running(self) -> bool:
        return bool(self._thread and self._thread.is_alive())

    @property
    def watchdog_running(self) -> bool:
        return bool(self._watchdog_thread and self._watchdog_thread.is_alive())

    @property
    def queue_size(self) -> int:
        return self._queue.qsize()

    def start(self) -> None:
        if self.running:
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._worker_loop, name="cs2-job-worker", daemon=True)
        self._thread.start()
        self._watchdog_thread = threading.Thread(
            target=self._watchdog_loop,
            name="cs2-job-watchdog",
            daemon=True,
        )
        self._watchdog_thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._queue.put(None)
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=2)
        if self._watchdog_thread and self._watchdog_thread.is_alive():
            self._watchdog_thread.join(timeout=2)
        self._thread = None
        self._watchdog_thread = None

    def _worker_loop(self) -> None:
        while not self._stop.is_set():
            try:
                item = self._queue.get(timeout=1)
            except queue.Empty:
                continue
            try:
                if item is None:
                    return
                kind, job_id, payload = item
                cancel_event = self.cancel_event(job_id)
                if cancel_event.is_set():
                    self.set_job(
                        job_id,
                        status="cancelled",
                        finished_at=datetime.now(UTC).isoformat(),
                    )
                    continue
                runner = self._runners.get(kind)
                if runner is None:
                    raise RuntimeError(f"No background runner registered for {kind}")
                runner(job_id, payload)
            except Exception:
                target_job_id = item[1] if item else "unknown"
                logger.exception("Unhandled background job failure for %s", target_job_id)
                if item:
                    self.set_job(
                        target_job_id,
                        status="failed",
                        error="Unhandled background worker failure",
                        finished_at=datetime.now(UTC).isoformat(),
                    )
            finally:
                self._queue.task_done()

    def _watchdog_loop(self) -> None:
        interval = min(60.0, max(0.05, self._stale_after_seconds / 2))
        while not self._stop.wait(interval):
            now = time.monotonic()
            stale_jobs: list[str] = []
            with self._lock:
                for job_id, job in self._jobs.items():
                    if job.get("status") != "running":
                        continue
                    if now - self._heartbeat.get(job_id, now) >= self._stale_after_seconds:
                        cancel_event = self._cancel_events.get(job_id)
                        if cancel_event:
                            cancel_event.set()
                        stale_jobs.append(job_id)
            for job_id in stale_jobs:
                self.set_job(
                    job_id,
                    status="cancelling",
                    error="Job stopped responding and cancellation was requested",
                )


class PeriodicScheduler:
    def __init__(self, callback: Callable[[], None], interval_seconds: float = 15) -> None:
        self._callback = callback
        self._interval_seconds = interval_seconds
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    @property
    def running(self) -> bool:
        return bool(self._thread and self._thread.is_alive())

    def start(self) -> None:
        if self.running:
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="cs2-automation", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=2)
        self._thread = None

    def _loop(self) -> None:
        while not self._stop.wait(self._interval_seconds):
            try:
                self._callback()
            except Exception:
                logger.exception("Automation scheduler iteration failed")
                self._stop.wait(5)
