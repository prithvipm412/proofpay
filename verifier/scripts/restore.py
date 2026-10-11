"""Restore check of a backup archive in readonly mode (V-B2). It never signs (V-06).

Usage (from the verifier/ folder):
    .venv/bin/python scripts/restore.py ../verifier-data/backups/proofpay-backup-....tar.gz
    .venv/bin/python scripts/restore.py ARCHIVE --to ../verifier-data/restore-test --port 8002

Steps:
  1. Unpack to a NEW folder (default ../verifier-data/restore-<UTC time>/) and verify every file in
     the manifest (size and SHA-256). The folder is marked `restored`.
  2. Start the verifier on it with VERIFIER_MODE=readonly (port 8002, no key, no lock), so it does
     the full event rebuild from START_BLOCK to the safe head (V-E5). This takes about
     (safe head - START_BLOCK) / 500 seconds; the progress is printed every 30 s.
  3. Stop it, then print: missing referenced files, claim counts by state, and the accepted claims
     compared with the chain's pass verdicts (read from contract state at the same block).
Exit code 0 only IF the files verify, the rebuild completes and the two sets are equal.

The live verifier can keep running (different folder, different port; this copy is readonly).
A restored copy goes live only after the old live verifier is stopped (V-B3, README failover).
"""

from __future__ import annotations

import argparse
import datetime as dt
import os
import pathlib
import subprocess
import sys
import time

import httpx
from web3 import Web3

VERIFIER_DIR = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(VERIFIER_DIR))

from app.backup import BackupError, chain_pass_verdicts, restore_report, unpack  # noqa: E402
from app.chain import load_abi  # noqa: E402
from app.config import ConfigError, environment, load_settings  # noqa: E402

TASK_KEYS = ("poster", "worker", "amount", "beforeHash", "proofHash", "createdAt", "submitBy", "reviewBy",
             "disputeUntil", "attempts", "score", "status", "title", "description")


def port_free(port: int) -> bool:
    import socket

    with socket.socket() as s:
        return s.connect_ex(("127.0.0.1", port)) != 0


def run_readonly_verifier(root: pathlib.Path, port: int, log_path: pathlib.Path, timeout_s: float) -> bool:
    env = dict(os.environ)
    env.update(DATA_ROOT=str(root), VERIFIER_MODE="readonly", VERIFIER_PRIVATE_KEY="", CRASH_AFTER_STATE="")
    with open(log_path, "a") as log:
        proc = subprocess.Popen([sys.executable, "-m", "app.main", "--port", str(port)],
                                cwd=VERIFIER_DIR, env=env, stdout=log, stderr=subprocess.STDOUT)
    try:
        end, last_print, started = time.monotonic() + timeout_s, 0.0, time.monotonic()
        while time.monotonic() < end:
            if proc.poll() is not None:
                print(f"The readonly verifier stopped (exit code {proc.returncode}). See {log_path}")
                return False
            try:
                h = httpx.get(f"http://127.0.0.1:{port}/health", timeout=5).json()
                if h["mode"] != "readonly":
                    print("STOP: the restore copy is not in readonly mode.")
                    return False
                if h["historyStatus"] == "complete" and h["ready"]:
                    print(f"   rebuild complete in {time.monotonic() - started:.0f} s; /health ready (readonly)")
                    return True
                status = f"{h['historyStatus']}, {h['eventLagBlocks']} blocks behind the safe head"
            except Exception as exc:
                status = f"starting ({type(exc).__name__})"
            if time.monotonic() - last_print >= 30:
                print(f"   {time.monotonic() - started:5.0f} s: {status}")
                last_print = time.monotonic()
            time.sleep(2)
        print("The rebuild did not finish in time.")
        return False
    finally:
        if proc.poll() is None:
            proc.terminate()
            proc.wait(timeout=60)


