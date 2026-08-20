"""Bounded SeedDream routing experiment for Movie Visual 60."""

from __future__ import annotations

import base64
import hashlib
import json
import os
import time
from collections import Counter
from decimal import Decimal
from pathlib import Path
from typing import TYPE_CHECKING, Any

import cv2
import numpy as np
import yaml
from PIL import Image, ImageOps

from .agents import deterministic_ranking, evidence_from_metrics
from .config import AnalysisConfig
from .costing import BudgetLedger
from .evaluation import EvaluationConfig, compute_proxy_metrics
from .models import AnalysisArtifact, RunManifest, TaskSpec
from .movie_visual60 import EGRESS_AUTHORIZATION
from .protection_detectors import ProtectionDetectorSuite
from .providers.seedream import (
    SeedDreamGenerationRequest,
    SeedDreamProvider,
    SeedDreamProviderConfig,
    SeedDreamProviderError,
)
from .storage import LocalArtifactStore
from .strict_review import (
    StrictCandidateReview,
    StrictVisionReviewBackend,
    build_pairwise_review_sheet,
)

if TYPE_CHECKING:
    from .plugin_catalog import PluginCatalog
    from .strategy import LoadedStrategyBundle

SEEDREAM_PROMPT = """Retarget the provided source image into a high-quality square 1:1 composition.
Preserve the exact identity and natural geometry of every important person, face, body, product,
logo and structural line. Preserve every visible Simplified-Chinese or English character, number,
price, date, button and badge exactly as written. Do not translate, rewrite, remove, duplicate or
invent content. Recompose or extend only unimportant background. Avoid stretching, seam-like
artifacts, blur, frames and watermark. Return one faithful, directly usable square image."""
PROMPT_VERSION = "movie60-faithful-square-v1"
HUMAN_CALIBRATION_FEEDBACK_VERSION = "user-feedback-v1"


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def load_aigc_human_calibration_feedback(
    run_dir: Path,
    task_id: str,
    candidate_id: str,
    candidate_sha256: str | None,
) -> dict[str, Any] | None:
    """Load an explicit human AIGC label without mutating the machine review."""

    path = (
        run_dir
        / "external-generation"
        / "human-calibration"
        / HUMAN_CALIBRATION_FEEDBACK_VERSION
        / f"{task_id}.json"
    )
    if not path.is_file():
        return None
    feedback = _read_json(path)
    if feedback.get("task_id") != task_id or feedback.get("candidate_id") != candidate_id:
        raise ValueError(f"human calibration identity mismatch: {path}")
    if feedback.get("human_grade") not in {"A", "B", "C", "D"}:
        raise ValueError(f"human calibration grade is invalid: {path}")
    if feedback.get("feedback_source") != "user_explicit_visual_review":
        raise ValueError(f"human calibration source is not explicit user review: {path}")
    if candidate_sha256 and feedback.get("candidate_sha256") != candidate_sha256:
        raise ValueError(f"human calibration candidate hash mismatch: {path}")
    return feedback


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, default=str) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def _split_codes(value: object) -> set[str]:
    return {item for item in str(value or "").split("|") if item}


def _automatic_material_defects(metrics: dict[str, Any]) -> set[str]:
    defects = _split_codes(metrics.get("hard_failures")) | _split_codes(
        metrics.get("critical_regressions")
    )
    thresholds = (
        ("ocr_character_recall", 0.85, "text_damage"),
        ("face_count_preservation", 0.95, "face_loss"),
        ("person_count_preservation", 0.95, "person_loss"),
        ("product_count_preservation", 0.95, "product_loss"),
        ("structure_line_similarity", 0.72, "structure_damage"),
        ("transform_safety_score", 0.72, "geometry_risk"),
    )
    for field, threshold, code in thresholds:
        value = metrics.get(field)
        if isinstance(value, (float, int)) and float(value) < threshold:
            defects.add(code)
    return defects


def should_rule_request_aigc(
    top2: tuple[dict[str, Any], dict[str, Any]],
) -> tuple[bool, tuple[str, ...]]:
    grades = tuple(str(item.get("proxy_grade")) for item in top2)
    if all(grade in {"proxy_c", "unknown"} for grade in grades):
        return True, ("rule_top2_both_proxy_c",)
    if grades == ("proxy_b", "proxy_b"):
        common = _automatic_material_defects(top2[0]) & _automatic_material_defects(top2[1])
        if common:
            return True, ("rule_top2_shared_material_b_defect",) + tuple(
                f"shared:{item}" for item in sorted(common)
            )
    return False, ()


