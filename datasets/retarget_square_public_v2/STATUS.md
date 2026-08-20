# V2 materialization status

Access date: `2026-08-12`

`retarget_square_public_v2` is complete at both denominators. The frozen
`pilot60` remains byte-identical. The new `heldout240` contains exactly 240
visually reviewed, non-sensitive, public real images and `full300` is the exact
union of those 240 rows and the original 60 pilot rows. No generated fixture is
part of either denominator.

Scene counts are exact:

| scene | heldout240 | full300 |
| --- | ---: | ---: |
| chinese_dense_poster | 40 | 50 |
| single_product_promo | 40 | 50 |
| multi_product_commercial | 40 | 50 |
| multi_person | 40 | 50 |
| portrait | 27 | 34 |
| landscape_architecture_structure | 26 | 33 |
| complex_mixed | 27 | 33 |

The pressure allocation is versioned as `heldout=104/86/50` and
`full300=144/98/58` for hard-1/hard-2/extreme. The originally requested
held-out allocation `50/138/52` was infeasible under the fixed scenes, public
license, safety and near-duplicate gates. A first `90/100/50` feasibility
proposal was superseded after the stricter people-scene safety and same-event
duplicate review. The frozen allocation is the measured feasible result: it
does not relabel wrong-scene images to manufacture a pressure target.

The final official-source candidate pool has 1,861 unique identities. 1,570
review objects were downloaded and 916 passed the pressure-specific pixel gate
(`hard_1=455`, `hard_2=327`, `extreme=134`). Every selected row was reviewed at
original-detail overview scale for scene, safety, real/non-fixture status,
license/author/attribution and non-copyright restrictions. All 240 held-out
rows are API-egress disabled; especially people, product, logo and text-bearing
content is not sent to a paid generation API by default.

The held-out/full pHash minimum is 6. The held-out minimum distance against
the old 12-image real Smoke and the downloaded V1 sample is 17. The final pass
also removed two near-duplicate Benz advertisements, one pilot-overlap event
image and all exact V1 overlaps. Raw and materialized SHA-256 values are stored
per row; EXIF/GPS metadata is absent from the lossless PNG materializations.

Reproduction and verification:

```powershell
.\.venv\Scripts\python.exe scripts/materialize_square_public_v2.py freeze-heldout-decisions
.\.venv\Scripts\python.exe scripts/materialize_square_public_v2.py materialize-heldout
.\.venv\Scripts\python.exe scripts/materialize_square_public_v2.py materialize-full
.\.venv\Scripts\retarget-agent.exe dataset validate local_data/retarget_square_public_v2/heldout240
.\.venv\Scripts\retarget-agent.exe dataset validate local_data/retarget_square_public_v2/full300
.\.venv\Scripts\python.exe -m pytest tests/test_square_public_dataset_v2.py -q
```

Pixels and raw caches remain in Git-ignored
`local_data/retarget_square_public_v2/`. Git stores the discovery/materialize
script, exact pool, 300 review decisions, split/full manifests, audits, tasks,
selection evidence and this status record. The two materialize commands are
non-overwriting with respect to the frozen pilot manifest and fail if its hash
or scene contract changes.
