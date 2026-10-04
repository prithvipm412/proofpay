"""Contract reads, event decoding, signing and broadcast (V-R1..V-R4, V-E, V-J3).

All functions here block on RPC calls. Callers run them in background threads, never on the
API event loop (V-02).

Signing and broadcast work only on a Chain made with `can_sign=True`, which main.py does only in
live mode. A readonly verifier cannot sign or broadcast (V-06).

Monad facts (docs reference/json-rpc/overview, developer-essentials/gas-pricing, 2026-10-04):
- "latest" is the Proposed state (speculative); "pending" "behaves the same as 'latest'".
- Receipts "can match a transaction in a non-finalized block", so a receipt counts only when its
  block is finalized and its block hash matches (`receipt`).
- Gas is charged on the gas LIMIT, so the limit is the estimate plus a small margin.
- EIP-1559: price = min(base + priority, max). We send type 2 transactions.
"""

from __future__ import annotations

import json
import pathlib
from dataclasses import dataclass
from typing import Any

from web3 import Web3
from web3._utils.events import get_event_data

ABI_PATH = pathlib.Path(__file__).resolve().parent / "abi" / "ProofPayEscrow.json"
READ_EVENTS = ("TaskCreated", "TaskAccepted", "ProofSubmitted", "VerdictRecorded")  # V-E3, V-A1 parties
RPC_TIMEOUT_S = 10
GAS_MARGIN_PCT = 125  # gas limit = estimate x 1.25 (Monad charges the limit; a revert by out-of-gas is worse)
DEFAULT_PRIORITY_WEI = 2 * 10**9


class ReadonlyError(Exception):
    """A readonly verifier tried to sign or broadcast (V-06)."""

STATUS_NAMES = ("Open", "Accepted", "Submitted", "Approved", "Disputed", "Paid", "Refunded")


def load_abi() -> list[dict]:
    return json.loads(ABI_PATH.read_text())


def hx(value: bytes) -> str:
    """bytes -> "0x" + lowercase hex."""
    return "0x" + bytes(value).hex()


def event_id(block_number: int, tx_index: int, log_index: int) -> str:
    """V-S5: zero-padded so that text order equals chain order."""
    return f"{block_number:012d}:{tx_index:06d}:{log_index:06d}"


@dataclass(frozen=True)
class Block:
    number: int
    hash: str
    timestamp: int


@dataclass(frozen=True)
class ChainEvent:
    name: str
    block_number: int
    tx_index: int
    log_index: int
    tx_hash: str
    args: dict[str, Any]

    @property
    def event_id(self) -> str:
        return event_id(self.block_number, self.tx_index, self.log_index)


@dataclass(frozen=True)
class Receipt:
    tx_hash: str
    status: int
    block_number: int
    finalized: bool
    events: list[ChainEvent]


def _norm_arg(value: Any) -> Any:
    if isinstance(value, (bytes, bytearray)):
        return hx(value)
    return value


