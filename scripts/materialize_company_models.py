"""Materialize and audit the current Windows CPU detector profile.

The command intentionally writes only below the Git-ignored ``models/`` tree.
It never disables TLS verification and pins the D-FINE repository revision in
code/profile metadata. PaddleOCR resolves the two official PP-OCRv6 model names.
"""

from __future__ import annotations

import argparse
import importlib.metadata
import json
from datetime import UTC, datetime
from pathlib import Path

from retarget_agent.config import AnalysisConfig
from retarget_agent.hashing import sha256_file
from retarget_agent.protection_detectors import CompanyCpuProtectionDetectorSuite


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model-root", type=Path, default=Path("models/analyzers"))
    parser.add_argument(
        "--check-only",
        action="store_true",
        help="Forbid D-FINE network download and verify the existing cache.",
    )
    args = parser.parse_args()
    profile = Path("datasets/analyzer_models_company_cpu_v2/profile.yaml").resolve()
    config = AnalysisConfig(
        detector_mode="required",
        detector_suite_plugin="company_cpu_v2",
        model_root=str(args.model_root),
    )
    suite = CompanyCpuProtectionDetectorSuite(
        config,
        allow_model_download=not args.check_only,
    )
    versions = {}
    for package in (
        "paddleocr",
        "paddlepaddle",
        "onnxruntime",
        "torch",
        "torchvision",
        "transformers",
    ):
        versions[package] = importlib.metadata.version(package)
    output = args.model_root.resolve() / "company_cpu_v2" / "materialization.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema_version": "1.0",
        "profile_id": "company_cpu_v2",
        "profile_sha256": sha256_file(profile),
        "materialized_at": datetime.now(UTC).isoformat(),
        "package_versions": versions,
        "analyzer_ids": list(suite.analyzer_ids),
        "model_assets": list(suite.model_audits),
        "model_root": str(args.model_root.resolve()),
        "network_download_allowed": not args.check_only,
    }
    temporary = output.with_suffix(".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temporary.replace(output)
    print(json.dumps(payload, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
