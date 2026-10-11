"""M7 trust flows and recovery test on Monad testnet (M7 tasks 1 to 3, demo steps 8 and 9).

The owner runs it in an own terminal (from the verifier/ folder):

    .venv/bin/python scripts/trust_testnet.py              # about 15 min, then waits about 1 h for step 7
    .venv/bin/python scripts/trust_testnet.py --expire 7   # only step 7 later, IF you stopped the wait

Before you start: stop every other live verifier (port 8000 must be free; section 4, V-07).
This script starts the live verifier itself, from verifier/.env, because the recovery test stops
it on purpose. It asks for two keystore passwords (cast prompts): `deployer` (poster and arbiter)
and `worker`. Keys stay in memory; nothing secret is printed or saved.

What it does on chain 10143 (each step prints PASS or FAIL and the transaction hashes):
  1. Recovery after `evaluated`: verifier started with CRASH_AFTER_STATE=evaluated, task R1 is made,
     the verifier stops itself after saving `evaluated`, a normal restart finishes the job:
     `confirmed`, one verdict transaction, verifier nonce + 1.
  2. R1: dispute by the poster, then resolveDispute(true) by the arbiter (worker paid).
  3. Recovery after `signed` (task R2), then R2: dispute by the poster (step 7 ends it).
  4. Recovery after `broadcast` (task R3), then R3: release by the worker after 60 s.
  5. Demo step 8: task R4 gets R1's accepted after photo as proof -> V-C2 fail, MON stays.
  6. Demo step 9: R4 passes its submitBy; the poster refunds it.
  7. R2: expireDispute by the worker at disputeUntil + arbitrationTimeout (about 1 h after step 3).
Photos are drawn scenes (a bench with litter, then without), new for each run.
Cost: about 0.25 MON of fees in total (deployer about 0.17, worker about 0.08), plus 0.001 MON
locked for each task and paid back.

`--rehearse` runs the same steps on a local anvil chain with a fake model and time skips (no
testnet, no keystores; public anvil accounts only; section 4 exception).
"""

from __future__ import annotations

import argparse
import datetime as dt
import http.server
import json
import os
import pathlib
import secrets
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time

import httpx
from eth_account import Account
from web3 import Web3

HERE = pathlib.Path(__file__).resolve().parent
VERIFIER_DIR = HERE.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(VERIFIER_DIR))
sys.path.insert(0, str(VERIFIER_DIR / "tests" / "fixtures"))
import e2e_local as e2e  # noqa: E402
from app.chain import GAS_MARGIN_PCT, load_abi  # noqa: E402
from app.config import environment, load_settings  # noqa: E402
from make_fixtures import scene  # noqa: E402
from setup_env import key_from_keystore  # noqa: E402  (cast password prompt; key never printed)

TESTNET_CHAIN_ID = 10143
OPEN, ACCEPTED, SUBMITTED, APPROVED, DISPUTED, PAID, REFUNDED = range(7)
STATUS = ["Open", "Accepted", "Submitted", "Approved", "Disputed", "Paid", "Refunded"]
CRASH_EXIT_CODE = 70  # app.jobs.CRASH_EXIT_CODE
TITLE = "Remove the litter around this bench"
DESCRIPTION = "Pick up all litter on the grass around the bench and put it in a bin."
LOG_DIR = VERIFIER_DIR.parent / "verifier-data" / "logs"


