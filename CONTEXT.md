# Retarget Engine Domain Language

- **Source**: an immutable input image identified by `source_id`, SHA-256, pixel dimensions and
  provenance.
- **Target**: the requested output canvas. The current product profile is one 1536×1536 square.
- **Task**: one Source-to-Target request with a stable `task_id`.
- **Shared Protection Analysis**: one versioned reading of OCR text, faces, people, products,
  Logo candidates, structure and saliency shared by every candidate method for the Task.
- **Protection Region**: an identified source area with keep importance, deformation tolerance
  and a semantic kind. It is evidence, not a guarantee that a method is visually acceptable.
- **Candidate**: one immutable method output for a Task. Technical success is not quality success.
- **Traditional Score**: deterministic, replayable quality and risk measurements for a Candidate.
- **Agent Skill**: a frozen, versioned judging policy that tells the visual Agent how to compare
  the Source, Candidates, Shared Protection Analysis and Traditional Scores. Existing versions
  never mutate.
- **Agent Decision**: a complete Candidate ranking, selected traditional fallback, confidence,
  reasons and route action produced under one Agent Skill version.
- **AIGC Request**: a paid, policy-gated request used only when no traditional Candidate is usable.
- **Route Result**: the final choice after Agent ranking, optional AIGC generation, AIGC review and
  mandatory traditional fallback.
- **Large-model Pre-review**: a provisional A/B/C/D/Skip review made by a large multimodal model.
  It is not a human gold label.
- **Review**: an append-only Reviewer judgement over a frozen Candidate, including six dimension
  grades, issue reasons and an optional task-best choice.
- **Calibration Split**: 20 Sources whose pre-review may be used to propose scoring or Agent Skill
  changes.
- **Validation Split**: 40 frozen Sources used once to test those changes; it must not be used to
  tune them.
