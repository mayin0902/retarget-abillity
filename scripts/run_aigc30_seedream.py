"""Run the paid, policy-audited SeedDream AIGC30 benchmark.

Credentials are accepted only from the SeedDream provider's documented runtime
environment variables.  Existing success/failure cache records suppress duplicate
paid submissions, so resuming the script never silently retries an uncertain call.
"""

from __future__ import annotations

import argparse
import base64
import csv
import hashlib
import json
import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from decimal import Decimal
from pathlib import Path
from typing import Any

import yaml
from PIL import Image, ImageOps

from retarget_agent.costing import BudgetLedger
from retarget_agent.providers.seedream import (
    SeedDreamGenerationRequest,
    SeedDreamProvider,
    SeedDreamProviderConfig,
    SeedDreamProviderError,
)

PROMPT = """Retarget the provided source image into a high-quality square 1:1 composition.
Preserve the original main subject, every important person and product, and all visible Chinese
or English text exactly as written. Preserve logos, prices, buttons, badges, faces, hands,
architecture and straight structural lines. Recompose or extend only unimportant background as
needed. Do not invent, rewrite, translate, remove or duplicate text or subjects. Avoid stretching,
cropping important content, blur, watermark, frames and obvious generative artifacts. Return one
clean square image faithful to the source."""
PROMPT_VERSION = "aigc30-faithful-square-v1"


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True, default=str) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def _normalize_square(source: Path, output: Path) -> dict[str, Any]:
    with Image.open(source) as opened:
        original_size = opened.size
        image = ImageOps.exif_transpose(opened).convert("RGB")
        if image.size != (1024, 1024):
            image = image.resize((1024, 1024), Image.Resampling.LANCZOS)
        output.parent.mkdir(parents=True, exist_ok=True)
        image.save(output, format="PNG", optimize=True)
    return {
        "provider_width": original_size[0],
        "provider_height": original_size[1],
        "evaluation_width": 1024,
        "evaluation_height": 1024,
        "evaluation_sha256": hashlib.sha256(output.read_bytes()).hexdigest(),
    }


