# ADR 0001: Keep the image engine in Python and expose stable seams for later Java integration

- Status: Accepted
- Date: 2026-08-18

## Context

The engine depends on OpenCV, OCR, object detectors, numerical optimization, local VLM inference
and an external image-generation provider. Reimplementing these algorithms in Java before the
prototype is calibrated would duplicate risk and slow visual iteration. At the same time, the
future business application is expected to be Java-based.

## Decision

Keep generation, protection analysis, evaluation, Agent routing and review-domain rules in Python.
Place stable interfaces at dataset, candidate-method, Agent-backend, AIGC-provider and artifact-
store seams. Persist immutable JSON contracts and relative artifact references. Do not implement a
production Java endpoint in the current milestone; document how an asynchronous Java orchestrator
can call the engine later without knowing algorithm internals.

## Consequences

- Current work can concentrate on algorithm and evaluation validity.
- Java developers receive explicit contracts instead of Python implementation details.
- A later HTTP/queue adapter is additive.
- The Python import namespace remains compatible with the source project during this milestone.
