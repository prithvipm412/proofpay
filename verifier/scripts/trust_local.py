"""Local test of the M7 trust flows and the recovery test on anvil. No real model, no testnet, no secrets.

Usage (from the verifier/ folder):
    .venv/bin/python scripts/trust_local.py --port 8001

What it does (each case prints PASS or FAIL):
  1. Release after the dispute window (C-20): the worker gets the original amount.
  2. Dispute, then resolveDispute(true) and resolveDispute(false) by the arbiter (C-17, C-18).
  3. Dispute, then expireDispute by another user after the arbitration timeout (C-19).
     resolveDispute is refused at that time (ArbitrationClosed).
  4. Refund of an Accepted task after submitBy, and Cancel and refund of an Open task (C-21).
  5. Recovery (M7 task 3): the verifier is stopped hard right after `evaluated`, after `signed`
     and after `broadcast` (CRASH_AFTER_STATE). After a normal restart each job ends `confirmed`
     with one transaction, and the verifier nonce goes up by exactly one.
  6. Restart + reuse (M8 task 4, local copy): after the restarts, the accepted photo of a
     recovery task is submitted for a new task -> V-C2 fail, the MON stays in the contract.
Time skips use anvil `evm_increaseTime` (local chain only).

Safety (section 4 exception): public default anvil accounts only, http://127.0.0.1:8545 only,
stops IF the chain ID is not 31337.
"""

from __future__ import annotations

import argparse
import http.server
import pathlib
import random
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import threading

import httpx
from PIL import Image, ImageDraw
from web3 import Web3

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import e2e_local as e2e  # noqa: E402

OPEN, ACCEPTED, SUBMITTED, APPROVED, DISPUTED, PAID, REFUNDED = range(7)
AMOUNT = Web3.to_wei(0.01, "ether")
CRASH_EXIT_CODE = 70  # app.jobs.CRASH_EXIT_CODE