class Chain:
    """Reads from the RPC node for one deployment."""

    def __init__(self, rpc_url: str, escrow_address: str, can_sign: bool = False):
        self.can_sign = can_sign
        self.w3 = Web3(Web3.HTTPProvider(rpc_url, request_kwargs={"timeout": RPC_TIMEOUT_S}))
        self.abi = load_abi()
        self.address = Web3.to_checksum_address(escrow_address)
        self.contract = self.w3.eth.contract(address=self.address, abi=self.abi)
        self._event_abis = {
            item["name"]: item for item in self.abi if item.get("type") == "event" and item["name"] in READ_EVENTS
        }
        self._topics = {}
        for name, item in self._event_abis.items():
            sig = f"{name}({','.join(i['type'] for i in item['inputs'])})"
            self._topics[hx(Web3.keccak(text=sig))] = name

    # ------------------------------------------------------------ blocks

    def chain_id(self) -> int:
        return int(self.w3.eth.chain_id)

    def block(self, ident: int | str) -> Block:
        b = self.w3.eth.get_block(ident)
        return Block(int(b["number"]), hx(b["hash"]), int(b["timestamp"]))

    def safe_head(self, method: str, offset: int) -> Block:
        """V-E1: the newest block that the event reader trusts."""
        if method == "finalized":
            return self.block("finalized")
        latest = int(self.w3.eth.block_number)
        return self.block(max(0, latest - offset))

    def latest_block_number(self) -> int:
        return int(self.w3.eth.block_number)

    # ------------------------------------------------------------ events

    def events(self, from_block: int, to_block: int) -> list[ChainEvent]:
        """TaskCreated, ProofSubmitted and VerdictRecorded in [from_block, to_block], in chain order."""
        logs = self.w3.eth.get_logs(
            {
                "address": self.address,
                "fromBlock": from_block,
                "toBlock": to_block,
                "topics": [list(self._topics)],
            }
        )
        out = [ev for ev in (self._decode(log) for log in logs if not log.get("removed")) if ev is not None]
        out.sort(key=lambda e: e.event_id)
        return out

    def _decode(self, log) -> ChainEvent | None:
        if Web3.to_checksum_address(log["address"]) != self.address or not log["topics"]:
            return None
        name = self._topics.get(hx(log["topics"][0]))
        if name is None:
            return None
        data = get_event_data(self.w3.codec, self._event_abis[name], log)
        return ChainEvent(
            name=name,
            block_number=int(log["blockNumber"]),
            tx_index=int(log["transactionIndex"]),
            log_index=int(log["logIndex"]),
            tx_hash=hx(log["transactionHash"]),
            args={k: _norm_arg(v) for k, v in data["args"].items()},
        )

    # ------------------------------------------------------------ transactions (V-J3)

    def latest_time(self) -> int:
        """Chain time: the timestamp of the latest block (V-J2 `remaining`)."""
        return self.block("latest").timestamp

    def nonce(self, address: str, tag: str = "latest") -> int:
        return int(self.w3.eth.get_transaction_count(Web3.to_checksum_address(address), tag))

    def receipt(self, tx_hash: str) -> Receipt | None:
        """The receipt of a transaction, or None IF the node knows no receipt.

        A receipt from a non-finalized block can change on Monad. `finalized` is True only when the
        block is finalized and the finalized chain has the same block hash at that number.
        Callers act on a receipt only when `finalized` is True; otherwise they wait.
        """
        from web3.exceptions import TransactionNotFound

        try:
            r = self.w3.eth.get_transaction_receipt(tx_hash)
        except TransactionNotFound:
            return None
        if r is None:
            return None
        number = int(r["blockNumber"])
        final = number <= self.block("finalized").number and self.block(number).hash == hx(r["blockHash"])
        events = [ev for ev in (self._decode(log) for log in r["logs"]) if ev is not None]
        return Receipt(hx(r["transactionHash"]), int(r["status"]), number, final, events)

    def send_raw(self, raw_tx: str) -> str:
        """Broadcast a signed transaction. "already known" is not an error (V-J3 step 4)."""
        if not self.can_sign:
            raise ReadonlyError("readonly mode never broadcasts (V-06)")
        try:
            return hx(self.w3.eth.send_raw_transaction(raw_tx))
        except Exception as exc:
            if "already known" in str(exc).lower():
                return hx(Web3.keccak(hexstr=raw_tx))
            raise

    def build_verdict(
        self, key: str, nonce: int, task_id: int, attempt: int, proof_hash: str, passed: bool, score: int, reason: str
    ) -> tuple[str, str]:
        """Sign recordVerdict. Returns (raw transaction hex, transaction hash). Nothing is sent."""
        if not self.can_sign:
            raise ReadonlyError("readonly mode never signs (V-06)")
        from eth_account import Account

        account = Account.from_key(key)
        fn = self.contract.functions.recordVerdict(task_id, attempt, bytes.fromhex(proof_hash[2:]), passed, score, reason)
        estimate = int(fn.estimate_gas({"from": account.address}))
        base = int(self.w3.eth.get_block("latest")["baseFeePerGas"])
        try:
            priority = int(self.w3.eth.max_priority_fee)
        except Exception:
            priority = DEFAULT_PRIORITY_WEI
        tx = fn.build_transaction(
            {
                "from": account.address,
                "nonce": nonce,
                "gas": estimate * GAS_MARGIN_PCT // 100,
                "maxPriorityFeePerGas": priority,
                "maxFeePerGas": 2 * base + priority,
                "chainId": self.chain_id(),
                "type": 2,
            }
        )
        signed = account.sign_transaction(tx)
        return hx(signed.raw_transaction), hx(signed.hash)

    # ------------------------------------------------------------ contract reads

    def code_exists(self) -> bool:
        return len(self.w3.eth.get_code(self.address)) > 0

    def verifier(self) -> str:
        return Web3.to_checksum_address(self.contract.functions.verifier().call())

    def task_count(self) -> int:
        return int(self.contract.functions.taskCount().call())

    def dispute_window(self) -> int:
        return int(self.contract.functions.disputeWindow().call())

    def review_grace(self) -> int:
        return int(self.contract.functions.reviewGrace().call())

    def balance(self, address: str) -> int:
        return int(self.w3.eth.get_balance(Web3.to_checksum_address(address)))

    def get_task(self, task_id: int) -> dict[str, Any]:
        t = self.contract.functions.getTask(task_id).call()
        keys = (
            "poster",
            "worker",
            "amount",
            "beforeHash",
            "proofHash",
            "createdAt",
            "submitBy",
            "reviewBy",
            "disputeUntil",
            "attempts",
            "score",
            "status",
            "title",
            "description",
        )
        task = {k: _norm_arg(v) for k, v in zip(keys, t)}
        task["status"] = STATUS_NAMES[int(task["status"])]
        return task
