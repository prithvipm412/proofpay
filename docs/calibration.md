# pHash calibration (M5, V-C3, V-C11)

**Status: PROPOSAL, waiting for the owner's approval.** Values in use now: `NEAR_HARD = 4`,
`NEAR_WARN = 10` (provisional since 2026-10-11). This page proposes to keep them and to close M5 with
21 pairs (the target was 30; see Limitations).

## What is measured

The near-reuse check (V-C3) compares the 64-bit perceptual hash (`imagehash.phash`, hash size 8) of the
canonical image (EXIF rotation applied, transparency on white, RGB; V-P1) of a new proof with every
accepted proof of another task. The distance is the number of differing bits (0 to 64).

- At distance `<= NEAR_HARD` the check fails (hard), unless `NEAR_HARD = -1`.
- At distance `<= NEAR_WARN` the report shows a warning only.

A threshold that is too high fails honest workers whose after photo looks like another task's photo
(same place, small change). A threshold that is too low misses re-used photos that were changed a little.

## Data

`verifier-data/calibration/` (not in git: the photos are private). `SOURCES.txt` there maps each file
to its source.

| | Count |
| --- | --- |
| Before/after pairs | 21 (target 30) |
| Places | 18 (target 10 or more) |
| Small-change pairs | 4 (target met) |
| Places with a second occasion | 3 (target 6): coffee-table, desk, single-bed |
| Sources | 14 pairs from public before/after photos (one pair per place, composites split at the divider), 7 pairs from the owner's phone (4 places, 8 Oct and 11 Oct) |

Split by place, never by pair (a place is in one set only): places sorted by the SHA-256 of their
name, the first round(2n/3) go to the calibration set. Calibration: 12 places, 14 pairs. Validation:
6 places, 7 pairs. Thresholds are chosen on the calibration set only; the validation set is reported
as raw counts.

Transformed copies of every photo (made by `scripts/calibrate.py`): `resize50` (half size, JPEG 90),
`jpeg60` (same size, JPEG 60), `crop10` (central 90 %), `rotate5` (5 degrees, black corners),
`screenshot` (1080 x 2340 PNG with black bars).

## Run

```zsh
cd verifier && .venv/bin/python scripts/calibrate.py --near-hard 4 --near-warn 10
```

Run on 2026-10-12 (13 s). Raw distances:

| Set | Genuine pairs | Same place, other occasion | resize50 | jpeg60 | crop10 | rotate5 | screenshot | Other places |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| Calibration | n=14: **10**, 12, 20, 20, 26, 26, 26, 28, 28, 28, 28, 30, 30, 30 | n=8: 20 .. 30 | 0 .. 2 | 0 .. 2 | 6 .. 20 | 8 .. 20 | 8 .. 32 | n=356: 20 .. 42 |
| Validation | n=7: **6**, 16, 22, 22, 22, 32, 34 | n=4: 10, 16, 20, 20 | 0 | 0 | 4 .. 16 | 6 .. 18 | 10 .. 30 | n=80: 20 .. 42 |

The two smallest genuine distances are small-change pairs (desk small-oct11 = 10, kitchen-counter-marble
small-v1 = 6).

## Choice (calibration set only)

- **`NEAR_HARD = 4`.** The smallest honest distance in the calibration set is 10 (a small-change pair);
  4 keeps a margin of 6 below it, and it fails every resize and JPEG re-save copy (0 .. 2).
  A larger value (6 or 8) would also catch a few crops, but it would leave little margin under the
  smallest honest distance, and the small-change pairs are the honest cases most at risk.
- **`NEAR_WARN = 10`.** Warns on the closest honest pairs and on part of the crop and rotate copies.
  A warning does not change the verdict.

Counts at `NEAR_HARD = 4`, `NEAR_WARN = 10`:

| Set | Honest pairs (genuine + same place) | Honest hard fail | Honest warn | Copies hard fail | Copies warn | Copies missed |
| --- | --- | --- | --- | --- | --- | --- |
| Calibration | 22 | 0 | 1 | 56 of 140 (all 28 resize50, all 28 jpeg60) | 21 | 63 |
| Validation | 11 | **0** | 2 | 29 of 70 (all resize50, all jpeg60, 1 crop10) | 10 | 31 |

Other places never came within `NEAR_WARN` (0 of 436 pairs).

## V-C11 result

**PASS.** No genuine or same-place pair in the validation set is at distance `<= 4` (closest: 6). The
hard level of V-C3 can stay on. V-C2 (exact SHA-256 reuse) is always hard.

## Limitations (also in the README)

1. **Small set.** 21 pairs from 18 places, not the 30 pairs planned; only 3 places have a second
   occasion. The owner's last 9 phone pairs were not added before the deadline for this step. One more
   small-change pair at distance 4 or less would make V-C11 fail and would force `NEAR_HARD = -1`.
2. **Mixed sources.** 14 pairs come from public before/after composites (often professionally shot,
   same tripod position); only 7 come from a phone camera held by hand.
3. **pHash catches only simple copies.** Resize and re-save copies (0 .. 2) are blocked. Crops,
   rotations and screenshots (4 .. 32) overlap the honest range, so most of them pass V-C3; some get a
   warning. Exact copies are always blocked by V-C2.
4. **The validation set is small** (7 pairs, 6 places), so "0 honest hard fails" is weak evidence.

## Approval

- [ ] Owner approves `NEAR_HARD = 4` and `NEAR_WARN = 10` (or gives other values).
- [ ] Owner agrees to close M5 with 21 pairs, with the limitations above.

If new photo pairs arrive: copy them into `verifier-data/calibration/<place>/<occasion>_before|after.<ext>`,
add them to `SOURCES.txt`, run the command above again (13 s), and update this page.