class Net:
    """One chain, three roles, and the verifier process that this script owns."""

    def __init__(self, w3: Web3, address: str, keys: dict[str, str], rehearse: bool, port: int, log: pathlib.Path):
        self.w3, self.rehearse, self.port, self.log = w3, rehearse, port, log
        self.contract = w3.eth.contract(address=Web3.to_checksum_address(address), abi=load_abi())
        self.acct = {role: Account.from_key(k) for role, k in keys.items()}
        self.proc: subprocess.Popen | None = None
        self.env_for = None  # (crash) -> environment of the verifier process
        self.db: pathlib.Path | None = None
        self.records: list[dict] = []
        self.results: list[tuple[str, bool]] = []
        self.seed = secrets.randbelow(10**9)
        self.tmp = pathlib.Path(tempfile.mkdtemp(prefix="proofpay-trust-photos-"))
        self.errors = {
            Web3.keccak(text=f"{e['name']}({','.join(i['type'] for i in e['inputs'])})").hex().removeprefix("0x")[:8]: e[
                "name"
            ]
            for e in self.contract.abi
            if e.get("type") == "error"
        }

    # ------------------------------------------------------------ verifier process

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def start(self, crash: str = "") -> None:
        log = open(self.log, "a")
        log.write(f"\n==== {dt.datetime.now().isoformat(timespec='seconds')} start (CRASH_AFTER_STATE={crash!r})\n")
        log.flush()
        self.proc = subprocess.Popen(
            [sys.executable, "-m", "app.main", "--port", str(self.port)],
            cwd=VERIFIER_DIR, env=self.env_for(crash), stdout=log, stderr=subprocess.STDOUT,
        )
        self.wait_ready()

    def wait_ready(self, timeout: float = 1800) -> None:
        end, last_print, lag = time.monotonic() + timeout, 0.0, None
        while time.monotonic() < end:
            if self.proc and self.proc.poll() is not None:
                raise RuntimeError(f"the verifier stopped during startup (exit code {self.proc.returncode}); see {self.log}")
            try:
                h = httpx.get(f"{self.url}/health", timeout=5).json()
                if h["ready"]:
                    return
                lag = h.get("eventLagBlocks")
            except Exception:
                pass
            if time.monotonic() - last_print > 30:
                print(f"      waiting for the verifier to be ready (event lag {lag} blocks)…")
                last_print = time.monotonic()
            time.sleep(1)
        raise RuntimeError("the verifier did not become ready")

    def stop(self) -> None:
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()
            self.proc.wait(timeout=60)
        self.proc = None

    def job_state(self, task_id: int) -> str | None:
        conn = sqlite3.connect(f"file:{self.db}?mode=ro", uri=True)
        try:
            row = conn.execute("SELECT state FROM jobs WHERE task_id = ? ORDER BY attempt DESC", (task_id,)).fetchone()
            return row[0] if row else None
        finally:
            conn.close()

    def report(self, task_id: int, timeout: float = 420) -> dict:
        """Wait until attempt 1 of the task has a result on the chain (approved or rejected)."""
        end, last = time.monotonic() + timeout, None
        while time.monotonic() < end:
            try:
                httpx.post(f"{self.url}/verify", json={"taskId": task_id}, timeout=10)
                r = httpx.get(f"{self.url}/tasks/{task_id}/attempts/1", timeout=10)
                if r.status_code == 200:
                    last = r.json()
                    if last["attemptResult"] in ("approved", "rejected") and last["jobState"] == "confirmed":
                        return last
            except Exception:
                pass
            time.sleep(3)
        state = last["jobState"] if last else None
        raise RuntimeError(f"task {task_id}: no confirmed result in {timeout:.0f} s (job state {state})")

    # ------------------------------------------------------------ chain

    def now(self) -> int:
        return int(self.w3.eth.get_block("latest")["timestamp"])

    def task(self, task_id: int) -> tuple:
        return self.contract.functions.getTask(task_id).call()

    def status(self, task_id: int) -> int:
        return self.task(task_id)[11]

    def balance(self, role: str) -> int:
        return self.w3.eth.get_balance(self.acct[role].address)

    def send(self, role: str, what: str, task_id: int | None, fn, value: int = 0) -> int:
        """Sign, send and wait for status 1. Returns the fee paid in wei."""
        a = self.acct[role]
        try:
            estimate = int(fn.estimate_gas({"from": a.address, "value": value}))
        except Exception as exc:
            raise RuntimeError(f"{what}: the contract refuses it ({self.error_name(exc)})") from None
        base = int(self.w3.eth.get_block("latest")["baseFeePerGas"])
        try:
            priority = int(self.w3.eth.max_priority_fee)
        except Exception:
            priority = 2 * 10**9
        tx = fn.build_transaction({
            "from": a.address, "value": value, "nonce": self.w3.eth.get_transaction_count(a.address),
            "gas": estimate * GAS_MARGIN_PCT // 100,  # Monad charges the limit (PROGRESS, M1)
            "maxPriorityFeePerGas": priority, "maxFeePerGas": 2 * base + priority,
            "chainId": self.w3.eth.chain_id, "type": 2,
        })
        h = self.w3.eth.send_raw_transaction(a.sign_transaction(tx).raw_transaction)
        r = self.w3.eth.wait_for_transaction_receipt(h, timeout=180)
        tx_hash = "0x" + bytes(h).hex()
        if r["status"] != 1:
            raise RuntimeError(f"{what}: transaction reverted ({tx_hash})")
        self.record(what, task_id, tx_hash)
        return int(r["gasUsed"]) * int(r["effectiveGasPrice"])

    def error_name(self, exc: Exception) -> str:
        text = repr(exc).lower()
        return next((name for sel, name in self.errors.items() if sel in text), type(exc).__name__)

    def refused_with(self, role: str, fn, error: str) -> bool:
        try:
            fn.estimate_gas({"from": self.acct[role].address})
        except Exception as exc:
            return self.error_name(exc) == error
        return False

    def wait_until(self, ts: int, why: str) -> None:
        left = ts - self.now()
        if left <= 0:
            return
        if self.rehearse:  # local chain only: skip the time
            self.w3.provider.make_request("evm_increaseTime", [left + 1])
            self.w3.provider.make_request("evm_mine", [])
            return
        print(f"      waiting {left} s ({why})…")
        last_print = time.monotonic()
        while self.now() < ts:
            time.sleep(min(15, max(1, ts - self.now())))
            if time.monotonic() - last_print >= 300:
                print(f"      {max(0, ts - self.now())} s left ({why})")
                last_print = time.monotonic()

    def record(self, what: str, task_id: int | None, tx_hash: str) -> None:
        self.records.append({"what": what, "task": task_id, "tx": tx_hash})
        print(f"      tx {what}: {tx_hash}")

    def check(self, name: str, ok: bool) -> None:
        print(f"   {'PASS' if ok else 'FAIL'}  {name}")
        self.results.append((name, ok))

    # ------------------------------------------------------------ photos and tasks

    def upload(self, img, name: str) -> str:
        path = self.tmp / f"{name}.jpg"
        img.save(path, "JPEG", quality=90)
        with open(path, "rb") as f:
            r = httpx.post(f"{self.url}/upload", files={"file": (path.name, f, "image/jpeg")}, timeout=60)
        r.raise_for_status()
        return r.json()["sha256"]

    def new_task(self, label: str, amount: int, submit_delay: int, after: str | None = None) -> tuple[int, str]:
        """create (poster) -> accept (worker) -> submit (worker). Returns (task id, after photo hash)."""
        self.seed += 1
        before = self.upload(scene(self.seed, True), f"{label}-before")
        after = after or self.upload(scene(self.seed, False), f"{label}-after")
        c = self.contract.functions
        self.send("poster", f"{label} createTask", None,
                  c.createTask(TITLE, DESCRIPTION, bytes.fromhex(before[2:]), self.now() + submit_delay), amount)
        task_id = c.taskCount().call()
        self.records[-1]["task"] = task_id
        self.send("worker", f"{label} acceptTask", task_id, c.acceptTask(task_id))
        self.send("worker", f"{label} submitProof", task_id, c.submitProof(task_id, bytes.fromhex(after[2:])))
        print(f"      {label} = task #{task_id}")
        return task_id, after

    # ------------------------------------------------------------ steps

    def wait_approved(self, task_id: int, timeout: float = 420) -> bool:
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            if self.status(task_id) == APPROVED:
                return True
            time.sleep(2)
        return False

    def recovery(self, label: str, state: str, amount: int, on_approved=None) -> tuple[int, str]:
        """Planned crash after `state`, normal restart. `on_approved(task_id)` runs as soon as the chain
        shows Approved, so that a dispute fits in the 60 s window."""
        verifier_addr = self.contract.functions.verifier().call()
        self.stop()
        self.start(crash=state)
        nonce0 = self.w3.eth.get_transaction_count(verifier_addr)
        task_id, after = self.new_task(label, amount, submit_delay=900)
        httpx.post(f"{self.url}/verify", json={"taskId": task_id}, timeout=10)
        try:
            code = self.proc.wait(timeout=420)
        except subprocess.TimeoutExpired:
            code = None
            self.stop()
        saved = self.job_state(task_id)
        self.proc = None
        self.start()
        approved = self.wait_approved(task_id)
        if approved and on_approved:
            on_approved(task_id)
        report = self.report(task_id)
        nonce1 = self.w3.eth.get_transaction_count(verifier_addr)
        txs = report["settlement"]["txHashes"]
        for h in txs:
            self.record(f"{label} recordVerdict (after restart)", task_id, h)
        print(f"      exit code {code}, saved state {saved}, after restart {report['jobState']}, "
              f"verifier nonce +{nonce1 - nonce0}")
        self.check(
            f"recovery after {state} (task #{task_id}): confirmed with one transaction",
            code == CRASH_EXIT_CODE and saved == state and report["jobState"] == "confirmed"
            and report["attemptResult"] == "approved" and len(txs) == 1 and nonce1 - nonce0 == 1
            and approved,
        )
        return task_id, after

    def dispute_resolve(self, task_id: int) -> None:
        c = self.contract.functions
        self.send("poster", "R1 dispute", task_id, c.dispute(task_id))
        before = self.balance("worker")
        self.send("arbiter", "R1 resolveDispute(true)", task_id, c.resolveDispute(task_id, True))
        gained = self.balance("worker") - before
        self.check(f"dispute -> resolveDispute(true) (task #{task_id}): Paid, worker got the amount",
                   self.status(task_id) == PAID and gained == self.task(task_id)[2])

    def dispute_only(self, task_id: int) -> None:
        self.send("poster", "R2 dispute", task_id, self.contract.functions.dispute(task_id))
        self.check(f"dispute (task #{task_id}): Disputed, arbiter does not act", self.status(task_id) == DISPUTED)

    def release(self, task_id: int) -> None:
        c = self.contract.functions
        in_window = self.now() < self.task(task_id)[8]
        early = self.refused_with("poster", c.release(task_id), "DisputeWindowOpen") if in_window else True
        self.wait_until(self.task(task_id)[8], "dispute window")
        time.sleep(2)
        before = self.balance("worker")
        self.send("poster", "R3 release", task_id, c.release(task_id))  # any user can release (C-20)
        gained = self.balance("worker") - before
        self.check(f"release after the window (task #{task_id}): Paid, worker got the amount"
                   f"{', refused in the window' if in_window else ''}",
                   early and self.status(task_id) == PAID and gained == self.task(task_id)[2])

    def reuse(self, photo: str, amount: int) -> int:
        held = self.w3.eth.get_balance(self.contract.address)
        task_id, _ = self.new_task("R4", amount, submit_delay=240, after=photo)
        report = self.report(task_id)
        checks = {c["id"]: (c["result"], c["detail"]) for c in (report["evaluation"] or {}).get("checks", [])}
        for h in report["settlement"]["txHashes"]:
            self.record("R4 recordVerdict (V-C2 fail)", task_id, h)
        print(f"      V-C2: {checks.get('V-C2')}; reason: {(report['evaluation'] or {}).get('reason')}")
        self.check(f"demo step 8 (task #{task_id}): exact reuse fails V-C2, MON stays in the contract",
                   report["attemptResult"] == "rejected" and checks.get("V-C2", ("",))[0] == "fail"
                   and self.status(task_id) == ACCEPTED
                   and self.w3.eth.get_balance(self.contract.address) == held + amount)
        return task_id

    def refund_expired(self, task_id: int) -> None:
        c = self.contract.functions
        early = self.refused_with("poster", c.refund(task_id), "RefundNotAvailable")
        self.wait_until(self.task(task_id)[6] + 1, "R4 submitBy")
        time.sleep(2)
        before = self.balance("poster")
        fee = self.send("poster", "R4 refund", task_id, c.refund(task_id))
        gained = self.balance("poster") - before + fee
        self.check(f"demo step 9 (task #{task_id}): refund refused before submitBy, works after it",
                   early and self.status(task_id) == REFUNDED and gained == self.task(task_id)[2])

    def expire(self, task_id: int) -> None:
        c = self.contract.functions
        t = self.task(task_id)
        if t[11] != DISPUTED:
            self.check(f"expireDispute (task #{task_id}): task is {STATUS[t[11]]}, not Disputed", False)
            return
        end = t[8] + c.arbitrationTimeout().call()
        self.wait_until(end, f"arbitration timeout of task #{task_id}")
        time.sleep(2)
        closed = self.refused_with("arbiter", c.resolveDispute(task_id, True), "ArbitrationClosed") \
            if "arbiter" in self.acct else True
        before = self.balance("poster") if "poster" in self.acct else None
        self.send("worker", "R2 expireDispute", task_id, c.expireDispute(task_id))
        refunded = before is None or self.balance("poster") - before == t[2]
        self.check(f"dispute -> expireDispute (task #{task_id}): Refunded to the poster; arbiter refused at the timeout",
                   self.status(task_id) == REFUNDED and closed and refunded)


