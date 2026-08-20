from __future__ import annotations

import csv
import json
from pathlib import Path

import pytest

from retarget_agent.proxy_validation import freeze_proxy_split


def _ratings(path: Path) -> None:
    fields = (
        "task_id",
        "scene_category",
        "method",
        "image_sha256",
        "suggested_grade",
        "evaluation_provenance",
    )
    scenes = (("person", 15), ("movie_poster", 15), ("film_still", 15), ("video_cover", 15))
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for scene, count in scenes:
            for task_index in range(count):
                for method_index in range(7):
                    writer.writerow(
                        {
                            "task_id": f"{scene}-{task_index:02d}",
                            "scene_category": scene,
                            "method": f"method-{method_index}",
                            "image_sha256": f"{method_index:064d}",
                            # Changing labels must not affect split assignment.
                            "suggested_grade": "A" if task_index % 2 else "D",
                            "evaluation_provenance": "fixture",
                        }
                    )


def test_proxy_split_is_scene_stratified_label_free_and_immutable(tmp_path: Path) -> None:
    ratings = tmp_path / "ratings.csv"
    output = tmp_path / "split.json"
    _ratings(ratings)

    split = freeze_proxy_split(ratings, output)

    assert split["development_task_count"] == 45
    assert split["proxy_holdout_task_count"] == 15
    holdout_scenes = [
        item["scene_category"]
        for item in split["records"]
        if item["partition"] == "proxy_holdout"
    ]
    assert holdout_scenes.count("person") == 3
    assert holdout_scenes.count("movie_poster") == 4
    assert holdout_scenes.count("film_still") == 4
    assert holdout_scenes.count("video_cover") == 4
    development_folds = {
        item["development_fold"]
        for item in split["records"]
        if item["partition"] == "development"
    }
    assert development_folds == {0, 1, 2, 3, 4}
    assert json.loads(output.read_text(encoding="utf-8"))["ratings_sha256"]
    with pytest.raises(FileExistsError):
        freeze_proxy_split(ratings, output)
