# Changelog

## 0.5.0 - 2026-08-21

- Add immutable Movie60 v3, v3.1 and v3.2 scoring, Prompt, Skill and override bundles while
  retaining v1/v2/v2.1 hashes and Run snapshots.
- Add human-screened-proxy scoring with transparent soft penalties and declarative scene/method
  gates; task IDs and filenames cannot participate in policy matching.
- Add frozen-metric Strategy Replay, label-free 45/15 task splitting, five-fold development
  diagnostics, C/D recall, ordinal ranking and same-grade tolerance reports.
- Force Rule Top1 into high-resolution review, allow up to two visually distinct Agent
  challengers, and persist separate Rule, Agent and Combined grades, confidence and evidence.
- Add v3.2.1 grading-only replay and v3.2.2 deployment freeze: Rule remains the final selector
  while the visual Agent supplies auditable advisory evidence after the proxy holdout rejected
  automatic Agent overrides.
- Add v2 private Release asset names and curated v3 Agent evidence without overwriting v1.

## 0.4.0 - 2026-08-20

- Add allowlisted registries for detector suites, reference/no-reference scorers, selectors and
  Agent backends; strategy files choose implementations without importing arbitrary Python paths.
- Add immutable Movie60 StrategyBundle v2.1 with external, hashed prompt templates while retaining
  v1/v2 loading and replay compatibility.
- Add the CPU-first `company_cpu_v2` profile: PP-OCRv6 small through ONNX Runtime, pinned D-FINE
  Nano, YuNet and logo-candidate analysis, plus auditable model materialization.
- Add standalone candidate scoring and source-versus-candidate scoring with JSON, Markdown,
  overlays, strategy snapshots and optional image-review Agent output.
- Replace the accumulated setup notes with a clean Windows installation, single/batch run,
  plug-in strategy and developer handoff path.

## 0.3.0 - 2026-08-20

- Add immutable StrategyBundle v1/v2 directories for scoring, Rule ranking, override gates and
  Agent Skill.
- Make A/B/C/D score ranges and primary Quality weights configurable without Python edits.
- Snapshot every strategy file and SHA-256 into Evaluation, Agent and strict-review artifacts.
- Add frozen Python constraints, audited Movie60 Release materialization and Windows bootstrap.
- Add a verified one-command seven-method single-image Rule workflow.

## 0.2.0 - in progress

- Clean import from `retarget-agent` commit `2ede363`.
- Add uncapped forward-energy `seam_full` with original-resolution coordinate remapping.
- Add protected two-dimensional `mesh_full` optimization and `seam_scale` hybrid.
- Preserve the original capped `seam` and separable `mesh` as legacy baselines.
- Add versioned Qwen4 selector Skill.
- Add `cn_square_v2` with capped Seam and legacy Mesh restored as first-class candidates.
- Preserve source aspect ratio in Agent comparison grids and render all seven candidates.
- Add selector Skill v3 with dynamic aliases, advisory `UNSAFE` semantics and local-distortion review.
- Add selector Skill v2 and a Schema-enforced candidate-alias boundary after CN60 replay exposed
  `SOURCE` alias confusion and insufficient 4096-token deployment context.
- Extend review grades to A/B/C/D/Skip with six required detail dimensions.
- Remove the legacy Streamlit adapter and text-repaste path from the clean project.