# ---------------------------------------------------------------- setup


def testnet_net(port: int, need: tuple[str, ...]) -> Net:
    settings = load_settings(environment())
    if settings.chain_id != TESTNET_CHAIN_ID:
        raise SystemExit(f"verifier/.env CHAIN_ID is {settings.chain_id}; this script runs only on {TESTNET_CHAIN_ID}.")
    w3 = Web3(Web3.HTTPProvider(settings.rpc_url, request_kwargs={"timeout": 30}))
    if w3.eth.chain_id != TESTNET_CHAIN_ID:  # section 4: never mainnet (143)
        raise SystemExit(f"RPC chain ID is {w3.eth.chain_id}, not {TESTNET_CHAIN_ID}. Stop.")
    keys = {}
    if "deployer" in need:
        print("Keystore `deployer` (poster and arbiter):")
        keys["poster"] = keys["arbiter"] = key_from_keystore("deployer")
    if "worker" in need:
        print("Keystore `worker`:")
        keys["worker"] = key_from_keystore("worker")
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    net = Net(w3, settings.escrow_address, keys, False, port,
              LOG_DIR / f"trust-testnet-verifier-{dt.datetime.now():%Y%m%d-%H%M}.log")
    net.db = VERIFIER_DIR / settings.data_dir / "proofpay.db"  # a relative DATA_ROOT is relative to verifier/

    def env_for(crash: str) -> dict:
        env = dict(os.environ)
        env["CRASH_AFTER_STATE"] = crash
        env["VERIFIER_MODE"] = "live"
        return env

    net.env_for = env_for
    if "arbiter" in net.acct:
        owner = net.contract.functions.owner().call()
        if owner.lower() != net.acct["arbiter"].address.lower():
            raise SystemExit(f"Keystore `deployer` is {net.acct['arbiter'].address}, but the contract owner is {owner}.")
    return net