def _source_data_uri(source_id: str, expected_sha256: str) -> str:
    path = Path("local_data/retarget_square_public_v2/full300/images") / f"{source_id}.png"
    payload = path.read_bytes()
    actual_sha256 = hashlib.sha256(payload).hexdigest()
    if actual_sha256 != expected_sha256:
        raise RuntimeError(f"materialized source hash mismatch: {source_id}")
    return "data:image/png;base64," + base64.b64encode(payload).decode("ascii")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--selection",
        type=Path,
        default=Path("datasets/retarget_square_public_v2/aigc30_selection.yaml"),
    )
    parser.add_argument(
        "--audit",
        type=Path,
        default=Path("datasets/retarget_square_public_v2/aigc30_source_audit.csv"),
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("runs/aigc30-seedream5-v3-20260812"),
    )
    parser.add_argument("--run-id", default="aigc30-seedream5-v3-20260812")
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--workers", type=int, default=4)
    args = parser.parse_args()
    if args.limit is not None and args.limit <= 0:
        raise ValueError("--limit must be positive")
    if not 1 <= args.workers <= 8:
        raise ValueError("--workers must be between 1 and 8")

    selection = yaml.safe_load(args.selection.read_text(encoding="utf-8"))
    tasks = selection["tasks"]
    if len(tasks) != 30:
        raise RuntimeError(f"AIGC30 selection must have 30 tasks, got {len(tasks)}")
    with args.audit.open("r", encoding="utf-8-sig", newline="") as handle:
        audit_by_task = {row["task_id"]: row for row in csv.DictReader(handle)}
    if set(audit_by_task) != {item["task_id"] for item in tasks}:
        raise RuntimeError("selection and source audit task sets differ")
    for row in audit_by_task.values():
        if (
            row["aigc30_api_egress_allowed"].lower() != "true"
            or row["public_release_eligible"].lower() != "true"
            or row["license_review_status"] != "approved"
            or row["content_safety_status"] != "approved"
        ):
            raise RuntimeError(f"policy audit rejected {row['task_id']}")

    # Match the verified image-to-image example in the user-provided provider
    # documentation. The provider may reject `watermark=false` for this model.
    config = SeedDreamProviderConfig.from_env(size="2K", watermark=True)
    output_dir = args.output_dir.resolve()
    budget = BudgetLedger(Decimal("18.00"), "CNY")
    results_dir = output_dir / "results"
    evaluation_images = output_dir / "evaluation_images"
    pending: list[tuple[int, dict[str, Any]]] = []
    for index, task in enumerate(tasks, 1):
        task_id = task["task_id"]
        result_path = results_dir / f"{task_id}.json"
        if result_path.exists():
            existing = json.loads(result_path.read_text(encoding="utf-8"))
            if existing.get("status") in {"success", "failed"}:
                print(f"aigc30={index}/30 cached_result={existing['status']}", flush=True)
                continue
        if args.limit is not None and len(pending) >= args.limit:
            break
        pending.append((index, task))

    def run_one(index: int, task: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        task_id = task["task_id"]
        result_path = results_dir / f"{task_id}.json"
        audit = audit_by_task[task_id]
        provider = SeedDreamProvider(
            config,
            output_root=output_dir / "provider_outputs",
            cache_path=output_dir / "provider_cache" / "tasks" / f"{task_id}.json",
            budget=budget,
        )
        request = SeedDreamGenerationRequest(
            task_id=task_id,
            run_id=args.run_id,
            request_id=f"aigc30-request-{index:02d}",
            source_data_uri=_source_data_uri(task["source_id"], audit["materialized_sha256"]),
            source_sha256=audit["materialized_sha256"],
            source_is_public=True,
            allow_data_egress=True,
            target_width=1024,
            target_height=1024,
            target_format="png",
            prompt=PROMPT,
            prompt_version=PROMPT_VERSION,
            max_cost_cny=Decimal("0.60"),
        )
        started = time.perf_counter()
        try:
            generated = provider.generate(request)
            normalized_path = evaluation_images / f"{task_id}.png"
            normalization = _normalize_square(generated.output_path, normalized_path)
            record = {
                "status": "success",
                "benchmark_id": selection["benchmark_id"],
                "task_id": task_id,
                "source_id": task["source_id"],
                "scene_category": task["scene_category"],
                "difficulty_tier": task["difficulty_tier"],
                "provider_id": generated.provider_id,
                "provider_version": generated.provider_version,
                "provider_model": os.environ.get("SEEDREAM_MODEL"),
                "request_hash": generated.request_hash,
                "prompt_sha256": request.prompt_sha256,
                "prompt_version": PROMPT_VERSION,
                "source_transport": "base64_data_uri",
                "cache_hit": generated.cache_hit,
                "provider_output_path": generated.output_path.relative_to(output_dir).as_posix(),
                "provider_output_sha256": generated.output_sha256,
                "media_type": generated.media_type,
                "evaluation_image_path": normalized_path.relative_to(output_dir).as_posix(),
                **normalization,
                "wall_seconds": time.perf_counter() - started,
                "estimated_cost_min_cny": str(generated.estimated_cost_min_cny),
                "estimated_cost_max_cny": str(generated.estimated_cost_max_cny),
                "actual_cost_cny": (
                    str(generated.actual_cost_cny)
                    if generated.actual_cost_cny is not None
                    else None
                ),
                "cost_basis": "estimated_range_provider_actual_unavailable",
            }
        except SeedDreamProviderError as error:
            record = {
                "status": "failed",
                "benchmark_id": selection["benchmark_id"],
                "task_id": task_id,
                "source_id": task["source_id"],
                "scene_category": task["scene_category"],
                "difficulty_tier": task["difficulty_tier"],
                "provider_model": os.environ.get("SEEDREAM_MODEL"),
                "prompt_sha256": request.prompt_sha256,
                "prompt_version": PROMPT_VERSION,
                "source_transport": "base64_data_uri",
                "wall_seconds": time.perf_counter() - started,
                "error_code": error.code.value,
                "error_type": type(error).__name__,
                "charge_may_have_occurred": error.charge_may_have_occurred,
                "estimated_cost_min_cny": "0.30" if error.charge_may_have_occurred else "0.00",
                "estimated_cost_max_cny": "0.60" if error.charge_may_have_occurred else "0.00",
                "actual_cost_cny": None,
                "cost_basis": "estimated_range_provider_actual_unavailable",
            }
        _write_json(result_path, record)
        return index, record

    with ThreadPoolExecutor(max_workers=args.workers) as executor:
        futures = {executor.submit(run_one, index, task): index for index, task in pending}
        for future in as_completed(futures):
            index, record = future.result()
            print(f"aigc30={index}/30 result={record['status']}", flush=True)

    records = [
        json.loads(path.read_text(encoding="utf-8")) for path in sorted(results_dir.glob("*.json"))
    ]
    success = [item for item in records if item["status"] == "success"]
    failed = [item for item in records if item["status"] == "failed"]
    summary = {
        "benchmark_id": selection["benchmark_id"],
        "run_id": args.run_id,
        "required_task_count": 30,
        "recorded_task_count": len(records),
        "success_count": len(success),
        "failure_count": len(failed),
        "complete": len(records) == 30,
        "prompt_version": PROMPT_VERSION,
        "prompt_sha256": hashlib.sha256(PROMPT.encode("utf-8")).hexdigest(),
        "provider_model": os.environ.get("SEEDREAM_MODEL"),
        "estimated_cost_min_cny": sum(Decimal(item["estimated_cost_min_cny"]) for item in records),
        "estimated_cost_max_cny": sum(Decimal(item["estimated_cost_max_cny"]) for item in records),
        "actual_cost_cny": None,
        "actual_cost_note": "The provider response does not return per-image billed cost.",
        "wall_seconds_sum": sum(float(item["wall_seconds"]) for item in records),
    }
    _write_json(output_dir / "run-summary.json", summary)
    print(json.dumps(summary, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