class Run:
    def __init__(self, tmp: pathlib.Path, chain: e2e.Local, address: str, block: int, model_url: str):
        self.tmp, self.chain, self.address, self.block, self.model_url = tmp, chain, address, block, model_url
        self.proc: subprocess.Popen | None = None
        self.results: list[tuple[str, bool]] = []
        self.photos = 0

    # ------------------------------------------------------------ verifier process

    def start(self, crash: str = "") -> None:
        env = e2e.verifier_env(self.address, self.block, self.model_url, self.tmp / "data", CRASH_AFTER_STATE=crash)
        self.proc = e2e.start_verifier(env, self.tmp / "verifier.log")
        e2e.wait_ready()

    def stop(self) -> None:
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()
            self.proc.wait(timeout=20)
        self.proc = None

    def db_job(self, task_id: int) -> sqlite3.Row:
        db = self.tmp / "data" / f"31337-{self.address.lower()}" / "proofpay.db"
        conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        try:
            return conn.execute("SELECT * FROM jobs WHERE task_id = ? ORDER BY attempt DESC", (task_id,)).fetchone()
        finally:
            conn.close()

    # ------------------------------------------------------------ chain helpers

    def photo(self) -> pathlib.Path:
        """A new, unique photo (random shapes), so that no two tasks share a pHash."""
        self.photos += 1
        rnd = random.Random(self.photos * 7919)
        img = Image.new("RGB", (640, 480), tuple(rnd.randrange(256) for _ in range(3)))
        d = ImageDraw.Draw(img)
        for _ in range(12):
            x, y = rnd.randrange(600), rnd.randrange(440)
            d.rectangle((x, y, x + rnd.randrange(40, 300), y + rnd.randrange(40, 250)),
                        fill=tuple(rnd.randrange(256) for _ in range(3)))
        path = self.tmp / f"photo-{self.photos}.jpg"
        img.save(path, "JPEG", quality=90)
        return path

    def now(self) -> int:
        return int(self.chain.w3.eth.get_block("latest")["timestamp"])

    def skip(self, seconds: int) -> None:
        self.chain.w3.provider.make_request("evm_increaseTime", [seconds])
        self.chain.w3.provider.make_request("evm_mine", [])

    def status(self, task_id: int) -> int:
        return self.chain.task(task_id)[11]

    def balance(self, who: str) -> int:
        return self.chain.w3.eth.get_balance(self.chain.acct[who].address)

    def fn(self):
        return self.chain.contract.functions

    def submitted_task(self, worker: str = "worker", after: str | None = None, submit_delay: int = 600) -> tuple[int, str]:
        """create -> accept -> submit. Returns (task id, proof hash)."""
        bh = e2e.upload(self.photo())
        ah = after or e2e.upload(self.photo())
        c = self.fn()
        self.chain.call_fn("poster", c.createTask("Trust test", "Remove the litter around this bench",
                                                  bytes.fromhex(bh[2:]), self.now() + submit_delay), value=AMOUNT)
        task_id = c.taskCount().call()
        self.chain.call_fn(worker, c.acceptTask(task_id))
        self.chain.call_fn(worker, c.submitProof(task_id, bytes.fromhex(ah[2:])))
        return task_id, ah

    def approved_task(self) -> int:
        task_id, _ = self.submitted_task()
        report = e2e.wait_report(task_id, 1)
        if report["attemptResult"] != "approved":
            raise RuntimeError(f"task {task_id} was not approved: {report['evaluation']}")
        return task_id

    def reverts(self, call, who: str, error: str) -> bool:
        """True IF the call reverts with the custom error `error` (matched by its 4-byte selector)."""
        try:
            call.call({"from": self.chain.acct[who].address})
        except Exception as exc:
            return self._selector(error) in repr(exc).lower()
        return False

    def _selector(self, error: str) -> str:
        entry = next(e for e in self.chain.contract.abi if e.get("type") == "error" and e["name"] == error)
        sig = f"{error}({','.join(i['type'] for i in entry['inputs'])})"
        return Web3.keccak(text=sig).hex().removeprefix("0x")[:8]

    def check(self, name: str, ok: bool) -> None:
        print(f"   {'PASS' if ok else 'FAIL'}  {name}")
        self.results.append((name, ok))

    # ------------------------------------------------------------ cases

    def case_release(self) -> None:
        print("1. Release after the dispute window")
        t = self.approved_task()
        early = self.reverts(self.fn().release(t), "worker2", "DisputeWindowOpen")
        self.skip(61)
        before = self.balance("worker")
        self.chain.call_fn("worker2", self.fn().release(t))  # any user can release (C-20)
        gained = self.balance("worker") - before
        self.check("release: refused in the window, then any user releases; worker gets the original amount",
                   early and self.status(t) == PAID and gained == AMOUNT and self.chain.task(t)[2] == AMOUNT)

    def case_dispute_resolve(self) -> None:
        print("2. Dispute -> resolveDispute(true) and resolveDispute(false)")
        for pay_worker in (True, False):
            t = self.approved_task()
            self.chain.call_fn("poster", self.fn().dispute(t))
            who = "worker" if pay_worker else "poster"
            before = self.balance(who)
            not_owner = self.reverts(self.fn().resolveDispute(t, pay_worker), "worker2", "OwnableUnauthorizedAccount")
            self.chain.call_fn("deployer", self.fn().resolveDispute(t, pay_worker))  # deployer = owner = arbiter
            gained = self.balance(who) - before
            want = PAID if pay_worker else REFUNDED
            self.check(f"dispute -> resolveDispute({str(pay_worker).lower()}): status "
                       f"{'Paid' if pay_worker else 'Refunded'}, {who} gets the amount, non-owner refused",
                       self.status(t) == want and gained == AMOUNT and not_owner)

    def case_dispute_expire(self) -> None:
        print("3. Dispute -> expireDispute after the arbitration timeout")
        t = self.approved_task()
        self.chain.call_fn("poster", self.fn().dispute(t))
        early = self.reverts(self.fn().expireDispute(t), "worker2", "ArbitrationNotExpired")
        self.skip(60 + 3600 + 1)
        closed = self.reverts(self.fn().resolveDispute(t, True), "deployer", "ArbitrationClosed")
        before = self.balance("poster")
        self.chain.call_fn("worker2", self.fn().expireDispute(t))  # any user (C-19)
        gained = self.balance("poster") - before
        self.check("dispute -> expireDispute: refused before the timeout; after it the arbiter is refused and "
                   "any user ends it; poster refunded", early and closed and self.status(t) == REFUNDED
                   and gained == AMOUNT)

    def case_refund(self) -> None:
        print("4. Refund after submitBy, and Cancel and refund of an Open task")
        e2e.wait_ready()  # the time skip above can make readiness lag for a few seconds
        c = self.fn()
        bh = e2e.upload(self.photo())
        self.chain.call_fn("poster", c.createTask("Expired task", "Refund test", bytes.fromhex(bh[2:]),
                                                  self.now() + 120), value=AMOUNT)
        t = c.taskCount().call()
        self.chain.call_fn("worker", c.acceptTask(t))
        early = self.reverts(c.refund(t), "poster", "RefundNotAvailable")
        self.skip(121)
        self.chain.call_fn("poster", c.refund(t))
        ok_expired = early and self.status(t) == REFUNDED
        self.chain.call_fn("poster", c.createTask("Open task", "Cancel test", bytes.fromhex(bh[2:]),
                                                  self.now() + 600), value=AMOUNT)
        t2 = c.taskCount().call()
        not_poster = self.reverts(c.refund(t2), "worker", "NotPoster")
        self.chain.call_fn("poster", c.refund(t2))
        self.check("refund: Accepted task refused before submitBy and refunded after it; Open task cancelled "
                   "by the poster only", ok_expired and not_poster and self.status(t2) == REFUNDED)

    def case_recovery(self) -> list[str]:
        print("5. Recovery: planned crash after evaluated, signed, broadcast; then a normal restart")
        signer = self.chain.acct["verifier"].address
        accepted_photos = []
        for state in ("evaluated", "signed", "broadcast"):
            e2e.wait_ready()
            self.stop()
            self.start(crash=state)
            nonce0 = self.chain.w3.eth.get_transaction_count(signer)
            t, ah = self.submitted_task()
            httpx.post(f"{e2e.VERIFIER_URL}/verify", json={"taskId": t}, timeout=10)
            code = self.proc.wait(timeout=120)
            job = self.db_job(t)
            saved = job["state"] if job else None
            self.proc = None
            self.start()
            report = e2e.wait_report(t, 1)
            nonce1 = self.chain.w3.eth.get_transaction_count(signer)
            ok = (
                code == CRASH_EXIT_CODE
                and saved == state
                and report["jobState"] == "confirmed"
                and report["attemptResult"] == "approved"
                and len(report["settlement"]["txHashes"]) == 1
                and nonce1 - nonce0 == 1
                and self.status(t) == APPROVED
            )
            print(f"      task {t}: exit code {code}, saved state {saved}, after restart {report['jobState']}, "
                  f"tx {report['settlement']['txHashes']}, verifier nonce +{nonce1 - nonce0}")
            self.check(f"recovery after {state}: confirmed with one transaction", ok)
            accepted_photos.append(ah)
        return accepted_photos

    def case_reuse_after_restart(self, photo: str) -> None:
        print("6. Restart + reuse: the accepted photo of a recovery task, on a new task")
        held = self.chain.w3.eth.get_balance(self.chain.contract.address)
        t, _ = self.submitted_task(worker="worker2", after=photo)
        report = e2e.wait_report(t, 1)
        checks = {c["id"]: c["result"] for c in (report["evaluation"] or {}).get("checks", [])}
        self.check("reuse after restarts: V-C2 fail verdict on chain, MON stays in the contract",
                   report["attemptResult"] == "rejected" and checks.get("V-C2") == "fail"
                   and self.status(t) == ACCEPTED
                   and self.chain.w3.eth.get_balance(self.chain.contract.address) == held + AMOUNT)


