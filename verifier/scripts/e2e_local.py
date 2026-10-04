"""Local end-to-end test on anvil (M4 task 3). No real model, no testnet, no secrets.

Usage (from the verifier/ folder):
    .venv/bin/python scripts/e2e_local.py

What it does:
  1. Starts anvil on http://127.0.0.1:8545 (chain 31337, one block each second).
  2. Builds and deploys ProofPayEscrow with the demo settings (60 / 300 / 3600).
  3. Starts a fake OpenAI-compatible model server on 127.0.0.1 (it always answers "task complete").
  4. Starts the verifier in live mode on port 8000 with a temporary data folder.
  5. Good pair: upload two fixture photos, create a task, accept, submit, wait for the verdict -> pass.
  6. Reuse: a second task gets the SAME after photo as proof -> V-C2 fail, the MON stays locked.
It prints PASS or FAIL for each case and stops every process it started.

Safety (section 4 exception): it uses only the public default anvil accounts, connects only to
http://127.0.0.1:8545 and stops IF the chain ID is not 31337.
"""

from __future__ import annotations

import http.server
import json
import pathlib
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time

import httpx
from eth_account import Account
from web3 import Web3

VERIFIER_DIR = pathlib.Path(__file__).resolve().parent.parent
REPO = VERIFIER_DIR.parent
FOUNDRY = REPO / "packages" / "foundry"
ARTIFACT = FOUNDRY / "out" / "ProofPayEscrow.sol" / "ProofPayEscrow.json"
FIXTURES = VERIFIER_DIR / "tests" / "fixtures"

RPC = "http://127.0.0.1:8545"
LOCAL_CHAIN_ID = 31337
VERIFIER_PORT = 8000
VERIFIER_URL = f"http://127.0.0.1:{VERIFIER_PORT}"

# Public default anvil test accounts (well known; never used on a real network).
ANVIL_KEYS = {
    "deployer": "0xac0974bec39a17e36ba4a6b4d238ff944bacb478cbed5efcae784d7bf4f2ff80",
    "verifier": "0x59c6995e998f97a5a0044966f0945389dc9e86dae88c7a8412f4603b6b78690d",
    "poster": "0x5de4111afa1a4b94908f83103eb1f1706367c2e68ca870fc3fb9a804cdab365a",
    "worker": "0x7c852118294e51e653712a81e05800f419141751be58f605c371e15141b007a6",
    "worker2": "0x47e179ec197488593b187f80a00eb0da91f1b9d0b13f8733639f19c30a34926a",
}
FAKE_REPLY = {"task_completed": True, "same_location": True, "confidence": 88, "reason": "Fake model: the work looks done."}
VERDICT_TIMEOUT_S = 120


def port_free(port: int) -> bool:
    with socket.socket() as s:
        return s.connect_ex(("127.0.0.1", port)) != 0


def wait_for(fn, timeout: float, what: str):
    end = time.monotonic() + timeout
    last = None
    while time.monotonic() < end:
        try:
            value = fn()
            if value:
                return value
        except Exception as exc:  # not up yet
            last = exc
        time.sleep(0.5)
    raise RuntimeError(f"timed out waiting for {what} ({last!r})")


# ---------------------------------------------------------------- fake model server


class FakeModelHandler(http.server.BaseHTTPRequestHandler):
    calls: list[int] = []  # number of images in each request

    def do_POST(self):  # noqa: N802
        body = json.loads(self.rfile.read(int(self.headers.get("content-length", 0))))
        images = sum(
            1
            for m in body.get("messages", [])
            if isinstance(m.get("content"), list)
            for part in m["content"]
            if part.get("type") == "image_url"
        )
        FakeModelHandler.calls.append(images)
        reply = {
            "id": "fake",
            "object": "chat.completion",
            "created": int(time.time()),
            "model": body.get("model", "fake"),
            "choices": [
                {"index": 0, "message": {"role": "assistant", "content": json.dumps(FAKE_REPLY)}, "finish_reason": "stop"}
            ],
            "usage": {"prompt_tokens": 1, "completion_tokens": 30, "total_tokens": 31},
        }
        data = json.dumps(reply).encode()
        self.send_response(200)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *args):
        pass


# ---------------------------------------------------------------- chain helpers