def main() -> int:
    p = argparse.ArgumentParser(description="ProofPay restore check in readonly mode (V-B2)")
    p.add_argument("archive", type=pathlib.Path)
    p.add_argument("--to", type=pathlib.Path, help="new DATA_ROOT folder (must be new or empty)")
    p.add_argument("--port", type=int, default=8002)
    p.add_argument("--timeout-min", type=float, default=240, help="rebuild time limit in minutes (default 240)")
    args = p.parse_args()
    try:
        settings = load_settings(environment())
    except ConfigError as exc:
        print(f"Settings problem: {exc}")
        return 2
    if not port_free(args.port):
        print(f"Port {args.port} is in use. Choose another --port.")
        return 2
    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    root = (args.to or VERIFIER_DIR.parent / "verifier-data" / f"restore-{stamp}").resolve()

    print(f"1. Unpack and verify: {args.archive} -> {root}")
    try:
        up = unpack(args.archive.resolve(), root)
    except BackupError as exc:
        print(f"RESTORE FAILED: {exc}")
        return 1
    m = up.manifest
    print(f"   backup made {m['createdAt']}, deployment {m['deploymentId']}, checkpoint block "
          f"{m['meta'].get('checkpointBlock')}, {len(m['files'])} files")
    if up.problems:
        for problem in up.problems:
            print(f"   PROBLEM: {problem}")
        print("RESTORE FAILED: the archive does not verify.")
        return 1
    print("   every file verifies (size and SHA-256)")
    if m["deploymentId"] != settings.deployment_id:
        print(f"RESTORE FAILED: the backup is for {m['deploymentId']}, verifier/.env is for {settings.deployment_id}.")
        return 1

    log_path = root / "restore-verifier.log"
    print(f"2. Readonly verifier on port {args.port}: full rebuild from START_BLOCK {settings.start_block} (log {log_path})")
    if not run_readonly_verifier(root, args.port, log_path, args.timeout_min * 60):
        return 1

    print("3. Compare with the chain")
    report = None
    meta_cp = None
    import sqlite3

    conn = sqlite3.connect(f"file:{up.data_dir / 'proofpay.db'}?mode=ro", uri=True)
    try:
        meta_cp = int(conn.execute("SELECT value FROM meta WHERE key = 'checkpointBlock'").fetchone()[0])
    finally:
        conn.close()
    w3 = Web3(Web3.HTTPProvider(settings.rpc_url, request_kwargs={"timeout": 30}))
    if w3.eth.chain_id != settings.chain_id:
        print("STOP: RPC chain ID differs from CHAIN_ID.")
        return 1
    contract = w3.eth.contract(address=Web3.to_checksum_address(settings.escrow_address), abi=load_abi())
    block = meta_cp
    try:
        count = contract.functions.taskCount().call(block_identifier=block)
    except Exception:
        block = "latest"  # the RPC may not keep old state; the checkpoint is seconds old
        count = contract.functions.taskCount().call()
        print("   note: contract state read at latest, not at the checkpoint block")

    def get_task(task_id: int) -> dict:
        t = contract.functions.getTask(task_id).call(block_identifier=block)
        d = dict(zip(TASK_KEYS, t))
        d["proofHash"] = "0x" + bytes(d["proofHash"]).hex()
        return d

    report = restore_report(up, chain_pass_verdicts(count, get_task))
    print(f"   checkpoint block {report.checkpoint}, historyStatus {report.history_status}, taskCount {count}")
    print(f"   claims by state: {report.claim_counts}")
    print(f"   missing referenced files: {len(report.missing_after_backup) + len(report.missing_at_backup) + len(report.missing_lost)}")
    for sha in report.missing_after_backup:
        print(f"     {sha}  claimed after the backup (expected; V-C2 still blocks by hash)")
    for sha in report.missing_at_backup:
        print(f"     {sha}  already missing when the backup was made")
    for sha in report.missing_lost:
        print(f"     {sha}  LOST: it was in the backup")
    print(f"   accepted claims: {len(report.accepted)}; chain pass verdicts: {len(report.chain_pass)}")
    for task_id, attempt, sha in sorted(report.accepted | report.chain_pass):
        where = ("both" if (task_id, attempt, sha) in report.accepted and (task_id, attempt, sha) in report.chain_pass
                 else "claims only" if (task_id, attempt, sha) in report.accepted else "chain only")
        after = "" if sha in m["referenced"] else "  (accepted after the backup)"
        print(f"     task {task_id} attempt {attempt} {sha[:12]}…  {where}{after}")
    if report.ok:
        print("RESTORE CHECK OK: accepted claims equal the chain's pass verdicts.")
        return 0
    print("RESTORE CHECK FAILED: " + ("sets differ" if not report.equal else "missing files or incomplete history"))
    return 1


if __name__ == "__main__":
    sys.exit(main())