def _task_candidate_metrics(
    run_dir: Path,
    evaluation_id: str,
    task_id: str,
    *,
    strategy_bundle: LoadedStrategyBundle | None = None,
    plugin_catalog: PluginCatalog | None = None,
) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]]]:
    rows: list[dict[str, Any]] = []
    by_id: dict[str, dict[str, Any]] = {}
    for path in sorted((run_dir / "candidates" / task_id).glob("*/candidate.json")):
        candidate = _read_json(path)
        metric = _read_json(
            run_dir
            / "evaluations"
            / evaluation_id
            / "metrics"
            / f"{candidate['candidate_id']}.json"
        )["metrics"]
        evidence = evidence_from_metrics(
            str(candidate["candidate_id"]), str(candidate["method_id"]), metric, candidate
        )
        row = {
            "candidate_id": evidence.candidate_id,
            "method_id": evidence.method_id,
            **metric,
        }
        rows.append(row)
        by_id[evidence.candidate_id] = row
    evidence_values = tuple(
        evidence_from_metrics(
            str(row["candidate_id"]),
            str(row["method_id"]),
            row,
            next(
                _read_json(path)
                for path in (run_dir / "candidates" / task_id).glob("*/candidate.json")
                if _read_json(path)["candidate_id"] == row["candidate_id"]
            ),
        )
        for row in rows
    )
    if strategy_bundle is None:
        ranking = deterministic_ranking(evidence_values)
    else:
        from .plugin_catalog import built_in_plugin_catalog

        catalog = plugin_catalog or built_in_plugin_catalog()
        ranking = catalog.selectors.get(strategy_bundle.bundle.rule_selector_plugin)(
            evidence_values, strategy_bundle.selection
        )
    return [by_id[candidate_id] for candidate_id in ranking], by_id


def _aigc_evaluation_root(
    run_dir: Path, strategy_bundle: LoadedStrategyBundle | None
) -> Path:
    if strategy_bundle is None:
        return run_dir / "external-generation" / "evaluation"
    key = f"{strategy_bundle.bundle.strategy_id}-{strategy_bundle.bundle.version}"
    return run_dir / "external-generation" / "evaluations" / key


def _aigc_prompt(
    strategy_bundle: LoadedStrategyBundle | None,
) -> tuple[str, str, str | None]:
    if (
        strategy_bundle is not None
        and strategy_bundle.prompts is not None
        and strategy_bundle.prompts.aigc_generation is not None
    ):
        template = strategy_bundle.prompts.aigc_generation
        return template.render(), template.spec.version, template.source_sha256
    return SEEDREAM_PROMPT, PROMPT_VERSION, None


