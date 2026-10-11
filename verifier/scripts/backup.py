"""Backup of the verifier data folder (V-B1). Safe in any mode, also while the live verifier runs.

Usage (from the verifier/ folder):
    .venv/bin/python scripts/backup.py                 # writes to ../verifier-data/backups/
    .venv/bin/python scripts/backup.py --out DIR

It reads verifier/.env only to find the data folder (DATA_ROOT, CHAIN_ID, ESCROW_ADDRESS); no
secret is printed or put in the archive. The archive has: a SQLite online backup of proofpay.db,
every original photo that a claim references (SHA-256 verified after the copy), their previews,
and manifest.json (sizes, SHA-256 values, meta values, config version).
Keep a copy of the archive off this Mac. Restore it with scripts/restore.py (readonly check).
"""

from __future__ import annotations

import argparse
import pathlib
import sys

VERIFIER_DIR = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(VERIFIER_DIR))

from app.backup import BackupError, make_backup  # noqa: E402
from app.config import ConfigError, environment, load_settings  # noqa: E402


def main() -> int:
    p = argparse.ArgumentParser(description="ProofPay verifier data backup (V-B1)")
    p.add_argument("--out", type=pathlib.Path, default=VERIFIER_DIR.parent / "verifier-data" / "backups")
    args = p.parse_args()
    try:
        settings = load_settings(environment())
    except ConfigError as exc:
        print(f"Settings problem: {exc}")
        return 2
    data_dir = VERIFIER_DIR / settings.data_dir  # a relative DATA_ROOT is relative to verifier/
    print(f"Data folder: {data_dir}")
    try:
        r = make_backup(data_dir, args.out.resolve(), settings.config_version, settings.mode)
    except BackupError as exc:
        print(f"BACKUP FAILED: {exc}")
        return 1
    print(f"Archive:     {r.archive}")
    print(f"Size:        {r.archive.stat().st_size} bytes")
    print(f"SHA-256:     {r.archive_sha256}")
    print(f"Referenced photos: {r.referenced}, copied and verified: {r.copied}, missing: {len(r.missing)}")
    for sha in r.missing:
        print(f"  MISSING original for {sha}")
    if r.missing:
        print("BACKUP INCOMPLETE: some referenced originals are missing in the data folder.")
        return 1
    print("BACKUP OK. Keep a copy of the archive off this Mac.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
