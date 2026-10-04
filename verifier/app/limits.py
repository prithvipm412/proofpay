"""Admission, daily caps and HTTP rate limits (V-A1..V-A7).

All jobs come from ProofSubmitted events (V-E3), so `Admission` covers every path. It runs inside
the event batch transaction, so a job and its counters are saved together.

Counters are kept for each UTC day of the verifier clock at admission time (V-A2). Keys:
`jobs`, `jobs_poster:<address>`, `jobs_worker:<address>`, `model_calls`, `verdict_tx`.
"""

from __future__ import annotations

import sqlite3

from .chain import ChainEvent
from .config import Settings
from .storage import Storage, bump, counter, now_s, utc_day

UPLOAD_LIMIT = "20/hour"  # V-A6
VERIFY_LIMIT = "60/hour"  # V-A6
WAITING_STATES = ("queued", "awaiting_files", "model_retry")  # V-A3

K_JOBS, K_MODEL_CALLS, K_VERDICT_TX = "jobs", "model_calls", "verdict_tx"


class Admission:
    """V-A1..V-A4. Returns (job state, reason). A `not_admitted` job gets no model call and no transaction."""

    def __init__(self, settings: Settings, clock=now_s):
        self.settings = settings
        self.clock = clock

    def __call__(self, conn: sqlite3.Connection, ev: ChainEvent) -> tuple[str, str | None]:
        s = self.settings
        row = conn.execute("SELECT poster, worker FROM parties WHERE task_id = ?", (int(ev.args["id"]),)).fetchone()
        poster, worker = (row["poster"], row["worker"]) if row else (None, None)
        if not poster or not worker:
            return "not_admitted", "unknown_parties"  # fail closed: TaskCreated/TaskAccepted not seen
        if s.admission_mode == "allowlist" and not (poster in s.allowlist and worker in s.allowlist):
            return "not_admitted", "not_on_allowlist"
        marks = ",".join("?" * len(WAITING_STATES))
        waiting = conn.execute(f"SELECT COUNT(*) FROM jobs WHERE state IN ({marks})", WAITING_STATES).fetchone()[0]
        if waiting >= s.max_queue:
            return "not_admitted", "queue_full"
        day = utc_day(self.clock())
        if counter(conn, day, K_JOBS) >= s.max_jobs_per_day:
            return "not_admitted", "daily_job_cap"
        if counter(conn, day, f"jobs_poster:{poster}") >= s.max_jobs_per_poster:
            return "not_admitted", "poster_daily_cap"
        if counter(conn, day, f"jobs_worker:{worker}") >= s.max_jobs_per_worker:
            return "not_admitted", "worker_daily_cap"
        bump(conn, day, K_JOBS)
        bump(conn, day, f"jobs_poster:{poster}")
        bump(conn, day, f"jobs_worker:{worker}")
        return "queued", None


class Budget:
    """Daily model-call and verdict-transaction budgets (V-A2, V-A5, V-J7, V-M7)."""

    def __init__(self, settings: Settings, storage: Storage, clock=now_s):
        self.settings = settings
        self.storage = storage
        self.clock = clock

    def used(self, key: str) -> int:
        with self.storage.read() as c:
            return counter(c, utc_day(self.clock()), key)

    def count_model_call(self) -> None:
        with self.storage.tx() as c:
            bump(c, utc_day(self.clock()), K_MODEL_CALLS)

    def model_round_available(self, calls: int) -> bool:
        """A round may need `calls` calls. Start it only IF all fit in today's budget."""
        return self.used(K_MODEL_CALLS) + calls <= self.settings.max_model_calls_per_day

    def verdict_tx_available(self) -> bool:
        return self.used(K_VERDICT_TX) < self.settings.max_verdict_tx_per_day


def make_limiter():
    """V-A6: per-IP limits in memory (one process, V-01).

    The client IP is the socket peer. uvicorn trusts X-Forwarded-For only from 127.0.0.1 (its
    default `forwarded_allow_ips`), which is where the ngrok agent connects from (M8).
    """
    from slowapi import Limiter
    from slowapi.util import get_remote_address

    return Limiter(key_func=get_remote_address, storage_uri="memory://")