def plan_movie60_aigc(
    run_dir: Path,
    evaluation_id: str,
    strict_run_id: str,
    plan_id: str,
    *,
    maximum_paid_calls: int = 20,
    calibration_cap: int = 8,
    validation_cap: int = 12,
    strategy_bundle: LoadedStrategyBundle | None = None,
    plugin_catalog: PluginCatalog | None = None,
) -> dict[str, Any]:
    """Freeze Rule/Qwen trigger decisions and their capped paid-task union."""

    if maximum_paid_calls != calibration_cap + validation_cap:
        raise ValueError("split caps must sum to the global paid-call cap")
    run_dir = run_dir.resolve()
    store = LocalArtifactStore(run_dir)
    run = RunManifest.model_validate(store.read_json("run.json"))
    output = run_dir / "external-generation" / "plans" / plan_id
    if output.exists():
        raise FileExistsError(output)
    if strategy_bundle is not None:
        strategy_bundle.snapshot_to(output / "strategy")
    generation_prompt, prompt_version, prompt_sha256 = _aigc_prompt(strategy_bundle)
    entries: list[dict[str, Any]] = []
    for task_id in run.task_ids:
        task = TaskSpec.model_validate(store.read_json(f"tasks/{task_id}.json"))
        rule_ranking, _ = _task_candidate_metrics(
            run_dir,
            evaluation_id,
            task_id,
            strategy_bundle=strategy_bundle,
            plugin_catalog=plugin_catalog,
        )
        rule_trigger, rule_reasons = should_rule_request_aigc((rule_ranking[0], rule_ranking[1]))
        strict_decision = _read_json(
            run_dir / "strict-reviews" / strict_run_id / "decisions" / f"{task_id}.json"
        )
        qwen_trigger = bool(strict_decision["request_external_aigc"])
        requested_by = []
        if rule_trigger:
            requested_by.append("rule_aigc")
        if qwen_trigger:
            requested_by.append("qwen4_aigc")
        entries.append(
            {
                "task_id": task_id,
                "source_id": task.source.source_id,
                "split": task.source.split,
                "scene_category": task.source.scene_category,
                "rule_selected_candidate_id": rule_ranking[0]["candidate_id"],
                "rule_selected_quality_score": rule_ranking[0].get("quality_score"),
                "rule_trigger": rule_trigger,
                "rule_trigger_reasons": list(rule_reasons),
                "qwen_selected_candidate_id": strict_decision["selected_candidate_id"],
                "qwen_selected_grade": strict_decision["selected_grade"],
                "qwen_trigger": qwen_trigger,
                "qwen_trigger_reasons": strict_decision["aigc_trigger_reasons"],
                "requested_by": requested_by,
                "selected_for_paid_generation": False,
                "paid_priority": None,
            }
        )
    requested = [item for item in entries if item["requested_by"]]
    selected: list[dict[str, Any]] = []
    for split, cap in (("calibration", calibration_cap), ("validation", validation_cap)):
        pool = [item for item in requested if item["split"] == split]
        pool.sort(
            key=lambda item: (
                -len(item["requested_by"]),
                float(item["rule_selected_quality_score"] or 101.0),
                item["task_id"],
            )
        )
        selected.extend(pool[:cap])
    if len(selected) > maximum_paid_calls:
        raise AssertionError("selected paid calls exceed global cap")
    for priority, item in enumerate(selected, start=1):
        item["selected_for_paid_generation"] = True
        item["paid_priority"] = priority
    selected_ids = {item["task_id"] for item in selected}
    for item in entries:
        if item["requested_by"] and item["task_id"] not in selected_ids:
            item["not_selected_reason"] = "split_or_global_paid_cap"
    smoke = next((item["task_id"] for item in selected if item["split"] == "calibration"), None)
    report = {
        "schema_version": "1.0",
        "plan_id": plan_id,
        "run_id": run.run_id,
        "evaluation_id": evaluation_id,
        "strict_run_id": strict_run_id,
        "task_count": len(entries),
        "requested_union_count": len(requested),
        "selected_paid_call_count": len(selected),
        "maximum_paid_calls": maximum_paid_calls,
        "calibration_cap": calibration_cap,
        "validation_cap": validation_cap,
        "estimated_cost_min_cny": str(Decimal("0.30") * len(selected)),
        "estimated_cost_max_cny": str(Decimal("0.60") * len(selected)),
        "smoke_task_id": smoke,
        "egress_authorization_basis": EGRESS_AUTHORIZATION,
        "watermark": False,
        "provider_hard_timeout_seconds": 300,
        "strategy_id": (
            strategy_bundle.bundle.strategy_id if strategy_bundle is not None else None
        ),
        "strategy_version": (
            strategy_bundle.bundle.version if strategy_bundle is not None else None
        ),
        "strategy_sha256": (
            strategy_bundle.source_sha256 if strategy_bundle is not None else None
        ),
        "aigc_prompt": generation_prompt,
        "aigc_prompt_version": prompt_version,
        "aigc_prompt_sha256": prompt_sha256,
        "entries": entries,
    }
    _write_json(output / "plan.json", report)
    return report


def _source_data_uri(path: Path, expected_sha256: str) -> str:
    payload = path.read_bytes()
    if hashlib.sha256(payload).hexdigest() != expected_sha256:
        raise ValueError("source pixels do not match frozen sha256")
    suffix = path.suffix.lower()
    media = "image/png" if suffix == ".png" else "image/jpeg"
    return f"data:{media};base64," + base64.b64encode(payload).decode("ascii")


def _normalize_provider_output(source: Path, destination: Path) -> dict[str, Any]:
    with Image.open(source) as opened:
        native = ImageOps.exif_transpose(opened).convert("RGB")
        native_size = native.size
        if native.width != native.height:
            raise ValueError("SeedDream output is not square")
        evaluation = native.resize((1536, 1536), Image.Resampling.LANCZOS)
        destination.parent.mkdir(parents=True, exist_ok=True)
        evaluation.save(destination, format="PNG", optimize=True)
    return {
        "native_width": native_size[0],
        "native_height": native_size[1],
        "evaluation_width": 1536,
        "evaluation_height": 1536,
        "evaluation_sha256": hashlib.sha256(destination.read_bytes()).hexdigest(),
    }


