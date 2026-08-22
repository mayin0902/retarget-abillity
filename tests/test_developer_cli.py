from pathlib import Path

from PIL import Image
from typer.testing import CliRunner

from retarget_agent import simple_workflow
from retarget_agent.cli import app


def test_doctor_and_current_strategy_are_available() -> None:
    runner = CliRunner()
    doctor = runner.invoke(app, ["doctor"])
    strategy = runner.invoke(app, ["strategy", "show"])

    assert doctor.exit_code == 0
    assert '"status": "READY"' in doctor.stdout
    assert strategy.exit_code == 0
    assert '"version": "3.3.0"' in strategy.stdout


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
    assert "--scene" in result.stdout
    assert "movie_poster" in result.stdout
    assert "configs/default.yaml" in result.stdout


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
