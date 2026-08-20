# retarget_square_public_v2

V2 preserves the blocked V1 audit and fixes the source-resolution/evaluation
contract. The scoring canvas is exactly `1024x1024`. Open Images V7 validation
uses the official stable S3 copies; no row is silently upscaled to pretend that
new source detail exists. An audited higher-resolution original may be retained
for a generation route, but every scored output is normalized to 1024 square.

## Stages

```powershell
.\.venv\Scripts\python.exe scripts/materialize_square_public_v2.py discover-pool
.\.venv\Scripts\python.exe scripts/materialize_square_public_v2.py download-pool
.\.venv\Scripts\python.exe scripts/materialize_square_public_v2.py build-shortlist
.\.venv\Scripts\python.exe scripts/materialize_square_public_v2.py build-heldout-review
.\.venv\Scripts\python.exe scripts/materialize_square_public_v2.py freeze-heldout-decisions
.\.venv\Scripts\python.exe scripts/materialize_square_public_v2.py materialize-heldout
.\.venv\Scripts\python.exe scripts/materialize_square_public_v2.py materialize-full
```

The first stage freezes at least three candidates per final slot (900 minimum
exact candidate identities; reviewed supplements may increase the pool). The second downloads exact Commons review renditions
and official Open Images S3 pixels into Git-ignored local storage. The third
enforces the pressure-specific source-size rules and produces scene overview
sheets for Codex review.

No candidate becomes part of the pilot because a search term or detector label
matched. Every selected image must have a row in `review_decisions.csv` proving
that Codex viewed the thumbnail, confirmed the scene, safety, real-world and
non-fixture status, and reviewed license/non-copyright boundaries. Only then may
this command succeed:

```powershell
.\.venv\Scripts\python.exe scripts/materialize_square_public_v2.py materialize-pilot
```

It requires one indivisible pilot60 with scene counts `10/10/10/10/7/7/6` and
availability-balanced pressure counts `40/12/8`. The complete held-out scene
counts are `40/40/40/40/27/26/27`; the complete full300 scene counts are
`50/50/50/50/34/33/33`. After bounded official-source expansion and the final
safety/near-duplicate review, the versioned pressure counts are held-out
`104/86/50` and full300 `144/98/58`. The evidence superseding the infeasible
original and first feasibility allocations is preserved in
`selection_policy.yaml`, `heldout_selection.yaml` and `STATUS.md`. A partial
method denominator remains invalid.

Chinese dense posters preferentially come from individually licensed Wikimedia
Commons files. Other scenes may use Commons or Open Images. Pixel files,
overviews and raw caches remain under `local_data/retarget_square_public_v2/`
and never enter Git.

The pilot and held-out split materialize the exact audited Commons evaluation rendition or the
official Open Images S3 validation pixel object already frozen in the candidate
pool. Its `source_url` therefore identifies those exact bytes and its
`raw_sha256` is rechecked against `review_sha256`. Higher-resolution Commons
original URLs remain represented by the upstream identity and licensing page;
they are optional generation-route assets and are not silently substituted into
the 1024 evaluation denominator. `source_manifest.csv` remains the byte-frozen
pilot; `heldout240_source_manifest.csv` and `full300_source_manifest.csv` are
new non-overwriting artifacts.
