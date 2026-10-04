"""Settings and their validation (V-03, V-CFG1..V-CFG7).

All settings come from verifier/.env and the process environment (the environment wins).
`load_settings` collects every problem and raises one ConfigError with all of them, so the
owner can repair the .env file in one pass. No secret value is ever put in an error message
or in the repr of Settings (V-04).
"""

from __future__ import annotations

import hashlib
import json
import os
import pathlib
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Mapping
from urllib.parse import urlparse

from eth_account import Account
from web3 import Web3

VERIFIER_DIR = pathlib.Path(__file__).resolve().parent.parent
ENV_FILE = VERIFIER_DIR / ".env"

ALLOWED_CHAIN_IDS = (10143, 31337)  # V-CFG1. 143 (Monad mainnet) is never allowed (section 4).
LOCAL_RPC_URLS = ("http://127.0.0.1:8545", "http://localhost:8545")  # V-CFG2
LOCAL_HOSTS = ("localhost", "127.0.0.1")

# V-E2 batch size. Owner decision 2026-10-04: 100, because the testnet RPC limits eth_getLogs to 100 blocks.
EVENT_BATCH_BLOCKS = 100

INT_SETTINGS = (
    "MAX_JOBS_PER_POSTER",
    "MAX_JOBS_PER_WORKER",
    "MAX_JOBS_PER_DAY",
    "MAX_MODEL_CALLS_PER_DAY",
    "MAX_VERDICT_TX_PER_DAY",
    "MAX_QUEUE",
    "MAX_STORAGE_MB",
)


class ConfigError(Exception):
    """A setting is missing or not valid (V-03)."""


@dataclass(frozen=True)
class Settings:
    chain_id: int
    rpc_url: str
    escrow_address: str  # checksum address
    start_block: int
    signer_key: str | None = field(repr=False)  # VERIFIER_PRIVATE_KEY; never logged (V-04)
    signer_address: str | None
    vision_api_key: str = field(repr=False)
    vision_base_url: str = ""
    vision_model: str = ""
    vision_reasoning_effort: str = ""
    cors_origins: tuple[str, ...] = ()
    data_root: pathlib.Path = pathlib.Path(".")
    min_confidence: int = 70
    near_hard: int = 4
    near_warn: int = 10
    mode: str = "readonly"
    admission_mode: str = "allowlist"
    allowlist: frozenset[str] = frozenset()  # lowercase addresses
    max_jobs_per_poster: int = 10
    max_jobs_per_worker: int = 10
    max_jobs_per_day: int = 150
    max_model_calls_per_day: int = 300
    max_verdict_tx_per_day: int = 150
    max_queue: int = 50
    min_signer_balance_wei: int = 5 * 10**16
    max_storage_mb: int = 2000
    admission_until: datetime = datetime(2026, 11, 15, tzinfo=timezone.utc)
    safe_head_method: str = "finalized"
    safe_head_offset: int = 0  # N for latest-minus-N

    @property
    def deployment_id(self) -> str:
        return f"{self.chain_id}:{self.escrow_address.lower()}"

    @property
    def data_dir(self) -> pathlib.Path:
        return self.data_root / f"{self.chain_id}-{self.escrow_address.lower()}"

    @property
    def live(self) -> bool:
        return self.mode == "live"

    @property
    def config_version(self) -> str:
        """V-CFG7: SHA-256 of the decision settings."""
        decision = {
            "MIN_CONFIDENCE": self.min_confidence,
            "NEAR_HARD": self.near_hard,
            "NEAR_WARN": self.near_warn,
            "VISION_MODEL": self.vision_model,
        }
        blob = json.dumps(decision, sort_keys=True, separators=(",", ":")).encode()
        return "0x" + hashlib.sha256(blob).hexdigest()


def read_env_file(path: pathlib.Path = ENV_FILE) -> dict[str, str]:
    """Read KEY=VALUE lines. Values are not printed anywhere."""
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def environment(path: pathlib.Path = ENV_FILE) -> dict[str, str]:
    """The .env values, with the process environment taking precedence."""
    env = read_env_file(path)
    env.update({k: v for k, v in os.environ.items()})
    return env