class Local:
    def __init__(self):
        self.w3 = Web3(Web3.HTTPProvider(RPC, request_kwargs={"timeout": 10}))
        cid = self.w3.eth.chain_id
        if cid != LOCAL_CHAIN_ID:  # section 4: local scripts stop on any other chain
            raise SystemExit(f"Chain ID is {cid}, not {LOCAL_CHAIN_ID}. Stop.")
        self.acct = {name: Account.from_key(k) for name, k in ANVIL_KEYS.items()}
        self.contract = None

    def send(self, who: str, tx: dict) -> dict:
        a = self.acct[who]
        tx = {**tx, "from": a.address, "nonce": self.w3.eth.get_transaction_count(a.address), "chainId": LOCAL_CHAIN_ID}
        tx.setdefault("gas", int(self.w3.eth.estimate_gas(tx) * 1.2))
        tx.setdefault("gasPrice", self.w3.eth.gas_price * 2)
        signed = a.sign_transaction(tx)
        h = self.w3.eth.send_raw_transaction(signed.raw_transaction)
        r = self.w3.eth.wait_for_transaction_receipt(h, timeout=30)
        if r["status"] != 1:
            raise RuntimeError(f"transaction from {who} reverted: {h.hex()}")
        return r

    def call_fn(self, who: str, fn, value: int = 0) -> dict:
        tx = fn.build_transaction({"from": self.acct[who].address, "value": value, "gas": 1_000_000, "nonce": 0})
        return self.send(who, {k: tx[k] for k in ("to", "data", "value", "gas")})

    def deploy(self) -> tuple[str, int]:
        art = json.loads(ARTIFACT.read_text())
        factory = self.w3.eth.contract(abi=art["abi"], bytecode=art["bytecode"]["object"])
        tx = factory.constructor(self.acct["verifier"].address, 60, 300, 3600).build_transaction(
            {"from": self.acct["deployer"].address, "gas": 6_000_000, "nonce": 0}
        )
        r = self.send("deployer", {"data": tx["data"], "gas": tx["gas"], "value": 0})
        self.contract = self.w3.eth.contract(address=r["contractAddress"], abi=art["abi"])
        return r["contractAddress"], int(r["blockNumber"])

    def task(self, task_id: int) -> tuple:
        return self.contract.functions.getTask(task_id).call()


# ---------------------------------------------------------------- verifier helpers


def upload(name: str) -> str:
    with open(FIXTURES / name, "rb") as f:
        r = httpx.post(f"{VERIFIER_URL}/upload", files={"file": (name, f, "image/jpeg")}, timeout=30)
    r.raise_for_status()
    return r.json()["sha256"]


def wait_report(task_id: int, attempt: int) -> dict:
    def done():
        httpx.post(f"{VERIFIER_URL}/verify", json={"taskId": task_id}, timeout=10)
        r = httpx.get(f"{VERIFIER_URL}/tasks/{task_id}/attempts/{attempt}", timeout=10)
        if r.status_code == 200 and r.json()["jobState"] in (
            "confirmed", "reverted", "expired", "model_failed", "not_admitted", "superseded",
            "blocked_config", "blocked_nonce",
        ):
            return r.json()
        return None

    return wait_for(done, VERDICT_TIMEOUT_S, f"the verdict of task {task_id}")


def run_case(chain: Local, task_id: int, before: str, after: str, worker: str) -> dict:
    bh, ah = upload(before), upload(after)
    submit_by = chain.w3.eth.get_block("latest")["timestamp"] + 600
    c = chain.contract.functions
    chain.call_fn("poster", c.createTask(f"E2E task {task_id}", "Remove the litter around this bench",
                                         bytes.fromhex(bh[2:]), submit_by), value=Web3.to_wei(0.05, "ether"))
    chain.call_fn(worker, c.acceptTask(task_id))
    chain.call_fn(worker, c.submitProof(task_id, bytes.fromhex(ah[2:])))
    return wait_report(task_id, 1)


def show(report: dict) -> None:
    ev = report.get("evaluation") or {}
    print(f"    job {report['jobState']}, attemptResult {report['attemptResult']}, "
          f"pass {ev.get('pass')}, score {ev.get('score')}, reason {ev.get('reason')!r}")
    for c in ev.get("checks", []):
        print(f"      {c['id']:5} {c['name']:15} {c['result']:8} {c['detail']}")
    print(f"    tx {report['settlement']['txHashes']}")