def main() -> int:
    parser = argparse.ArgumentParser(description="ProofPay M7 trust flows and recovery test on anvil")
    parser.add_argument("--port", type=int, default=8001, help="local verifier port (default 8001)")
    e2e.set_port(parser.parse_args().port)
    for port, what in ((8545, "anvil"), (e2e.VERIFIER_PORT, "a verifier")):
        if not e2e.port_free(port):
            print(f"Port {port} is in use by {what}. Use another --port, or stop it first.")
            return 2
    tmp = pathlib.Path(tempfile.mkdtemp(prefix="proofpay-trust-"))
    anvil = server = run = None
    try:
        subprocess.run(["forge", "build"], cwd=e2e.FOUNDRY, check=True, capture_output=True)
        anvil = e2e.start_anvil(tmp)
        chain = e2e.Local()
        address, block = chain.deploy()
        server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), e2e.FakeModelHandler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        run = Run(tmp, chain, address, block, f"http://127.0.0.1:{server.server_address[1]}/v1")
        print(f"Anvil chain 31337, contract {address}, verifier port {e2e.VERIFIER_PORT}")
        run.start()
        run.case_release()
        run.case_dispute_resolve()
        run.case_dispute_expire()
        run.case_refund()
        photos = run.case_recovery()
        run.case_reuse_after_restart(photos[0])
    except Exception as exc:
        print(f"ERROR: {type(exc).__name__}: {exc}")
        log = tmp / "verifier.log"
        if log.exists():
            print("---- last verifier log lines ----")
            print("\n".join(log.read_text().splitlines()[-40:]))
        if run is not None:
            run.results.append(("run completed", False))
    finally:
        if run is not None:
            run.stop()
        if anvil is not None:
            anvil.terminate()
            anvil.wait(timeout=15)
        if server is not None:
            server.shutdown()
        shutil.rmtree(tmp, ignore_errors=True)

    results = run.results if run else [("setup", False)]
    print("\nResults:")
    for name, ok in results:
        print(f"  {'PASS' if ok else 'FAIL'}  {name}")
    return 0 if results and all(ok for _, ok in results) else 1


if __name__ == "__main__":
    sys.exit(main())
