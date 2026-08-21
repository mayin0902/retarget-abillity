"""One auditable readiness check for developers and the Windows launcher."""

from __future__ import annotations

import importlib.util
import platform
import sys
from pathlib import Path
from typing import Any

from .defaults import current_strategy_path, project_root
from .strategy import load_strategy_bundle


def _dependency(name: str) -> dict[str, Any]:
    available = importlib.util.find_spec(name) is not None
    return {"name": name, "available": available}


def _file_check(root: Path, relative_path: str) -> dict[str, Any]:
    path = root / relative_path
    return {"path": relative_path, "available": path.is_file()}


def run_doctor(start: Path | None = None) -> dict[str, Any]:
    """Report core, generation and optional Agent readiness without mutating disk."""
    root = project_root(start)
    core_dependencies = [
        _dependency(name)
        for name in ("fastapi", "numpy", "cv2", "PIL", "pydantic", "yaml")
    ]
    core_files = [
        _file_check(root, "configs/default.yaml"),
        _file_check(root, "src/retarget_agent/web_movie60/index.html"),
        _file_check(root, "src/retarget_agent/web_movie60/app.js"),
        _file_check(root, "src/retarget_agent/web_movie60/app.css"),
    ]
    strategy: dict[str, Any]
    try:
        loaded = load_strategy_bundle(current_strategy_path(root))
        strategy = {
            "available": True,
            "id": f"{loaded.bundle.strategy_id}@{loaded.bundle.version}",
            "sha256": loaded.source_sha256,
        }
    except (FileNotFoundError, KeyError, TypeError, ValueError) as error:
        strategy = {"available": False, "error": str(error)}

    company_dependencies = [
        _dependency(name)
        for name in ("paddleocr", "paddle", "onnxruntime", "torch", "transformers")
    ]
    materialization = root / "models" / "analyzers" / "company_cpu_v2" / "materialization.json"
    company_models_ready = materialization.is_file() and all(
        item["available"] for item in company_dependencies
    )
    core_ready = (
        (3, 11) <= sys.version_info[:2] < (3, 14)
        and all(item["available"] for item in core_dependencies)
        and all(item["available"] for item in core_files)
        and bool(strategy["available"])
    )
    return {
        "schema_version": "1.0",
        "status": "READY" if core_ready else "NOT_READY",
        "ready": core_ready,
        "project_root": str(root),
        "python": {
            "executable": sys.executable,
            "version": platform.python_version(),
            "supported": (3, 11) <= sys.version_info[:2] < (3, 14),
        },
        "core_dependencies": core_dependencies,
        "core_files": core_files,
        "strategy": strategy,
        "capabilities": {
            "review_ui": core_ready,
            "rule_scoring": core_ready,
            "generation_with_company_models": core_ready and company_models_ready,
            "agent": {
                "ready": False,
                "reason": "Agent requires an explicitly configured private endpoint profile.",
            },
            "aigc": {
                "ready": False,
                "reason": "AIGC is never enabled by the default local workflow.",
            },
        },
        "company_models": {
            "ready": company_models_ready,
            "materialization_record": str(materialization.relative_to(root)),
            "dependencies": company_dependencies,
            "remediation": (
                None
                if company_models_ready
                else "Run scripts\\bootstrap_windows.ps1 to install and materialize models."
            ),
        },
    }
