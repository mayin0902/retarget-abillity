"""Command-line adapters. Business logic lives behind RetargetApplicationService."""

from __future__ import annotations

import json
import threading
import webbrowser
from datetime import UTC, datetime
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
score_app = typer.Typer(help="Score one candidate with or without a source reference.")
plugin_app = typer.Typer(help="Inspect allowlisted runtime plugins.")
app.add_typer(dataset_app, name="dataset")
app.add_typer(run_app, name="run")
app.add_typer(replay_app, name="replay")
app.add_typer(review_app, name="review")
app.add_typer(agent_app, name="agent")
app.add_typer(benchmark_app, name="benchmark")
app.add_typer(generation_app, name="generation")
app.add_typer(strategy_app, name="strategy")
app.add_typer(score_app, name="score")
app.add_typer(plugin_app, name="plugins")


@plugin_app.command("list")
def plugins_list() -> None:
    """Print executable plugin IDs accepted by StrategyBundle."""
    from .plugin_catalog import built_in_plugin_catalog

    typer.echo(json.dumps(built_in_plugin_catalog().describe(), ensure_ascii=False, indent=2))


def _image_review_backend(
    loaded_strategy: object,
    backend_url: str | None,
    model: str | None,
    api_key_env: str | None,
):
    if backend_url is None and model is None:
        return None
    if not backend_url or not model:
        raise typer.BadParameter("--agent-backend-url and --agent-model must be used together")
    if loaded_strategy.prompts is None or loaded_strategy.prompts.standalone_image is None:
        raise typer.BadParameter("strategy has no standalone-image Agent prompt")
    from .plugin_catalog import built_in_plugin_catalog

    factory = built_in_plugin_catalog().agent_backends.get(
        loaded_strategy.bundle.image_review_backend_plugin
    )
    return factory(
        base_url=backend_url,
        model_version=model,
        api_key_env=api_key_env,
        prompt_template=loaded_strategy.prompts.standalone_image,
    )


@score_app.command("reference")
def score_reference(
    source: Annotated[Path, typer.Argument(exists=True, dir_okay=False)],
    candidate: Annotated[Path, typer.Argument(exists=True, dir_okay=False)],
    output_dir: Annotated[Path | None, typer.Option("--output-dir")] = None,
    strategy: Annotated[
        Path | None,
        typer.Option("--strategy", exists=True, dir_okay=False),
    ] = None,
    agent_backend_url: Annotated[
        str | None, typer.Option("--agent-backend-url")
    ] = None,
    agent_model: Annotated[str | None, typer.Option("--agent-model")] = None,
    agent_api_key_env: Annotated[
        str | None, typer.Option("--agent-api-key-env")
    ] = None,
) -> None:
    """Compare one candidate with its source and emit JSON/Markdown/overlay."""
    from .defaults import current_strategy_path, project_root
    from .image_scoring import score_image
    from .strategy import load_strategy_bundle

    strategy = strategy or current_strategy_path()
    output_dir = output_dir or (
        project_root()
        / "workspace"
        / "scores"
        / f"reference-{datetime.now(UTC):%Y%m%d-%H%M%S-%f}"
    )
    loaded = load_strategy_bundle(strategy)
    result = score_image(
        source_path=source,
        candidate_path=candidate,
        output_dir=output_dir,
        strategy=loaded,
        agent_backend=_image_review_backend(
            loaded, agent_backend_url, agent_model, agent_api_key_env
        ),
    )
    typer.echo(json.dumps(result, ensure_ascii=False, indent=2))


@score_app.command("standalone")
def score_standalone(
    candidate: Annotated[Path, typer.Argument(exists=True, dir_okay=False)],
    output_dir: Annotated[Path | None, typer.Option("--output-dir")] = None,
    strategy: Annotated[
        Path | None,
        typer.Option("--strategy", exists=True, dir_okay=False),
    ] = None,
    agent_backend_url: Annotated[
        str | None, typer.Option("--agent-backend-url")
    ] = None,
    agent_model: Annotated[str | None, typer.Option("--agent-model")] = None,
    agent_api_key_env: Annotated[
        str | None, typer.Option("--agent-api-key-env")
    ] = None,
) -> None:
    """Inspect one candidate without making source-preservation or grade claims."""
    from .defaults import current_strategy_path, project_root
    from .image_scoring import score_image
    from .strategy import load_strategy_bundle

    strategy = strategy or current_strategy_path()
    output_dir = output_dir or (
        project_root()
        / "workspace"
        / "scores"
        / f"standalone-{datetime.now(UTC):%Y%m%d-%H%M%S-%f}"
    )
    loaded = load_strategy_bundle(strategy)
    result = score_image(
        candidate_path=candidate,
        output_dir=output_dir,
        strategy=loaded,
        agent_backend=_image_review_backend(
            loaded, agent_backend_url, agent_model, agent_api_key_env
        ),
    )
    typer.echo(json.dumps(result, ensure_ascii=False, indent=2))


@app.command()
def version() -> None:
    """Print the installed package version."""
    typer.echo(__version__)


@app.command()
def doctor() -> None:
    """Check the local environment and print one READY / NOT READY result."""
    from .doctor import run_doctor

    result = run_doctor()
    typer.echo(json.dumps(result, ensure_ascii=False, indent=2))
    if not result["ready"]:
        raise typer.Exit(code=2)


