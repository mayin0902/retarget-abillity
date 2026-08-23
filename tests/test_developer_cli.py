from pathlib import Path

from click import unstyle
from PIL import Image
from typer.testing import CliRunner

from retarget_agent import cli, simple_workflow
from retarget_agent.cli import app


def test_doctor_and_current_strategy_are_available() -> None:
    runner = CliRunner()
    doctor = runner.invoke(app, ["doctor"])
    strategy = runner.invoke(app, ["strategy", "show"])

    assert doctor.exit_code == 0
    assert '"status": "READY"' in doctor.stdout
    assert strategy.exit_code == 0
    assert '"strategy_id": "retarget"' in strategy.stdout
    assert '"version": "1.0.0"' in strategy.stdout


def test_review_import_cli_materializes_external_case(tmp_path: Path) -> None:
    case = tmp_path / "case"
    output = tmp_path / "workspace"
    candidates = case / "candidates"
    candidates.mkdir(parents=True)
    Image.new("RGB", (80, 60), "red").save(case / "source.jpg")
    Image.new("RGB", (64, 64), "blue").save(candidates / "crop.png")

    result = CliRunner().invoke(
        app,
        ["review", "import", str(case), "--output-dir", str(output)],
    )

    assert result.exit_code == 0
    assert '"candidate_count": 1' in result.stdout
    assert (output / "review-workspace.json").is_file()


def test_public_run_help_exposes_scene_and_config_owned_target_default() -> None:
    result = CliRunner().invoke(app, ["run", "image", "--help"])

    assert result.exit_code == 0
    output = unstyle(result.stdout)
    assert "--scene" in output
    assert "movie_poster" in output
    assert "configs/default.yaml" in output


def test_generation_run_preflight_needs_no_secret_or_business_egress_manifest(
    tmp_path: Path,
) -> None:
    image = tmp_path / "poster.png"
    prompt = tmp_path / "prompt.txt"
    output = tmp_path / "external-generation"
    Image.new("RGB", (40, 60), "red").save(image)
    prompt.write_text("保留主体与重要文字。\n", encoding="utf-8")

    result = CliRunner().invoke(
        app,
        [
            "generation",
            "run",
            str(image),
            "--output-root",
            str(output),
            "--request-id",
            "request-001",
            "--task-id",
            "poster-001",
            "--prompt-file",
            str(prompt),
        ],
    )

    assert result.exit_code == 0
    assert '"status": "planned"' in result.stdout
    assert '"executed": false' in result.stdout
    assert not output.exists()


def test_unspecified_scene_warns_before_public_workflow(
    tmp_path: Path, monkeypatch
) -> None:
    image = tmp_path / "poster.png"
    Image.new("RGB", (20, 20), "red").save(image)

    def fake_run_image(*_args, **kwargs):
        assert kwargs["target"] is None
        assert kwargs["scene"] == "unspecified"
        return {"status": "completed", "warnings": [simple_workflow.UNSPECIFIED_SCENE_WARNING]}

    monkeypatch.setattr(simple_workflow, "run_image", fake_run_image)
    result = CliRunner().invoke(app, ["run", "image", str(image)])

    assert result.exit_code == 0
    assert result.stdout.count("Scene category not specified") == 1


def test_json_output_falls_back_on_legacy_non_unicode_console(monkeypatch) -> None:
    messages: list[str] = []

    def cp1252_echo(value: str) -> None:
        value.encode("cp1252")
        messages.append(value)

    monkeypatch.setattr(cli.typer, "echo", cp1252_echo)

    cli._echo_json({"路径": "中文海报"})

    assert len(messages) == 1
    assert "\\u8def\\u5f84" in messages[0]
    assert "\\u4e2d\\u6587\\u6d77\\u62a5" in messages[0]
