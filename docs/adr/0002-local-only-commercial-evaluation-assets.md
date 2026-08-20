# ADR 0002: Keep recent commercial Chinese assets local-only by default

- Status: Accepted
- Date: 2026-08-18

## Context

Recent movie, series, event and product posters are representative of the target business, but a
public official page does not automatically grant redistribution or third-party AIGC upload rights.

## Decision

Store pixels under Git-ignored `local_data/`. Commit only source manifests, download/materialize
code, hashes, audit state and attribution instructions. Treat download, redistribution and API
egress as independent decisions. Default commercial and identifiable-person assets to
`LOCAL_ONLY` and `api_egress_allowed=false`.

## Consequences

- The local benchmark can reflect Chinese business scenes without misrepresenting license status.
- Public reproducibility relies on the smaller item-by-item CC/PD subset.
- SeedDream experiments may cover fewer than 20 tasks if the egress gate does not pass enough
  sources; the report must show that denominator explicitly.