def rehearsal_net(tmp: pathlib.Path, port: int) -> tuple[Net, list]:
    anvil = e2e.start_anvil(tmp)
    chain = e2e.Local()
    address, block = chain.deploy()
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), e2e.FakeModelHandler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    model_url = f"http://127.0.0.1:{server.server_address[1]}/v1"
    keys = {"poster": e2e.ANVIL_KEYS["deployer"], "arbiter": e2e.ANVIL_KEYS["deployer"], "worker": e2e.ANVIL_KEYS["worker"]}
    net = Net(chain.w3, address, keys, True, port, tmp / "verifier.log")
    net.db = tmp / "data" / f"31337-{address.lower()}" / "proofpay.db"
    net.env_for = lambda crash: e2e.verifier_env(address, block, model_url, tmp / "data", CRASH_AFTER_STATE=crash)
    return net, [anvil, server]


def save_records(net: Net) -> pathlib.Path | None:
    if net.rehearse or not net.records:
        return None
    path = VERIFIER_DIR.parent / "verifier-data" / f"trust-testnet-{dt.datetime.now():%Y%m%d-%H%M%S}.json"
    path.write_text(json.dumps({"records": net.records, "results": net.results}, indent=2))
    return path


def summary(net: Net) -> int:
    print("\nTransactions (paste into PROGRESS.md, Important transactions):")
    day = dt.date.today().isoformat()
    for r in net.records:
        task = f"task {r['task']} " if r["task"] else ""
        print(f"| {day} | M7 {task}{r['what']} | {r['tx']} |")
    print("\nResults:")
    for name, ok in net.results:
        print(f"  {'PASS' if ok else 'FAIL'}  {name}")
    path = save_records(net)
    if path:
        print(f"\nSaved: {path}")
    return 0 if net.results and all(ok for _, ok in net.results) else 1


