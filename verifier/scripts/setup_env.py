"""Fill verifier/.env for the owner without showing any secret (section 4, V-04).

Run from verifier/:
    .venv/bin/python scripts/setup_env.py                                # fill empty settings only
    .venv/bin/python scripts/setup_env.py --signer-from-keystore verifier

1. Each EMPTY non-secret setting gets the section 8 value. A setting that has a value is not changed.
2. With --signer-from-keystore NAME, the script runs `cast wallet private-key --account NAME`
   (cast asks for the keystore password), checks that the key's address equals the contract's
   verifier() address, and writes VERIFIER_PRIVATE_KEY. It prints only the address, never the key.
The file is rewritten atomically with mode 600.
"""

from __future__ import annotations

import argparse
import os
import pathlib
import re
import subprocess
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from app.config import ENV_FILE, read_env_file  # noqa: E402

DEFAULTS = {  # section 8; owner decisions in PROGRESS.md
    "MONAD_RPC_URL": "https://testnet-rpc.monad.xyz",
    "CORS_ORIGINS": "http://localhost:3000",
    "DATA_ROOT": "../verifier-data",
    "MIN_CONFIDENCE": "70",
    "NEAR_HARD": "4",
    "NEAR_WARN": "10",
    "VERIFIER_MODE": "live",
    "ADMISSION_MODE": "open",
    "MAX_JOBS_PER_POSTER": "10",
    "MAX_JOBS_PER_WORKER": "10",
    "MAX_JOBS_PER_DAY": "150",
    "MAX_MODEL_CALLS_PER_DAY": "300",
    "MAX_VERDICT_TX_PER_DAY": "150",
    "MAX_QUEUE": "50",
    "MIN_SIGNER_BALANCE": "0.05",
    "MAX_STORAGE_MB": "2000",
    "ADMISSION_UNTIL": "2026-11-15T00:00:00Z",
    "SAFE_HEAD_METHOD": "finalized",
}
KEY_RE = re.compile(r"0x[0-9a-fA-F]{64}")


def set_values(path: pathlib.Path, values: dict[str, str], only_if_empty: bool) -> list[str]:
    """Write values into the .env file, keeping other lines and comments. Returns changed key names."""
    lines = path.read_text().splitlines() if path.exists() else []
    current = read_env_file(path)
    changed, seen = [], set()
    out = []
    for line in lines:
        m = re.match(r"^\s*([A-Z0-9_]+)\s*=", line)
        if m and m.group(1) in values:
            key = m.group(1)
            seen.add(key)
            if not (only_if_empty and current.get(key)):
                if current.get(key) != values[key]:
                    changed.append(key)
                line = f"{key}={values[key]}"
        out.append(line)
    for key, value in values.items():
        if key not in seen:
            out.append(f"{key}={value}")
            changed.append(key)
    tmp = path.with_name(path.name + ".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write("\n".join(out) + "\n")
    os.replace(tmp, path)
    os.chmod(path, 0o600)
    return changed


def key_from_keystore(name: str) -> str:
    """Run cast; its password prompt uses the terminal. Only stdout is captured, never printed."""
    proc = subprocess.run(["cast", "wallet", "private-key", "--account", name], stdout=subprocess.PIPE, text=True)
    found = KEY_RE.findall(proc.stdout or "")
    if proc.returncode != 0 or len(found) != 1:
        raise SystemExit(f"cast could not read the keystore {name!r} (exit code {proc.returncode}).")
    return found[0]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--signer-from-keystore", metavar="NAME")
    parser.add_argument("--env-file", default=str(ENV_FILE))
    args = parser.parse_args()
    path = pathlib.Path(args.env_file)

    filled = set_values(path, DEFAULTS, only_if_empty=True)
    print("Filled empty settings:", ", ".join(filled) if filled else "none")

    if args.signer_from_keystore:
        from eth_account import Account

        from app.chain import Chain

        env = read_env_file(path)
        key = key_from_keystore(args.signer_from_keystore)
        address = Account.from_key(key).address
        expected = Chain(env["MONAD_RPC_URL"], env["ESCROW_ADDRESS"]).verifier()
        if address != expected:
            print(f"STOP: keystore address {address} is not the contract verifier {expected}. Nothing written.")
            return 1
        set_values(path, {"VERIFIER_PRIVATE_KEY": key}, only_if_empty=False)
        print(f"VERIFIER_PRIVATE_KEY written for {address} (matches contract verifier()).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
