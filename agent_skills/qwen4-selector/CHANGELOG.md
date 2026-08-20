# Qwen4 selector skill changelog

## 2.3.0

- Requires every free-text output value to use concise Simplified Chinese.
- Keeps JSON keys and stable reason-code identifiers unchanged for compatibility.
- Leaves the Rule-first challenger and high-resolution pair gate unchanged.

## 2.2.0

- Adds an explicit challenger candidate separate from the overall ranking head.
- Records whether the nominated challenger preserves core content.
- Keeps Rule as the default while guaranteeing an auditable candidate for pair review.

## 2.1.0

- Clarifies that overview Top1 is a challenger proposal, not the final override.
- Surfaces a plausible challenger when it fixes visible Rule deformation without content loss.
- Leaves every final override to the separate high-resolution, fail-closed pair gate.

## 2.0.0

- Treats the complete Rule ranking and Rule Top1 as an explicit trusted prior.
- Limits the overview model to proposing one challenger; the Rule candidate remains the default.
- Requires high-resolution Rule-vs-challenger evidence before an override.
- Blocks overrides when core content, critical text, or protected-subject counts regress.
- Falls back to Rule whenever visual and deterministic evidence conflict.

## 1.2.0

- Supports a dynamic candidate count instead of hard-coding five aliases.
- Treats `UNSAFE` as advisory risk evidence rather than an automatic grade ceiling.
- Requires local distortion inspection for text, people, products, logos and structure lines.
- Requires comparison against the aspect-preserved source preview.

## 1.1.0

- Restricts model output to candidate aliases and excludes `SOURCE` from the ranking.
- Keeps `best_candidate_alias` equal to the first ranked alias.

## 1.0.0

- Initial frozen policy for the 20-image calibration and 40-image validation protocol.
- Uses source semantics, shared protection evidence, candidate images and deterministic metrics.
- Requires an explicit all-traditional-unusable finding before requesting paid generation.

New versions must be added in a new directory. Existing versions are immutable run inputs.
