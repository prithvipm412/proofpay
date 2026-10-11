"""Chain event reader (V-E1..V-E5).

One background thread. Each 3 seconds (or at once after `wake()`), it scans from
checkpointBlock + 1 to the safe head in batches of EVENT_BATCH_BLOCKS. Each batch is saved
with its checkpoint in ONE database transaction (V-E2). Claims are a projection of these
events and can be rebuilt from START_BLOCK (V-S6, V-E5).
"""

from __future__ import annotations

import logging
import sqlite3
import threading
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeout
from typing import Callable

from requests.exceptions import ConnectionError as RequestsConnectionError, HTTPError, Timeout
from web3.exceptions import Web3RPCError

from .chain import Block, ChainEvent
from .config import EVENT_BATCH_BLOCKS, Settings
from .jobs import finalize, job_key
from .storage import Storage, now_s

log = logging.getLogger("proofpay.events")

SCAN_INTERVAL_S = 3.0  # V-E2
MAX_LAG_S = 30  # V-R7
MAX_BATCHES_PER_SCAN = 50  # keeps the heartbeat fresh during a long rebuild
CLEANUP_INTERVAL_S = 600  # V-S8
FETCH_WORKERS = 8
FETCH_ATTEMPTS = 3
RETRY_DELAY_S = 0.5


class ScanStopped(Exception):
    """Stop fetching without moving the checkpoint past unsaved work."""


def retryable(exc: Exception) -> bool:
    if isinstance(exc, (ConnectionError, TimeoutError, RequestsConnectionError, Timeout)):
        return True
    if isinstance(exc, HTTPError):
        return exc.response is not None and (exc.response.status_code == 429 or exc.response.status_code >= 500)
    if isinstance(exc, Web3RPCError):
        message = str(exc).lower()
        return any(word in message for word in ("rate limit", "too many requests", "timeout", "temporarily unavailable"))
    return False

# Admission hook (V-A). Called inside the batch transaction for a new job. Returns (job state, reason).
Admit = Callable[[sqlite3.Connection, ChainEvent], tuple[str, str | None]]


def admit_all(conn: sqlite3.Connection, event: ChainEvent) -> tuple[str, str | None]:
    return "queued", None


