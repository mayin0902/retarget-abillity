"""Assemble and zip the self-contained AIGC30 evidence package."""

from __future__ import annotations

import hashlib
import json
import shutil
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RUN = ROOT / "runs/aigc30-seedream5-v3-20260812"
OUTPUT = ROOT / "local_data/deliverables/retarget-agent-aigc30-20260812-v7"
ZIP = OUTPUT.with_suffix(".zip")


def _copy(source: Path, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, target)


def main() -> None:
    if not OUTPUT.is_dir():
        raise FileNotFoundError(OUTPUT)
    if ZIP.exists():
        raise FileExistsError(ZIP)

    for source in (
        ROOT / "docs/runs/aigc30-seedream5-20260812.md",
        ROOT / "docs/runs/aigc-rescue-estimate-20260812.md",
        ROOT / "docs/runs/square-public-v2-full300-20260812-detailed.md",
        ROOT / "docs/reviews/aigc30-codex-visual-review.md",
        ROOT / "docs/reviews/aigc30-codex-visual-review.csv",
        ROOT / "datasets/retarget_square_public_v2/aigc30_selection.yaml",
        ROOT / "datasets/retarget_square_public_v2/aigc30_source_audit.csv",
    ):
        _copy(source, OUTPUT / "reports-and-audit" / source.name)

    evidence = OUTPUT / "machine-readable-evidence"
    _copy(
        ROOT / "local_data/aigc-rescue-estimate-v2.json",
        evidence / "full300-aigc-rescue-estimate-v2.json",
    )
    _copy(RUN / "run-summary.json", evidence / "run-summary.json")
    shutil.copytree(RUN / "results", evidence / "generation-results")
    shutil.copytree(
        RUN / "evaluations/auto-proxy-v1p1-aigc30-20260812",
        evidence / "automatic-evaluation",
    )
    shutil.copytree(
        RUN / "benchmarks/aigc30-api-only-v1",
        evidence / "route-benchmark",
    )

    full_outputs = OUTPUT / "all-aigc-successes-2048"
    evaluation_outputs = OUTPUT / "all-aigc-successes-1024-evaluation"
    full_outputs.mkdir(parents=True, exist_ok=True)
    evaluation_outputs.mkdir(parents=True, exist_ok=True)
    success_count = 0
    for result_path in sorted((RUN / "results").glob("*.json")):
        result = json.loads(result_path.read_text(encoding="utf-8"))
        if result["status"] != "success":
            continue
        success_count += 1
        task_id = result["task_id"]
        _copy(
            RUN / result["provider_output_path"],
            full_outputs / f"{task_id}__seedream5-2048.jpg",
        )
        _copy(
            RUN / result["evaluation_image_path"],
            evaluation_outputs / f"{task_id}__seedream5-1024.png",
        )

    code = OUTPUT / "code-snapshot"
    for directory in ("src", "scripts", "tests", "configs", ".streamlit"):
        source = ROOT / directory
        if source.is_dir():
            shutil.copytree(
                source,
                code / directory,
                ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
            )
    for filename in ("pyproject.toml", "README.md", "AGENTS.md", "CONTEXT.md"):
        _copy(ROOT / filename, code / filename)

    readme = f"""# Retarget Agent AIGC30 evidence package

This package contains the complete 30-task pure-AIGC and hybrid comparison.

- 30 paid-call terminal records: 21 success, 9 failure.
- {success_count} original SeedDream 2048x2048 JPEG outputs.
- {success_count} normalized 1024x1024 evaluation PNG outputs.
- 14 representative task folders, each with Source, four ranked traditional candidates,
  Qwen4 selection, SeedDream output/failure, metrics, license and review reasons.
- Detailed Chinese technical report and Codex visual-review table.
- Machine-readable generation, evaluation and route benchmark evidence.
- Source-code snapshot. GitHub remains the authoritative versioned source.

Costs are estimates because the provider did not return actual billed amounts. AIGC30
costs 8.70-17.40 CNY. The Full300 Qwen4-without-AIGC baseline is 279/300 (93.0%);
Qwen4 Hybrid is estimated at 289/300 (96.3%) with 21 AIGC calls and 6.0-12.0 CNY.
Agent token cost is zero in the requested company scenario.
"""
    (OUTPUT / "DELIVERABLE_README.md").write_text(readme, encoding="utf-8")

    checksum_lines = []
    for path in sorted(item for item in OUTPUT.rglob("*") if item.is_file()):
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        checksum_lines.append(f"{digest}  {path.relative_to(OUTPUT).as_posix()}")
    (OUTPUT / "SHA256SUMS.txt").write_text("\n".join(checksum_lines) + "\n", encoding="ascii")

    with zipfile.ZipFile(ZIP, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        for path in sorted(item for item in OUTPUT.rglob("*") if item.is_file()):
            archive.write(path, path.relative_to(OUTPUT.parent))
    payload = {
        "directory": str(OUTPUT),
        "zip": str(ZIP),
        "zip_bytes": ZIP.stat().st_size,
        "zip_sha256": hashlib.sha256(ZIP.read_bytes()).hexdigest(),
        "files": sum(1 for path in OUTPUT.rglob("*") if path.is_file()),
        "full_2048_outputs": success_count,
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