def run_seedream_plan(
    run_dir: Path,
    plan_id: str,
    *,
    limit: int,
    budget_cny: Decimal = Decimal("12.00"),
    read_timeout_seconds: float = 300.0,
) -> dict[str, Any]:
    """Run a prefix of the frozen plan; limit=1 is the mandatory paid smoke."""

    if limit <= 0 or limit > 20:
        raise ValueError("limit must be between 1 and 20")
    if not 30.0 <= read_timeout_seconds <= 1800.0:
        raise ValueError("read_timeout_seconds must be between 30 and 1800")
    run_dir = run_dir.resolve()
    store = LocalArtifactStore(run_dir)
    plan = _read_json(run_dir / "external-generation" / "plans" / plan_id / "plan.json")
    selected = sorted(
        (item for item in plan["entries"] if item["selected_for_paid_generation"]),
        key=lambda item: int(item["paid_priority"]),
    )[:limit]
    provider = SeedDreamProvider(
        SeedDreamProviderConfig.from_env(
            size="2K",
            watermark=False,
            connect_timeout_seconds=10,
            read_timeout_seconds=read_timeout_seconds,
        ),
        output_root=run_dir / "external-generation" / "provider-native",
        cache_path=run_dir / "external-generation" / "provider-cache" / "seedream.json",
        budget=BudgetLedger(budget_cny),
    )
    statuses: list[dict[str, Any]] = []
    for item in selected:
        task_id = str(item["task_id"])
        result_path = run_dir / "external-generation" / "results" / f"{task_id}.json"
        if result_path.is_file():
            statuses.append(_read_json(result_path))
            continue
        task = TaskSpec.model_validate(store.read_json(f"tasks/{task_id}.json"))
        source_ref = store.read_json(f"sources/{task.source.source_id}.json")
        source_path = store.path(source_ref["relative_path"])
        started = time.perf_counter()
        try:
            result = provider.generate(
                SeedDreamGenerationRequest(
                    task_id=task_id,
                    run_id=str(plan["run_id"]),
                    request_id=f"seedream-{task_id}",
                    source_data_uri=_source_data_uri(source_path, task.source.sha256),
                    source_sha256=task.source.sha256,
                    source_is_public=False,
                    allow_data_egress=True,
                    egress_authorization_basis=EGRESS_AUTHORIZATION,
                    target_width=1536,
                    target_height=1536,
                    prompt=str(plan.get("aigc_prompt") or SEEDREAM_PROMPT),
                    prompt_version=str(
                        plan.get("aigc_prompt_version") or PROMPT_VERSION
                    ),
                    max_cost_cny=Decimal("0.60"),
                )
            )
            evaluation_path = (
                run_dir / "external-generation" / "evaluation-images" / f"{task_id}.png"
            )
            normalization = _normalize_provider_output(result.output_path, evaluation_path)
            status = {
                "task_id": task_id,
                "status": "success",
                "wall_seconds": time.perf_counter() - started,
                "provider_native_path": result.output_path.relative_to(run_dir).as_posix(),
                "provider_native_sha256": result.output_sha256,
                "evaluation_path": evaluation_path.relative_to(run_dir).as_posix(),
                "cache_hit": result.cache_hit,
                "estimated_cost_min_cny": str(result.estimated_cost_min_cny),
                "estimated_cost_max_cny": str(result.estimated_cost_max_cny),
                "actual_cost_cny": None,
                "normalization": normalization,
                "watermark": False,
            }
        except SeedDreamProviderError as error:
            status = {
                "task_id": task_id,
                "status": "failed",
                "error_code": error.code.value,
                "charge_may_have_occurred": error.charge_may_have_occurred,
                "wall_seconds": time.perf_counter() - started,
                "estimated_cost_min_cny": "0.30" if error.charge_may_have_occurred else "0.00",
                "estimated_cost_max_cny": "0.60" if error.charge_may_have_occurred else "0.00",
                "actual_cost_cny": None,
            }
        _write_json(result_path, status)
        statuses.append(status)
    summary = {
        "plan_id": plan_id,
        "requested_limit": limit,
        "provider_read_timeout_seconds": read_timeout_seconds,
        "result_count": len(statuses),
        "success_count": sum(item["status"] == "success" for item in statuses),
        "failure_count": sum(item["status"] != "success" for item in statuses),
        "estimated_cost_min_cny": str(
            sum(Decimal(str(item["estimated_cost_min_cny"])) for item in statuses)
        ),
        "estimated_cost_max_cny": str(
            sum(Decimal(str(item["estimated_cost_max_cny"])) for item in statuses)
        ),
    }
    _write_json(run_dir / "external-generation" / f"execution-prefix-{limit}.json", summary)
    return summary