def _origin_ok(value: str) -> bool:
    p = urlparse(value)
    return (
        p.scheme in ("http", "https")
        and bool(p.hostname)
        and p.path in ("", "/")
        and not p.query
        and not p.fragment
        and not p.params
        and p.username is None
    )


def load_settings(env: Mapping[str, str]) -> Settings:
    problems: list[str] = []

    def get(name: str, required: bool = True) -> str:
        value = (env.get(name) or "").strip()
        if required and not value:
            problems.append(f"{name} is missing")
        return value

    def as_int(name: str, value: str) -> int | None:
        if value == "":
            return None
        if not re.fullmatch(r"-?\d+", value):
            problems.append(f"{name} must be an integer")
            return None
        return int(value)

    # V-CFG1
    chain_id = as_int("CHAIN_ID", get("CHAIN_ID"))
    if chain_id is not None and chain_id not in ALLOWED_CHAIN_IDS:
        problems.append(f"CHAIN_ID must be 10143 or 31337, not {chain_id}")

    # V-CFG2
    rpc_url = get("MONAD_RPC_URL").rstrip("/")
    if rpc_url and urlparse(rpc_url).scheme not in ("http", "https"):
        problems.append("MONAD_RPC_URL must be an http or https URL")
    if chain_id == 31337 and rpc_url and rpc_url not in LOCAL_RPC_URLS:
        problems.append("CHAIN_ID 31337 needs MONAD_RPC_URL http://127.0.0.1:8545 or http://localhost:8545")

    # V-CFG3 (START_BLOCK <= current block is checked at startup against the chain)
    escrow = get("ESCROW_ADDRESS")
    if escrow and not Web3.is_address(escrow):
        problems.append("ESCROW_ADDRESS is not a valid 20-byte address")
    escrow = Web3.to_checksum_address(escrow) if escrow and Web3.is_address(escrow) else escrow
    start_block = as_int("START_BLOCK", get("START_BLOCK"))
    if start_block is not None and start_block < 0:
        problems.append("START_BLOCK must be 0 or more")

    # V-06
    mode = get("VERIFIER_MODE")
    if mode and mode not in ("live", "readonly"):
        problems.append("VERIFIER_MODE must be live or readonly")

    # Signer key: required in live mode only. A readonly copy never signs (V-06).
    key = get("VERIFIER_PRIVATE_KEY", required=(mode == "live"))
    signer_address = None
    if key:
        try:
            signer_address = Account.from_key(key).address
        except Exception:
            problems.append("VERIFIER_PRIVATE_KEY is not a valid private key")  # value not shown (V-04)

    # Vision model (used in M4; validated now, V-03)
    vision_key = get("VISION_API_KEY")
    vision_url = get("VISION_BASE_URL")
    if vision_url:
        p = urlparse(vision_url)
        local = p.scheme == "http" and p.hostname in LOCAL_HOSTS  # Ollama fallback (decision 2026-10-04)
        if not (p.scheme == "https" and p.hostname) and not local:
            problems.append("VISION_BASE_URL must be https, or http on localhost/127.0.0.1")
    vision_model = get("VISION_MODEL")
    effort = get("VISION_REASONING_EFFORT", required=False)

    # V-CFG6
    origins_raw = get("CORS_ORIGINS")
    origins = tuple(o.strip().rstrip("/") for o in origins_raw.split(",") if o.strip())
    for o in origins:
        if not _origin_ok(o):
            problems.append(f"CORS_ORIGINS has an entry that is not an http or https origin: {o!r}")

    data_root_raw = get("DATA_ROOT")
    data_root = pathlib.Path(data_root_raw).expanduser()
    if data_root_raw and not data_root.is_absolute():
        data_root = (VERIFIER_DIR / data_root).resolve()

    # V-CFG5
    min_conf = as_int("MIN_CONFIDENCE", get("MIN_CONFIDENCE"))
    if min_conf is not None and not 1 <= min_conf <= 100:
        problems.append("MIN_CONFIDENCE must be 1 to 100")

    # V-CFG4
    near_hard = as_int("NEAR_HARD", get("NEAR_HARD"))
    near_warn = as_int("NEAR_WARN", get("NEAR_WARN"))
    if near_hard is not None and near_warn is not None and not -1 <= near_hard <= near_warn <= 64:
        problems.append("NEAR_HARD and NEAR_WARN must satisfy -1 <= NEAR_HARD <= NEAR_WARN <= 64")

    # V-A1
    admission = get("ADMISSION_MODE")
    if admission and admission not in ("open", "allowlist"):
        problems.append("ADMISSION_MODE must be open or allowlist")
    allow_raw = get("ALLOWLIST", required=(admission == "allowlist"))
    allowlist = set()
    for a in (x.strip() for x in allow_raw.split(",") if x.strip()):
        if Web3.is_address(a):
            allowlist.add(a.lower())
        else:
            problems.append(f"ALLOWLIST has an entry that is not an address: {a!r}")

    ints: dict[str, int | None] = {}
    for name in INT_SETTINGS:
        ints[name] = as_int(name, get(name))
        if ints[name] is not None and ints[name] <= 0:
            problems.append(f"{name} must be a positive integer")

    min_balance_wei = None
    mb = get("MIN_SIGNER_BALANCE")
    if mb:
        try:
            d = Decimal(mb)
            if not d.is_finite() or d <= 0:
                problems.append("MIN_SIGNER_BALANCE must be more than 0 (MON)")
            else:
                min_balance_wei = int(d * 10**18)
        except InvalidOperation:
            problems.append("MIN_SIGNER_BALANCE must be a number of MON, for example 0.05")

    admission_until = None
    au = get("ADMISSION_UNTIL")
    if au:
        try:
            admission_until = datetime.fromisoformat(au.replace("Z", "+00:00"))
            if admission_until.tzinfo is None:
                problems.append("ADMISSION_UNTIL needs a time zone, for example 2026-11-15T00:00:00Z")
        except ValueError:
            problems.append("ADMISSION_UNTIL must be an ISO time, for example 2026-11-15T00:00:00Z")

    # V-E1 (method chosen at M0: finalized)
    shm = get("SAFE_HEAD_METHOD")
    safe_method, safe_offset = "finalized", 0
    if shm:
        m = re.fullmatch(r"latest-minus-(\d+)", shm)
        if shm == "finalized":
            pass
        elif m:
            safe_method, safe_offset = "latest-minus-N", int(m.group(1))
        else:
            problems.append("SAFE_HEAD_METHOD must be finalized or latest-minus-<N>")

    if problems:
        raise ConfigError("Settings are not valid:\n  - " + "\n  - ".join(problems))

    return Settings(
        chain_id=chain_id,
        rpc_url=rpc_url,
        escrow_address=escrow,
        start_block=start_block,
        signer_key=key or None,
        signer_address=signer_address,
        vision_api_key=vision_key,
        vision_base_url=vision_url,
        vision_model=vision_model,
        vision_reasoning_effort=effort,
        cors_origins=origins,
        data_root=data_root,
        min_confidence=min_conf,
        near_hard=near_hard,
        near_warn=near_warn,
        mode=mode,
        admission_mode=admission,
        allowlist=frozenset(allowlist),
        max_jobs_per_poster=ints["MAX_JOBS_PER_POSTER"],
        max_jobs_per_worker=ints["MAX_JOBS_PER_WORKER"],
        max_jobs_per_day=ints["MAX_JOBS_PER_DAY"],
        max_model_calls_per_day=ints["MAX_MODEL_CALLS_PER_DAY"],
        max_verdict_tx_per_day=ints["MAX_VERDICT_TX_PER_DAY"],
        max_queue=ints["MAX_QUEUE"],
        min_signer_balance_wei=min_balance_wei,
        max_storage_mb=ints["MAX_STORAGE_MB"],
        admission_until=admission_until,
        safe_head_method=safe_method,
        safe_head_offset=safe_offset,
    )
