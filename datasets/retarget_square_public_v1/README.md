# retarget_square_public_v1

This directory defines a proposed 300-source, public, real-world benchmark for a
single `1536x1536` task per source. It uses 125 exact Wikimedia Commons file
titles and 175 exact Open Images V7 validation IDs. Images are materialized only
under the Git-ignored `local_data/retarget_square_public_v1/` directory.

## Current state

The repository manifest is a **candidate freeze**, not a claim that 300 images
are publication-ready. Automated Commons search and Open Images bounding-box
heuristics are discovery aids only. Every row starts with
`public_release_eligible=false`, `api_egress_allowed=false`, and pending
license/scene/safety/duplicate review states. The strict `finalize` command
fails until each row has been reviewed and every quota, resolution, aspect,
orientation, hash, and metadata-removal contract passes.

This prevents fixtures, old Smoke images, search-result assumptions, and
automatic detector output from silently becoming benchmark ground truth.
Once `source_manifest.csv` exists, `freeze-candidates` validates and reuses it;
it will not rerun mutable search results or replace IDs. A changed source list is
a new dataset version, not an in-place v1 overwrite.

## Reproduction

```powershell
.\.venv\Scripts\python.exe scripts/materialize_square_public_v1.py freeze-candidates
.\.venv\Scripts\python.exe scripts/materialize_square_public_v1.py validate-candidates
.\.venv\Scripts\python.exe scripts/materialize_square_public_v1.py download-review
.\.venv\Scripts\python.exe scripts/materialize_square_public_v1.py status
```

`download-review` downloads only the frozen IDs/titles from the official paths,
stores raw bytes in `raw_cache/`, writes metadata-free lossless PNG working
copies to `images/`, and records both SHA-256 values. It does not approve rows.

After per-image review has updated the audit statuses, run:

```powershell
.\.venv\Scripts\python.exe scripts/materialize_square_public_v1.py finalize
```

The command is intentionally fail-closed. It requires 300 approved rows,
pilot60/held-out240 and seven-scene quotas, 125/175 upstream quotas, 90/150/60
aspect tiers, balanced orientation per scene, dimensions, unique hashes,
allowlisted hosts/licenses, safe relative paths, and no API egress permission.

## Files

- `source_manifest.csv`: exact candidate identities and current audit state.
- `source_audit.csv`: review/audit projection; currently mirrors the manifest.
- `review_queue.csv`: compact queue for per-image thumbnail/license review.
- `commons_file_titles.txt`: exact Commons titles, one per line.
- `openimages_validation_ids.txt`: exact `$SPLIT/$IMAGE_ID` list accepted by the
  official Open Images downloader.
- `targets.csv` and `tasks.csv`: one square target and exactly one task per row.
- `ATTRIBUTION.md`: publication requirements and intentionally incomplete state.

## Important limitation discovered during implementation

Open Images' official CVDF/S3 downloader serves the dataset copies used by the
benchmark. Their pixel dimensions must be measured after download; image-info
CSV rows do not provide them. If those copies do not meet the frozen
1024-short-side/1600-long-side rule, they remain candidates only. The script
does not substitute a non-official URL or upscale pixels to fake eligibility.
