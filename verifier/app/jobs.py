"""Jobs (V-J1..V-J8), verdict rules (V-19..V-21) and reports (V-22).

The worker loop is the only code that signs. It runs in one thread and does one job at a time:
it starts a new job only IF no job is in `evaluating`, `evaluated`, `signed` or `broadcast` (V-J1).

Transition table (section 10.11) and where each row is written:
  queued / awaiting_files / model_retry -> `_start`  (V-J2 expiry, superseded, files, evaluation)
  evaluating                            -> `_evaluate` (inside `_start`); restart: `startup` -> queued
  evaluated                             -> `_sign`
  signed                                -> `_broadcast`
  broadcast                             -> `_reconcile` (V-J3), each 15 s
`finalize` (V-J4) is the one function that records a chain verdict, for the event reader (V-E3)
and for the reconciler (V-J3).

Monad: the "pending" block tag "behaves the same as 'latest'" (docs json-rpc overview), so the
pending-vs-latest nonce check of the `signed` row cannot see a transaction in flight. The serial
gate (V-J1) and the reconciler's used-nonce check (V-J3 step 3) are the protection.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from typing import Callable

from .checks import Check, CheckRun, run_checks
from .config import Settings
from .images import decode_in_child, model_input_in_child, remove_quietly
from .limits import K_VERDICT_TX, Budget
from .storage import UNSETTLED_STATES, Storage, bump, now_s, utc_day
from .vision import CALLS_PER_ROUND, ModelConfigError, ModelVerdict, RoundFailed, Vision

log = logging.getLogger("proofpay.jobs")

TERMINAL_STATES = ("confirmed", "reverted", "superseded", "expired", "model_failed", "not_admitted")
STOP_STATES = ("blocked_config", "blocked_nonce")
WAITING_STATES = ("queued", "awaiting_files", "model_retry")

EXPIRE_REMAINING_S = 150  # V-J2
SIGN_MIN_REMAINING_S = 60  # `evaluated` row
AWAIT_FILES_POLL_S = 15  # `awaiting_files` row
# `awaiting_files` row: "At reviewBy - 150 s, continue without them". V-J2 expires a job at
# remaining < 150, so the job continues at remaining <= 180 s: two 15 s polls before that line.
CONTINUE_WITHOUT_FILES_S = 180
RETRY_DELAYS_S = (30, 60)  # V-J5: after round 1, after round 2
MAX_ROUNDS = 3  # V-J5
RECEIPT_WAIT_S = 60  # `broadcast` row
RECONCILE_INTERVAL_S = 15  # V-J3
EXPIRE_AFTER_REVIEW_S = 600  # V-J3 step 5
IDLE_WAIT_S = 1.0
ERROR_WAIT_S = 5.0
CAP_WAIT_S = 15.0
RECEIPT_POLL_S = 2.0

AI_NOT_SAME = "AI check: not the same place"
AI_NOT_DONE = "AI check: task not complete"
AI_LOW = "AI check: confidence {n} is below {min}"
AI_PASS = "AI check: task complete (confidence {n})"
REASON_MAX_BYTES = 120  # C-11


def job_key(deployment_id: str, task_id: int, attempt: int, proof_hash: str) -> str:
    return f"{deployment_id}:{task_id}:{attempt}:{proof_hash}"


# ---------------------------------------------------------------- finalize (V-J4)


def finalize(
    conn: sqlite3.Connection,
    deployment_id: str,
    task_id: int,
    attempt: int,
    proof_hash: str,
    passed: bool,
    score: int,
    tx_hash: str,
    event_id: str,
) -> None:
    """V-J4: record a chain verdict. The caller owns the ONE database transaction.

    The chain event decides the claim: `accepted` for pass, `rejected` for fail. IF a local job
    exists, it becomes `confirmed` with the transaction hash. IF the event's pass or score differs
    from the local evaluation, the job gets `conflict = 1` (health warning). Calling it again with
    the same event changes nothing (V-E4).
    """
    state = "accepted" if passed else "rejected"
    # The ProofSubmitted claim always comes first in chain order. The insert only covers a
    # data folder that lost it; it never replaces a claim for a different photo.
    conn.execute(
        "INSERT INTO claims (task_id, attempt, role, sha256, state, event_id) VALUES (?, ?, 'after', ?, ?, ?)"
        " ON CONFLICT (task_id, attempt, role) DO UPDATE SET state = excluded.state"
        " WHERE claims.sha256 = excluded.sha256",
        (task_id, attempt, proof_hash, state, event_id),
    )

    key = job_key(deployment_id, task_id, attempt, proof_hash)
    row = conn.execute("SELECT evaluation_json, tx_hashes FROM jobs WHERE job_key = ?", (key,)).fetchone()
    if row is None:
        return  # for example after a restore: only the claim is updated
    hashes = json.loads(row["tx_hashes"] or "[]")
    if tx_hash not in hashes:
        hashes.append(tx_hash)
    conflict = 0
    if row["evaluation_json"]:
        ev = json.loads(row["evaluation_json"])
        conflict = int(bool(ev.get("pass")) != bool(passed) or int(ev.get("score", -1)) != int(score))
    new_hashes = json.dumps(hashes)
    conn.execute(
        "UPDATE jobs SET state = 'confirmed', tx_hashes = ?, conflict = MAX(conflict, ?), updated_at = ?"
        " WHERE job_key = ? AND (state != 'confirmed' OR tx_hashes != ? OR conflict < ?)",
        (new_hashes, conflict, now_s(), key, new_hashes, conflict),
    )


# ---------------------------------------------------------------- verdict rules (V-19..V-21)


def decide(run: CheckRun, verdict: ModelVerdict | None, min_confidence: int) -> tuple[bool, int, str, Check]:
    """Returns (pass, score, reason, V-C7 check).

    V-19: pass only IF V-C1, V-C2, the hard level of V-C3 and V-C7 pass.
    V-20: score = model confidence IF the model gave a valid result, otherwise 0.
    V-21: the first reason that applies.
    """
    if not run.vision_allowed or verdict is None:
        c7 = Check("V-C7", "skipped")
        return False, 0, run.fail_reason or "Check could not complete", c7
    n = verdict.confidence
    detail = f"confidence {n}: {verdict.reason}"[:200]
    if not verdict.same_location:
        return False, n, AI_NOT_SAME, Check("V-C7", "fail", detail)
    if not verdict.task_completed:
        return False, n, AI_NOT_DONE, Check("V-C7", "fail", detail)
    if n < min_confidence:
        return False, n, AI_LOW.format(n=n, min=min_confidence), Check("V-C7", "fail", detail)
    return True, n, AI_PASS.format(n=n), Check("V-C7", "pass", detail)


def ascii_reason(reason: str) -> str:
    """V-21: ASCII, 120 bytes or less."""
    return reason.encode("ascii", "replace").decode("ascii")[:REASON_MAX_BYTES]


# ---------------------------------------------------------------- reports (V-22)


def _iso(ts: int | None) -> str | None:
    return datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ") if ts else None


def _settlement_state(job: sqlite3.Row | None) -> str:
    if job is None:
        return "none"
    if job["state"] in ("signed", "broadcast", "confirmed", "reverted", "blocked_nonce"):
        return job["state"]
    if job["raw_tx"]:
        return job["state"]  # for example expired after broadcast
    return "not_sent"


def build_report(storage: Storage, deployment_id: str, task_id: int, attempt: int) -> dict | None:
    """V-22 report for one attempt. None IF there is no job and no claim (HTTP 404)."""
    with storage.read() as c:
        job = c.execute(
            "SELECT * FROM jobs WHERE task_id = ? AND attempt = ? ORDER BY event_id DESC LIMIT 1", (task_id, attempt)
        ).fetchone()
        claim = c.execute(
            "SELECT * FROM claims WHERE task_id = ? AND attempt = ? AND role = 'after'", (task_id, attempt)
        ).fetchone()
    if job is None and claim is None:
        return None
    claim_state = claim["state"] if claim else None
    return {
        "deploymentId": deployment_id,
        "taskId": task_id,
        "attempt": attempt,
        "proofHash": job["proof_hash"] if job else claim["sha256"],
        "jobState": job["state"] if job else None,
        "attemptResult": {"accepted": "approved", "rejected": "rejected"}.get(claim_state, "none"),
        "evaluation": json.loads(job["evaluation_json"]) if job and job["evaluation_json"] else None,
        "settlement": {
            "state": _settlement_state(job),
            "txHashes": json.loads(job["tx_hashes"]) if job else [],
            "error": job["last_error"] if job else None,
            "conflict": bool(job["conflict"]) if job else False,
        },
        "createdAt": _iso(job["created_at"]) if job else None,
    }


def attempts_of_task(storage: Storage, task_id: int) -> list[int]:
    with storage.read() as c:
        rows = c.execute(
            "SELECT attempt FROM jobs WHERE task_id = ?"
            " UNION SELECT attempt FROM claims WHERE task_id = ? AND role = 'after' ORDER BY attempt",
            (task_id, task_id),
        ).fetchall()
    return [int(r[0]) for r in rows]


def current_job(storage: Storage, task_id: int) -> sqlite3.Row | None:
    """The job of the newest attempt of a task (V-11)."""
    with storage.read() as c:
        return c.execute(
            "SELECT job_key, state FROM jobs WHERE task_id = ? ORDER BY attempt DESC, event_id DESC LIMIT 1",
            (task_id,),
        ).fetchone()


# ---------------------------------------------------------------- worker loop


class Worker:
    """The worker loop (V-02, V-J). In readonly mode it only writes its heartbeat (V-06)."""

    def __init__(
        self,
        settings: Settings,
        storage: Storage,
        chain,
        vision: Vision,
        budget: Budget,
        readiness,
        decode: Callable = decode_in_child,
        model_input: Callable = model_input_in_child,
        clock: Callable[[], float] = time.time,
    ):
        self.settings = settings
        self.storage = storage
        self.chain = chain
        self.vision = vision
        self.budget = budget
        self.readiness = readiness  # .snapshot() and .refresh() -> {"ready": bool, ...}
        self.decode = decode
        self.model_input = model_input
        self.clock = clock
        self.heartbeat: float | None = None  # V-R9
        self.last_error: str | None = None
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._next_reconcile = 0.0
        self._pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="worker-call")

    # ------------------------------------------------------------ helpers

    def beat(self) -> None:
        self.heartbeat = time.monotonic()

    def _call(self, fn: Callable, *args):
        """Run a bounded blocking call (model, decode) in a helper thread and keep the heartbeat
        fresh while it runs, so V-R9 sees a live loop. The call itself has its own timeout."""
        fut = self._pool.submit(fn, *args)
        while True:
            try:
                return fut.result(timeout=5)
            except TimeoutError:
                self.beat()

    def _job(self, key: str) -> sqlite3.Row:
        with self.storage.read() as c:
            return c.execute("SELECT * FROM jobs WHERE job_key = ?", (key,)).fetchone()

    def _set(self, key: str, state: str, expect: str | None = None, **fields) -> None:
        """Change a job. With `expect`, only IF the job is still in that state (a concurrent
        `finalize` by the event reader wins)."""
        fields["state"] = state
        fields["updated_at"] = now_s()
        if "last_error" in fields and fields["last_error"]:
            fields["last_error"] = self._redact(str(fields["last_error"]))[:300]
        cols = ", ".join(f"{k} = ?" for k in fields)
        sql = f"UPDATE jobs SET {cols} WHERE job_key = ?"
        params = [*fields.values(), key]
        if expect is not None:
            sql += " AND state = ?"
            params.append(expect)
        with self.storage.tx() as c:
            c.execute(sql, params)
        log.info("Job %s -> %s", key.split(":", 2)[-1][:40], state)

    def _redact(self, text: str) -> str:
        for secret in (self.settings.vision_api_key, self.settings.signer_key):
            if secret:
                text = text.replace(secret, "<redacted>")
        return text

    def _task_matches(self, job: sqlite3.Row, task: dict) -> bool:
        return (
            task["status"] == "Submitted"
            and int(task["attempts"]) == int(job["attempt"])
            and task["proofHash"] == job["proof_hash"]
        )

    # ------------------------------------------------------------ startup (restart column)

    def startup(self) -> None:
        """Restart actions: `evaluating` -> `queued` (keep eval_rounds; a partial result is never
        saved). `blocked_config` -> `queued` IF a model test call succeeds (V-J6). Other states
        continue in the loop: `evaluated` re-validates, `signed` broadcasts the saved raw_tx,
        `broadcast` reconciles at once."""
        if not self.settings.live:
            return
        with self.storage.tx() as c:
            n = c.execute(
                "UPDATE jobs SET state = 'queued', evaluation_json = NULL, updated_at = ? WHERE state = 'evaluating'",
                (now_s(),),
            ).rowcount
        if n:
            log.warning("Restart: %d job(s) in evaluating set back to queued", n)
        if self.storage.count_jobs(("blocked_config",)):
            try:
                self._call(self.vision.test_call)
            except Exception as exc:
                log.warning("Model test call failed; blocked_config jobs stay blocked: %s", type(exc).__name__)
                return
            with self.storage.tx() as c:
                c.execute(
                    "UPDATE jobs SET state = 'queued', updated_at = ? WHERE state = 'blocked_config'", (now_s(),)
                )
            log.info("Model test call OK: blocked_config jobs set back to queued")

    # ------------------------------------------------------------ loop

    def wake(self) -> None:
        self._wake.set()

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()

    def run(self) -> None:
        self.beat()
        try:
            self.startup()
        except Exception as exc:
            log.warning("Worker startup step failed: %s", type(exc).__name__)
        while not self._stop.is_set():
            self.beat()
            wait = IDLE_WAIT_S
            try:
                result = self.step()
                if result is True:
                    wait = 0
                elif isinstance(result, (int, float)) and not isinstance(result, bool):
                    wait = float(result)
                self.last_error = None
            except Exception as exc:
                self.last_error = self._redact(f"{type(exc).__name__}: {str(exc)[:200]}")
                log.warning("Worker step failed: %s", self.last_error)
                wait = ERROR_WAIT_S
            self.beat()
            if wait:
                self._wake.wait(wait)
                self._wake.clear()
        self._pool.shutdown(wait=False)

    def step(self) -> bool | float:
        """Do one action. True = did work (run again at once); a number = seconds to wait."""
        if not self.settings.live:
            return IDLE_WAIT_S  # V-06: readonly never evaluates, signs or broadcasts
        with self.storage.read() as c:
            marks = ",".join("?" * len(UNSETTLED_STATES))
            unsettled = c.execute(
                f"SELECT * FROM jobs WHERE state IN ({marks}) ORDER BY event_id", UNSETTLED_STATES
            ).fetchall()
        if len(unsettled) > 1:
            raise RuntimeError(f"{len(unsettled)} unsettled jobs; V-J1 allows one")
        if unsettled:
            job = unsettled[0]
            if job["state"] == "evaluated":
                return self._sign(job)
            if job["state"] == "signed":
                return self._broadcast(job)
            if job["state"] == "broadcast":
                if time.monotonic() < self._next_reconcile:
                    return IDLE_WAIT_S
                return self._reconcile(job)
            # `evaluating` is only seen here after a failure inside _start: treat as a restart.
            self._set(job["job_key"], "queued", expect="evaluating", evaluation_json=None)
            return True
        if not self.readiness.snapshot()["ready"] or self.storage.count_jobs(STOP_STATES[1:]):
            return IDLE_WAIT_S  # V-R10 also checked here: no new job while a nonce is blocked
        job = self._select()
        if job is None:
            return IDLE_WAIT_S
        return self._start(job)

    def _select(self) -> sqlite3.Row | None:
        """V-J2: the eligible job with the lowest event_id."""
        with self.storage.read() as c:
            return c.execute(
                "SELECT * FROM jobs WHERE state = 'queued'"
                " OR (state IN ('awaiting_files', 'model_retry') AND COALESCE(next_try_at, 0) <= ?)"
                " ORDER BY event_id LIMIT 1",
                (now_s(),),
            ).fetchone()

    # ------------------------------------------------------------ queued / awaiting_files / model_retry

    def _start(self, job: sqlite3.Row) -> bool | float:
        key = job["job_key"]
        task = self.chain.get_task(job["task_id"])
        chain_now = self.chain.latest_time()
        remaining = int(task["reviewBy"]) - chain_now
        if remaining < EXPIRE_REMAINING_S:  # V-J2
            self._set(key, "expired", expect=job["state"], last_error=f"remaining {remaining} s < 150 s")
            return True
        if not self._task_matches(job, task):  # V-T6
            self._set(key, "superseded", expect=job["state"], last_error=f"task status {task['status']}")
            return True
        missing = [
            h for h in (task["beforeHash"], job["proof_hash"]) if self.storage.original_path(h) is None
        ]
        if missing and remaining > CONTINUE_WITHOUT_FILES_S:
            self._set(key, "awaiting_files", expect=job["state"], next_try_at=now_s() + AWAIT_FILES_POLL_S)
            return True
        # Budgets (V-A5, V-J7): eligible jobs wait in their state; V-J2 still applies.
        if not self.budget.verdict_tx_available() or not self.budget.model_round_available(CALLS_PER_ROUND):
            return CAP_WAIT_S

        self._set(key, "evaluating", expect=job["state"])
        try:
            return self._evaluate(job, task, remaining)
        except Exception as exc:
            # An unexpected error is a failed round (bounded by MAX_ROUNDS), never a verdict.
            self._round_failed(self._job(key), f"evaluation error: {type(exc).__name__}: {exc}")
            return True

    def _evaluate(self, job: sqlite3.Row, task: dict, remaining0: float) -> bool:
        key = job["job_key"]
        t0 = time.monotonic()
        evaluated_block = self.chain.latest_block_number()
        run = self._call(
            run_checks,
            self.storage,
            self.settings,
            job["task_id"],
            task["beforeHash"],
            job["proof_hash"],
            int(task["createdAt"]),
            self.decode,
        )
        verdict, rounds = None, int(job["eval_rounds"])
        if run.vision_allowed:
            tmp = self.storage.tmp
            b_path, a_path = tmp / f"{key[-16:]}-before.model.jpg", tmp / f"{key[-16:]}-after.model.jpg"
            try:
                before_jpeg = self._call(self.model_input, self.storage.original_path(task["beforeHash"]), b_path)
                after_jpeg = self._call(self.model_input, self.storage.original_path(job["proof_hash"]), a_path)
            finally:
                remove_quietly(b_path)
                remove_quietly(a_path)
            try:
                verdict = self._call(
                    self.vision.run_round,
                    task["title"],
                    task["description"],
                    before_jpeg,
                    after_jpeg,
                    lambda: remaining0 - (time.monotonic() - t0),
                )
            except ModelConfigError as exc:  # V-M6, V-J6
                self._set(key, "blocked_config", expect="evaluating", last_error=str(exc))
                return True
            except RoundFailed as exc:  # V-M5, V-J5
                self._round_failed(self._job(key), str(exc))
                return True
            rounds += 1
        passed, score, reason, c7 = decide(run, verdict, self.settings.min_confidence)
        evaluation = {
            "pass": passed,
            "score": score,
            "reason": ascii_reason(reason),
            "evaluatedBlock": evaluated_block,
            "model": self.settings.vision_model,
            "configVersion": self.settings.config_version,
            "checks": [c.as_dict() for c in run.checks] + [c7.as_dict()],
        }
        self._set(
            key,
            "evaluated",
            expect="evaluating",
            evaluation_json=json.dumps(evaluation),
            config_version=self.settings.config_version,
            eval_rounds=rounds,
            last_error=None,
        )
        return True

    def _round_failed(self, job: sqlite3.Row, problem: str) -> None:
        rounds = int(job["eval_rounds"]) + 1
        if rounds >= MAX_ROUNDS:
            self._set(job["job_key"], "model_failed", expect="evaluating", eval_rounds=rounds, last_error=problem)
        else:
            delay = RETRY_DELAYS_S[min(rounds, len(RETRY_DELAYS_S)) - 1]
            self._set(
                job["job_key"],
                "model_retry",
                expect="evaluating",
                eval_rounds=rounds,
                next_try_at=now_s() + delay,
                last_error=problem,
            )

    # ------------------------------------------------------------ evaluated -> signed

    def _requeue(self, job: sqlite3.Row, why: str) -> bool:
        self._set(job["job_key"], "queued", expect="evaluated", evaluation_json=None, last_error=why)
        return True

    def _sign(self, job: sqlite3.Row) -> bool | float:
        key = job["job_key"]
        task = self.chain.get_task(job["task_id"])
        remaining = int(task["reviewBy"]) - self.chain.latest_time()
        if not self._task_matches(job, task):
            self._set(key, "superseded", expect="evaluated", last_error=f"task status {task['status']}")
            return True
        if remaining < SIGN_MIN_REMAINING_S:
            self._set(key, "expired", expect="evaluated", last_error=f"remaining {remaining} s < 60 s at signing")
            return True
        if job["config_version"] != self.settings.config_version:
            return self._requeue(job, "config version changed")
        evaluation = json.loads(job["evaluation_json"])
        if evaluation["pass"]:
            # V-C2 and V-C3 again, against the accepted claims of now.
            run = self._call(
                run_checks,
                self.storage,
                self.settings,
                job["task_id"],
                task["beforeHash"],
                job["proof_hash"],
                int(task["createdAt"]),
                self.decode,
            )
            if any(run.get(i).result == "fail" for i in ("V-C1", "V-C2", "V-C3")):
                return self._requeue(job, "duplicate check changed before signing")
        if not self.readiness.refresh()["ready"]:
            return CAP_WAIT_S  # V-R: the worker loop signs only when ready
        if not self.budget.verdict_tx_available():
            return CAP_WAIT_S
        signer = self.settings.signer_address
        latest = self.chain.nonce(signer, "latest")
        pending = self.chain.nonce(signer, "pending")
        if pending != latest:
            self._set(key, "blocked_nonce", expect="evaluated", last_error=f"pending nonce {pending} != latest {latest}")
            return True
        raw, tx_hash = self.chain.build_verdict(
            self.settings.signer_key,
            latest,
            job["task_id"],
            job["attempt"],
            job["proof_hash"],
            bool(evaluation["pass"]),
            int(evaluation["score"]),
            evaluation["reason"],
        )
        # Save nonce, raw_tx and its hash BEFORE broadcast, in one transaction with the counter.
        with self.storage.tx() as c:
            cur = c.execute(
                "UPDATE jobs SET state = 'signed', nonce = ?, raw_tx = ?, tx_hashes = ?, updated_at = ?"
                " WHERE job_key = ? AND state = 'evaluated'",
                (latest, raw, json.dumps([tx_hash]), now_s(), key),
            )
            if cur.rowcount == 1:
                bump(c, utc_day(now_s()), K_VERDICT_TX)
        log.info("Job %s signed: nonce %d, tx %s", key.split(":", 2)[-1][:40], latest, tx_hash)
        return True

    # ------------------------------------------------------------ signed -> broadcast

    def _broadcast(self, job: sqlite3.Row) -> bool:
        key = job["job_key"]
        self._set(key, "broadcast", expect="signed")
        try:
            self.chain.send_raw(job["raw_tx"])
        except Exception as exc:  # the reconciler sends the same raw_tx again (V-J3 step 4)
            self._set(key, "broadcast", expect="broadcast", last_error=f"send: {type(exc).__name__}: {exc}")
        hashes = json.loads(job["tx_hashes"])
        deadline = time.monotonic() + RECEIPT_WAIT_S
        while time.monotonic() < deadline and not self._stop.is_set():
            self.beat()
            receipts = [r for r in (self.chain.receipt(h) for h in hashes) if r is not None]
            if any(r.finalized for r in receipts):
                break
            self._stop.wait(RECEIPT_POLL_S)
        return self._reconcile(self._job(key))

    # ------------------------------------------------------------ broadcast: reconcile (V-J3)

    def _reconcile(self, job: sqlite3.Row) -> bool | float:
        self._next_reconcile = time.monotonic() + RECONCILE_INTERVAL_S
        key = job["job_key"]
        if job["state"] != "broadcast":
            return True
        signer = self.settings.signer_address
        latest_nonce = self.chain.nonce(signer, "latest")  # read BEFORE the receipts (no race)
        # Step 1: a receipt for one of our hashes.
        for h in json.loads(job["tx_hashes"]):
            r = self.chain.receipt(h)
            if r is None:
                continue
            if not r.finalized:
                return IDLE_WAIT_S  # wait until its block is finalized
            if r.status == 1:
                ev = next(
                    (
                        e
                        for e in r.events
                        if e.name == "VerdictRecorded"
                        and int(e.args["id"]) == int(job["task_id"])
                        and int(e.args["attempt"]) == int(job["attempt"])
                        and e.args["proofHash"] == job["proof_hash"]
                    ),
                    None,
                )
                if ev is None:
                    raise RuntimeError(f"receipt {h} has status 1 but no matching VerdictRecorded event")
                with self.storage.tx() as c:
                    finalize(
                        c,
                        self.settings.deployment_id,
                        int(job["task_id"]),
                        int(job["attempt"]),
                        job["proof_hash"],
                        bool(ev.args["pass"]),
                        int(ev.args["score"]),
                        r.tx_hash,
                        ev.event_id,
                    )
                return True
            task = self.chain.get_task(job["task_id"])
            self._set(
                key,
                "reverted",
                expect="broadcast",
                last_error=f"receipt status 0; task status {task['status']}, attempts {task['attempts']}",
            )
            return True
        # Step 2: the event reader saw the VerdictRecorded event and called finalize.
        if self._job(key)["state"] == "confirmed":
            return True
        # Step 3: our nonce is used, but not by our transaction.
        if latest_nonce > int(job["nonce"]):
            self._set(
                key,
                "blocked_nonce",
                expect="broadcast",
                last_error=f"nonce {job['nonce']} used (latest {latest_nonce}) with no receipt for this job",
            )
            return True
        task = self.chain.get_task(job["task_id"])
        chain_now = self.chain.latest_time()
        if chain_now < int(task["reviewBy"]):  # Step 4: send the same raw_tx again
            try:
                self.chain.send_raw(job["raw_tx"])
            except Exception as exc:
                self._set(key, "broadcast", expect="broadcast", last_error=f"resend: {type(exc).__name__}: {exc}")
            return IDLE_WAIT_S
        if chain_now >= int(task["reviewBy"]) + EXPIRE_AFTER_REVIEW_S:  # Step 5
            self._set(key, "expired", expect="broadcast", last_error="no receipt 10 minutes after reviewBy")
            return True
        return IDLE_WAIT_S  # Step 5: wait. Step 6: never sign a different transaction.