def main() -> int:
    p = argparse.ArgumentParser(description="ProofPay M7 trust flows and recovery test on Monad testnet")
    p.add_argument("--rehearse", action="store_true", help="run on a local anvil chain with a fake model")
    p.add_argument("--port", type=int, help="verifier port (default 8000; 8001 with --rehearse)")
    p.add_argument("--amount", default="0.001", help="MON locked in each task (default 0.001, the minimum)")
    p.add_argument("--no-expire-wait", action="store_true", help="stop after step 6; run --expire ID later")
    p.add_argument("--expire", type=int, metavar="TASK_ID", help="only step 7: expireDispute for this task")
    args = p.parse_args()
    port = args.port or (8001 if args.rehearse else 8000)
    amount = Web3.to_wei(args.amount, "ether")

    if args.expire is not None:
        net = testnet_net(port, ("worker",))
        net.expire(args.expire)
        return summary(net)

    for busy, what in ((port, "a verifier"),) + (((8545, "anvil"),) if args.rehearse else ()):
        if not e2e.port_free(busy):
            print(f"Port {busy} is in use by {what}. Stop it first (section 4: one live verifier).")
            return 2
    tmp = pathlib.Path(tempfile.mkdtemp(prefix="proofpay-trust-testnet-"))
    extra: list = []
    net = None
    try:
        if args.rehearse:
            subprocess.run(["forge", "build"], cwd=e2e.FOUNDRY, check=True, capture_output=True)
            net, extra = rehearsal_net(tmp, port)
        else:
            net = testnet_net(port, ("deployer", "worker"))
            need_poster, need_worker = 4 * amount + Web3.to_wei(0.2, "ether"), Web3.to_wei(0.1, "ether")
            print(f"Balances: poster {Web3.from_wei(net.balance('poster'), 'ether')} MON, "
                  f"worker {Web3.from_wei(net.balance('worker'), 'ether')} MON")
            if net.balance("poster") < need_poster or net.balance("worker") < need_worker:
                print("Not enough test MON. Get more at https://faucet.monad.xyz and run again.")
                return 2
        print(f"Chain {net.w3.eth.chain_id}, contract {net.contract.address}, verifier port {port}, "
              f"log {net.log}")
        print("1-2. Recovery after `evaluated` (task R1); R1: dispute, then resolveDispute(true) by the arbiter")
        r1, r1_photo = net.recovery("R1", "evaluated", amount, on_approved=net.dispute_resolve)
        print("3. Recovery after `signed` (task R2), then R2: dispute (right after the approval)")
        r2, _ = net.recovery("R2", "signed", amount, on_approved=net.dispute_only)
        net.expire_later = r2
        print("4. Recovery after `broadcast` (task R3), then R3: release after the window")
        r3, _ = net.recovery("R3", "broadcast", amount)
        net.release(r3)
        print("5. Demo step 8: R1's accepted photo as proof for task R4")
        r4 = net.reuse(r1_photo, amount)
        print("6. Demo step 9: refund of R4 after its submitBy")
        net.refund_expired(r4)
        net.stop()
        if args.no_expire_wait and not args.rehearse:
            print(f"\nStep 7 skipped. Later, run:  .venv/bin/python scripts/trust_testnet.py --expire {r2}")
        else:
            if not args.rehearse:
                print("\nThe script's verifier is stopped. Start your normal verifier now in another terminal:\n"
                      "    cd verifier && .venv/bin/python -m app.main")
            print(f"7. R2 (task #{r2}): expireDispute after the arbitration timeout")
            net.expire(r2)
    except KeyboardInterrupt:
        print("\nStopped by the user.")
        if net:
            net.results.append(("run completed", False))
            if getattr(net, "expire_later", None) and not args.rehearse:
                print(f"To end the dispute later: .venv/bin/python scripts/trust_testnet.py --expire {net.expire_later}")
    except Exception as exc:
        print(f"ERROR: {type(exc).__name__}: {exc}")
        if net:
            print(f"Verifier log: {net.log}")
            net.results.append(("run completed", False))
    finally:
        if net:
            net.stop()
            shutil.rmtree(net.tmp, ignore_errors=True)
        for x in extra:
            if hasattr(x, "terminate"):
                x.terminate()
                x.wait(timeout=15)
            else:
                x.shutdown()
        if args.rehearse:
            shutil.rmtree(tmp, ignore_errors=True)
    return summary(net) if net else 1


if __name__ == "__main__":
    sys.exit(main())
