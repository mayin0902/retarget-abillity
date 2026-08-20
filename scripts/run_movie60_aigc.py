from __future__ import annotations

import argparse
import json
from decimal import Decimal
from pathlib import Path

from retarget_agent.aigc_experiment import (
    build_four_arm_report,
    evaluate_seedream_results,
    plan_movie60_aigc,
    review_seedream_results,
    run_seedream_plan,
)
from retarget_agent.plugin_catalog import built_in_plugin_catalog
from retarget_agent.strategy import load_strategy_bundle


def main() -> None:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)

    plan = subparsers.add_parser("plan")
    plan.add_argument("run_dir", type=Path)
    plan.add_argument("--evaluation-id", required=True)
    plan.add_argument("--strict-run-id", required=True)
    plan.add_argument("--plan-id", required=True)
    plan.add_argument(
        "--strategy",
        type=Path,
        default=Path("strategies/movie60/v3_2_2/bundle.yaml"),
    )

    generate = subparsers.add_parser("generate")
    generate.add_argument("run_dir", type=Path)
    generate.add_argument("--plan-id", required=True)
    generate.add_argument("--limit", type=int, required=True)
    generate.add_argument("--budget-cny", type=Decimal, default=Decimal("12.00"))
    generate.add_argument("--read-timeout-seconds", type=float, default=300.0)

    evaluate = subparsers.add_parser("evaluate")
    evaluate.add_argument("run_dir", type=Path)
    evaluate.add_argument("--plan-id", required=True)
    evaluate.add_argument(
        "--strategy",
        type=Path,
        default=Path("strategies/movie60/v3_2_2/bundle.yaml"),
    )

    review = subparsers.add_parser("review")
    review.add_argument("run_dir", type=Path)
    review.add_argument("--plan-id", required=True)
    review.add_argument("--review-id", required=True)
    review.add_argument("--backend-url", required=True)
    review.add_argument("--model", required=True)
    review.add_argument(
        "--strategy",
        type=Path,
        default=Path("strategies/movie60/v3_2_2/bundle.yaml"),
    )
    review.add_argument(
        "--task-id",
        action="append",
        dest="task_ids",
        help="Review only this paid-plan task; repeat for multiple tasks.",
    )

    report = subparsers.add_parser("report")
    report.add_argument("run_dir", type=Path)
    report.add_argument("--evaluation-id", required=True)
    report.add_argument("--strict-run-id", required=True)
    report.add_argument("--plan-id", required=True)
    report.add_argument("--seedream-review-id", required=True)
    report.add_argument("--report-id", required=True)
    report.add_argument(
        "--strategy",
        type=Path,
        default=Path("strategies/movie60/v3_2_2/bundle.yaml"),
    )

    args = parser.parse_args()
    if args.command == "plan":
        strategy = load_strategy_bundle(args.strategy)
        result = plan_movie60_aigc(
            args.run_dir,
            args.evaluation_id,
            args.strict_run_id,
            args.plan_id,
            strategy_bundle=strategy,
        )
    elif args.command == "generate":
        result = run_seedream_plan(
            args.run_dir,
            args.plan_id,
            limit=args.limit,
            budget_cny=args.budget_cny,
            read_timeout_seconds=args.read_timeout_seconds,
        )
    elif args.command == "evaluate":
        strategy = load_strategy_bundle(args.strategy)
        result = evaluate_seedream_results(
            args.run_dir, args.plan_id, strategy_bundle=strategy
        )
    elif args.command == "review":
        strategy = load_strategy_bundle(args.strategy)
        backend = built_in_plugin_catalog().agent_backends.get(
            strategy.bundle.strict_review_backend_plugin
        )(
            base_url=args.backend_url,
            model_version=args.model,
            timeout_seconds=60,
            cache_path=args.run_dir / "agent-cache" / "strict-seedream-qwen4.json",
            prompt_template=(strategy.prompts.strict_candidate if strategy.prompts else None),
        )
        result = review_seedream_results(
            args.run_dir,
            args.plan_id,
            args.review_id,
            backend,
            task_ids=set(args.task_ids) if args.task_ids else None,
            strategy_bundle=strategy,
        )
    else:
        strategy = load_strategy_bundle(args.strategy)
        result = build_four_arm_report(
            args.run_dir,
            args.evaluation_id,
            args.strict_run_id,
            args.plan_id,
            args.seedream_review_id,
            args.report_id,
            strategy_bundle=strategy,
        )
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
