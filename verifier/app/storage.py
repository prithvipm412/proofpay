"""Files and SQLite (V-S1..V-S10, V-R5).

Hashes are stored in the database as "0x" + 64 lowercase hex characters. File names use the hex
without "0x" (V-S1). Times in the database are Unix seconds (UTC).

Each call to `tx()` opens its own short connection (SQLite WAL, V-S3). No write transaction is kept
open during a model call or a chain call: callers read from the chain first, then write.
"""

from __future__ import annotations

import os
import pathlib
import re
import sqlite3
import threading
import time
import uuid
from contextlib import contextmanager
from typing import Callable, Iterator

from .images import EXTENSIONS, MAX_UPLOAD_BYTES, ReceivedFile, UploadError, remove_quietly

SCHEMA_VERSION = "1"
MB = 1024 * 1024
STORAGE_RESERVE_BYTES = 300 * MB  # V-S7
CLEANUP_AGE_S = 24 * 3600  # V-S8
STALE_TMP_AGE_S = 3600

_HASH = re.compile(r"^(0x)?([0-9a-fA-F]{64})$")

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS images (
    sha256 TEXT PRIMARY KEY,
    format TEXT NOT NULL,
    width INTEGER NOT NULL,
    height INTEGER NOT NULL,
    size_bytes INTEGER NOT NULL,
    exif_time_utc TEXT,
    exif_time_status TEXT NOT NULL,
    phash TEXT,                      -- pHash cache keyed by SHA-256 (V-S10)
    created_at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS claims (
    task_id INTEGER NOT NULL,
    attempt INTEGER NOT NULL,        -- 0 for before
    role TEXT NOT NULL CHECK (role IN ('before', 'after')),
    sha256 TEXT NOT NULL,
    state TEXT NOT NULL CHECK (state IN ('history', 'pending', 'accepted', 'rejected')),
    event_id TEXT NOT NULL,
    UNIQUE (task_id, attempt, role)
);
CREATE INDEX IF NOT EXISTS claims_sha256 ON claims (sha256);
CREATE INDEX IF NOT EXISTS claims_state ON claims (state);
CREATE TABLE IF NOT EXISTS jobs (
    job_key TEXT PRIMARY KEY,
    event_id TEXT NOT NULL UNIQUE,
    task_id INTEGER NOT NULL,
    attempt INTEGER NOT NULL,
    proof_hash TEXT NOT NULL,
    state TEXT NOT NULL,
    eval_rounds INTEGER NOT NULL DEFAULT 0,
    next_try_at INTEGER,
    config_version TEXT,
    evaluation_json TEXT,
    nonce INTEGER,
    raw_tx TEXT,
    tx_hashes TEXT NOT NULL DEFAULT '[]',
    conflict INTEGER NOT NULL DEFAULT 0,
    last_error TEXT,
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS jobs_state ON jobs (state);
CREATE TABLE IF NOT EXISTS counters (
    day TEXT NOT NULL,
    key TEXT NOT NULL,
    value INTEGER NOT NULL,
    PRIMARY KEY (day, key)
);
"""

UNSETTLED_STATES = ("evaluating", "evaluated", "signed", "broadcast")  # V-J1


class DataFolderError(Exception):
    """The data folder belongs to a different deployment, or is not usable (V-R5)."""


def norm_hash(value: str) -> str:
    """Return "0x" + 64 lowercase hex characters, or raise ValueError."""
    m = _HASH.match(value or "")
    if not m:
        raise ValueError("not a 32-byte hex hash")
    return "0x" + m.group(2).lower()


def now_s() -> int:
    return int(time.time())


class Storage:
    def __init__(self, data_dir: pathlib.Path, deployment_id: str, max_storage_mb: int, start_block: int):
        self.data_dir = data_dir
        self.deployment_id = deployment_id
        self.max_storage_bytes = max_storage_mb * MB
        self.start_block = start_block
        self.originals = data_dir / "originals"
        self.previews = data_dir / "previews"
        self.tmp = data_dir / "tmp"
        self.db_path = data_dir / "proofpay.db"
        self._admit_lock = threading.Lock()  # V-S7
        self._reserved = 0
        self._files_lock = threading.Lock()  # upload commit vs cleanup

    # ------------------------------------------------------------ database

    def connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=30, isolation_level=None, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")  # V-S3
        conn.execute("PRAGMA synchronous=FULL")
        conn.execute("PRAGMA busy_timeout=30000")
        return conn

    @contextmanager
    def tx(self) -> Iterator[sqlite3.Connection]:
        """One write transaction. Commit on success, roll back on any exception."""
        conn = self.connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            try:
                yield conn
            except BaseException:
                conn.execute("ROLLBACK")
                raise
            conn.execute("COMMIT")
        finally:
            conn.close()

    @contextmanager
    def read(self) -> Iterator[sqlite3.Connection]:
        conn = self.connect()
        try:
            yield conn
        finally:
            conn.close()

    def open(self) -> None:
        """Make folders and tables, then check the deployment ID (V-R5)."""
        for d in (self.data_dir, self.originals, self.previews, self.tmp):
            d.mkdir(parents=True, exist_ok=True)
        with self.read() as c:
            c.executescript(SCHEMA)
        with self.tx() as c:
            row =c.execute("SELECT value FROM meta WHERE key = 'deploymentId'").fetchone()
            if row is None:
                c.executemany(
                    "INSERT INTO meta (key, value) VALUES (?, ?)",
                    [
                        ("deploymentId", self.deployment_id),
                        ("schemaVersion", SCHEMA_VERSION),
                        ("checkpointBlock", str(self.start_block - 1)),
                        ("checkpointHash", ""),
                        ("checkpointTime", ""),
                        ("historyStatus", "rebuilding"),
                    ],
                )
            elif row["value"] != self.deployment_id:
                raise DataFolderError(
                    f"Data folder {self.data_dir} belongs to deployment {row['value']}, "
                    f"not {self.deployment_id}. Stop."
                )

    def meta(self, conn: sqlite3.Connection | None = None) -> dict[str, str]:
        if conn is not None:
            return {r["key"]: r["value"] for r in conn.execute("SELECT key, value FROM meta")}
        with self.read() as c:
            return self.meta(c)

    @staticmethod
    def set_meta(conn: sqlite3.Connection, **values: str) -> None:
        conn.executemany(
            "INSERT INTO meta (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            [(k, str(v)) for k, v in values.items()],
        )

    # ------------------------------------------------------------ files (V-S1, V-S2)

    def original_path(self, sha: str) -> pathlib.Path | None:
        hexpart = norm_hash(sha)[2:]
        for ext in EXTENSIONS:
            p = self.originals / f"{hexpart}.{ext}"
            if p.exists():
                return p
        return None

    def preview_path(self, sha: str) -> pathlib.Path:
        return self.previews / f"{norm_hash(sha)[2:]}.jpg"

    @staticmethod
    def _place(src: pathlib.Path, dst: pathlib.Path) -> None:
        """Put a finished temporary file at its final name. Never overwrite (V-S1)."""
        try:
            os.link(src, dst)
        except FileExistsError:
            pass  # same name = same SHA-256 = same bytes

    def used_bytes(self) -> int:
        """V-S7: originals, previews, temporary files and the database files."""
        total = 0
        for d in (self.originals, self.previews, self.tmp):
            with os.scandir(d) as it:
                for e in it:
                    try:
                        total += e.stat().st_size
                    except FileNotFoundError:
                        pass
        for suffix in ("", "-wal", "-shm"):
            p = pathlib.Path(str(self.db_path) + suffix)
            if p.exists():
                total += p.stat().st_size
        return total

    @contextmanager
    def admit_upload(self) -> Iterator[None]:
        """V-S7 storage admission under one lock.

        Each accepted upload reserves 10 MB until it ends, so concurrent uploads cannot pass the limit.
        """
        with self._admit_lock:
            if self.used_bytes() + self._reserved + MAX_UPLOAD_BYTES + STORAGE_RESERVE_BYTES > self.max_storage_bytes:
                raise UploadError(507, "Storage is full")
            self._reserved += MAX_UPLOAD_BYTES
        try:
            yield
        finally:
            with self._admit_lock:
                self._reserved -= MAX_UPLOAD_BYTES

    def store_upload(self, received: ReceivedFile, decoder: Callable) -> str:
        """V-09: keep a received upload. Returns "0x" + SHA-256 of the original bytes."""
        sha = "0x" + received.sha256_hex
        preview_tmp = self.tmp / f"{uuid.uuid4().hex}.preview"
        try:
            with self._files_lock:
                if self._touch_existing(sha):
                    return sha  # V-09: the hash exists
            info = decoder(received.path, preview_tmp)  # V-L5 child process
            with self._files_lock:
                self._place(received.path, self.originals / f"{received.sha256_hex}.{info['ext']}")
                self._place(preview_tmp, self.preview_path(sha))
                with self.tx() as c:
                    c.execute(
                        "INSERT OR IGNORE INTO images (sha256, format, width, height, size_bytes, exif_time_utc,"
                        " exif_time_status, phash, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                        (
                            sha,
                            info["format"],
                            info["width"],
                            info["height"],
                            received.size,
                            info["exif_time_utc"],
                            info["exif_time_status"],
                            info["phash"],
                            now_s(),
                        ),
                    )
            return sha
        finally:
            remove_quietly(received.path)
            remove_quietly(preview_tmp)

    def _touch_existing(self, sha: str) -> bool:
        """IF the image is stored, refresh its upload time (cleanup clock) and return True."""
        if self.original_path(sha) is None or not self.preview_path(sha).exists():
            return False
        with self.tx() as c:
            cur = c.execute("UPDATE images SET created_at = ? WHERE sha256 = ?", (now_s(), sha))
            return cur.rowcount == 1

    def image(self, sha: str) -> sqlite3.Row | None:
        with self.read() as c:
            return c.execute("SELECT * FROM images WHERE sha256 = ?", (norm_hash(sha),)).fetchone()

    def cache_phash(self, sha: str, phash: str) -> None:
        with self.tx() as c:
            c.execute("UPDATE images SET phash = ? WHERE sha256 = ?", (phash, norm_hash(sha)))

    # ------------------------------------------------------------ cleanup (V-S8, V-S9)

    def cleanup(self, now: int, checkpoint_time: int | None, caught_up: bool) -> list[str]:
        """Delete unreferenced uploads. Returns the deleted hashes.

        All must be true for a hash: no claim references it; the upload is older than 24 hours;
        the event reader is caught up and complete; its checkpoint block time is more than 24 hours
        after the upload. Referenced hashes are never deleted, so they are kept until
        ADMISSION_UNTIL + 30 days and later (V-S9).
        """
        deleted: list[str] = []
        with self._files_lock:
            for p in self.tmp.iterdir():  # stale partial uploads from a crash
                try:
                    if now - p.stat().st_mtime > STALE_TMP_AGE_S:
                        remove_quietly(p)
                except FileNotFoundError:
                    pass
            if not caught_up or checkpoint_time is None:
                return deleted
            with self.tx() as c:
                if self.meta(c).get("historyStatus") != "complete":
                    return deleted  # claims are incomplete during a rebuild (V-E5)
                rows = c.execute(
                    "SELECT sha256 FROM images i WHERE i.created_at < ? AND i.created_at + ? < ?"
                    " AND NOT EXISTS (SELECT 1 FROM claims WHERE claims.sha256 = i.sha256)",
                    (now - CLEANUP_AGE_S, CLEANUP_AGE_S, checkpoint_time),
                ).fetchall()
                deleted = [r["sha256"] for r in rows]
                c.executemany("DELETE FROM images WHERE sha256 = ?", [(s,) for s in deleted])
            for sha in deleted:
                hexpart = sha[2:]
                for ext in EXTENSIONS:
                    remove_quietly(self.originals / f"{hexpart}.{ext}")
                remove_quietly(self.preview_path(sha))
        return deleted

    # ------------------------------------------------------------ claims and jobs (reads)

    def claims_of_other_tasks(self, task_id: int, states: tuple[str, ...]) -> list[sqlite3.Row]:
        marks = ",".join("?" * len(states))
        with self.read() as c:
            return c.execute(
                f"SELECT * FROM claims WHERE task_id != ? AND state IN ({marks}) ORDER BY event_id",
                (task_id, *states),
            ).fetchall()

    def claims_of_task(self, task_id: int) -> list[sqlite3.Row]:
        with self.read() as c:
            return c.execute("SELECT * FROM claims WHERE task_id = ? ORDER BY attempt, role", (task_id,)).fetchall()

    def count_jobs(self, states: tuple[str, ...]) -> int:
        marks = ",".join("?" * len(states))
        with self.read() as c:
            return c.execute(f"SELECT COUNT(*) FROM jobs WHERE state IN ({marks})", states).fetchone()[0]

    def oldest_eligible_job_created_at(self, now: int) -> int | None:
        """Creation time of the oldest eligible job (V-J2 definition)."""
        with self.read() as c:
            row = c.execute(
                "SELECT MIN(created_at) FROM jobs WHERE state = 'queued'"
                " OR (state IN ('awaiting_files', 'model_retry') AND COALESCE(next_try_at, 0) <= ?)",
                (now,),
            ).fetchone()
        return row[0]
