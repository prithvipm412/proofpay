"""Checks V-C1..V-C6 (section 10.12). The AI vision check V-C7 comes in M4 (app/vision.py).

Rules that this module keeps:
- IF V-C1 fails, every other check is `skipped` (the caller also marks V-C7 `skipped`).
- IF a hard check fails, the remaining hard checks and the vision check are skipped; soft checks run.
- Only `accepted` claims can make another task fail (V-C8). Other claims only give warnings.
- A match is a similarity signal, not proof. Reports do not use the word "fraud" (V-C9).
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Callable

from .config import Settings
from .images import decode_in_child, distance, sha256_file
from .storage import Storage

REASON_C1 = "Photo file missing or does not match the commitment"
REASON_C2 = "Same photo as the accepted proof of task #{id}"
REASON_C3 = "Nearly the same photo as the accepted proof of task #{id}"
REASON_C3_INCOMPLETE = "Near reuse check could not complete"
SAME_AS_BEFORE_MAX = 4  # V-C5
SEEN_ELSEWHERE_MIN = 4  # V-C4: max(NEAR_HARD, 4)

CHECK_NAMES = {
    "V-C1": "Integrity",
    "V-C2": "Exact reuse",
    "V-C3": "Near reuse",
    "V-C4": "Seen elsewhere",
    "V-C5": "Same as before",
    "V-C6": "Capture time",
    "V-C7": "AI vision",
}
ROLE_WORDS = {"history": "before photo", "pending": "submitted proof", "rejected": "rejected proof"}

Decoder = Callable  # (path, preview_path=None) -> info dict; raises ImageRejected


@dataclass
class Check:
    id: str
    result: str  # pass, warn, fail, skipped
    detail: str = ""
    name: str = field(init=False)

    def __post_init__(self):
        self.name = CHECK_NAMES[self.id]

    def as_dict(self) -> dict:
        d = asdict(self)
        return {"id": d["id"], "name": d["name"], "result": d["result"], "detail": d["detail"]}


@dataclass
class CheckRun:
    checks: list[Check]
    fail_reason: str | None  # reason of the first failed non-AI hard check (V-21, rule 1)
    vision_allowed: bool  # False IF a hard check failed

    def get(self, check_id: str) -> Check:
        return next(c for c in self.checks if c.id == check_id)


def phash_of(storage: Storage, sha: str, decode: Decoder) -> str | None:
    """pHash from the cache keyed by SHA-256, or from the original (V-S10). None IF no original."""
    row = storage.image(sha)
    if row is not None and row["phash"]:
        return row["phash"]
    path = storage.original_path(sha)
    if path is None:
        return None
    try:
        ph = decode(path)["phash"]
    except Exception:
        return None
    storage.cache_phash(sha, ph)
    return ph


def _integrity(storage: Storage, sha: str, decode: Decoder, label: str) -> tuple[dict | None, str]:
    path = storage.original_path(sha)
    if path is None:
        return None, f"{label} photo file is missing"
    if "0x" + sha256_file(path) != sha:
        return None, f"{label} photo file does not match its hash"
    try:
        info = decode(path)
    except Exception:
        return None, f"{label} photo file does not decode"
    return info, ""


def run_checks(
    storage: Storage,
    settings: Settings,
    task_id: int,
    before_hash: str,
    proof_hash: str,
    created_at: int,
    decode: Decoder = decode_in_child,
) -> CheckRun:
    # ---- V-C1 Integrity (hard)
    before_info, problem = _integrity(storage, before_hash, decode, "Before")
    after_info = None
    if before_info is not None:
        after_info, problem = _integrity(storage, proof_hash, decode, "After")
    if before_info is None or after_info is None:
        checks = [Check("V-C1", "fail", problem)] + [
            Check(i, "skipped") for i in ("V-C2", "V-C3", "V-C4", "V-C5", "V-C6")
        ]
        return CheckRun(checks, REASON_C1, vision_allowed=False)
    for sha, info in ((before_hash, before_info), (proof_hash, after_info)):
        if storage.image(sha) is not None:
            storage.cache_phash(sha, info["phash"])
    after_ph = after_info["phash"]
    checks = [Check("V-C1", "pass")]
    fail_reason = None

    accepted = storage.claims_of_other_tasks(task_id, ("accepted",))

    # ---- V-C2 Exact reuse (hard, always on)
    exact = next((c for c in accepted if c["sha256"] == proof_hash), None)
    if exact is not None:
        fail_reason = REASON_C2.format(id=exact["task_id"])
        checks.append(Check("V-C2", "fail", f"Same file as the accepted proof of task #{exact['task_id']}"))
    else:
        checks.append(Check("V-C2", "pass"))

    # ---- V-C3 Near reuse (hard at <= NEAR_HARD unless -1; warning at <= NEAR_WARN)
    if fail_reason is not None:
        checks.append(Check("V-C3", "skipped"))
    else:
        hard_on = settings.near_hard >= 0
        closest: tuple[int, int] | None = None  # (distance, task id)
        missing: int | None = None
        for c in accepted:
            ph = phash_of(storage, c["sha256"], decode)
            if ph is None:
                missing = missing if missing is not None else c["task_id"]
                continue
            d = distance(after_ph, ph)
            if closest is None or d < closest[0]:
                closest = (d, c["task_id"])
        if missing is not None and hard_on:
            # Fail closed: a hard check that did not complete cannot pass (section 4).
            fail_reason = REASON_C3_INCOMPLETE
            checks.append(Check("V-C3", "fail", f"No file to compare with the accepted proof of task #{missing}"))
        elif closest is not None and hard_on and closest[0] <= settings.near_hard:
            fail_reason = REASON_C3.format(id=closest[1])
            checks.append(
                Check("V-C3", "fail", f"distance {closest[0]} to the accepted proof of task #{closest[1]}")
            )
        elif closest is not None and closest[0] <= settings.near_warn:
            checks.append(
                Check("V-C3", "warn", f"distance {closest[0]} to the accepted proof of task #{closest[1]}")
            )
        elif missing is not None:
            checks.append(Check("V-C3", "warn", f"No file to compare with the accepted proof of task #{missing}"))
        elif closest is not None:
            checks.append(Check("V-C3", "pass", f"closest distance {closest[0]}"))
        else:
            checks.append(Check("V-C3", "pass", "no accepted proofs to compare"))

    # ---- V-C4 Seen elsewhere (soft)
    limit = max(settings.near_hard, SEEN_ELSEWHERE_MIN)
    matches: list[str] = []
    for c in storage.claims_of_other_tasks(task_id, ("history", "pending", "rejected")):
        words = f"{ROLE_WORDS[c['state']]} of task #{c['task_id']}"
        if c["sha256"] == proof_hash:
            matches.append(f"Same file as the {words}")
            continue
        ph = phash_of(storage, c["sha256"], decode)
        if ph is not None and (d := distance(after_ph, ph)) <= limit:
            matches.append(f"Similar (distance {d}) to the {words}")
    if matches:
        more = f" (and {len(matches) - 1} more)" if len(matches) > 1 else ""
        checks.append(Check("V-C4", "warn", matches[0] + more))
    else:
        checks.append(Check("V-C4", "pass"))

    # ---- V-C5 Same as before (soft)
    d_before = distance(before_info["phash"], after_ph)
    if d_before <= SAME_AS_BEFORE_MAX:
        checks.append(Check("V-C5", "warn", f"distance {d_before}: the after photo looks like the before photo"))
    else:
        checks.append(Check("V-C5", "pass", f"distance {d_before}"))

    # ---- V-C6 Capture time (soft). EXIF is not authenticated.
    status, when = after_info["exif_time_status"], after_info["exif_time_utc"]
    if status == "none":
        checks.append(Check("V-C6", "warn", "No capture time"))
    elif status == "no_timezone":
        checks.append(Check("V-C6", "warn", "Capture time has no time zone"))
    else:
        taken = datetime.strptime(when, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
        if taken.timestamp() < created_at:
            checks.append(Check("V-C6", "warn", "Capture time is before the task was created"))
        else:
            checks.append(Check("V-C6", "pass"))

    return CheckRun(checks, fail_reason, vision_allowed=fail_reason is None)