def _read_rgb(path: Path) -> np.ndarray:
    with Image.open(path) as opened:
        return np.asarray(ImageOps.exif_transpose(opened).convert("RGB")).copy()


def evaluate_seedream_results(
    run_dir: Path,
    plan_id: str,
    *,
    strategy_bundle: LoadedStrategyBundle | None = None,
    plugin_catalog: PluginCatalog | None = None,
) -> dict[str, Any]:
    """Run the same proxy detector/metric family over every successful paid output."""

    run_dir = run_dir.resolve()
    store = LocalArtifactStore(run_dir)
    plan = _read_json(run_dir / "external-generation" / "plans" / plan_id / "plan.json")
    with (run_dir / "config" / "run.yaml").open("r", encoding="utf-8") as handle:
        analysis_config = AnalysisConfig.model_validate((yaml.safe_load(handle) or {})["analysis"])
    if strategy_bundle is None:
        detector = ProtectionDetectorSuite(analysis_config)
        scorer = compute_proxy_metrics
        config = EvaluationConfig()
        scoring_policy = None
    else:
        from .plugin_catalog import built_in_plugin_catalog

        catalog = plugin_catalog or built_in_plugin_catalog()
        analysis_config = analysis_config.model_copy(
            update={
                "detector_suite_plugin": strategy_bundle.bundle.detector_suite_plugin
            }
        )
        detector = catalog.detector_suites.get(
            strategy_bundle.bundle.detector_suite_plugin
        )(analysis_config)
        scorer = catalog.reference_scorers.get(
            strategy_bundle.bundle.reference_scorer_plugin
        )
        scoring_policy = strategy_bundle.scoring
        config = EvaluationConfig(
            evaluator_id=scoring_policy.evaluator_id,
            evaluator_version=scoring_policy.evaluator_version,
            max_analysis_edge=scoring_policy.max_analysis_edge,
            proxy_a_threshold=scoring_policy.proxy_a_threshold,
            proxy_b_threshold=scoring_policy.proxy_b_threshold,
            proxy_c_threshold=scoring_policy.proxy_c_threshold,
            critical_text_recall=scoring_policy.critical_text_recall,
            blank_std_threshold=scoring_policy.blank_std_threshold,
            direct_warp_proxy_a_cap_d_stretch=(
                scoring_policy.direct_warp_proxy_a_cap_d_stretch
            ),
            direct_warp_proxy_c_cap_d_stretch=(
                scoring_policy.direct_warp_proxy_c_cap_d_stretch
            ),
        )
    evaluation_root = _aigc_evaluation_root(run_dir, strategy_bundle)
    if strategy_bundle is not None and not (evaluation_root / "strategy").exists():
        strategy_bundle.snapshot_to(evaluation_root / "strategy")
    metrics_dir = evaluation_root / "metrics"
    records: list[dict[str, Any]] = []
    reused_count = 0
    new_count = 0
    selected = [item for item in plan["entries"] if item["selected_for_paid_generation"]]
    for item in selected:
        task_id = str(item["task_id"])
        result_path = run_dir / "external-generation" / "results" / f"{task_id}.json"
        if not result_path.is_file():
            continue
        metric_path = metrics_dir / f"{task_id}--seedream--v1.json"
        if metric_path.is_file():
            records.append(_read_json(metric_path))
            reused_count += 1
            continue
        result = _read_json(result_path)
        task = TaskSpec.model_validate(store.read_json(f"tasks/{task_id}.json"))
        if result["status"] != "success":
            metrics = {
                "technical_valid": False,
                "hard_failures": f"seedream_{result.get('error_code', 'failed').lower()}",
                "critical_regressions": "",
                "quality_score": None,
                "proxy_grade": "proxy_c",
                "proxy_business_success": False,
                "proxy_is_a": False,
            }
        else:
            source_ref = store.read_json(f"sources/{task.source.source_id}.json")
            source = _read_rgb(store.path(source_ref["relative_path"]))
            candidate = _read_rgb(run_dir / result["evaluation_path"])
            analysis = AnalysisArtifact.model_validate(
                store.read_json(f"analysis/{task_id}/analysis.json")
            )
            try:
                candidate_regions = detector.detect(candidate, 0.0)
                metrics = scorer(
                    source=source,
                    candidate=candidate,
                    task=task,
                    source_regions=analysis.regions,
                    candidate_regions=candidate_regions,
                    transform=None,
                    config=config,
                    scoring_policy=scoring_policy,
                )
            except (OSError, ValueError, cv2.error) as error:
                metrics = {
                    "technical_valid": False,
                    "hard_failures": "seedream_detector_failure",
                    "critical_regressions": "",
                    "quality_score": None,
                    "proxy_grade": "proxy_c",
                    "proxy_business_success": False,
                    "proxy_is_a": False,
                    "detector_error": f"{type(error).__name__}: {error}"[:300],
                }
        payload = {
            "task_id": task_id,
            "candidate_id": f"{task_id}--seedream--v1",
            "method_id": "seedream",
            "strategy_sha256": (
                strategy_bundle.source_sha256 if strategy_bundle is not None else None
            ),
            "metrics": metrics,
        }
        _write_json(metric_path, payload)
        records.append(payload)
        new_count += 1
    summary = {
        "plan_id": plan_id,
        "evaluated_count": len(records),
        "newly_evaluated_count": new_count,
        "reused_metric_count": reused_count,
        "success_count": sum(
            item["metrics"].get("proxy_business_success", False) for item in records
        ),
        "proxy_a_count": sum(item["metrics"].get("proxy_is_a", False) for item in records),
        "grade_counts": dict(Counter(item["metrics"].get("proxy_grade") for item in records)),
    }
    _write_json(evaluation_root / "summary.json", summary)
    return summary


