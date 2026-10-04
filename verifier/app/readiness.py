"""Startup and readiness checks (V-R1..V-R10), the signer lock (V-07) and GET /health (V-08).

`ready` is true only IF every V-R item passes. The worker loop (M4) signs only when `ready` is true.
The output never contains a secret (V-04).
"""

from __future__ import annotations

import fcntl
import logging
import os
import pathlib
import shutil
import threading
import time
from dataclasses import dataclass
from typing import Callable

from .config import Settings
from .events import MAX_LAG_S, EventReader
from .storage import STORAGE_RESERVE_BYTES, UNSETTLED_STATES, Storage, now_s

log = logging.getLogger("proofpay.readiness")

CHECK_INTERVAL_S = 15.0
MAX_HEARTBEAT_AGE_S = 30.0  # V-R9


class SignerLockError(Exception):
    pass


def acquire_signer_lock(data_dir: pathlib.Path) -> int:
    """V-07: exclusive lock on <data>/signer.lock. Keep the returned descriptor open for the
    life of the process. Raises SignerLockError IF another live verifier holds it."""
    fd = os.open(data_dir / "signer.lock", os.O_RDWR | os.O_CREAT, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        os.close(fd)
        raise SignerLockError(
            f"Another live verifier holds {data_dir / 'signer.lock'}. Stop it first, or use VERIFIER_MODE=readonly."
        )
    return fd


@dataclass
class Item:
    id: str
    name: str
    ok: bool
    detail: str = ""


class Readiness:
    def __init__(
        self,
        settings: Settings,
        storage: Storage,
        chain,
        reader: EventReader,
        worker_heartbeat: Callable[[], float | None] = lambda: None,
    ):
        self.settings = settings
        self.storage = storage
        self.chain = chain
        self.reader = reader
        self.worker_heartbeat = worker_heartbeat  # M4 worker loop
        self._lock = threading.Lock()
        self._snapshot: dict | None = None
        self._stop = threading.Event()

    # ------------------------------------------------------------ items

    def _item(self, id_: str, name: str, fn: Callable[[], tuple[bool, str]]) -> Item:
        try:
            ok, detail = fn()
        except Exception as exc:
            ok, detail = False, f"{type(exc).__name__}: {str(exc)[:160]}"
        return Item(id_, name, ok, detail)

    def evaluate(self) -> dict:
        s, warnings = self.settings, []
        safe_box: dict = {}

        def r1():
            cid = self.chain.chain_id()
            return cid == s.chain_id, f"RPC chain ID {cid}"

        def r2():
            if not self.chain.code_exists():
                return False, "no contract code at ESCROW_ADDRESS"
            v, n = self.chain.verifier(), self.chain.task_count()
            dw, rg = self.chain.dispute_window(), self.chain.review_grace()
            return True, f"taskCount {n}, disputeWindow {dw}, reviewGrace {rg}, verifier {v}"

        def r3():
            if not s.live:
                return True, "not checked in readonly mode (no signing)"
            v = self.chain.verifier()
            return v == s.signer_address, f"contract verifier {v}, signer {s.signer_address}"

        def r4():
            if not s.live:
                return True, "not checked in readonly mode (no signing)"
            bal = self.chain.balance(s.signer_address)
            ok = bal >= s.min_signer_balance_wei
            if not ok:
                warnings.append("paused_low_balance")
            return ok, f"signer balance {bal / 1e18:.4f} MON, minimum {s.min_signer_balance_wei / 1e18:g} MON"

        def r5():
            dep = self.storage.meta().get("deploymentId")
            return dep == s.deployment_id, f"data folder deployment {dep}"

        def r6():
            probe = self.storage.data_dir / ".write-probe"
            probe.write_bytes(b"ok")
            probe.unlink()
            free = shutil.disk_usage(self.storage.data_dir).free
            return free > STORAGE_RESERVE_BYTES, f"free space {free // (1024 * 1024)} MB"

        def r7():
            safe = self.chain.safe_head(s.safe_head_method, s.safe_head_offset)
            safe_box["safe"] = safe
            blocks, seconds = self.reader.lag(safe)
            if seconds is None:
                return False, f"no checkpoint yet; safe head {safe.number}"
            return seconds < MAX_LAG_S, f"{blocks} blocks / {seconds} s behind safe head {safe.number}"

        def r8():
            status = self.storage.meta().get("historyStatus")
            return status == "complete", f"historyStatus {status}"

        def r9():
            ages = self.heartbeat_ages()
            parts, ok = [], True
            for name, age in ages.items():
                if age is None:
                    ok = False
                    parts.append(f"{name} not running")
                else:
                    ok = ok and age < MAX_HEARTBEAT_AGE_S
                    parts.append(f"{name} {age:.0f} s ago")
            return ok, ", ".join(parts)

        def r10():
            n = self.storage.count_jobs(("blocked_nonce",))
            return n == 0, f"{n} jobs in blocked_nonce"

        items = [
            self._item("V-R1", "Chain ID", r1),
            self._item("V-R2", "Contract ABI", r2),
            self._item("V-R3", "Verifier address", r3),
            self._item("V-R4", "Signer balance", r4),
            self._item("V-R5", "Data folder deployment", r5),
            self._item("V-R6", "Data folder writable", r6),
            self._item("V-R7", "Event lag", r7),
            self._item("V-R8", "History complete", r8),
            self._item("V-R9", "Loop heartbeats", r9),
            self._item("V-R10", "No blocked nonce", r10),
        ]

        meta = self.storage.meta()
        now = now_s()
        if self.storage.count_jobs(("blocked_config",)):
            warnings.append("model_config")
        with self.storage.read() as c:
            if c.execute("SELECT 1 FROM jobs WHERE conflict = 1 LIMIT 1").fetchone():
                warnings.append("verdict_conflict")
        if self.reader.last_error:
            warnings.append("event_reader_error")
        oldest = self.storage.oldest_eligible_job_created_at(now)
        blocks, _ = self.reader.lag(safe_box.get("safe"))
        ready = all(i.ok for i in items)
        return {
            "ready": ready,
            "mode": s.mode,
            "deploymentId": s.deployment_id,
            "model": s.vision_model,
            "historyStatus": meta.get("historyStatus"),
            "eventLagBlocks": blocks,
            "oldestEligibleJobAgeSec": (now - oldest) if oldest is not None else None,
            "unsettledJobs": self.storage.count_jobs(UNSETTLED_STATES),
            "heartbeatAgeSec": {k: (round(v, 1) if v is not None else None) for k, v in self.heartbeat_ages().items()},
            "warnings": warnings,
            "failed": [i.id for i in items if not i.ok],
            "checks": [{"id": i.id, "name": i.name, "pass": i.ok, "detail": i.detail} for i in items],
            "checkedAt": now,
        }

    def heartbeat_ages(self) -> dict[str, float | None]:
        now = time.monotonic()
        reader, worker = self.reader.heartbeat, self.worker_heartbeat()
        return {
            "eventReader": (now - reader) if reader is not None else None,
            "workerLoop": (now - worker) if worker is not None else None,
        }

    # ------------------------------------------------------------ loop

    def refresh(self) -> dict:
        snap = self.evaluate()
        with self._lock:
            self._snapshot = snap
        if not snap["ready"]:
            log.info("Not ready: %s", ", ".join(snap["failed"]))
        return snap

    def snapshot(self) -> dict:
        with self._lock:
            snap = self._snapshot
        return snap if snap is not None else self.refresh()

    @property
    def ready(self) -> bool:
        return bool(self.snapshot()["ready"])

    def stop(self) -> None:
        self._stop.set()

    def run(self) -> None:
        while not self._stop.is_set():
            try:
                self.refresh()
            except Exception as exc:
                log.warning("Readiness check failed: %s", type(exc).__name__)
            self._stop.wait(CHECK_INTERVAL_S)