def main() -> int:
    for port, what in ((8545, "anvil"), (VERIFIER_PORT, "the verifier")):
        if not port_free(port):
            print(f"Port {port} is in use. Stop the running {what} first (section 3.1: one of each).")
            return 2
    procs: list[subprocess.Popen] = []
    tmp = pathlib.Path(tempfile.mkdtemp(prefix="proofpay-e2e-"))
    server = None
    results: list[tuple[str, bool]] = []
    try:
        print("1. Build the contract (forge build)")
        subprocess.run(["forge", "build"], cwd=FOUNDRY, check=True, capture_output=True)

        print("2. Start anvil (chain 31337)")
        anvil_log = open(tmp / "anvil.log", "w")
        procs.append(subprocess.Popen(
            ["anvil", "--port", "8545", "--block-time", "1", "--slots-in-an-epoch", "1"],
            stdout=anvil_log, stderr=subprocess.STDOUT,
        ))
        wait_for(lambda: Web3(Web3.HTTPProvider(RPC)).is_connected(), 30, "anvil")
        chain = Local()

        print("3. Deploy ProofPayEscrow (60 / 300 / 3600)")
        address, block = chain.deploy()
        print(f"   {address} at block {block}")

        print("4. Start the fake model server")
        server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), FakeModelHandler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        model_url = f"http://127.0.0.1:{server.server_address[1]}/v1"

        print("5. Start the verifier (live, temporary data folder)")
        env = {
            "PATH": "/usr/bin:/bin",
            "CHAIN_ID": str(LOCAL_CHAIN_ID),
            "MONAD_RPC_URL": RPC,
            "ESCROW_ADDRESS": address,
            "START_BLOCK": str(block),
            "VERIFIER_PRIVATE_KEY": ANVIL_KEYS["verifier"],
            "VISION_API_KEY": "fake",
            "VISION_BASE_URL": model_url,
            "VISION_MODEL": "fake-model",
            "VISION_REASONING_EFFORT": "",
            "CORS_ORIGINS": "http://localhost:3000",
            "DATA_ROOT": str(tmp / "data"),
            "MIN_CONFIDENCE": "70",
            "NEAR_HARD": "4",
            "NEAR_WARN": "10",
            "VERIFIER_MODE": "live",
            "ADMISSION_MODE": "open",
            "ALLOWLIST": "",
            "MAX_JOBS_PER_POSTER": "10",
            "MAX_JOBS_PER_WORKER": "10",
            "MAX_JOBS_PER_DAY": "150",
            "MAX_MODEL_CALLS_PER_DAY": "300",
            "MAX_VERDICT_TX_PER_DAY": "150",
            "MAX_QUEUE": "50",
            "MIN_SIGNER_BALANCE": "0.05",
            "MAX_STORAGE_MB": "2000",
            "ADMISSION_UNTIL": "2099-01-01T00:00:00Z",
            "SAFE_HEAD_METHOD": "finalized",
        }
        vlog = open(tmp / "verifier.log", "w")
        procs.append(subprocess.Popen(
            [sys.executable, "-m", "app.main", "--port", str(VERIFIER_PORT)],
            cwd=VERIFIER_DIR, env=env, stdout=vlog, stderr=subprocess.STDOUT,
        ))
        wait_for(lambda: httpx.get(f"{VERIFIER_URL}/health", timeout=5).json()["ready"], 90, "verifier ready")
        print("   /health ready: true")

        print("6. Good pair: task 1 (a_before -> a_after)")
        r1 = run_case(chain, 1, "a_before.jpg", "a_after.jpg", "worker")
        show(r1)
        ok1 = r1["jobState"] == "confirmed" and r1["evaluation"]["pass"] is True and r1["attemptResult"] == "approved"
        ok1 = ok1 and chain.task(1)[11] == 3  # Status.Approved
        results.append(("good pair: pass verdict on chain", ok1))

        print("7. Reuse: task 2 (b_before -> the after photo of task 1)")
        balance_before = chain.w3.eth.get_balance(chain.contract.address)
        r2 = run_case(chain, 2, "b_before.jpg", "a_after.jpg", "worker2")
        show(r2)
        checks = {c["id"]: c["result"] for c in r2["evaluation"]["checks"]}
        ok2 = (
            r2["jobState"] == "confirmed"
            and r2["evaluation"]["pass"] is False
            and checks.get("V-C2") == "fail"
            and r2["evaluation"]["reason"] == "Same photo as the accepted proof of task #1"
            and chain.task(2)[11] == 1  # back to Accepted: no payout
            and chain.w3.eth.get_balance(chain.contract.address) >= balance_before
        )
        results.append(("reuse: V-C2 fail verdict on chain, MON stays in the contract", ok2))
        results.append(("fake model got 2 images per call, 1 call in total", FakeModelHandler.calls == [2]))
    except Exception as exc:
        print(f"ERROR: {type(exc).__name__}: {exc}")
        log = tmp / "verifier.log"
        if log.exists():
            print("---- last verifier log lines ----")
            print("\n".join(log.read_text().splitlines()[-30:]))
        results.append(("run completed", False))
    finally:
        for p in reversed(procs):
            p.terminate()
            try:
                p.wait(timeout=15)
            except subprocess.TimeoutExpired:
                p.kill()
        if server is not None:
            server.shutdown()
        shutil.rmtree(tmp, ignore_errors=True)

    print("\nResults:")
    for name, ok in results:
        print(f"  {'PASS' if ok else 'FAIL'}  {name}")
    return 0 if results and all(ok for _, ok in results) else 1


if __name__ == "__main__":
    sys.exit(main())