@strategy_app.command("show")
def strategy_show(
    bundle: Annotated[Path | None, typer.Argument(exists=True, dir_okay=False)] = None,
) -> None:
    """Validate and show one immutable strategy bundle and its hashes."""
    from .defaults import current_strategy_path
    from .strategy import load_strategy_bundle

    loaded = load_strategy_bundle(bundle or current_strategy_path())
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


@run_app.command("image")
def run_image_command(
    input_image: Annotated[Path, typer.Argument(exists=True, dir_okay=False)],
    target: Annotated[str, typer.Option("--target")] = "1536x1536",
    agent_profile: Annotated[
        Path | None,
        typer.Option("--agent-profile", exists=True, dir_okay=False),
    ] = None,
) -> None:
    """Run current Rule for one image; optionally run Agent with a private profile."""
    from .simple_workflow import run_image

    typer.echo(
        json.dumps(
            run_image(input_image, target=target, agent_profile=agent_profile),
            ensure_ascii=False,
            indent=2,
        )
    )


@run_app.command("batch")
def run_batch_command(
    input_dir: Annotated[Path, typer.Argument(exists=True, file_okay=False)],
    target: Annotated[str, typer.Option("--target")] = "1536x1536",
    agent_profile: Annotated[
        Path | None,
        typer.Option("--agent-profile", exists=True, dir_okay=False),
    ] = None,
) -> None:
    """Run current Rule for a folder; optionally run Agent with a private profile."""
    from .simple_workflow import run_batch

    typer.echo(
        json.dumps(
            run_batch(input_dir, target=target, agent_profile=agent_profile),
            ensure_ascii=False,
            indent=2,
        )
    )


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
    """Deprecated alias for ``review open RUN_DIR``."""
    typer.echo("Deprecated: use 'retarget-engine review open RUN_DIR'.")
    _launch_unified_review(
        run_dir,
        host=host,
        port=port,
        evaluation_id=None,
        agent_run_id=agent_run_id,
        open_browser=True,
    )


def _launch_unified_review(
    workspace: Path,
    *,
    host: str,
    port: int,
    evaluation_id: str | None,
    agent_run_id: str | None,
    open_browser: bool,
) -> None:
    import uvicorn

    from .unified_review_app import create_unified_review_app

    url = f"http://{host}:{port}/"
    if host not in {"127.0.0.1", "localhost", "::1"}:
        typer.echo("Warning: non-loopback binding exposes this unauthenticated local review tool.")
    if open_browser:
        threading.Timer(1.0, lambda: webbrowser.open(url)).start()
    typer.echo(f"Review website: {url}")
    typer.echo(f"Workspace: {workspace.resolve()}")
    uvicorn.run(
        create_unified_review_app(
            workspace,
            evaluation_id=evaluation_id,
            agent_run_id=agent_run_id,
        ),
        host=host,
        port=port,
        log_level="info",
    )


@review_app.command("open")
def review_open(
    workspace: Annotated[Path | None, typer.Argument(exists=True, file_okay=False)] = None,
    host: Annotated[str, typer.Option("--host")] = "127.0.0.1",
    port: Annotated[int, typer.Option("--port", min=1, max=65535)] = 8765,
    evaluation_id: Annotated[str | None, typer.Option("--evaluation-id")] = None,
    agent_run_id: Annotated[str | None, typer.Option("--agent-run-id")] = None,
    open_browser: Annotated[bool, typer.Option("--open-browser/--no-open-browser")] = True,
) -> None:
    """Open Movie60, a standard Run or an imported case in the same review UI."""
    from .defaults import load_default_config
    from .review_workspace import latest_completed_run

    if workspace is None:
        root, defaults = load_default_config()
        runs_root = root / str(defaults["review"]["runs_root"])
        movie60 = root / str(defaults["review"]["movie60_workspace"])
        try:
            workspace = latest_completed_run(runs_root)
        except FileNotFoundError:
            if not movie60.is_dir():
                raise FileNotFoundError(
                    "no completed Run and no materialized Movie60 review workspace"
                ) from None
            workspace = movie60
    _launch_unified_review(
        workspace,
        host=host,
        port=port,
        evaluation_id=evaluation_id,
        agent_run_id=agent_run_id,
        open_browser=open_browser,
    )


@review_app.command("latest")
def review_latest(
    runs_root: Annotated[
        Path,
        typer.Option("--runs-root", exists=True, file_okay=False),
    ] = Path("runs"),
    host: Annotated[str, typer.Option("--host")] = "127.0.0.1",
    port: Annotated[int, typer.Option("--port", min=1, max=65535)] = 8765,
) -> None:
    """Open the most recently completed standard Run."""
    from .review_workspace import latest_completed_run

    _launch_unified_review(
        latest_completed_run(runs_root),
        host=host,
        port=port,
        evaluation_id=None,
        agent_run_id=None,
        open_browser=True,
    )


@review_app.command("import")
def review_import(
    source_dir: Annotated[Path, typer.Argument(exists=True, file_okay=False)],
    output_dir: Annotated[Path | None, typer.Option("--output-dir")] = None,
) -> None:
    """Convert source.* + candidates/ into a standard local review workspace."""
    from .defaults import project_root
    from .review_workspace import import_review_case

    target = output_dir or project_root() / "local_data" / "reviews" / source_dir.name
    typer.echo(
        json.dumps(import_review_case(source_dir, target), ensure_ascii=False, indent=2)
    )