def review_seedream_results(
    run_dir: Path,
    plan_id: str,
    review_id: str,
    backend: StrictVisionReviewBackend,
    *,
    task_ids: set[str] | None = None,
    strategy_bundle: LoadedStrategyBundle | None = None,
    plugin_catalog: PluginCatalog | None = None,
) -> dict[str, Any]:
    """Strictly review successful SeedDream outputs against the same source."""

    run_dir = run_dir.resolve()
    store = LocalArtifactStore(run_dir)
    plan = _read_json(run_dir / "external-generation" / "plans" / plan_id / "plan.json")
    output = run_dir / "external-generation" / "strict-reviews" / review_id
    if output.exists():
        raise FileExistsError(output)
    output.mkdir(parents=True)
    if strategy_bundle is not None:
        strategy_bundle.snapshot_to(output / "strategy")
    with (run_dir / "config" / "run.yaml").open("r", encoding="utf-8") as handle:
        analysis_config = AnalysisConfig.model_validate((yaml.safe_load(handle) or {})["analysis"])
    if strategy_bundle is None:
        detector = ProtectionDetectorSuite(analysis_config)
    else:
        from .plugin_catalog import built_in_plugin_catalog

        catalog = plugin_catalog or built_in_plugin_catalog()
        analysis_config = analysis_config.model_copy(
            update={
                "detector_suite_plugin": strategy_bundle.bundle.detector_suite_plugin
            }
        )
        detector = catalog.detector_suites.get(
            strategy_bundle.bundle.detector_suite_plugin
        )(analysis_config)
    evaluation_root = _aigc_evaluation_root(run_dir, strategy_bundle)
    reviews: list[dict[str, Any]] = []
    for item in plan["entries"]:
        if not item["selected_for_paid_generation"]:
            continue
        task_id = str(item["task_id"])
        if task_ids is not None and task_id not in task_ids:
            continue
        result_path = run_dir / "external-generation" / "results" / f"{task_id}.json"
        if not result_path.is_file() or _read_json(result_path)["status"] != "success":
            continue
        result = _read_json(result_path)
        task = TaskSpec.model_validate(store.read_json(f"tasks/{task_id}.json"))
        source_ref = store.read_json(f"sources/{task.source.source_id}.json")
        analysis = AnalysisArtifact.model_validate(
            store.read_json(f"analysis/{task_id}/analysis.json")
        )
        metric = _read_json(
            evaluation_root
            / "metrics"
            / f"{task_id}--seedream--v1.json"
        )["metrics"]
        candidate_id = f"{task_id}--seedream--v1"
        sheet = output / "sheets" / f"{task_id}.png"
        candidate_path = run_dir / result["evaluation_path"]
        candidate_regions = detector.detect(_read_rgb(candidate_path), 0.0)
        _write_json(
            output / "candidate-regions" / f"{task_id}.json",
            {
                "task_id": task_id,
                "candidate_id": candidate_id,
                "spatially_aligned_to_source": False,
                "regions": [item.model_dump(mode="json") for item in candidate_regions],
            },
        )
        metadata = build_pairwise_review_sheet(
            store.path(source_ref["relative_path"]),
            candidate_path,
            analysis,
            sheet,
            candidate_regions=candidate_regions,
            spatially_aligned=False,
        )
        invocation = backend.review(
            task_id=task_id,
            candidate_id=candidate_id,
            sheet_path=sheet,
            evidence={
                "method_id": "seedream",
                "candidate_kind": "generative_recomposition",
                "spatial_correspondence": "independently_localized_not_pixel_aligned",
                "ocr_and_detector_counts_are_advisory": True,
                **metric,
            },
        )
        payload = {
            "task_id": task_id,
            "candidate_id": candidate_id,
            "sheet": metadata,
            "invocation": invocation.model_dump(mode="json"),
        }
        _write_json(output / "reviews" / f"{task_id}.json", payload)
        reviews.append(payload)
    summary = {
        "review_id": review_id,
        "requested_task_ids": sorted(task_ids) if task_ids is not None else None,
        "review_count": len(reviews),
        "grade_counts": dict(
            Counter(item["invocation"]["review"]["overall_grade"] for item in reviews)
        ),
        "directly_usable_count": sum(
            bool(item["invocation"]["review"]["directly_usable"]) for item in reviews
        ),
    }
    _write_json(output / "summary.json", summary)
    return summary