class EventReader:
    def __init__(self, settings: Settings, storage: Storage, chain, admit: Admit = admit_all):
        self.settings = settings
        self.storage = storage
        self.chain = chain
        self.admit = admit
        self.heartbeat: float | None = None  # monotonic time of the last loop pass (V-R9)
        self.safe_head: Block | None = None
        self.last_error: str | None = None
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._last_cleanup = 0.0

    # ------------------------------------------------------------ startup (V-E5)

    def startup(self) -> None:
        """Start a rebuild IF the checkpoint hash does not match the chain, IF history is not
        complete, or IF the folder was restored."""
        meta = self.storage.meta()
        reason = None
        cp = int(meta["checkpointBlock"])
        if meta.get("restored") == "1":
            reason = "restored folder"
        elif meta.get("historyStatus") != "complete":
            reason = "history not complete"
        elif cp >= self.settings.start_block:
            stored = meta.get("checkpointHash", "")
            if not stored or self.chain.block(cp).hash != stored:
                reason = f"checkpoint hash mismatch at block {cp}"
        if reason:
            log.warning("Event history rebuild from START_BLOCK %d: %s", self.settings.start_block, reason)
            self.start_rebuild()

    def start_rebuild(self) -> None:
        """V-E5: clear claims and scan again from START_BLOCK. Jobs rows are kept."""
        with self.storage.tx() as c:
            c.execute("DELETE FROM claims")
            c.execute("DELETE FROM parties")
            c.execute("DELETE FROM meta WHERE key = 'restored'")
            Storage.set_meta(
                c,
                historyStatus="rebuilding",
                checkpointBlock=str(self.settings.start_block - 1),
                checkpointHash="",
                checkpointTime="",
            )

    # ------------------------------------------------------------ scanning (V-E2)

    def _read(self, fn, *args):
        for attempt in range(FETCH_ATTEMPTS):
            if self._stop.is_set():
                raise ScanStopped()
            try:
                return fn(*args)
            except Exception as exc:
                if not retryable(exc) or attempt == FETCH_ATTEMPTS - 1:
                    raise
                if self._stop.wait(RETRY_DELAY_S * 2**attempt):
                    raise ScanStopped() from None

    def _fetch(self, start: int, end: int, safe: Block):
        events = self._read(self.chain.events, start, end)
        end_block = safe if end == safe.number else self._read(self.chain.block, end)
        return events, end_block

    def scan_once(self) -> int:
        """Scan up to the safe head. Returns the number of batches saved."""
        safe = self.chain.safe_head(self.settings.safe_head_method, self.settings.safe_head_offset)
        self.safe_head = safe
        batches = 0
        # Bounded prefetch: at most eight ranges in memory. Only this thread writes.
        with ThreadPoolExecutor(max_workers=FETCH_WORKERS, thread_name_prefix="event-fetch") as pool:
            pending = []
            try:
                while not self._stop.is_set() and batches < MAX_BATCHES_PER_SCAN:
                    cp = int(self.storage.meta()["checkpointBlock"])
                    if cp >= safe.number:
                        break
                    pending = []
                    for _ in range(min(FETCH_WORKERS, MAX_BATCHES_PER_SCAN - batches)):
                        if cp >= safe.number:
                            break
                        end = min(safe.number, cp + EVENT_BATCH_BLOCKS)
                        pending.append((cp, pool.submit(self._fetch, cp + 1, end, safe)))
                        cp = end
                    for expected_cp, future in pending:
                        while True:
                            if self._stop.is_set():
                                raise ScanStopped()
                            self.heartbeat = time.monotonic()
                            try:
                                events, end_block = future.result(timeout=0.5)
                                break
                            except FutureTimeout:
                                if future.done():  # a fetch itself raised TimeoutError
                                    raise
                        if self._stop.is_set():
                            raise ScanStopped()
                        self.save_batch(expected_cp, events, end_block)
                        batches += 1
                        self.heartbeat = time.monotonic()
            except ScanStopped:
                return batches
            finally:
                for _, future in pending:
                    future.cancel()
        meta = self.storage.meta()
        if int(meta["checkpointBlock"]) >= safe.number and meta.get("historyStatus") != "complete":
            with self.storage.tx() as c:
                Storage.set_meta(c, historyStatus="complete")
            log.info("Event history complete at block %s", meta["checkpointBlock"])
        return batches

    def save_batch(self, expected_cp: int, events: list[ChainEvent], end_block: Block) -> None:
        """Apply the events of one batch in event_id order and move the checkpoint. ONE transaction."""
        with self.storage.tx() as c:
            cp = int(self.storage.meta(c)["checkpointBlock"])
            if cp != expected_cp:
                raise RuntimeError(f"checkpoint moved from {expected_cp} to {cp} during a batch")
            for ev in sorted(events, key=lambda e: e.event_id):
                self.apply_event(c, ev)
            Storage.set_meta(
                c,
                checkpointBlock=str(end_block.number),
                checkpointHash=end_block.hash,
                checkpointTime=str(end_block.timestamp),
            )

    def apply_event(self, c: sqlite3.Connection, ev: ChainEvent) -> None:
        """V-E3. Applying the same event two times changes nothing (V-E4)."""
        a = ev.args
        task_id = int(a["id"])
        if ev.name == "TaskCreated":
            c.execute(
                "INSERT INTO claims (task_id, attempt, role, sha256, state, event_id)"
                " VALUES (?, 0, 'before', ?, 'history', ?) ON CONFLICT DO NOTHING",
                (task_id, a["beforeHash"], ev.event_id),
            )
            c.execute(
                "INSERT INTO parties (task_id, poster) VALUES (?, ?) ON CONFLICT DO NOTHING",
                (task_id, str(a["poster"]).lower()),
            )
        elif ev.name == "TaskAccepted":
            c.execute(
                "INSERT INTO parties (task_id, worker) VALUES (?, ?)"
                " ON CONFLICT (task_id) DO UPDATE SET worker = excluded.worker",
                (task_id, str(a["worker"]).lower()),
            )
        elif ev.name == "ProofSubmitted":
            attempt = int(a["attempt"])
            c.execute(
                "INSERT INTO claims (task_id, attempt, role, sha256, state, event_id)"
                " VALUES (?, ?, 'after', ?, 'pending', ?) ON CONFLICT DO NOTHING",
                (task_id, attempt, a["proofHash"], ev.event_id),
            )
            key = job_key(self.settings.deployment_id, task_id, attempt, a["proofHash"])
            if c.execute("SELECT 1 FROM jobs WHERE job_key = ?", (key,)).fetchone() is None:
                state, reason = self.admit(c, ev)
                now = now_s()
                c.execute(
                    "INSERT INTO jobs (job_key, event_id, task_id, attempt, proof_hash, state, last_error,"
                    " created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (key, ev.event_id, task_id, attempt, a["proofHash"], state, reason, now, now),
                )
        elif ev.name == "VerdictRecorded":
            finalize(
                c,
                self.settings.deployment_id,
                task_id,
                int(a["attempt"]),
                a["proofHash"],
                bool(a["pass"]),
                int(a["score"]),
                ev.tx_hash,
                ev.event_id,
            )

    # ------------------------------------------------------------ status

    def lag(self, safe: Block | None = None) -> tuple[int | None, int | None]:
        """(blocks, seconds) between the checkpoint and the safe head."""
        safe = safe or self.safe_head
        meta = self.storage.meta()
        if safe is None:
            return None, None
        blocks = max(0, safe.number - int(meta["checkpointBlock"]))
        cp_time = meta.get("checkpointTime")
        seconds = max(0, safe.timestamp - int(cp_time)) if cp_time else None
        return blocks, seconds

    def caught_up(self, safe: Block | None = None) -> bool:
        """V-R7: checkpoint less than 30 seconds behind the safe head."""
        _, seconds = self.lag(safe)
        return seconds is not None and seconds < MAX_LAG_S

    # ------------------------------------------------------------ loop

    def wake(self) -> None:
        self._wake.set()

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()

    def run(self) -> None:
        while not self._stop.is_set():
            more = False
            try:
                more = self.scan_once() >= MAX_BATCHES_PER_SCAN  # behind: continue without a pause
                self.last_error = None
                self._maybe_cleanup()
            except Exception as exc:  # RPC or database problem: log, keep the loop alive
                self.last_error = f"{type(exc).__name__}: {str(exc)[:200]}"
                log.warning("Event scan failed: %s", self.last_error)
            self.heartbeat = time.monotonic()
            if more:
                continue
            self._wake.wait(SCAN_INTERVAL_S)
            self._wake.clear()

    def _maybe_cleanup(self) -> None:
        if time.monotonic() - self._last_cleanup < CLEANUP_INTERVAL_S:
            return
        self._last_cleanup = time.monotonic()
        cp_time = self.storage.meta().get("checkpointTime")
        deleted = self.storage.cleanup(now_s(), int(cp_time) if cp_time else None, self.caught_up())
        if deleted:
            log.info("Cleanup deleted %d unreferenced uploads", len(deleted))
