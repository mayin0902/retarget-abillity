# Materialization status — 2026-08-11

## Outcome

- Exact discovery freeze: **300 candidates**.
- Source quota: **125 Wikimedia Commons + 175 Open Images V7 validation**.
- Split quota: **pilot60=60, held-out240=240**.
- Scene quota: **50/50/50/50/34/33/33**, as specified in
  `selection_policy.yaml`.
- Exact one-task-per-source mapping: **300 `square-1536x1536` tasks**.
- Actual review-copy downloads: **10**, with **0 network/decode failures**.
- Raw and metadata-free materialized SHA-256: recorded for all 10 downloads.
- Commons upstream SHA-1: rechecked against all five downloaded Commons files.
- EXIF/GPS: stripped by decoding, applying EXIF orientation, and storing decoded
  pixels losslessly as PNG; the sanitizer test verifies no EXIF remains.
- Duplicate sample check: the ten review copies are mutually distinct and are
  not near-duplicates of the existing 12-image HD Smoke under the implemented
  pHash check. The minimum Hamming distance was 21 in both comparisons; the
  final 300-row duplicate review remains pending.
- Public-release approval: **0/300**. External API egress: **0/300 allowed**.

The strict finalizer is correctly **BLOCKED**. The 300 rows are an exact review
queue, not the final public benchmark.

## Commands actually run

```powershell
.\.venv\Scripts\python.exe scripts/materialize_square_public_v1.py freeze-candidates
.\.venv\Scripts\python.exe scripts/materialize_square_public_v1.py validate-candidates
.\.venv\Scripts\python.exe scripts/materialize_square_public_v1.py download-review --limit 5
.\.venv\Scripts\python.exe scripts/materialize_square_public_v1.py download-review --offset 125 --limit 5
.\.venv\Scripts\python.exe scripts/materialize_square_public_v1.py status
.\.venv\Scripts\ruff.exe check scripts/materialize_square_public_v1.py tests/test_square_public_dataset.py
.\.venv\Scripts\pytest.exe -q tests/test_square_public_dataset.py  # 15 passed
```

The candidate validator reported all requested counts exactly. The current
status reports 300 pending reviews, 10 downloaded review copies, and final
validation blocked.

## Evidence-backed blockers

### 1. Official Open Images pixels conflict with the resolution contract

Five frozen Open Images validation IDs were downloaded from the same public S3
bucket used by the official Open Images downloader. All five had a long side of
exactly 1024 pixels and therefore failed the benchmark rule requiring a short
side of at least 1024 and a long side of at least 1600:

| ID | measured size | aspect result |
|---|---:|---|
| `00075905539074f2` | 1024×914 | ineligible (<1.5 pressure) |
| `0007cebe1b2ba653` | 1024×683 | ineligible (<1.5 pressure) |
| `000a045a0715d64d` | 1024×680 | `aspect_hard_1` |
| `000a1249af2bc5f0` | 1024×678 | `aspect_hard_1` |
| `0013e81caff9a7b2` | 1024×683 | ineligible (<1.5 pressure) |

Downloading all remaining 170 Open Images candidates would spend bandwidth but
would not resolve this structural mismatch. The final set needs one explicit
spec decision: lower the source-resolution floor for official Open Images
copies, permit individually audited original upstream pixels, or replace Open
Images with another official high-resolution source. The script does not
upscale pixels or silently switch to a non-official URL.

### 2. Automatic scene discovery is not reliable enough for ground truth

Codex inspected the ten-image overview at
`local_data/retarget_square_public_v1/review_overview.png`. Seven rows have a
clear scene mismatch, one otherwise matching tobacco advertisement is unsuitable
for the intended non-sensitive set, and two need closer manual review (one of
those still fails resolution). Exact observations are recorded in
`codex_sample_review.csv`.

Open Images bbox heuristics and Commons search terms therefore remain discovery
signals only. They cannot approve scene labels or content safety. Continuing to
download all 300 before replacing the failed candidates would be wasteful.

### 3. The frozen aspect distribution is far from the final 90/150/60 target

Before Open Images pixel downloads, 175 rows have unknown aspect. Among the 125
Commons metadata rows, the automatic freeze contains 112 `aspect_hard_1`, 12
`aspect_hard_2`, and only 1 `aspect_extreme`. This is a review pool, not a pool
that can satisfy the final aspect quota by approval alone. Candidate discovery
must intentionally oversample extreme panoramas/tall images and then rebalance
scene/source/orientation quotas.

## Required continuation

1. Resolve the Open Images resolution/source conflict without weakening it
   silently.
2. Build a larger candidate pool per scene instead of taking the first N search
   results; download low-resolution review renditions and review them before
   fetching original bytes.
3. Replace the nine rejected/sample-blocked rows and explicitly target the
   missing `aspect_hard_2` and `aspect_extreme` quotas.
4. Review all surviving rows for file-page license evidence, scene, safety,
   personality/trademark restrictions, and near-duplicate pHash against the
   existing 12-image Smoke.
5. Only after every status is approved, run `finalize` to create the local
   descriptor and immutable dataset fingerprint.
