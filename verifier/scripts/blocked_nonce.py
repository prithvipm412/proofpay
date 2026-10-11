"""Operator recovery for jobs in `blocked_nonce` (V-J3 step 3, V-R10; README "blocked_nonce recovery").

While any job is in `blocked_nonce`, `ready` is false and the verifier signs nothing. Run this with
the live verifier STOPPED (the script takes the signer lock, so it refuses while a verifier runs):

    cd verifier
    .venv/bin/python scripts/blocked_nonce.py                    # show each blocked job and the chain facts
    .venv/bin/python scripts/blocked_nonce.py --apply            # apply the safe actions below
    .venv/bin/python scripts/blocked_nonce.py --apply --expire-foreign   # also the last case (read it first)

For each blocked job:
  - No transaction was signed (blocked before signing)        -> `queued`  (safe: nothing was signed)
  - Our saved transaction has a final receipt with status 1     -> no change; start the verifier, the
                                                                 event reader confirms the job (V-J4)
  - Our saved transaction has a final receipt with status 0     -> `reverted`
  - Our nonce is still unused (latest nonce <= job nonce)        -> `broadcast` (the same raw_tx is sent again)
  - Our nonce was used by another transaction (no receipt)       -> `expired` ONLY with --expire-foreign.
    Before you use it: open the verifier address on the explorer, find the transaction with that nonce,
    and make sure no other copy of the verifier uses the key (section 4). The job never signs a
    different transaction (V-J3 step 6); the poster can refund at reviewBy (V-J8).
"""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import sys

VERIFIER_DIR = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(VERIFIER_DIR))

from app.config import ConfigError, environment, load_settings  # noqa: E402
from app.readiness import SignerLockError, acquire_signer_lock  # noqa: E402
from app.storage import Storage, now_s  # noqa: E402


def plan(storage: Storage, chain, signer: str) -> list[dict]:
    """One action for each blocked_nonce job: queued, none, reverted, broadcast or expire_foreign."""
    with storage.read() as c:
        jobs = c.execute("SELECT * FROM jobs WHERE state = 'blocked_nonce' ORDER BY event_id").fetchall()
    latest = chain.nonce(signer, "latest") if jobs else None
    out = []
    for job in jobs:
        item = {"job_key": job["job_key"], "task_id": job["task_id"], "attempt": job["attempt"],
                "nonce": job["nonce"], "last_error": job["last_error"], "latest_nonce": latest}
        if not job["raw_tx"]:
            item.update(action="queued", why="no transaction was signed for this job")
        else:
            receipts = [r for r in (chain.receipt(h) for h in json.loads(job["tx_hashes"] or "[]")) if r]
            if any(r.status == 1 for r in receipts):
                item.update(action="none", why="our verdict transaction was mined; the event reader confirms the job")
            elif receipts:
                item.update(action="reverted", why="our verdict transaction reverted (receipt status 0)")
            elif latest <= int(job["nonce"]):
                item.update(action="broadcast", why=f"nonce {job['nonce']} is unused (latest {latest}); send the same raw_tx")
            else:
                item.update(action="expire_foreign",
                            why=f"nonce {job['nonce']} was used by another transaction (latest {latest}), no receipt for ours")
        out.append(item)
    return out


def apply(storage: Storage, items: list[dict], expire_foreign: bool) -> list[str]:
    done = []
    with storage.tx() as c:
        for it in items:
            state = {"queued": "queued", "reverted": "reverted", "broadcast": "broadcast",
                     "expire_foreign": "expired" if expire_foreign else None}.get(it["action"])
            if state is None:
                continue
            note = f"operator: blocked_nonce -> {state}: {it['why']}"
            cur = c.execute(
                "UPDATE jobs SET state = ?, last_error = ?, updated_at = ? WHERE job_key = ? AND state = 'blocked_nonce'",
                (state, note[:500], now_s(), it["job_key"]),
            )
            if cur.rowcount:
                done.append(f"{it['job_key']} -> {state}")
    return done


def main() -> int:
    p = argparse.ArgumentParser(description="Recovery for blocked_nonce jobs (README)")
    p.add_argument("--apply", action="store_true", help="apply the safe actions")
    p.add_argument("--expire-foreign", action="store_true", help="also expire jobs whose nonce another transaction used")
    args = p.parse_args()
    try:
        settings = load_settings(environment())
    except ConfigError as exc:
        print(f"Settings problem: {exc}")
        return 2
    if not settings.live:
        print("VERIFIER_MODE is not live; a readonly copy never signs, so it has no blocked_nonce jobs to fix.")
        return 2
    data_dir = VERIFIER_DIR / settings.data_dir
    try:
        lock = acquire_signer_lock(data_dir)  # refuses while the live verifier runs (V-07)
    except SignerLockError as exc:
        print(f"STOP: {exc}")
        return 2
    try:
        from app.chain import Chain

        storage = Storage(data_dir, settings.deployment_id, settings.max_storage_mb, settings.start_block)
        chain = Chain(settings.rpc_url, settings.escrow_address)
        items = plan(storage, chain, settings.signer_address)
        if not items:
            print("No job is in blocked_nonce.")
            return 0
        for it in items:
            print(f"task {it['task_id']} attempt {it['attempt']} nonce {it['nonce']}: {it['action']} ({it['why']})")
        if not args.apply:
            print("Nothing changed. Run again with --apply (and --expire-foreign only after the explorer check).")
            return 0
        for line in apply(storage, items, args.expire_foreign):
            print(f"changed: {line}")
        left = [it for it in items if it["action"] == "expire_foreign" and not args.expire_foreign]
        if left:
            print(f"{len(left)} job(s) still blocked: they need --expire-foreign after the explorer check.")
            return 1
        print("Done. Start the verifier again; /health shows ready when V-R10 passes.")
        return 0
    finally:
        os.close(lock)


if __name__ == "__main__":
    sys.exit(main())
