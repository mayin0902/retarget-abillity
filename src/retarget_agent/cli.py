"""Command-line adapters. Business logic lives behind RetargetApplicationService."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated

import typer

from . import __version__

app = typer.Typer(help="Replayable multi-method image retargeting engine.")
strategy_app = typer.Typer(help="Inspect immutable scoring and Agent strategy bundles.")
dataset_app = typer.Typer(help="Dataset materialization and validation.")
run_app = typer.Typer(help="Generation runs.")
replay_app = typer.Typer(help="Evaluation Replay over frozen candidates.")
review_app = typer.Typer(help="Human review tools.")
agent_app = typer.Typer(help="Controlled Agent routing over frozen evaluations.")
benchmark_app = typer.Typer(help="Complete-denominator automatic benchmark reports.")
generation_app = typer.Typer(help="Budgeted external-generation planning and execution.")
app.add_typer(dataset_app, name="dataset")
app.add_typer(run_app, name="run")
app.add_typer(replay_app, name="replay")
app.add_typer(review_app, name="review")
app.add_typer(agent_app, name="agent")
app.add_typer(benchmark_app, name="benchmark")
app.add_typer(generation_app, name="generation")
app.add_typer(strategy_app, name="strategy")


@app.command()
def version() -> None:
    """Print the installed package version."""
    typer.echo(__version__)


@strategy_app.command("show")
def strategy_show(
    bundle: Annotated[Path, typer.Argument(exists=True, dir_okay=False)],
) -> None:
    """Validate and show one immutable strategy bundle and its hashes."""
    from .strategy import load_strategy_bundle

    loaded = load_strategy_bundle(bundle)
    typer.echo(
        json.dumps(
            {
                "bundle": loaded.bundle.model_dump(mode="json"),
                "strategy_sha256": loaded.source_sha256,
                "file_hashes": loaded.file_hashes,
                "grade_thresholds": {
                    "A_min": loaded.scoring.proxy_a_threshold,
                    "B_min": loaded.scoring.proxy_b_threshold,
                    "C_min": loaded.scoring.proxy_c_threshold,
                    "D": f"score < {loaded.scoring.proxy_c_threshold}",
                },
            },
            ensure_ascii=False,
            indent=2,
        )
    )


@strategy_app.command("diff")
def strategy_diff(
    old_bundle: Annotated[Path, typer.Argument(exists=True, dir_okay=False)],
    new_bundle: Annotated[Path, typer.Argument(exists=True, dir_okay=False)],
) -> None:
    """Show every policy and Skill field changed between two bundles."""
    from .strategy import diff_strategy_bundles, load_strategy_bundle

    result = diff_strategy_bundles(
        load_strategy_bundle(old_bundle), load_strategy_bundle(new_bundle)
    )
    typer.echo(json.dumps(result, ensure_ascii=False, indent=2))


@dataset_app.command("validate")
def dataset_validate(
    dataset_root: Annotated[Path, typer.Argument(exists=True, file_okay=False)],
) -> None:
    """Validate a Folder/CSV dataset without generating candidates."""
    from .service import RetargetApplicationService

    result = RetargetApplicationService.default().validate_dataset(dataset_root)
    typer.echo(json.dumps(result, ensure_ascii=False, indent=2))
    if not result["valid"]:
        raise typer.Exit(code=2)


@run_app.command("generate")
def run_generate(
    config_path: Annotated[Path, typer.Argument(exists=True, dir_okay=False)],
) -> None:
    """Execute one frozen method-profile Generation Run."""
    from .service import RetargetApplicationService

    result = RetargetApplicationService.default().generate_from_config(config_path)
    typer.echo(json.dumps(result, ensure_ascii=False, indent=2))


@app.command("report")
def report(
    run_dir: Annotated[Path, typer.Argument(exists=True, file_okay=False)],
) -> None:
    """Rebuild a report from frozen candidate and review records."""
    from .service import RetargetApplicationService

    result = RetargetApplicationService.default().build_report(run_dir)
    typer.echo(json.dumps(result, ensure_ascii=False, indent=2))


@app.command("audit")
def audit(
    run_dir: Annotated[Path, typer.Argument(exists=True, file_okay=False)],
) -> None:
    """Audit a frozen run against its declared method-profile contract."""
    from .audit import audit_run_contract

    result = audit_run_contract(run_dir)
    typer.echo(json.dumps(result, ensure_ascii=False, indent=2))
    if result["status"] != "PASS":
        raise typer.Exit(code=2)


@replay_app.command("run")
def replay_run(
    run_dir: Annotated[Path, typer.Argument(exists=True, file_okay=False)],
    replay_id: Annotated[str, typer.Option("--replay-id")],
) -> None:
    """Create a new Decision set without changing Candidate artifacts."""
    from .service import RetargetApplicationService

    result = RetargetApplicationService.default().replay(run_dir, replay_id)
    typer.echo(json.dumps(result, ensure_ascii=False, indent=2))


@app.command("evaluate")
def evaluate(
    run_dir: Annotated[Path, typer.Argument(exists=True, file_okay=False)],
    evaluation_id: Annotated[str, typer.Option("--evaluation-id")],
    no_detectors: Annotated[
        bool,
        typer.Option(
            "--no-detectors",
            help="Skip candidate OCR/face/object/logo re-detection (development only).",
        ),
    ] = False,
    strategy: Annotated[
        Path | None,
        typer.Option("--strategy", exists=True, dir_okay=False, help="Strategy bundle YAML."),
    ] = None,
) -> None:
    """Compute uncalibrated automatic proxy metrics over frozen candidates."""
    from .service import RetargetApplicationService

    result = RetargetApplicationService.default().evaluate(
        run_dir,
        evaluation_id,
        rerun_detectors=not no_detectors,
        strategy_path=strategy,
    )
    typer.echo(json.dumps(result, ensure_ascii=False, indent=2))


@agent_app.command("replay")
def agent_replay(
    run_dir: Annotated[Path, typer.Argument(exists=True, file_okay=False)],
    evaluation_id: Annotated[str, typer.Option("--evaluation-id")],
    agent_run_id: Annotated[str, typer.Option("--agent-run-id")],
    mode: Annotated[
        str,
        typer.Option(help="hard_ranker, conditional_agent, or always_on_agent."),
    ] = "hard_ranker",
    backend_url: Annotated[
        str | None,
        typer.Option(help="OpenAI-compatible VLM endpoint; never persisted as a credential."),
    ] = None,
    model: Annotated[str | None, typer.Option(help="Exact VLM model ID or revision.")] = None,
    api_key_env: Annotated[
        str | None,
        typer.Option(help="Environment-variable name containing the endpoint token."),
    ] = None,
    allow_external_aigc: Annotated[
        bool,
        typer.Option(help="Permit a route decision to request external generation."),
    ] = False,
    max_agent_calls: Annotated[
        int | None,
        typer.Option(min=0, help="Hard cap for VLM judge calls in this replay."),
    ] = None,
    fixed_method: Annotated[
        str | None,
        typer.Option(help="No-Agent fixed traditional method (hard_ranker mode only)."),
    ] = None,
    skill: Annotated[
        Path | None,
        typer.Option("--skill", exists=True, dir_okay=False, help="Frozen Agent skill YAML."),
    ] = None,
    strategy: Annotated[
        Path | None,
        typer.Option("--strategy", exists=True, dir_okay=False, help="Strategy bundle YAML."),
    ] = None,
    comparison_dir: Annotated[
        Path | None,
        typer.Option(
            "--comparison-dir",
            exists=True,
            file_okay=False,
            help="Run-local unbiased Agent comparison images; defaults to visualizations/.",
        ),
    ] = None,
) -> None:
    """Create immutable per-task Agent route decisions from one proxy evaluation."""
    from .service import RetargetApplicationService

    result = RetargetApplicationService.default().replay_agent(
        run_dir,
        evaluation_id,
        agent_run_id,
        mode=mode,
        backend_url=backend_url,
        model_version=model,
        api_key_env=api_key_env,
        allow_external_aigc=allow_external_aigc,
        max_agent_calls=max_agent_calls,
        fixed_method_id=fixed_method,
        skill_path=skill,
        strategy_path=strategy,
        comparison_dir=comparison_dir,
    )
    typer.echo(json.dumps(result, ensure_ascii=False, indent=2))


@benchmark_app.command("report")
def benchmark_report(
    run_dir: Annotated[Path, typer.Argument(exists=True, file_okay=False)],
    evaluation_id: Annotated[str, typer.Option("--evaluation-id")],
    benchmark_id: Annotated[str, typer.Option("--benchmark-id")],
    route_ids: Annotated[
        list[str] | None,
        typer.Option(
            "--route-id",
            help="Complete Agent/routing run to include; repeat for multiple arms.",
        ),
    ] = None,
) -> None:
    """Aggregate only arms that cover the full frozen task denominator."""
    from .service import RetargetApplicationService

    result = RetargetApplicationService.default().build_benchmark(
        run_dir,
        evaluation_id,
        benchmark_id,
        tuple(route_ids or ()),
    )
    typer.echo(json.dumps(result, ensure_ascii=False, indent=2))


@generation_app.command("plan")
def generation_plan(
    run_dir: Annotated[Path, typer.Argument(exists=True, file_okay=False)],
    evaluation_id: Annotated[str, typer.Option("--evaluation-id")],
    generation_plan_id: Annotated[str, typer.Option("--generation-plan-id")],
    source_audit: Annotated[
        Path,
        typer.Option("--source-audit", exists=True, dir_okay=False),
    ],
    agent_run_ids: Annotated[
        list[str],
        typer.Option(
            "--agent-run-id",
            help="Complete Agent run contributing generation votes; repeat as needed.",
        ),
    ],
    maximum_paid_calls: Annotated[
        int,
        typer.Option("--maximum-paid-calls", min=0),
    ] = 12,
) -> None:
    """Freeze a multi-Agent generation queue without calling a provider."""
    from .service import RetargetApplicationService

    result = RetargetApplicationService.default().plan_external_generation(
        run_dir,
        evaluation_id,
        generation_plan_id,
        tuple(agent_run_ids),
        source_audit,
        maximum_paid_calls=maximum_paid_calls,
    )
    typer.echo(json.dumps(result, ensure_ascii=False, indent=2))


@review_app.command("web")
def review_web(
    run_dir: Annotated[Path, typer.Argument(exists=True, file_okay=False)],
    host: Annotated[str, typer.Option("--host", help="Bind address.")] = "127.0.0.1",
    port: Annotated[
        int,
        typer.Option("--port", min=1, max=65535, help="Local HTTP port."),
    ] = 8765,
    agent_run_id: Annotated[
        str | None,
        typer.Option(
            "--agent-run-id",
            help="Large-model pre-review to show; defaults to the newest complete replay.",
        ),
    ] = None,
) -> None:
    """Launch the FastAPI review website against a frozen run."""
    from .service import RetargetApplicationService

    typer.echo(f"Review website: http://{host}:{port}")
    if host not in {"127.0.0.1", "localhost", "::1"}:
        typer.echo("Warning: non-loopback binding exposes this unauthenticated local review tool.")
    RetargetApplicationService.default().launch_review_web(
        run_dir,
        host=host,
        port=port,
        agent_run_id=agent_run_id,
    )
