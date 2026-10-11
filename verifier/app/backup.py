"""Backup and restore of a verifier data folder (V-B1, V-B2, V-B3).

Photo files cannot be rebuilt from the chain (V-S6), so a backup keeps every original that a claim
references, with its SHA-256, plus a SQLite online backup of the database. A restore unpacks to a
NEW folder, verifies every file, and marks the folder `restored`, so the verifier rebuilds the
claims from START_BLOCK at its next start (V-E5). The comparison of accepted claims with the
chain's pass verdicts uses contract state, not the event scan, so it is an independent check:
`disputeUntil` is set only by a pass verdict, and a pass verdict ends the attempts of a task, so the
pass verdicts are exactly {(id, attempts, proofHash) : disputeUntil > 0}.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import pathlib
import shutil
import sqlite3
import tarfile
import tempfile
from dataclasses import dataclass, field
from typing import Callable

from .storage import Storage, norm_hash

MANIFEST = "manifest.json"
FORMAT_VERSION = 1


class BackupError(Exception):
    pass


def sha256_of(path: pathlib.Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return "0x" + h.hexdigest()


def _folder_name(deployment_id: str) -> str:
    chain_id, address = deployment_id.split(":")
    return f"{chain_id}-{address.lower()}"


# ---------------------------------------------------------------- V-B1 backup


@dataclass
class BackupResult:
    archive: pathlib.Path
    referenced: int
    copied: int
    missing: list[str]
    archive_sha256: str


def make_backup(data_dir: pathlib.Path, out_dir: pathlib.Path, config_version: str, mode: str) -> BackupResult:
    """SQLite online backup + every referenced original (hash verified) + manifest, in one .tar.gz.
    Works while a live verifier writes to the folder (any mode)."""
    db_path = data_dir / "proofpay.db"
    if not db_path.exists():
        raise BackupError(f"No database at {db_path}")
    out_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="proofpay-backup-") as tmp_s:
        tmp = pathlib.Path(tmp_s)
        src = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=30)
        try:
            meta = {k: v for k, v in src.execute("SELECT key, value FROM meta")}
            folder = tmp / _folder_name(meta["deploymentId"])
            (folder / "originals").mkdir(parents=True)
            (folder / "previews").mkdir()
            dst = sqlite3.connect(folder / "proofpay.db")
            try:
                src.backup(dst)  # consistent snapshot, even with a writer (V-B1)
            finally:
                dst.close()
        finally:
            src.close()

        snap = sqlite3.connect(folder / "proofpay.db")
        try:
            snap.execute("PRAGMA journal_mode=DELETE")  # one self-contained file in the archive
            referenced = sorted({norm_hash(r[0]) for r in snap.execute("SELECT DISTINCT sha256 FROM claims")})
            meta = {k: v for k, v in snap.execute("SELECT key, value FROM meta")}
        finally:
            snap.close()

        store = Storage(data_dir, meta["deploymentId"], 1, 0)  # path helpers only; nothing is opened
        files, missing = [], []
        for sha in referenced:
            original = store.original_path(sha)
            if original is None:
                missing.append(sha)
                continue
            if sha256_of(original) != sha:
                raise BackupError(f"Original {original.name} does not match its SHA-256 name. Stop.")
            target = folder / "originals" / original.name
            shutil.copyfile(original, target)
            if sha256_of(target) != sha:  # verify the copy (V-B1)
                raise BackupError(f"Copy of {original.name} does not match. Stop.")
            files.append(_entry(folder, target, "original", sha))
            preview = store.preview_path(sha)
            if preview.exists():
                ptarget = folder / "previews" / preview.name
                shutil.copyfile(preview, ptarget)
                files.append(_entry(folder, ptarget, "preview", sha))

        manifest = {
            "format": FORMAT_VERSION,
            "createdAt": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
            "deploymentId": meta["deploymentId"],
            "folder": folder.name,
            "meta": meta,
            "configVersion": config_version,
            "modeAtBackup": mode,
            "database": _entry(folder, folder / "proofpay.db", "database", None),
            "referenced": referenced,
            "missing": missing,
            "files": files,
        }
        (tmp / MANIFEST).write_text(json.dumps(manifest, indent=2, sort_keys=True))

        stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        archive = out_dir / f"proofpay-backup-{folder.name}-{stamp}.tar.gz"
        partial = archive.with_suffix(".partial")
        with tarfile.open(partial, "w:gz") as tar:
            tar.add(tmp / MANIFEST, arcname=MANIFEST)
            tar.add(folder, arcname=folder.name)
        partial.rename(archive)
    return BackupResult(archive, len(referenced), sum(f["kind"] == "original" for f in files), missing, sha256_of(archive))


def _entry(folder: pathlib.Path, path: pathlib.Path, kind: str, sha: str | None) -> dict:
    return {
        "path": f"{folder.name}/{path.relative_to(folder).as_posix()}",
        "kind": kind,
        "sha256": sha256_of(path),
        "size": path.stat().st_size,
        **({"image": sha} if sha else {}),
    }


# ---------------------------------------------------------------- V-B2 restore


@dataclass
class Unpacked:
    root: pathlib.Path  # the new DATA_ROOT
    data_dir: pathlib.Path
    manifest: dict
    problems: list[str] = field(default_factory=list)


def unpack(archive: pathlib.Path, new_root: pathlib.Path) -> Unpacked:
    """Unpack to a NEW folder, verify every manifest file, and mark the folder restored (V-E5)."""
    if new_root.exists() and any(new_root.iterdir()):
        raise BackupError(f"{new_root} is not empty. A restore needs a new folder (V-B2).")
    new_root.mkdir(parents=True, exist_ok=True)
    with tarfile.open(archive, "r:gz") as tar:
        tar.extractall(new_root, filter="data")  # rejects absolute paths, '..' and links outside
    manifest = json.loads((new_root / MANIFEST).read_text())
    if manifest.get("format") != FORMAT_VERSION:
        raise BackupError(f"Unknown backup format {manifest.get('format')!r}")
    data_dir = new_root / manifest["folder"]
    problems = []
    for entry in [manifest["database"], *manifest["files"]]:
        path = new_root / entry["path"]
        if not path.is_file():
            problems.append(f"missing in archive: {entry['path']}")
        elif path.stat().st_size != entry["size"] or sha256_of(path) != entry["sha256"]:
            problems.append(f"hash or size differs: {entry['path']}")
    if not problems:
        conn = sqlite3.connect(data_dir / "proofpay.db")
        try:
            conn.execute(
                "INSERT INTO meta (key, value) VALUES ('restored', '1') "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value"
            )
            conn.commit()
        finally:
            conn.close()
        for d in ("originals", "previews", "tmp"):
            (data_dir / d).mkdir(exist_ok=True)
    return Unpacked(new_root, data_dir, manifest, problems)


# ---------------------------------------------------------------- comparison


def chain_pass_verdicts(task_count: int, get_task: Callable[[int], dict]) -> set[tuple[int, int, str]]:
    """{(taskId, attempt, proofHash)} of every pass verdict on the chain, from contract state."""
    out = set()
    for task_id in range(1, task_count + 1):
        t = get_task(task_id)
        if int(t.get("disputeUntil") or 0) > 0:
            out.add((task_id, int(t["attempts"]), norm_hash(t["proofHash"])))
    return out


@dataclass
class RestoreReport:
    claim_counts: dict[str, int]
    accepted: set[tuple[int, int, str]]
    chain_pass: set[tuple[int, int, str]]
    missing_after_backup: list[str]  # referenced now, not referenced at backup time: uploaded after it (expected)
    missing_at_backup: list[str]  # referenced at backup time, but the backup already had no file for it
    missing_lost: list[str]  # copied into the backup, but missing now (a real loss)
    history_status: str
    checkpoint: int

    @property
    def equal(self) -> bool:
        return self.accepted == self.chain_pass

    @property
    def ok(self) -> bool:
        return self.equal and not self.missing_lost and self.history_status == "complete"


def restore_report(unpacked: Unpacked, chain_pass: set[tuple[int, int, str]]) -> RestoreReport:
    conn = sqlite3.connect(f"file:{unpacked.data_dir / 'proofpay.db'}?mode=ro", uri=True)
    try:
        counts = {s: 0 for s in ("history", "pending", "accepted", "rejected")}
        for state, n in conn.execute("SELECT state, COUNT(*) FROM claims GROUP BY state"):
            counts[state] = n
        accepted = {
            (r[0], r[1], norm_hash(r[2]))
            for r in conn.execute("SELECT task_id, attempt, sha256 FROM claims WHERE state = 'accepted' AND role = 'after'")
        }
        referenced = sorted({norm_hash(r[0]) for r in conn.execute("SELECT DISTINCT sha256 FROM claims")})
        meta = {k: v for k, v in conn.execute("SELECT key, value FROM meta")}
    finally:
        conn.close()
    store = Storage(unpacked.data_dir, meta["deploymentId"], 1, 0)
    at_backup, missing_then = set(unpacked.manifest["referenced"]), set(unpacked.manifest["missing"])
    missing = [sha for sha in referenced if store.original_path(sha) is None]
    return RestoreReport(
        claim_counts=counts,
        accepted=accepted,
        chain_pass=chain_pass,
        missing_after_backup=[s for s in missing if s not in at_backup],
        missing_at_backup=[s for s in missing if s in missing_then],
        missing_lost=[s for s in missing if s in at_backup and s not in missing_then],
        history_status=meta.get("historyStatus", ""),
        checkpoint=int(meta.get("checkpointBlock", "-1")),
    )
