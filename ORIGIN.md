# Origin and clean-import record

`retarget-abillity` is the clean handoff distribution of the private `retarget-engine` work. It
keeps the stable Python distribution name `retarget-engine` and import namespace `retarget_agent`.

| Field | Value |
|---|---|
| Imported repository | `mayin0902/retarget-engine` |
| Imported branch | `agent/movie60-handoff` |
| Imported commit | `39ae563` plus the 0.3.0 package metadata correction in this repository |
| Import date | 2026-08-20 |
| Import form | Curated code, tests, current docs, immutable strategies and non-pixel manifests |

The clean import intentionally excludes master prompts, Grill records, personal collaboration
notes, historical Run bytes, local images, model weights, credentials, deliverable working trees
and uncommitted files. Authorized Movie60 pixels and review evidence are distributed only through
the private, checksum-pinned GitHub Release.

Major changes after the import are recorded in normal Git commits and `CHANGELOG.md`.