def build_four_arm_report(
    run_dir: Path,
    evaluation_id: str,
    strict_run_id: str,
    plan_id: str,
    seedream_review_id: str,
    report_id: str,
    *,
    strategy_bundle: LoadedStrategyBundle | None = None,
    plugin_catalog: PluginCatalog | None = None,
) -> dict[str, Any]:
    """Finalize four complete routes without retroactively changing trigger decisions."""

    run_dir = run_dir.resolve()
    store = LocalArtifactStore(run_dir)
    run = RunManifest.model_validate(store.read_json("run.json"))
    plan = _read_json(run_dir / "external-generation" / "plans" / plan_id / "plan.json")
    plan_by_task = {item["task_id"]: item for item in plan["entries"]}
    seedream_reviews = run_dir / "external-generation" / "strict-reviews" / seedream_review_id
    task_rows: list[dict[str, Any]] = []
    arms: dict[str, list[dict[str, Any]]] = {
        "rule": [],
        "qwen4": [],
        "rule_aigc": [],
        "qwen4_aigc": [],
    }
    paid_task_ids: set[str] = set()
    evaluation_root = _aigc_evaluation_root(run_dir, strategy_bundle)
    for task_id in run.task_ids:
        entry = plan_by_task[task_id]
        rule_id = entry["rule_selected_candidate_id"]
        qwen_id = entry["qwen_selected_candidate_id"]
        metric_by_id = _task_candidate_metrics(
            run_dir,
            evaluation_id,
            task_id,
            strategy_bundle=strategy_bundle,
            plugin_catalog=plugin_catalog,
        )[1]
        rule_metric = metric_by_id[rule_id]
        qwen_metric = metric_by_id[qwen_id]
        strict = _read_json(
            run_dir / "strict-reviews" / strict_run_id / "decisions" / f"{task_id}.json"
        )
        rule_final_id = rule_id
        rule_final_metric = rule_metric
        qwen_final_id = qwen_id
        qwen_final_metric = qwen_metric
        seedream_metric_path = (
            evaluation_root
            / "metrics"
            / f"{task_id}--seedream--v1.json"
        )
        seedream_review_path = seedream_reviews / "reviews" / f"{task_id}.json"
        seedream_metric_available = seedream_metric_path.is_file()
        seedream_review_available = seedream_review_path.is_file()
        seedream_metric = (
            _read_json(seedream_metric_path)["metrics"] if seedream_metric_available else None
        )
        seedream_review = (
            StrictCandidateReview.model_validate(
                _read_json(seedream_review_path)["invocation"]["review"]
            )
            if seedream_review_available
            else None
        )
        result_path = run_dir / "external-generation" / "results" / f"{task_id}.json"
        generation_result = _read_json(result_path) if result_path.is_file() else None
        candidate_sha256 = (
            generation_result.get("normalization", {}).get("evaluation_sha256")
            if generation_result is not None
            else None
        )
        human_feedback = load_aigc_human_calibration_feedback(
            run_dir,
            task_id,
            f"{task_id}--seedream--v1",
            candidate_sha256,
        )
        seedream_grade = (
            str(human_feedback["human_grade"])
            if human_feedback is not None
            else seedream_review.overall_grade.value
            if seedream_review is not None
            else None
        )
        seedream_directly_usable = (
            bool(human_feedback["directly_usable"])
            if human_feedback is not None
            else bool(seedream_review and seedream_review.directly_usable)
        )
        if (
            entry["rule_trigger"]
            and seedream_metric is not None
            and seedream_grade is not None
            and seedream_directly_usable
            and seedream_metric.get("technical_valid")
            and seedream_metric.get("quality_score") is not None
            and float(seedream_metric["quality_score"])
            > float(rule_metric.get("quality_score") or -1)
        ):
            rule_final_id = f"{task_id}--seedream--v1"
            rule_final_metric = seedream_metric
            paid_task_ids.add(task_id)
        if entry["qwen_trigger"] and seedream_metric is not None and seedream_grade is not None:
            grade_rank = {"A": 0, "B": 1, "C": 2, "D": 3}
            if grade_rank[seedream_grade] < grade_rank[strict["selected_grade"]]:
                qwen_final_id = f"{task_id}--seedream--v1"
                qwen_final_metric = seedream_metric
                paid_task_ids.add(task_id)
        for arm, candidate_id, metric in (
            ("rule", rule_id, rule_metric),
            ("qwen4", qwen_id, qwen_metric),
            ("rule_aigc", rule_final_id, rule_final_metric),
            ("qwen4_aigc", qwen_final_id, qwen_final_metric),
        ):
            arms[arm].append(
                {
                    "task_id": task_id,
                    "candidate_id": candidate_id,
                    "quality_score": metric.get("quality_score"),
                    "proxy_grade": metric.get("proxy_grade"),
                    "proxy_business_success": metric.get("proxy_business_success", False),
                }
            )
        task_rows.append(
            {
                "task_id": task_id,
                "rule_trigger": entry["rule_trigger"],
                "qwen_trigger": entry["qwen_trigger"],
                "seedream_metric_available": seedream_metric_available,
                "seedream_review_available": seedream_review_available,
                "seedream_grade": seedream_grade,
                "seedream_grade_source": (
                    "human_calibration"
                    if human_feedback is not None
                    else "qwen4_high_resolution_strict_review"
                    if seedream_review is not None
                    else None
                ),
                "rule_final_candidate_id": rule_final_id,
                "qwen_final_candidate_id": qwen_final_id,
            }
        )
    arm_summaries = {}
    for arm, rows in arms.items():
        scores = [float(row["quality_score"]) for row in rows if row["quality_score"] is not None]
        arm_summaries[arm] = {
            "task_count": len(rows),
            "quality_score_mean": sum(scores) / len(scores) if scores else None,
            "proxy_a_rate": sum(row["proxy_grade"] == "proxy_a" for row in rows) / len(rows),
            "proxy_success_rate": sum(row["proxy_business_success"] for row in rows) / len(rows),
            "selected_seedream_count": sum("--seedream--" in row["candidate_id"] for row in rows),
        }
    result_files = list((run_dir / "external-generation" / "results").glob("*.json"))
    paid_attempts = [_read_json(path) for path in result_files]
    report = {
        "schema_version": "1.0",
        "report_id": report_id,
        "task_count": len(run.task_ids),
        "arms": arm_summaries,
        "paid_unique_task_count": len(paid_attempts),
        "paid_used_by_final_routes_count": len(paid_task_ids),
        "estimated_cost_min_cny": str(
            sum(Decimal(str(item["estimated_cost_min_cny"])) for item in paid_attempts)
        ),
        "estimated_cost_max_cny": str(
            sum(Decimal(str(item["estimated_cost_max_cny"])) for item in paid_attempts)
        ),
        "actual_cost_cny": None,
        "agent_token_cost_assumption": "company_internal_zero",
        "strategy_sha256": (
            strategy_bundle.source_sha256 if strategy_bundle is not None else None
        ),
        "limitations": [
            "Proxy grades are automatic evidence, not official human labels.",
            "Codex visual audit is reported separately and never named human ground truth.",
            "SeedDream actual billing was not returned; the 0.30-0.60 CNY range is used.",
        ],
    }
    output = run_dir / "benchmarks" / report_id
    if output.exists():
        raise FileExistsError(output)
    output.mkdir(parents=True)
    if strategy_bundle is not None:
        strategy_bundle.snapshot_to(output / "strategy")
    _write_json(output / "report.json", report)
    _write_json(output / "tasks.json", task_rows)
    for arm, rows in arms.items():
        _write_json(output / f"{arm}.json", rows)
    return report


__all__ = [
    "build_four_arm_report",
    "evaluate_seedream_results",
    "plan_movie60_aigc",
    "review_seedream_results",
    "run_seedream_plan",
    "should_rule_request_aigc",
]
