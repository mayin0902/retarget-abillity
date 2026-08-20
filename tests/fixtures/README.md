# Programmatic fixtures

`scripts/materialize_fixtures.py` generates deterministic synthetic images below `tests/fixtures/generated/`.

These images are only for unit tests, plugin contracts, failure isolation, resume/replay checks, and algorithm stress tests. They are not real-world samples and must never be counted in `retarget_smoke_real_v1`, baseline, Pilot, or business-quality statistics.

The versioned catalog is `tests/fixtures/programmatic_fixture_catalog.csv`; generated pixels and runtime manifests are Git ignored.
