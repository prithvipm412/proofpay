# Release gate (section 15) — evidence for the M9 review

Prepared by the agent on 2026-10-12 (overnight). The owner goes through it at M9. A condition passes
only with evidence; a README note does not replace a pass.

Commands used (all from the repo root):

```zsh
cd packages/foundry && forge test --match-test "CT4|CT5|CT6|CT8|CT9|CT10|CT14"      # 36 passed (2026-10-12)
cd verifier && .venv/bin/python -m pytest -k "VT1_ or VT2_ or VT3_ or VT5_ or VT6_ or VT7_ or VT16_ or VT17_ or VT22_ or M7_crash"   # 34 passed
cd verifier && .venv/bin/python scripts/trust_local.py     # anvil: 9/9 PASS
cd verifier && .venv/bin/python scripts/trust_testnet.py --rehearse   # anvil: 9/9 PASS
```

Full suites on 2026-10-12: forge 67 passed, verifier pytest 337 passed, frontend Vitest 92 passed.

## 15.1 Mandatory conditions

| # | Condition | Evidence | Status |
| --- | --- | --- | --- |
| 1 | Money: balance invariant for random action sequences; payouts and withdrawals correct, including a rejecting receiver | `invariant_CT14_balanceEqualsOwed`, `invariant_CT14_taskFieldsStayInRange` (128 runs x depth 150); `test_CT8_*` (3), `test_CT9_*` (4) | PASS (tests) |
| 2 | Authorization: each restricted function rejects wrong callers | `test_CT10_*` (9) | PASS (tests) |
| 3 | Time rules: every boundary in 9.3, including the arbitration boundary | `test_CT5_*` (11) and `testFuzz_CT5_arbitrationExactlyOnePathOpen`, `test_CT6_*` (4) | PASS (tests) |
| 4 | Evidence binding: an old verdict cannot change a new attempt; both photos checked against their commitments | `test_CT4_*` (2), `test_VT6_*` (2), `test_VT1_*` (4) | PASS (tests) |
| 5 | Duplicate history: poisoning and rejected claims do not block honest proofs; an unsettled verdict blocks the next job; history survives a restart and a restore | `test_VT2_*` (2), `test_VT3_*`, `test_VT5_*`, `test_VT16_*` (2); local restart + reuse: `trust_local.py` case 6 PASS. **Testnet:** restart test = `trust_testnet.py` step 5 (owner run, Needs owner B); restore of the 02:27 backup after that run (Needs owner C). Restore of the same backup before the run: see PROGRESS (agent run 2026-10-12). | Tests PASS; testnet part OPEN (owner) |
| 6 | Recovery: a crash in each job state recovers with at most one transaction per job; `finalize` is atomic | `test_VT7_*` (7), `test_M7_crash_switch_*`, `test_VT22_*` (3); `trust_local.py` and `trust_testnet.py --rehearse` crash after evaluated/signed/broadcast: PASS. **Testnet:** `trust_testnet.py` steps 1, 3, 4 (owner run). | Tests PASS; testnet part OPEN (owner) |
| 7 | Signer safety: readonly never signs; the lock stops a second live start on the same folder | `test_VT17_*` (4); manual check on 2026-10-04 (second copy stopped with "Another live verifier holds …/signer.lock"); `blocked_nonce.py` also refuses while the live verifier runs (checked 2026-10-12) | PASS |

IF a condition fails at M9 and cannot be repaired in time: do not mark M9 DONE, present the app as a
prototype in the README and the video, and say which condition failed (section 15.1).

## 15.2 Disclosed limits — where they are in the README

| # | Limit | README place |
| --- | --- | --- |
| 1 | Verifier key and arbiter are trusted | Top note; section 6, first two bullets |
| 2 | A photo does not prove work, photographer or time | Top note; section 6 |
| 3 | Vision model results incl. injection cases | Section 6 (0 wrong verdicts in 44 runs; not proof of safety); `docs/model-eval.md` |
| 4 | pHash calibration and selected NEAR_HARD | Section 6; `docs/calibration.md` (proposal until the owner approves) |
| 5 | Capacity: one verdict at a time; admission mode and caps | Section 5 ("One verdict at a time"); section 6 ("Public demo capacity", "settles one verdict at a time") |
| 6 | C-19 policy and V-C10 duplicate policy | Section 5 (arbitration timeout, duplicate policy); section 6 (favors the poster) |
| 7 | Manual recovery for `blocked_nonce`; failover procedure | Section 9 ("Failover", "blocked_nonce recovery") |
| 8 | Owner's phone, funded demo wallets, public hosts, timed practice recording tested | Section 6 last bullet: **placeholder** — fill after Needs owner F and G |
