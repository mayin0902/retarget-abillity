from pathlib import Path

import pytest
from PIL import Image

from retarget_agent.config import load_run_config
from retarget_agent.datasets import FolderCsvDatasetAdapter
from scripts.prepare_single_image_dataset import materialize_single_image_dataset


def test_materialize_single_image_dataset_is_valid_and_runnable(tmp_path: Path) -> None:
    source = tmp_path / "input.jpg"
    Image.new("RGB", (640, 360), (20, 80, 140)).save(source, quality=92)
    output = tmp_path / "dataset"

    result = materialize_single_image_dataset(
        source,
        output,
        source_id="poster_001",
        run_id="single-poster-v1",
        scene_category="movie_poster",
    )

    validation = FolderCsvDatasetAdapter().validate(output)
    config = load_run_config(output / "run.yaml")
    assert validation.valid
    assert len(validation.tasks) == 1
    assert validation.tasks[0].task_id == "poster_001__square-1536"
    assert config.dataset_root == output.resolve().as_posix()
    assert config.run_id == "single-poster-v1"
    assert config.method_profile == "retarget_default_v1"
    assert result["task_id"] == "poster_001__square-1536"


def test_materialize_single_image_dataset_refuses_overwrite(tmp_path: Path) -> None:
    source = tmp_path / "input.png"
    Image.new("RGB", (100, 80), "white").save(source)
    output = tmp_path / "dataset"
    output.mkdir()

    with pytest.raises(FileExistsError):
        materialize_single_image_dataset(
            source,
            output,
            source_id="demo",
            run_id="single-demo-v1",
        )


@pytest.mark.parametrize("source_id", ["Bad ID", "中文", "../escape"])
def test_materialize_single_image_dataset_rejects_unsafe_ids(
    tmp_path: Path, source_id: str
) -> None:
    source = tmp_path / "input.png"
    Image.new("RGB", (100, 80), "white").save(source)

    with pytest.raises(ValueError):
        materialize_single_image_dataset(
            source,
            tmp_path / "dataset",
            source_id=source_id,
            run_id="single-demo-v1",
        )
