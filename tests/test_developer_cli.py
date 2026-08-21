from pathlib import Path

from PIL import Image
from typer.testing import CliRunner

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
