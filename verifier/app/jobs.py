"""Jobs (V-J). M3 has the job key and `finalize` (V-J4), because the event reader calls `finalize`
for every VerdictRecorded event (V-E3). The worker loop, transition table and reconciler come in M4.
"""

from __future__ import annotations

import json
import sqlite3

from .storage import now_s

TERMINAL_STATES = ("confirmed", "reverted", "superseded", "expired", "model_failed", "not_admitted")


def job_key(deployment_id: str, task_id: int, attempt: int, proof_hash: str) -> str:
    return f"{deployment_id}:{task_id}:{attempt}:{proof_hash}"


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
