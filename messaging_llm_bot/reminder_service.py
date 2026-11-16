from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable
from typing import Any

from assistant_framework.reminders import ReminderStore


class ReminderService:
    """Deliver prepared messages on minute boundaries, independently of the LLM."""

    def __init__(
        self, store: ReminderStore, send: Callable[[dict[str, Any]], None], *, clock: Callable[[], float] = time.time,
    ) -> None:
        self.store = store
        self.send = send
        self.clock = clock
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._active: dict[str, threading.Thread] = {}
        self._retry_at: dict[str, tuple[str, float]] = {}

    def start(self) -> None:
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return
            self._stop.clear()
            self._thread = threading.Thread(target=self._run, name="reminder-service", daemon=True)
            self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        deadline = time.monotonic() + 1.0
        with self._lock:
            workers = [self._thread, *self._active.values()]
        for worker in workers:
            if worker is not None and worker is not threading.current_thread():
                worker.join(timeout=max(0.0, deadline - time.monotonic()))

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                self.poll_once()
            except Exception:
                logging.exception("Reminder service poll failed; retrying next minute")
            self._stop.wait(60 - self.clock() % 60)

    def poll_once(self) -> None:
        with self._lock:
            if self._stop.is_set():
                return
            records = self.store.list()
            live_ids = {record["id"] for record in records}
            self._retry_at = {key: value for key, value in self._retry_at.items() if key in live_ids}
            now = self.clock()
            for record in records:
                reminder_id = record["id"]
                if record.get("delivery") != "message" or int(record["unix_time"]) > now or reminder_id in self._active:
                    continue
                revision, retry_at = self._retry_at.get(reminder_id, ("", 0.0))
                if revision == record.get("revision") and retry_at > now:
                    continue
                worker = threading.Thread(
                    target=self._deliver, args=(record,), name=f"reminder-{reminder_id}", daemon=True,
                )
                self._active[reminder_id] = worker
                try:
                    worker.start()
                except Exception:
                    self._active.pop(reminder_id, None)
                    raise

    def _deliver(self, record: dict[str, Any]) -> None:
        try:
            if self._stop.is_set() or not self.store.is_current(record):
                return
            self.send(record)
            self.store.complete(record, now=self.clock())
        except Exception:
            logging.exception("Reminder delivery failed id=%s; retrying in a minute", record["id"])
            with self._lock:
                self._retry_at[record["id"]] = (record.get("revision", ""), self.clock() + 60)
        finally:
            with self._lock:
                self._active.pop(record["id"], None)
